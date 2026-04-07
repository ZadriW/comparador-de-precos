"""
Scraper da Dental Shop.

Plataforma  : VTEX IO (Intelligent Search)
Estratégia  : REST JSON — API pública, sem autenticação necessária
Endpoint    : GET https://www.dentalshop.com.br/api/io/_v/api/intelligent-search/
                  product_search/?query={query}&count={n}&page={p}
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

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
        self.session = requests.Session()
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
        """
        if not termo or not termo.strip():
            return []

        params = {
            "query": termo.strip(),
            "count": min(tamanho, 50),
            "page": pagina,
        }

        try:
            resp = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            logger.error("[DentalShop] Timeout ao buscar '%s'.", termo)
            return []
        except requests.ConnectionError as exc:
            logger.error("[DentalShop] Erro de conexão: %s", exc)
            return []
        except requests.HTTPError as exc:
            logger.error("[DentalShop] HTTP %s.", exc.response.status_code)
            return []
        except Exception as exc:
            logger.exception("[DentalShop] Erro inesperado: %s", exc)
            return []

        produtos: list[DentalCremerProduct] = []
        for raw in data.get("products", []):
            try:
                prod = self._parse_product(raw)
                if prod:
                    produtos.append(prod)
            except Exception as exc:
                logger.warning("[DentalShop] Falha ao parsear produto: %s", exc)

        logger.info("[DentalShop] %d produto(s) extraídos para '%s'.", len(produtos), termo)
        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_product(self, raw: dict) -> Optional[DentalCremerProduct]:
        """Converte um produto da API VTEX em DentalCremerProduct."""
        nome: str = raw.get("productName", "").strip()
        if not nome:
            return None

        link: str = raw.get("link", "")
        url = f"{DENTAL_SHOP_BASE}{link}" if link.startswith("/") else link

        brand: str = raw.get("brand", "")
        sku_ref: str = raw.get("productReference", "")

        # Primeira variante (sku/item)
        items: list = raw.get("items", [])
        if not items:
            return None

        item = items[0]
        sellers = item.get("sellers", [])
        if not sellers:
            return None

        offer = sellers[0].get("commertialOffer", {})
        preco_final: float = float(offer.get("Price") or 0)
        preco_original: float = float(offer.get("ListPrice") or preco_final)
        disponivel: bool = int(offer.get("AvailableQuantity", 0)) > 0

        if preco_final <= 0:
            return None

        # SKU real do item
        sku: str = sku_ref or item.get("itemId", "")

        # Preço Pix: mesma que spot (VTEX não diferencia neste endpoint)
        spot: float = float(offer.get("spotPrice") or preco_final)
        preco_pix: Optional[float] = spot if spot < preco_final else None

        # Desconto
        pct = 0
        if preco_original > preco_final:
            pct = round((1 - preco_final / preco_original) * 100)

        # Imagem da primeira variante
        images = item.get("images", [])
        imagem_url = images[0].get("imageUrl", "") if images else ""

        # Categoria (última na hierarquia)
        cats: list = raw.get("categories", [])
        categoria = cats[-1].strip("/").split("/")[-1] if cats else ""

        return DentalCremerProduct(
            nome=nome,
            sku=sku,
            preco_original=preco_original,
            preco_final=preco_final,
            preco_pix=preco_pix,
            percentual_desconto=pct,
            url=url,
            imagem_url=imagem_url,
            disponivel=disponivel,
            marca=brand,
            categoria=categoria,
            fonte="Dental Shop",
        )


# ---------------------------------------------------------------------------
# Execução standalone
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
