"""
Scraper da Dental Paiva usando a API SmartHint (pública, sem autenticação).

Plataforma  : Wbuy, com a busca feita pela SmartHint (shcode SH-428351)
Estratégia  : a mesma API da Dental Cremer (``SmartHintScraper``). A busca HTML
              do site e as páginas de produto passam por um anti-bot
              (/anti-bot-check), por isso só a API é usada.

Particularidades do feed da Paiva:
  - ``Sku`` vem "N/A": o código do produto na Wbuy está em ``Mpn``;
  - o preço com Pix (5% de desconto na página) vem em ``BankSlipPrice``;
  - todo item traz a variação "Gênero: unisex", ignorada pelo SmartHintScraper.
"""

from __future__ import annotations

import logging

from .dentalcremer import (
    DEFAULT_TIMEOUT,
    DentalCremerProduct,
    SmartHintScraper,
    _montar_precos,
)

logger = logging.getLogger(__name__)

SMARTHINT_SHCODE = "SH-428351"
DENTAL_PAIVA_BASE = "https://www.dentalpaivanovo.com"

# Valores de ``Sku`` que não identificam o produto
_EMPTY_SKUS = frozenset({"", "n/a", "na"})


class DentalPaivaScraper(SmartHintScraper):
    """
    Busca produtos na Dental Paiva via API SmartHint.

    Uso:
        scraper = DentalPaivaScraper()
        resultado = scraper.buscar(nome_produto)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        super().__init__(
            shcode=SMARTHINT_SHCODE,
            base_url=DENTAL_PAIVA_BASE,
            fonte="Dental Paiva",
            timeout=timeout,
        )

    def _parsear_produto(self, item: dict) -> DentalCremerProduct:
        produto = super()._parsear_produto(item)

        if produto.sku.lower() in _EMPTY_SKUS:
            produto.sku = str(item.get("Mpn") or item.get("ProductId") or "").strip()

        if produto.preco_pix is None:
            _, _, produto.preco_pix = _montar_precos(
                produto.preco_original, produto.preco_final, item.get("BankSlipPrice")
            )
        return produto


# ---------------------------------------------------------------------------
# Execução standalone para testes rápidos:  python -m scrapers.dentalpaiva "termo"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "lima manual k"
    resultado = DentalPaivaScraper().buscar(termo_teste, tamanho=10)

    print(f"\n=== Dental Paiva — Busca por: '{resultado.termo_buscado}' ===")
    print(f"Total encontrado: {resultado.total_encontrado}")
    if not resultado.sucesso:
        print(f"ERRO: {resultado.erro}")
        sys.exit(1)

    for i, p in enumerate(resultado.produtos, 1):
        pix = f"  |  Pix: {p.preco_pix_formatado}" if p.preco_pix_formatado else ""
        print(f"\n[{i}] {p.nome}")
        print(f"     SKU      : {p.sku}  |  Marca: {p.marca}")
        print(f"     Preço    : {p.preco_formatado}{pix}")
        print(f"     Estoque  : {'Disponível' if p.disponivel else 'Indisponível'}")
        print(f"     URL      : {p.url}")
