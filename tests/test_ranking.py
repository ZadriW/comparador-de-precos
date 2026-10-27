"""
Testes da escolha da melhor opção (comparator._rank_items).
Não acessam a internet.

Rodar:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comparator import ComparisonItem, _rank_items  # noqa: E402
from scrapers.dentalcremer import DentalCremerProduct  # noqa: E402
from scrapers.odontomaster import OdontoMasterProduct  # noqa: E402

OM = OdontoMasterProduct(
    name="Sonda Exploradora - ICE", sku="143026", product_id=1, variant_id=1,
    price=13.27, list_price=13.33, url="https://om/sonda",
)


def _prod(sku: str, price: float, disponivel: bool = True) -> DentalCremerProduct:
    return DentalCremerProduct(
        nome=f"Produto {sku}", sku=sku, preco_original=price, preco_final=price,
        preco_pix=None, percentual_desconto=0, url=f"https://loja/p-{sku}",
        imagem_url="", disponivel=disponivel, marca="Ice", categoria="",
    )


def _item(sku, price, score, level, **kw) -> ComparisonItem:
    return ComparisonItem(
        odonto_master=OM, concorrente=_prod(sku, price, kw.pop("disponivel", True)),
        relevance_score=score, match_level=level, brand_match=True, **kw,
    )


class RankingTests(unittest.TestCase):
    def test_ean_beats_name(self):
        items = [_item("a", 13.0, 1.0, "exato"), _item("b", 14.0, 1.0, "exato", ean_match=True)]
        self.assertEqual(_rank_items(items, OM.price)[0].concorrente.sku, "b")

    def test_outlier_loses_within_level(self):
        # Nota maior, mas preço 5× o da Odonto Master
        items = [_item("caro", 64.90, 1.0, "exato"), _item("ok", 14.49, 0.88, "exato")]
        self.assertEqual(_rank_items(items, OM.price)[0].concorrente.sku, "ok")

    def test_higher_level_beats_plausible_price(self):
        items = [_item("caro", 64.90, 0.9, "exato"), _item("barato", 13.0, 0.7, "provavel")]
        self.assertEqual(_rank_items(items, OM.price)[0].concorrente.sku, "caro")

    def test_in_stock_breaks_tie(self):
        items = [
            _item("esgotado", 13.27, 1.0, "exato", disponivel=False),
            _item("estoque", 13.90, 0.98, "exato"),
        ]
        self.assertEqual(_rank_items(items, OM.price)[0].concorrente.sku, "estoque")


if __name__ == "__main__":
    unittest.main()
