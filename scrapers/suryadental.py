"""
Scraper da Surya Dental.

Plataforma  : Adobe Commerce (Magento 2 headless) com Akamai WAF
Estratégia  : GraphQL POST via curl-cffi (impersonação Chrome para bypass do WAF)
Endpoint    : POST https://www.suryadental.com.br/graphql
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests  # type: ignore
    _HAS_CURL_CFFI = False

from .dentalcremer import DentalCremerProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SURYA_BASE = "https://www.suryadental.com.br"
GRAPHQL_URL = f"{SURYA_BASE}/graphql"
DEFAULT_TIMEOUT = 20
DEFAULT_PAGE_SIZE = 20

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Origin": SURYA_BASE,
    "Referer": f"{SURYA_BASE}/",
}

PRODUCTS_QUERY = """
query SearchProducts($search: String!, $pageSize: Int!) {
  products(search: $search, pageSize: $pageSize, sort: { relevance: DESC }) {
    items {
      name
      sku
      url_key
      price_range {
        minimum_price {
          regular_price { value }
          final_price { value }
          discount { amount_off percent_off }
        }
      }
      small_image { url label }
      categories { name }
    }
    total_count
  }
}
"""


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class SuryaDentalScraper:
    """
    Busca produtos na Surya Dental via GraphQL (Adobe Commerce).

    Uso::

        scraper = SuryaDentalScraper()
        produtos = scraper.buscar("sonda exploradora")
        for p in produtos:
            print(p.nome, p.preco_final)
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
            tamanho: Quantidade máxima de resultados (máx 48).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Surya Dental"``.
        """
        if not termo or not termo.strip():
            return []

        payload = {
            "query": PRODUCTS_QUERY,
            "variables": {
                "search": termo.strip(),
                "pageSize": min(tamanho, 48),
            },
        }

        try:
            if _HAS_CURL_CFFI:
                resp = cffi_requests.post(
                    GRAPHQL_URL,
                    json=payload,
                    headers=DEFAULT_HEADERS,
                    impersonate="chrome124",
                    timeout=self.timeout,
                )
            else:
                resp = cffi_requests.post(
                    GRAPHQL_URL,
                    json=payload,
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout,
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("[SuryaDental] Erro ao buscar '%s': %s", termo, exc)
            return []

        if "errors" in data:
            logger.error("[SuryaDental] Erros GraphQL: %s", data["errors"])
            return []

        items_raw = (
            data.get("data", {}).get("products", {}).get("items", [])
        )
        total = data.get("data", {}).get("products", {}).get("total_count", 0)
        logger.info(
            "[SuryaDental] %d produto(s) recebidos (total=%d) para '%s'.",
            len(items_raw),
            total,
            termo,
        )

        produtos: list[DentalCremerProduct] = []
        for raw in items_raw:
            try:
                prod = self._parse_item(raw)
                if prod:
                    produtos.append(prod)
            except Exception as exc:
                logger.warning("[SuryaDental] Falha ao parsear item '%s': %s", raw.get("name"), exc)

        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_item(self, raw: dict) -> Optional[DentalCremerProduct]:
        """Converte um item GraphQL em DentalCremerProduct."""
        nome: str = (raw.get("name") or "").strip()
        sku: str = (raw.get("sku") or "").strip()
        url_key: str = (raw.get("url_key") or "").strip()

        if not nome or not url_key:
            return None

        url = f"{SURYA_BASE}/{url_key}"

        # Preços
        min_price = (
            raw.get("price_range", {})
            .get("minimum_price", {})
        )
        preco_original: float = float(
            (min_price.get("regular_price") or {}).get("value") or 0
        )
        preco_final: float = float(
            (min_price.get("final_price") or {}).get("value") or 0
        )
        if preco_final <= 0:
            preco_final = preco_original
        if preco_final <= 0:
            return None

        discount = min_price.get("discount") or {}
        pct: int = int(discount.get("percent_off") or 0)

        # Imagem
        small_image = raw.get("small_image") or {}
        imagem_url: str = small_image.get("url", "")

        # Categoria
        cats: list = raw.get("categories") or []
        categoria: str = cats[-1]["name"] if cats else ""

        # Marca via padrão "Nome - Marca"
        marca = ""
        if " - " in nome:
            marca = nome.rsplit(" - ", 1)[-1].strip()

        return DentalCremerProduct(
            nome=nome,
            sku=sku,
            preco_original=preco_original,
            preco_final=preco_final,
            preco_pix=None,
            percentual_desconto=pct,
            url=url,
            imagem_url=imagem_url,
            disponivel=True,
            marca=marca,
            categoria=categoria,
            fonte="Surya Dental",
        )


# ---------------------------------------------------------------------------
# Execução standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    termo = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = SuryaDentalScraper()
    resultados = scraper.buscar(termo, tamanho=5)
    print(f"\n=== Surya Dental — '{termo}' ({len(resultados)} resultados) ===")
    for p in resultados:
        print(f"  [{p.sku}] {p.nome} — R$ {p.preco_final:.2f}  {p.url}")
