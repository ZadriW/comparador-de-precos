"""
Scraper da Dental Odonto Master
Utiliza a API GraphQL da plataforma Wake/Fbits.

Endpoint  : https://storefront-api.fbits.net/graphql
Auth      : header TCS-Access-Token
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

GRAPHQL_ENDPOINT = "https://storefront-api.fbits.net/graphql"
ACCESS_TOKEN = "tcs_odont_803f41baa4af4d42ae096a75514ea458"
BASE_URL = "https://www.odontomaster.com.br"

DEFAULT_HEADERS = {
    "TCS-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# Queries GraphQL
# ---------------------------------------------------------------------------

QUERY_BY_SKU = """
query GetProductBySku($sku: [String]) {
  products(first: 1, filters: { sku: $sku }) {
    nodes {
      productName
      sku
      productId
      productVariantId
      aliasComplete
      prices {
        price
        listPrice
      }
    }
  }
}
"""

QUERY_SEARCH = """
query SearchProducts($query: String!, $first: Int!) {
  search(query: $query) {
    products(first: $first) {
      nodes {
        productName
        sku
        productId
        productVariantId
        aliasComplete
        prices {
          price
          listPrice
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------


@dataclass
class OdontoMasterProduct:
    name: str
    sku: str
    product_id: int | None
    variant_id: int | None
    price: float
    list_price: float
    url: str
    image_url: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def discount_pct(self) -> float:
        """Percentual de desconto em relação ao preço de lista."""
        if self.list_price and self.list_price > self.price:
            return round((1 - self.price / self.list_price) * 100, 1)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sku": self.sku,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "price": self.price,
            "list_price": self.list_price,
            "discount_pct": self.discount_pct,
            "url": self.url,
            "image_url": self.image_url,
            "source": "Odonto Master",
        }


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class OdontoMasterScraper:
    """
    Realiza consultas à API GraphQL da Odonto Master (plataforma Wake/Fbits).

    Uso básico::

        scraper = OdontoMasterScraper()
        product = scraper.get_by_sku("143026")
        if product:
            print(product.name, product.price)
    """

    def __init__(self, timeout: int = 15) -> None:
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------

    def get_by_sku(self, sku: str) -> OdontoMasterProduct | None:
        """
        Busca um produto pelo SKU exato.

        Returns:
            OdontoMasterProduct ou None se não encontrado.
        """
        sku = sku.strip()
        logger.info("[OdontoMaster] Buscando SKU: %s", sku)

        payload = {
            "query": QUERY_BY_SKU,
            "variables": {"sku": [sku]},
        }

        data = self._post(payload)
        if data is None:
            return None

        nodes = data.get("data", {}).get("products", {}).get("nodes", [])

        if not nodes:
            logger.warning("[OdontoMaster] SKU '%s' não encontrado.", sku)
            return None

        return self._parse_node(nodes[0])

    def search(self, query: str, first: int = 10) -> list[OdontoMasterProduct]:
        """
        Busca produtos por texto livre.

        Args:
            query: Termo de busca (ex: nome do produto).
            first: Quantidade máxima de resultados.

        Returns:
            Lista de OdontoMasterProduct (pode estar vazia).
        """
        query_str = query.strip()
        logger.info("[OdontoMaster] Buscando texto: '%s' (first=%d)", query_str, first)

        payload = {
            "query": QUERY_SEARCH,
            "variables": {"query": query_str, "first": first},
        }

        data = self._post(payload)
        if data is None:
            return []

        nodes = (
            data.get("data", {}).get("search", {}).get("products", {}).get("nodes", [])
        )

        products = [self._parse_node(n) for n in nodes]
        logger.info(
            "[OdontoMaster] %d produto(s) encontrado(s) para '%s'.",
            len(products),
            query_str,
        )
        return products

    # ------------------------------------------------------------------
    # Métodos privados
    # ------------------------------------------------------------------

    def _post(self, payload: dict) -> dict | None:
        """Envia a requisição GraphQL e retorna o JSON ou None em caso de erro."""
        try:
            response = self.session.post(
                GRAPHQL_ENDPOINT,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            # GraphQL pode retornar erros mesmo com status 200
            if "errors" in result:
                logger.error(
                    "[OdontoMaster] Erros na resposta GraphQL: %s",
                    result["errors"],
                )
                return None

            return result

        except requests.exceptions.Timeout:
            logger.error("[OdontoMaster] Timeout ao conectar ao endpoint GraphQL.")
        except requests.exceptions.ConnectionError as exc:
            logger.error("[OdontoMaster] Erro de conexão: %s", exc)
        except requests.exceptions.HTTPError as exc:
            logger.error("[OdontoMaster] HTTP %s: %s", exc.response.status_code, exc)
        except ValueError as exc:
            logger.error("[OdontoMaster] Resposta inválida (não é JSON): %s", exc)

        return None

    @staticmethod
    def _parse_node(node: dict) -> OdontoMasterProduct:
        """Converte um nó GraphQL em OdontoMasterProduct."""
        alias = node.get("aliasComplete", "")
        url = f"{BASE_URL}/{alias}" if alias else BASE_URL

        prices = node.get("prices") or {}
        price = float(prices.get("price") or 0)
        list_price = float(prices.get("listPrice") or price)

        return OdontoMasterProduct(
            name=node.get("productName", "Sem nome"),
            sku=node.get("sku", ""),
            product_id=node.get("productId"),
            variant_id=node.get("productVariantId"),
            price=price,
            list_price=list_price,
            url=url,
            image_url="",
            raw=node,
        )


# ---------------------------------------------------------------------------
# Teste rápido (execução direta)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    scraper = OdontoMasterScraper()

    print("=== Busca por SKU ===")
    prod = scraper.get_by_sku("143026")
    if prod:
        print(f"  Nome   : {prod.name}")
        print(f"  SKU    : {prod.sku}")
        print(f"  Preço  : R$ {prod.price:.2f}")
        print(f"  Lista  : R$ {prod.list_price:.2f}")
        print(f"  Desc.  : {prod.discount_pct}%")
        print(f"  URL    : {prod.url}")
    else:
        print("  Produto não encontrado.")

    print("\n=== Busca por texto ===")
    results = scraper.search("sonda exploradora", first=3)
    for r in results:
        print(f"  [{r.sku}] {r.name} — R$ {r.price:.2f} → {r.url}")
