"""
Scraper da Dental MedSul.

Plataforma  : Magento 1 (Yepcomm), sem API pública de catálogo (sem GraphQL)
Estratégia  : página de busca HTML via curl-cffi (impersonação Chrome)
URL de busca: https://www.dentalmedsul.com.br/catalogsearch/result/?q={query}&limit={n}

Particularidades (verificadas em 10/2026):
  - O ``dataLayer`` da página traz ``categoryProducts`` (id, nome, SKU e preço
    normal) em JSON; o restante vem do card ``li[data-product-id=<id>]``:
    link, imagem, resumo da embalagem ("Embalagem com 1 seringa de 4g."),
    preço "de" (``.old-price``) e preço à vista (último ``.price``, ~3% menor).
  - Produto esgotado: card sem preço e com "Me avise quando chegar".
  - Com um único resultado, o Magento redireciona para a página do produto
    (``pageType`` "catalog/product/view", dados em ``productId``/``productName``…).
  - Configuráveis (luvas P/M/G) aparecem uma vez; os tamanhos só estão na
    página do produto e, nos casos vistos, têm o mesmo preço.
  - Sem EAN; a marca é o sufixo " - Marca" do nome.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests  # type: ignore
    _HAS_CURL_CFFI = False

from matching import package_excerpt, split_brand_suffix

from .base import ScraperError, call_with_retries, parse_brl
from .dentalcremer import DentalCremerProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DENTAL_MEDSUL_BASE = "https://www.dentalmedsul.com.br"
SEARCH_URL = f"{DENTAL_MEDSUL_BASE}/catalogsearch/result/"
DEFAULT_TIMEOUT = 20
DEFAULT_PAGE_SIZE = 36

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": DENTAL_MEDSUL_BASE,
}

_DATALAYER_RE = re.compile(r"var dataLayer = \[\((\{.*?\})\)\]", re.S)
_OUT_OF_STOCK_TEXT = "me avise"
_PRODUCT_PAGE = "catalog/product/view"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_datalayer(html: str) -> dict:
    """Objeto do ``dataLayer`` da página; levanta ScraperError se não houver."""
    m = _DATALAYER_RE.search(html)
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("pageType"):
            return data
    raise ScraperError(
        "A Dental MedSul devolveu uma página inesperada (possível bloqueio ou layout novo)."
    )


def _price_box(scope: Tag) -> tuple[float, Optional[float]]:
    """(preço "de", preço à vista) do ``.price-box``; 0.0/None quando ausentes."""
    box = scope.select_one(".price-box")
    if box is None:
        return 0.0, None
    old = box.select_one(".old-price")
    preco_de = parse_brl(old.get_text(strip=True)) if old else 0.0
    prices = [
        parse_brl(p.get_text(strip=True))
        for p in box.select(".price")
        if not p.find_parent(class_="old-price")
    ]
    prices = [p for p in prices if p > 0]
    return preco_de, (prices[-1] if prices else None)


def _build_product(
    *,
    nome: str,
    sku: str,
    preco: float,
    preco_de: float,
    a_vista: Optional[float],
    url: str,
    imagem_url: str,
    disponivel: bool,
    resumo: str,
) -> DentalCremerProduct:
    preco_original = max(preco_de, preco)
    preco_pix = a_vista if a_vista and 0 < a_vista < preco else None
    pct = round((1 - preco / preco_original) * 100) if preco_original > preco else 0
    return DentalCremerProduct(
        nome=nome,
        sku=sku,
        preco_original=preco_original,
        preco_final=preco,
        preco_pix=preco_pix,
        percentual_desconto=pct,
        url=url,
        imagem_url=imagem_url,
        disponivel=disponivel,
        marca=split_brand_suffix(nome)[1],
        categoria="",
        fonte="Dental MedSul",
        descricao=package_excerpt(resumo),
    )


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class DentalMedSulScraper:
    """
    Busca produtos na Dental MedSul (página de busca do Magento).

    Uso::

        scraper = DentalMedSulScraper()
        produtos = scraper.buscar("transbond xt")
        for p in produtos:
            print(p.nome, p.preco_final, p.preco_pix, p.url)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def buscar(
        self,
        termo: str,
        tamanho: int = DEFAULT_PAGE_SIZE,
    ) -> list[DentalCremerProduct]:
        """
        Busca produtos por texto.

        Args:
            termo:   Texto de busca.
            tamanho: Quantidade máxima de resultados (12, 24 ou 36 por página).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Dental MedSul"``.

        Raises:
            ScraperError: falha de rede, bloqueio ou página fora do formato.
        """
        if not termo or not termo.strip():
            return []

        params = {"q": termo.strip(), "limit": 36 if tamanho > 24 else 24 if tamanho > 12 else 12}
        extra = {"impersonate": "chrome124"} if _HAS_CURL_CFFI else {}
        try:
            resp = call_with_retries(
                lambda: cffi_requests.get(
                    SEARCH_URL,
                    params=params,
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout,
                    **extra,
                ),
                label="[DentalMedSul]",
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[DentalMedSul] Erro ao buscar '%s': %s", termo, exc)
            raise ScraperError(f"Não foi possível consultar a Dental MedSul: {exc}") from exc

        produtos = self.parse_page(resp.text, str(resp.url))
        logger.info("[DentalMedSul] %d produto(s) para '%s'.", len(produtos), termo)
        return produtos[:tamanho]

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_page(self, html: str, url: str = "") -> list[DentalCremerProduct]:
        """Produtos de uma página de busca ou, no redirecionamento, de produto."""
        data = _read_datalayer(html)
        soup = BeautifulSoup(html, "html.parser")
        if data.get("pageType") == _PRODUCT_PAGE:
            produto = self._parse_product_page(soup, data, url)
            return [produto] if produto else []

        items = data.get("categoryProducts")
        if not isinstance(items, list):
            raise ScraperError(
                "Resposta inesperada da Dental MedSul (o layout pode ter mudado)."
            )
        cards = {li.get("data-product-id"): li for li in soup.select("li[data-product-id]")}

        produtos: list[DentalCremerProduct] = []
        for item in items:
            try:
                produto = self._parse_card(item, cards.get(str(item.get("id"))))
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("[DentalMedSul] Falha ao parsear '%s': %s", item.get("name"), exc)
                continue
            if produto:
                produtos.append(produto)
        return produtos

    @staticmethod
    def _parse_card(item: dict, card: Optional[Tag]) -> Optional[DentalCremerProduct]:
        nome = str(item.get("name") or "").strip()
        preco = parse_brl(str(item.get("price") or "").replace(".", ","))
        if not nome or card is None:
            return None

        link = card.select_one("a.product-name") or card.select_one("a[href]")
        img = card.select_one("img")
        resumo = card.select_one("a.product-name + div[class*='line-clamp-2']")
        preco_de, a_vista = _price_box(card)
        disponivel = _OUT_OF_STOCK_TEXT not in card.get_text(" ", strip=True).lower()
        if preco <= 0:
            preco = a_vista or 0.0
        if preco <= 0:
            return None

        return _build_product(
            nome=nome,
            sku=str(item.get("sku") or item.get("id") or "").strip(),
            preco=preco,
            preco_de=preco_de,
            a_vista=a_vista,
            url=link["href"] if link else "",
            imagem_url=img.get("src", "") if img else "",
            disponivel=disponivel,
            resumo=resumo.get_text(" ", strip=True) if resumo else "",
        )

    @staticmethod
    def _parse_product_page(
        soup: BeautifulSoup, data: dict, url: str
    ) -> Optional[DentalCremerProduct]:
        nome = str(data.get("productName") or "").strip()
        preco = parse_brl(str(data.get("productPrice") or "").replace(".", ","))
        shop = soup.select_one(".product-shop") or soup
        preco_de, a_vista = _price_box(shop)
        if not nome or preco <= 0:
            return None

        og_url = soup.select_one('meta[property="og:url"]')
        og_img = soup.select_one('meta[property="og:image"]')
        resumo = soup.select_one(".short-description")
        return _build_product(
            nome=nome,
            sku=str(data.get("productSku") or data.get("productId") or "").strip(),
            preco=preco,
            preco_de=preco_de,
            a_vista=a_vista,
            url=(og_url.get("content") if og_url else "") or url,
            imagem_url=og_img.get("content", "") if og_img else "",
            disponivel=_OUT_OF_STOCK_TEXT not in shop.get_text(" ", strip=True).lower(),
            resumo=resumo.get_text(" ", strip=True) if resumo else "",
        )


# ---------------------------------------------------------------------------
# Execução standalone para testes rápidos:  python -m scrapers.dentalmedsul "termo"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "transbond xt"
    for i, p in enumerate(DentalMedSulScraper().buscar(termo_teste), 1):
        pix = f"  |  À vista: {p.preco_pix_formatado}" if p.preco_pix_formatado else ""
        print(f"\n[{i}] {p.nome}")
        print(f"     SKU      : {p.sku}  |  Marca: {p.marca}")
        print(f"     Preço    : {p.preco_formatado} (de R$ {p.preco_original:.2f}){pix}")
        print(f"     Estoque  : {'Disponível' if p.disponivel else 'Indisponível'}")
        print(f"     Embalagem: {p.descricao}")
        print(f"     URL      : {p.url}")
