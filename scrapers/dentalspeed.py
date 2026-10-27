"""
Scraper da Dental Speed.

Plataforma  : Magento 2 com Akamai WAF; busca do site feita pela SmartHint
Estratégia  : 1) API SmartHint (JSON, mesmo provedor da Dental Cremer, shcode SH-176187)
              2) Fallback: HTML parsing via curl-cffi (impersonação Chrome para bypass do WAF)
URL de busca: https://www.dentalspeed.com/catalogsearch/result/?q={query}&limit={n}

A busca HTML do Magento é do tipo "OU" e prioriza a marca (ex.: "sonda
exploradora ice" devolve qualquer produto Ice), por isso só é usada quando
a SmartHint falha.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests  # type: ignore
    _HAS_CURL_CFFI = False

from .base import ScraperError, call_with_retries
from .dentalcremer import DentalCremerProduct, SmartHintScraper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DENTAL_SPEED_BASE = "https://www.dentalspeed.com"
SEARCH_URL = f"{DENTAL_SPEED_BASE}/catalogsearch/result/"
SMARTHINT_SHCODE = "SH-176187"
DEFAULT_TIMEOUT = 20
_NO_RESULTS_TEXT = "não retornou resultados"
DEFAULT_PAGE_SIZE = 24

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": DENTAL_SPEED_BASE,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_price(price_str: str) -> float:
    """Converte string de preço ('R$14,90' ou '1.234,50') para float."""
    cleaned = re.sub(r"[R$\s]", "", price_str)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _extract_brand(nome: str) -> str:
    """Extrai a marca do padrão 'Produto - Marca'."""
    if " - " in nome:
        return nome.rsplit(" - ", 1)[-1].strip()
    return ""


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class DentalSpeedScraper:
    """
    Busca produtos na Dental Speed (SmartHint, com fallback para HTML).

    Uso::

        scraper = DentalSpeedScraper()
        produtos = scraper.buscar("sonda exploradora")
        for p in produtos:
            print(p.nome, p.preco_final)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._smarthint = SmartHintScraper(
            shcode=SMARTHINT_SHCODE,
            base_url=DENTAL_SPEED_BASE,
            fonte="Dental Speed",
            timeout=timeout,
        )

    def buscar(
        self,
        termo: str,
        tamanho: int = DEFAULT_PAGE_SIZE,
        pagina: int = 1,
    ) -> list[DentalCremerProduct]:
        """
        Busca produtos por texto.

        Args:
            termo:   Texto de busca.
            tamanho: Quantidade máxima de itens por página.
            pagina:  Número da página (começa em 1).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Dental Speed"``.

        Raises:
            ScraperError: se a SmartHint e o fallback HTML falharem.
        """
        if not termo or not termo.strip():
            return []

        resultado = self._smarthint.buscar(termo, tamanho=tamanho, pagina=pagina - 1)
        if resultado.sucesso:
            logger.info(
                "[DentalSpeed] %d produto(s) via SmartHint para '%s'.",
                len(resultado.produtos),
                termo,
            )
            return resultado.produtos

        logger.warning(
            "[DentalSpeed] SmartHint falhou (%s); usando busca HTML.", resultado.erro
        )
        return self._buscar_html(termo, tamanho, pagina)

    # ------------------------------------------------------------------
    # Fallback HTML
    # ------------------------------------------------------------------

    def _buscar_html(
        self, termo: str, tamanho: int, pagina: int
    ) -> list[DentalCremerProduct]:
        params = {
            "q": termo.strip(),
            "limit": min(tamanho, 96),
            "p": pagina,
        }

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
                label="[DentalSpeed]",
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[DentalSpeed] Erro ao buscar '%s': %s", termo, exc)
            raise ScraperError(f"Não foi possível consultar a Dental Speed: {exc}") from exc

        return self._parse_html(resp.text)

    def _parse_html(self, html: str) -> list[DentalCremerProduct]:
        """Parseia o HTML da página de resultados do Magento 2."""
        soup = BeautifulSoup(html, "html.parser")
        products: list[DentalCremerProduct] = []

        items = soup.select("li.product-item")
        logger.debug("[DentalSpeed] %d li.product-item encontrados.", len(items))

        # Página sem produtos e sem o aviso de busca vazia = bloqueio do WAF
        # (desafio/captcha) ou layout novo — não é "nenhum produto".
        if not items and _NO_RESULTS_TEXT not in soup.get_text(" ", strip=True).lower():
            title = soup.title.get_text(strip=True) if soup.title else "sem título"
            logger.error("[DentalSpeed] Página inesperada na busca: '%s'", title)
            raise ScraperError(
                "A Dental Speed devolveu uma página inesperada (possível bloqueio)."
            )

        for item in items:
            try:
                prod = self._parse_item(item)
                if prod:
                    products.append(prod)
            except Exception as exc:
                logger.warning("[DentalSpeed] Falha ao parsear item: %s", exc)

        logger.info("[DentalSpeed] %d produto(s) extraídos do HTML.", len(products))
        return products

    def _parse_item(self, item) -> Optional[DentalCremerProduct]:
        """Parseia um único item da listagem."""
        # ── Nome + URL ────────────────────────────────────────────────
        name_tag = item.select_one("a.product-item-link") or item.select_one(
            ".product-item-name a"
        )
        if not name_tag:
            return None

        nome = name_tag.get_text(strip=True)
        url = name_tag.get("href", "").strip()
        if not nome or not url:
            return None

        # ── Imagem ────────────────────────────────────────────────────
        img = item.select_one("img.product-image-photo")
        imagem_url = img.get("src", "") if img else ""

        # ── SKU via data-sku ou URL ────────────────────────────────────
        sku_container = item.select_one("[data-sku]")
        sku = sku_container.get("data-sku", "") if sku_container else ""
        if not sku:
            m = re.search(r"-(\d+)\.html$", url)
            sku = m.group(1) if m else ""

        # ── Preço final ───────────────────────────────────────────────
        price_tag = (
            item.select_one(".special-price .price")
            or item.select_one(".price-wrapper .price")
            or item.select_one(".regular-price .price")
            or item.select_one("span.price")
        )
        preco_str = price_tag.get_text(strip=True) if price_tag else ""
        preco_final = _parse_price(preco_str)

        # Fallback: tenta extrair de texto de parcelamento "1x de R$XX,YY"
        if preco_final <= 0:
            full_text = item.get_text()
            m = re.search(r"1x\s*de\s*R\$\s*([\d.,]+)", full_text)
            if m:
                preco_final = _parse_price(m.group(1))

        if preco_final <= 0:
            return None

        # ── Preço original (de) ───────────────────────────────────────
        # O bloco .old-price pode existir vazio; nunca deixar o "de" abaixo do "por".
        old_tag = item.select_one(".old-price .price")
        preco_original = _parse_price(old_tag.get_text(strip=True)) if old_tag else 0.0
        preco_original = max(preco_original, preco_final)

        # ── Desconto ──────────────────────────────────────────────────
        pct = 0
        if preco_original > preco_final:
            pct = round((1 - preco_final / preco_original) * 100)

        # ── Marca via padrão "Nome - Marca" ───────────────────────────
        marca = _extract_brand(nome)

        return DentalCremerProduct(
            nome=nome,
            sku=sku,
            preco_original=preco_original,
            preco_final=preco_final,
            preco_pix=None,
            percentual_desconto=pct,
            url=url,
            imagem_url=imagem_url,
            # Item esgotado traz o selo "stock unavailable" e o botão "Avise-me"
            disponivel=item.select_one(".stock.unavailable") is None,
            marca=marca,
            categoria="",
            fonte="Dental Speed",
        )


# ---------------------------------------------------------------------------
# Execução standalone:  python -m scrapers.dentalspeed "termo"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    termo = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = DentalSpeedScraper()
    resultados = scraper.buscar(termo, tamanho=6)
    print(f"\n=== Dental Speed — '{termo}' ({len(resultados)} resultados) ===")
    for p in resultados:
        pix = f"  Pix: R$ {p.preco_pix:.2f}" if p.preco_pix else ""
        print(f"  [{p.sku}] {p.nome} — R$ {p.preco_final:.2f}{pix}  {p.url}")
