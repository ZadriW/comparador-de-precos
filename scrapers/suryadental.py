"""
Scraper da Surya Dental.

Plataforma  : Adobe Commerce (Magento 2 headless / PWA) com Akamai WAF
Estratégia  : GraphQL POST via curl-cffi (impersonação Chrome para bypass do WAF)
Endpoint    : POST https://www.suryadental.com.br/graphql

Particularidades da API (verificadas em 10/2026):
  - A URL do produto é ``url_key + url_suffix`` (".html"); só ``url_key`` dá 404.
  - ``price_range.regular_price`` já vem com a promoção aplicada; o preço "de"
    real está em ``table_price[].price`` quando ``promo_price`` é o preço final.
  - ``first_price`` é o preço à vista no Pix (3% de desconto).
  - A página do produto é renderizada no navegador, então o GraphQL é a fonte
    dos preços exibidos no site.
  - Não use ``sort: { relevance: DESC }``: com mais de uma palavra ele devolve
    itens irrelevantes primeiro ("Luva Procedimento Látex Pó" → kit de silicone).
    Sem ``sort``, a ordem já é a de relevância da loja.
  - O GraphQL não expõe EAN e a busca por EAN não retorna nada. Luvas e
    resinas vêm como SimpleProduct, sem tamanhos/cores; só ConfigurableProduct
    traz variantes.
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

from matching import append_variation, package_excerpt

from .base import ScraperError, call_with_retries, html_to_text
from .dentalcremer import DentalCremerProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SURYA_BASE = "https://www.suryadental.com.br"
GRAPHQL_URL = f"{SURYA_BASE}/graphql"
DEFAULT_TIMEOUT = 20
DEFAULT_PAGE_SIZE = 20
DEFAULT_URL_SUFFIX = ".html"

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

# Categorias de vitrine/campanha que não descrevem o produto
_PROMO_CATEGORIES = (
    "ganhe desconto",
    "promo",
    "oferta",
    "black",
    "outlet",
    "liquida",
    "lancamento",
    "lançamento",
    "mais vendidos",
    "marcas",
)

# Campos de preço/estoque comuns ao produto e às variantes
_PRICE_FIELDS = """
      stock_status
      special_price
      first_price
      table_price { price promo_price }
      price_range {
        minimum_price {
          regular_price { value }
          final_price { value }
        }
      }
