"""
Scraper da Dental Speed.

Plataforma  : Magento 2 (fbits) com Akamai WAF
Estratégia  : HTML parsing via curl-cffi (impersonação Chrome para bypass do WAF)
URL de busca: https://www.dentalspeed.com/catalogsearch/result/?q={query}&limit={n}
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

from .dentalcremer import DentalCremerProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DENTAL_SPEED_BASE = "https://www.dentalspeed.com"
SEARCH_URL = f"{DENTAL_SPEED_BASE}/catalogsearch/result/"
DEFAULT_TIMEOUT = 20
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
    Busca produtos na Dental Speed via HTML (Magento 2).

    Uso::

        scraper = DentalSpeedScraper()
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
        pagina: int = 1,
    ) -> list[DentalCremerProduct]:
        """
        Busca produtos por texto.

        Args:
            termo:   Texto de busca.
            tamanho: Quantidade máxima de itens por página (máx 96).
            pagina:  Número da página (começa em 1).

        Returns:
            Lista de DentalCremerProduct com ``fonte="Dental Speed"``.
        """
        if not termo or not termo.strip():
            return []

        params = {
            "q": termo.strip(),
            "limit": min(tamanho, 96),
            "p": pagina,
        }

        try:
            if _HAS_CURL_CFFI:
                resp = cffi_requests.get(
                    SEARCH_URL,
                    params=params,
                    headers=DEFAULT_HEADERS,
                    impersonate="chrome124",
                    timeout=self.timeout,
                )
            else:
                resp = cffi_requests.get(
                    SEARCH_URL,
                    params=params,
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout,
                )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[DentalSpeed] Erro ao buscar '%s': %s", termo, exc)
            return []

        return self._parse_html(resp.text)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_html(self, html: str) -> list[DentalCremerProduct]:
        """Parseia o HTML da página de resultados do Magento 2."""
        soup = BeautifulSoup(html, "html.parser")
        products: list[DentalCremerProduct] = []

        items = soup.select("li.product-item")
        logger.debug("[DentalSpeed] %d li.product-item encontrados.", len(items))

        for item in items:
            try:
                prod = self._parse_item(item)
                if prod:
                    products.append(prod)
            except Exception as exc:
                logger.warning("[DentalSpeed] Falha ao parsear item: %s", exc)

        logger.info("[DentalSpeed] %d produto(s) extraídos.", len(products))
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
        old_tag = item.select_one(".old-price .price")
        preco_original = (
            _parse_price(old_tag.get_text(strip=True)) if old_tag else preco_final
        )

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
            disponivel=True,
            marca=marca,
            categoria="",
            fonte="Dental Speed",
        )


# ---------------------------------------------------------------------------
# Execução standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    termo = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = DentalSpeedScraper()
    resultados = scraper.buscar(termo, tamanho=6)
    print(f"\n=== Dental Speed — '{termo}' ({len(resultados)} resultados) ===")
    for p in resultados:
        print(f"  [{p.sku}] {p.nome} — R$ {p.preco_final:.2f}  {p.url}")
