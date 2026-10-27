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

from dataclasses import replace  # noqa: E402

from comparator import ComparisonItem, _rank_items, _score_product  # noqa: E402
from matching import parse_name, parse_package, with_package  # noqa: E402
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

    def test_same_package_beats_stock(self):
        items = [
            _item("estoque", 13.27, 1.0, "exato"),
            _item("embalagem", 13.90, 1.0, "exato", disponivel=False, package_match=True),
        ]
        self.assertEqual(_rank_items(items, OM.price)[0].concorrente.sku, "embalagem")


class PackageScoringTests(unittest.TestCase):
    """Kit e unidade com o mesmo título (Transbond XT na Dental Cremer)."""

    def test_unit_beats_kit_with_same_title(self):
        om = OdontoMasterProduct(
            name="Adesivo Ortodôntico 3M Transbond XT - Solventum", sku="116548",
            product_id=1, variant_id=1, price=269.90, list_price=340.15,
            url="https://om/transbond", brand="3M",
            package_text="Embalagem com 1 seringa de 4g.",
        )
        ref_pkg = parse_package(om.package_text)
        ref_sig = with_package(parse_name(om.match_name, om.brand), ref_pkg)
        nome = "Adesivo Ortodôntico 3M™ Transbond™ XT - Solventum"
        kit = replace(
            _prod("kit", 583.99), nome=nome, marca="Solventum",
            descricao="Kit com 2 Seringas Adesivo Ortodôntico Transbond XT de 4g cada + 1 Primer de 6ml.",
        )
        # A unidade está sem estoque: antes, o kit vencia o desempate
        unidade = replace(
            _prod("unidade", 295.90, disponivel=False), nome=nome, marca="Solventum",
            descricao="Embalagem com 1 seringa de 4g.",
        )
        items = []
        for prod in (kit, unidade):
            ms, ean, pkg = _score_product(ref_sig, "", prod, ref_pkg)
            items.append(ComparisonItem(
                odonto_master=om, concorrente=prod, relevance_score=ms.value,
                match_level=ms.level, brand_match=ms.brand_match, reasons=ms.reasons,
                ean_match=ean, package_match=pkg,
            ))
        ranked = _rank_items(items, om.price)
        self.assertEqual(ranked[0].concorrente.sku, "unidade")
        self.assertEqual(ranked[0].match_level, "exato")
        self.assertIn("kit × unidade (descrição)", ranked[1].reasons)


if __name__ == "__main__":
    unittest.main()
