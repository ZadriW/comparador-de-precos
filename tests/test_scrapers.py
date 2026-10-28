"""
Testes da leitura das respostas da Dental Paiva (SmartHint) e da Dental
MedSul (HTML do Magento). Usam respostas de exemplo no formato real (10/2026);
não acessam a internet.

Rodar:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.base import ScraperError  # noqa: E402
from scrapers.dentalmedsul import DentalMedSulScraper  # noqa: E402
from scrapers.dentalpaiva import DentalPaivaScraper  # noqa: E402

PAIVA_ITEM = {
    "Title": "Lima Manual K - Maillefer",
    "Price": 69.9,
    "SalePrice": 69.9,
    "BankSlipPrice": 66.4,
    "Availability": "in stock",
    "ProductId": "FWFPV",
    "Link": "//www.dentalpaivanovo.com/lima-manual-k-661854637cf33/",
    "ImageLink": "https://assets.sistemawbuy.com.br/lima.png",
    "Brand": "MAILLEFER",
    "Mpn": "2799988",
    "Sku": "N/A",
    "Description": "Embalagem com 6 unidades.",
    "AditionalFeatures": {"g:quantity": "1"},
    "Specifications": [{
        "sku": "N/A",
        "specificationId": "2799988",
        "availability": "in stock",
        "variations": [{"Name": "Gênero", "Value": "unisex"}],
        "changes": [
            {"valueDouble": 69.9, "name": "Price"},
            {"valueDouble": 69.9, "name": "SalePrice"},
            {"valueDouble": 66.4, "name": "BankSlipPrice"},
        ],
    }],
}


class DentalPaivaTests(unittest.TestCase):
    def setUp(self):
        self.scraper = DentalPaivaScraper()

    def test_parses_feed_item(self):
        [p] = self.scraper._parsear_produtos({"Products": [PAIVA_ITEM]})
        self.assertEqual(p.nome, "Lima Manual K - Maillefer")  # sem "unisex"
        self.assertEqual(p.variacao, "")
        self.assertEqual(p.sku, "2799988")
        self.assertEqual(p.preco_final, 69.9)
        self.assertEqual(p.preco_pix, 66.4)
        self.assertEqual(p.fonte, "Dental Paiva")
        self.assertEqual(p.url, "https://www.dentalpaivanovo.com/lima-manual-k-661854637cf33/")

    def test_real_variations_are_kept(self):
        item = dict(PAIVA_ITEM, Specifications=[
            {"sku": "A", "variations": [{"Name": "Gênero", "Value": "unisex"},
                                        {"Name": "Tamanho", "Value": "25mm"}], "changes": []},
            {"sku": "B", "variations": [{"Name": "Tamanho", "Value": "31mm"}], "changes": []},
        ])
        nomes = [p.nome for p in self.scraper._parsear_produtos({"Products": [item]})]
        self.assertEqual(nomes, ["Lima Manual K 25mm - Maillefer", "Lima Manual K 31mm - Maillefer"])

    def test_pix_not_below_price_is_ignored(self):
        item = dict(PAIVA_ITEM, BankSlipPrice=69.9, Specifications=[])
        [p] = self.scraper._parsear_produtos({"Products": [item]})
        self.assertIsNone(p.preco_pix)


def _medsul_search(products: list[dict], cards: str) -> str:
    layer = {"categoryProducts": products, "pageType": "catalogsearch/result/index"}
    return f"<html><script>var dataLayer = [({json.dumps(layer)})];</script><ul>{cards}</ul></html>"


MEDSUL_CARDS = """
<li data-product-id="114119" data-card-configurable="0">
  <img src="https://www.dentalmedsul.com.br/kit.jpg"/>
  <a class="product-name" href="https://www.dentalmedsul.com.br/kit-transbond">Kit Adesivo Ortodôntico Transbond XT - 3M</a>
  <div class="text-xs/4 line-clamp-2 min-h-[32px]">Embalagem com 2 seringas de 4g + Primer ...</div>
  <div class="price-box">
    <p class="old-price"><s class="price">R$650,90</s></p>
    <p class="special-price"><span class="price">R$583,99</span></p>
    <p class="special-price"><span class="price">R$566,47</span></p>
  </div>
  <button>Adicionar ao carrinho</button>
</li>
<li data-product-id="103862" data-card-configurable="0">
  <a class="product-name" href="https://www.dentalmedsul.com.br/protecao">Proteção Plástica para Fotopolimerizador Radii Cal - SDI</a>
  <div class="text-xs/4 line-clamp-2">Embalagem com 1000 unidades.</div>
  <a>Me avise quando chegar</a>
</li>
"""


class DentalMedSulTests(unittest.TestCase):
    def setUp(self):
        self.scraper = DentalMedSulScraper()

    def test_search_page(self):
        html = _medsul_search(
            [
                {"id": "114119", "name": "Kit Adesivo Ortodôntico Transbond XT - 3M",
                 "sku": "25.0731", "price": "583.99"},
                {"id": "103862", "name": "Proteção Plástica para Fotopolimerizador Radii Cal - SDI",
                 "sku": "26.7059", "price": "304.30"},
            ],
            MEDSUL_CARDS,
        )
        kit, protecao = self.scraper.parse_page(html)
        self.assertEqual(kit.preco_final, 583.99)
        self.assertEqual(kit.preco_original, 650.90)
        self.assertEqual(kit.preco_pix, 566.47)
        self.assertEqual(kit.marca, "3M")
        self.assertEqual(kit.sku, "25.0731")
        self.assertTrue(kit.disponivel)
        self.assertEqual(kit.url, "https://www.dentalmedsul.com.br/kit-transbond")
        self.assertTrue(kit.descricao.startswith("Embalagem com 2 seringas"))
        self.assertEqual(protecao.preco_final, 304.30)
        self.assertFalse(protecao.disponivel)
        self.assertIsNone(protecao.preco_pix)

    def test_empty_search(self):
        self.assertEqual(self.scraper.parse_page(_medsul_search([], "")), [])

    def test_single_result_redirects_to_product_page(self):
        layer = {"productId": "100172", "productName": "Anestésico Prilonest 3% - DFL",
                 "productSku": "25.6082", "productPrice": "260.90",
                 "pageType": "catalog/product/view"}
        html = f"""<html><head>
          <meta property="og:url" content="https://www.dentalmedsul.com.br/anestesico-prilonest-3-dfl"/>
          <meta property="og:image" content="https://www.dentalmedsul.com.br/prilonest.jpg"/>
          <script>var dataLayer = [({json.dumps(layer)})];</script></head><body>
          <div class="product-shop"><h1>Anestésico Prilonest 3% - DFL</h1>
            <div class="short-description">Embalagem com 50 tubetes de vidro de 1,8ml.</div>
            <div class="price-box"><p class="old-price"><s class="price">R$279,50</s></p>
              <span class="price">R$260,90</span><span class="price">R$253,07</span></div>
          </div></body></html>"""
        [p] = self.scraper.parse_page(html)
        self.assertEqual((p.nome, p.sku, p.preco_final), ("Anestésico Prilonest 3% - DFL", "25.6082", 260.90))
        self.assertEqual((p.preco_original, p.preco_pix), (279.50, 253.07))
        self.assertEqual(p.url, "https://www.dentalmedsul.com.br/anestesico-prilonest-3-dfl")
        self.assertEqual(p.descricao, "Embalagem com 50 tubetes de vidro de 1,8ml.")

    def test_unexpected_page_raises(self):
        with self.assertRaises(ScraperError):
            self.scraper.parse_page("<html><title>Verificando...</title></html>")


if __name__ == "__main__":
    unittest.main()