"""

PRODUCTS_QUERY = """
query SearchProducts($search: String!, $pageSize: Int!) {
  products(search: $search, pageSize: $pageSize) {
    items {
      __typename
      name
      sku
      url_key
      url_suffix
      canonical_url
      small_image { url }
      short_description { html }
      categories { name level }
%(price_fields)s
      ... on ConfigurableProduct {
        variants {
          attributes { label }
          product {
            name
            sku
            small_image { url }
%(price_fields)s
          }
        }
      }
    }
    total_count
  }
}
""" % {"price_fields": _PRICE_FIELDS}


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _extract_prices(raw: dict) -> Optional[tuple[float, float, Optional[float]]]:
    """
    Extrai (preco_original, preco_final, preco_pix) de um produto/variante.
    Retorna None se o produto não tiver preço válido.
    """
    min_price = (raw.get("price_range") or {}).get("minimum_price") or {}
    regular = _to_float((min_price.get("regular_price") or {}).get("value"))
    final = _to_float((min_price.get("final_price") or {}).get("value"))
    if final <= 0:
        final = regular
    if final <= 0:
        return None

    # Preço "de": tabela cuja promoção é exatamente o preço final exibido
    original = max(regular, final)
    for table in raw.get("table_price") or []:
        promo = _to_float(table.get("promo_price"))
        price = _to_float(table.get("price"))
        if promo > 0 and abs(promo - final) < 0.01 and price > original:
            original = price

    pix = _to_float(raw.get("first_price"))
    preco_pix = pix if 0 < pix < final else None

    return original, final, preco_pix


def _pick_category(categories: list, marca: str) -> str:
    """Escolhe a categoria mais específica, ignorando vitrines e a própria marca."""
    marca_l = marca.lower()
    candidatas = [
        c
        for c in categories or []
        if c.get("name")
        and c["name"].strip().lower() != marca_l
        and not any(p in c["name"].lower() for p in _PROMO_CATEGORIES)
    ]
    if not candidatas:
        return ""
    best = max(candidatas, key=lambda c: c.get("level") or 0)
    return best["name"].strip()


def _extract_brand(nome: str) -> str:
    """Extrai a marca do padrão 'Produto - Marca'."""
    if " - " in nome:
        return nome.rsplit(" - ", 1)[-1].strip()
    return ""


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
            tamanho: Quantidade máxima de resultados (máx 48).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Surya Dental"``.
            Produtos configuráveis são expandidos em uma entrada por variante.

        Raises:
            ScraperError: falha de rede, bloqueio ou erro GraphQL.
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

        extra = {"impersonate": "chrome124"} if _HAS_CURL_CFFI else {}
        try:
            resp = call_with_retries(
                lambda: cffi_requests.post(
                    GRAPHQL_URL,
                    json=payload,
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout,
                    **extra,
                ),
                label="[SuryaDental]",
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("[SuryaDental] Erro ao buscar '%s': %s", termo, exc)
            raise ScraperError(f"Não foi possível consultar a Surya Dental: {exc}") from exc

        if not isinstance(data, dict):
            raise ScraperError("Resposta inesperada da Surya Dental.")
        if data.get("errors"):
            logger.error("[SuryaDental] Erros GraphQL: %s", data["errors"])
            msg = data["errors"][0].get("message", "erro desconhecido")
            raise ScraperError(f"Erro na API da Surya Dental: {msg}")

        products_data = (data.get("data") or {}).get("products")
        if not isinstance(products_data, dict) or not isinstance(products_data.get("items"), list):
            logger.error("[SuryaDental] Resposta fora do formato esperado: %.200s", data)
            raise ScraperError("Resposta inesperada da Surya Dental (a API pode ter mudado).")
        items_raw = products_data["items"]
        logger.info(
            "[SuryaDental] %d produto(s) recebidos (total=%d) para '%s'.",
            len(items_raw),
            products_data.get("total_count") or 0,
            termo,
        )

        produtos: list[DentalCremerProduct] = []
        for raw in items_raw:
            try:
                produtos.extend(self._parse_item(raw))
            except Exception as exc:
                logger.warning(
                    "[SuryaDental] Falha ao parsear item '%s': %s", raw.get("name"), exc
                )

        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_item(self, raw: dict) -> list[DentalCremerProduct]:
        """Converte um item GraphQL em um ou mais DentalCremerProduct."""
        nome: str = (raw.get("name") or "").strip()
        if not nome:
            return []

        url = self._build_url(raw)
        if not url:
            return []

        marca = _extract_brand(nome)
        categoria = _pick_category(raw.get("categories") or [], marca)
        imagem_pai = (raw.get("small_image") or {}).get("url", "")
        descricao = package_excerpt(
            html_to_text((raw.get("short_description") or {}).get("html"))
        )

        variants = raw.get("variants") or []
        if raw.get("__typename") == "ConfigurableProduct" and variants:
            produtos = []
            for variant in variants:
                vprod = variant.get("product") or {}
                labels = [
                    a.get("label") for a in variant.get("attributes") or [] if a.get("label")
                ]
                vnome = append_variation(nome, labels) if labels else (
                    vprod.get("name") or nome
                )
                prod = self._build_product(
                    vprod,
                    nome=vnome,
                    url=url,
                    marca=marca,
                    categoria=categoria,
                    imagem_fallback=imagem_pai,
                    variacao=" / ".join(labels),
                    descricao=descricao,
                )
                if prod:
                    produtos.append(prod)
            return produtos

        prod = self._build_product(
            raw,
            nome=nome,
            url=url,
            marca=marca,
            categoria=categoria,
            imagem_fallback=imagem_pai,
            descricao=descricao,
        )
        return [prod] if prod else []

    @staticmethod
    def _build_url(raw: dict) -> str:
        url_key: str = (raw.get("url_key") or "").strip()
        if url_key:
            suffix = raw.get("url_suffix") or DEFAULT_URL_SUFFIX
            return f"{SURYA_BASE}/{url_key}{suffix}"
        canonical: str = (raw.get("canonical_url") or "").strip()
        if canonical:
            return f"{SURYA_BASE}/{canonical.lstrip('/')}"
        return ""

    @staticmethod
    def _build_product(
        raw: dict,
        *,
        nome: str,
        url: str,
        marca: str,
        categoria: str,
        imagem_fallback: str,
        variacao: str = "",
        descricao: str = "",
    ) -> Optional[DentalCremerProduct]:
        prices = _extract_prices(raw)
        if prices is None:
            return None
        preco_original, preco_final, preco_pix = prices

        pct = 0
        if preco_original > preco_final:
            pct = round((1 - preco_final / preco_original) * 100)

        imagem_url = (raw.get("small_image") or {}).get("url") or imagem_fallback

        return DentalCremerProduct(
            nome=nome,
            sku=(raw.get("sku") or "").strip(),
            preco_original=preco_original,
            preco_final=preco_final,
            preco_pix=preco_pix,
            percentual_desconto=pct,
            url=url,
            imagem_url=imagem_url,
            disponivel=raw.get("stock_status") == "IN_STOCK",
            marca=marca,
            categoria=categoria,
            fonte="Surya Dental",
            variacao=variacao,
            descricao=descricao,
        )


# ---------------------------------------------------------------------------
# Execução standalone:  python -m scrapers.suryadental "termo"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    termo = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = SuryaDentalScraper()
    resultados = scraper.buscar(termo, tamanho=5)
    print(f"\n=== Surya Dental — '{termo}' ({len(resultados)} resultados) ===")
    for p in resultados:
        de = f" (de R$ {p.preco_original:.2f})" if p.preco_original > p.preco_final else ""
        pix = f"  Pix: R$ {p.preco_pix:.2f}" if p.preco_pix else ""
        estoque = "" if p.disponivel else "  [INDISPONÍVEL]"
        print(f"  [{p.sku}] {p.nome} — R$ {p.preco_final:.2f}{de}{pix}{estoque}")
        print(f"      {p.url}  | {p.categoria}")
