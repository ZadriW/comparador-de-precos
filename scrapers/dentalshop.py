"""
Scraper da Dental Shop.

Plataforma  : VTEX IO (Intelligent Search)
Estratégia  : REST JSON — API pública, sem autenticação necessária
Endpoint    : GET https://www.dentalshop.com.br/api/io/_v/api/intelligent-search/
                  product_search/?query={query}&count={n}&page={p}

Cada variante (item/SKU da VTEX) vira um produto com nome, EAN, preço e
estoque próprios. A busca aceita o EAN como termo, o que permite achar o
produto exato da Odonto Master.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from matching import append_variation

from .base import ScraperError, retrying_session
from .dentalcremer import DentalCremerProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DENTAL_SHOP_BASE = "https://www.dentalshop.com.br"
SEARCH_URL = (
    f"{DENTAL_SHOP_BASE}/api/io/_v/api/intelligent-search/product_search/"
)
DEFAULT_TIMEOUT = 15
DEFAULT_PAGE_SIZE = 20

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Origin": DENTAL_SHOP_BASE,
    "Referer": f"{DENTAL_SHOP_BASE}/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_offer(item: dict) -> Optional[dict]:
    """
    Oferta de um item: a do primeiro vendedor com estoque e preço; sem
    estoque, a primeira com preço. None se nenhum vendedor tiver preço.
    """
    offers = [
        (s.get("commertialOffer") or {})
        for s in item.get("sellers") or []
        if isinstance(s, dict)
    ]
    offers = [o for o in offers if float(o.get("Price") or 0) > 0]
    for offer in offers:
        if int(offer.get("AvailableQuantity") or 0) > 0:
            return offer
    return offers[0] if offers else None


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class DentalShopScraper:
    """
    Busca produtos na Dental Shop via VTEX Intelligent Search.

    Uso::

        scraper = DentalShopScraper()
        produtos = scraper.buscar("sonda exploradora")
        for p in produtos:
            print(p.nome, p.preco_final)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.session = retrying_session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = timeout

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
            tamanho: Quantidade máxima de resultados (máx 50).
            pagina:  Número da página (1-based).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Dental Shop"``.

        Raises:
            ScraperError: falha de rede, HTTP ou resposta inválida.
        """
        if not termo or not termo.strip():
            return []

        # O padrão da VTEX é "and": basta uma palavra que não esteja no nome
        # da loja (ex.: "Procedimento") para a busca voltar quase vazia. Com
        # "or" a ordem continua por relevância e o comparador faz a triagem.
        params = {
            "query": termo.strip(),
            "count": min(tamanho, 50),
            "page": pagina,
            "operator": "or",
        }

        try:
            resp = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout as exc:
            logger.error("[DentalShop] Timeout ao buscar '%s'.", termo)
            raise ScraperError("Tempo limite excedido ao acessar a Dental Shop.") from exc
        except requests.ConnectionError as exc:
            logger.error("[DentalShop] Erro de conexão: %s", exc)
            raise ScraperError("Não foi possível conectar à Dental Shop.") from exc
        except requests.HTTPError as exc:
            logger.error("[DentalShop] HTTP %s.", exc.response.status_code)
            raise ScraperError(
                f"Erro HTTP {exc.response.status_code} da Dental Shop."
            ) from exc
        except Exception as exc:
            logger.exception("[DentalShop] Erro inesperado: %s", exc)
            raise ScraperError(f"Erro inesperado na Dental Shop: {exc}") from exc

        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            logger.error("[DentalShop] Resposta fora do formato esperado: %.200s", data)
            raise ScraperError("Resposta inesperada da Dental Shop (a API pode ter mudado).")

        produtos: list[DentalCremerProduct] = []
        for raw in data.get("products", []):
            try:
                produtos.extend(self._parse_product(raw))
            except Exception as exc:
                logger.warning("[DentalShop] Falha ao parsear produto: %s", exc)

        logger.info("[DentalShop] %d produto(s) extraídos para '%s'.", len(produtos), termo)
        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_product(self, raw: dict) -> list[DentalCremerProduct]:
        """
        Converte um produto da API VTEX em um DentalCremerProduct por variante.

        Cada item (SKU) da VTEX é uma variante com nome ("P", "M"…), EAN,
        preço e estoque próprios — ex.: "Luva de Látex com Pó - Medix" tem
        os itens PP, P, M e G.
        """
        nome: str = (raw.get("productName") or "").strip()
        if not nome:
            return []

        link: str = raw.get("link", "")
        url = f"{DENTAL_SHOP_BASE}{link}" if link.startswith("/") else link

        brand: str = raw.get("brand", "")
        sku_ref: str = raw.get("productReference", "")

        # Categoria (última na hierarquia)
        cats: list = raw.get("categories", [])
        categoria = cats[-1].strip("/").split("/")[-1] if cats else ""

        items: list = [i for i in raw.get("items") or [] if isinstance(i, dict)]
        multiplas = len(items) > 1

        produtos: list[DentalCremerProduct] = []
        for item in items:
            offer = _pick_offer(item)
            if offer is None:
                continue

            preco_final: float = float(offer.get("Price") or 0)
            preco_original: float = max(float(offer.get("ListPrice") or 0), preco_final)
            disponivel: bool = int(offer.get("AvailableQuantity") or 0) > 0

            # Preço à vista (a VTEX não diferencia o Pix neste endpoint)
            spot: float = float(offer.get("spotPrice") or preco_final)
            preco_pix: Optional[float] = spot if spot < preco_final else None

            pct = 0
            if preco_original > preco_final:
                pct = round((1 - preco_final / preco_original) * 100)

            item_id = str(item.get("itemId") or "")
            # O nome do item é a variação ("P"); em produtos sem variação
            # ele repete o nome do produto e é ignorado por append_variation.
            variacao = (item.get("name") or "").strip()
            if variacao.lower() == nome.lower():
                variacao = ""

            images = item.get("images") or []
            imagem_url = images[0].get("imageUrl", "") if images else ""

            produtos.append(
                DentalCremerProduct(
                    nome=append_variation(nome, [variacao]) if variacao else nome,
                    sku=item_id if multiplas else (sku_ref or item_id),
                    preco_original=preco_original,
                    preco_final=preco_final,
                    preco_pix=preco_pix,
                    percentual_desconto=pct,
                    # ?skuId= abre a página já com a variante selecionada
                    url=f"{url}?skuId={item_id}" if multiplas and item_id else url,
                    imagem_url=imagem_url,
                    disponivel=disponivel,
                    marca=brand,
                    categoria=categoria,
                    fonte="Dental Shop",
                    ean=str(item.get("ean") or "").strip(),
                    variacao=variacao,
                )
            )

        return produtos


# ---------------------------------------------------------------------------
# Execução standalone:  python -m scrapers.dentalshop "termo"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    termo = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = DentalShopScraper()
    resultados = scraper.buscar(termo, tamanho=5)
    print(f"\n=== Dental Shop — '{termo}' ({len(resultados)} resultados) ===")
    for p in resultados:
        pix = f"  Pix: R$ {p.preco_pix:.2f}" if p.preco_pix else ""
        print(f"  [{p.sku}] {p.nome} — R$ {p.preco_final:.2f}{pix}  {p.url}")
