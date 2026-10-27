"""
Testes das regras de correspondência (matching.py).

Rodar:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching import (  # noqa: E402
    EXACT_THRESHOLD,
    PROBABLE_THRESHOLD,
    append_variation,
    build_queries,
    normalize_gtin,
    package_excerpt,
    parse_name,
    parse_package,
    score,
    score_package,
    split_brand_suffix,
    with_package,
)


def _score(a: str, b: str):
    return score(parse_name(a), parse_name(b))


class SameProductTests(unittest.TestCase):
    """Pares que são o mesmo produto: devem dar "exato"."""

    CASES = [
        ("Luva Nitrilica c/100 - Supermax", "Luva Nitrilica Cx 100 - Supermax"),
        ("Hipoclorito 1L - Asfer", "Hipoclorito 1000ml - Asfer"),
        ("Luva Látex Com Pó GRANDE - Medix", "Luva de Látex com Pó G - Medix"),
        ("Luva Látex EX PEQUENA - Nugard", "Luva Látex PP - Nugard"),
        ("Sonda Exploradora N°05 - Golgran", "Sonda Exploradora Nº 5 - Golgran"),
        ("Luva Nitrílica Azul - Medix", "Luva Nitrilo Azul - Medix"),
        ("Luva Látex Sem Pó - Unigloves", "Luva Látex Powder Free - Unigloves"),
        ("Luva Látex s/ Pó - Unigloves", "Luva Látex Sem Pó - Unigloves"),
        ("Gel Dental 1kg - Maquira", "Gel Dental 1000g - Maquira"),
    ]

    def test_exact(self):
        for a, b in self.CASES:
            with self.subTest(a=a, b=b):
                r = _score(a, b)
                self.assertGreaterEqual(r.value, EXACT_THRESHOLD, r.reasons)


class DifferentProductTests(unittest.TestCase):
    """Pares que são produtos diferentes: nunca podem passar de "similar"."""

    CASES = [
        ("Luva Procedimento Latex Tamanho P - Supermax", "Luva Procedimento Latex Tamanho M - Supermax"),
        ("Agulha Gengival Curta 30G - Injex", "Agulha Gengival Longa 30G - Injex"),
        ("Luva Latex Sem Po - Supermax", "Luva Latex Po - Supermax"),
        ("Forceps 18R Adulto - Golgran", "Forceps 18L Adulto - Golgran"),
        ("Sonda Exploradora Nº47 - ICE", "Sonda Exploradora Nº 5 - Ice"),
        ("Luva Cirúrgica Estéril - Medix", "Luva Cirúrgica Não Estéril - Medix"),
        ("Luva Procedimento Látex - Medix", "Luva Procedimento Nitrílica - Medix"),
        ("Resina Filtek Z350 XT A2 - 3M", "Resina Filtek Z250 XT A2 - 3M"),
        ("Luva Látex Com Pó - Medix", "Luva Látex Sem Pó - Medix"),
    ]

    def test_not_probable(self):
        for a, b in self.CASES:
            with self.subTest(a=a, b=b):
                r = _score(a, b)
                self.assertLess(r.value, PROBABLE_THRESHOLD, r.reasons)


class BrandTests(unittest.TestCase):
    def test_3m_suffix_is_brand(self):
        self.assertEqual(split_brand_suffix("Resina Z350 XT - 3M"), ("Resina Z350 XT", "3M"))
        r = _score("Resina Filtek Z350 XT A2 - 3M", "Resina Filtek Z350 XT A2 - Solventum")
        self.assertTrue(r.brand_match)
        self.assertGreaterEqual(r.value, EXACT_THRESHOLD, r.reasons)

    def test_number_suffix_is_not_brand(self):
        self.assertEqual(split_brand_suffix("Lima K - 25mm")[1], "")

    def test_aliases(self):
        for a, b in [
            ("Broca 1012 - SS White", "Broca 1012 - S.S. White"),
            ("Silicone Perfil - Vigodent", "Silicone Perfil - Coltene"),
        ]:
            with self.subTest(a=a, b=b):
                self.assertTrue(_score(a, b).brand_match)

    def test_other_brand(self):
        self.assertFalse(_score("Sonda Exploradora - Ice", "Sonda Exploradora - Golgran").brand_match)


class HelperTests(unittest.TestCase):
    def test_gtin(self):
        self.assertEqual(normalize_gtin("7898475421986"), "7898475421986")
        self.assertEqual(normalize_gtin("07898475421986"), "7898475421986")
        self.assertEqual(normalize_gtin("0"), "")
        self.assertEqual(normalize_gtin("SEM GTIN"), "")
        self.assertEqual(normalize_gtin("7898475421987"), "")  # dígito errado

    def test_append_variation(self):
        self.assertEqual(append_variation("Sonda Exploradora - ICE", ["Nº47"]), "Sonda Exploradora Nº47 - ICE")
        self.assertEqual(
            append_variation("Sonda Exploradora N° 47 Colors - Ice", ["Nº 47 - Azul"]),
            "Sonda Exploradora N° 47 Colors Azul - Ice",
        )
        self.assertEqual(append_variation("Luva - Medix", []), "Luva - Medix")

    def test_queries_keep_codes(self):
        queries = build_queries(
            "Resina Composta Fotopolimerizável Filtek Z350 XT Seringa 4g Cor A2 - 3M", "3M"
        )
        self.assertIn("Z350", queries[0])
        self.assertIn("A2", queries[0])
        self.assertTrue(any(q.startswith("Resina Z350 A2") for q in queries), queries)

    def test_queries_simple(self):
        self.assertEqual(
            build_queries("Sonda Exploradora - ICE", "ICE"),
            ["Sonda Exploradora", "Sonda Exploradora ICE"],
        )


class PackageTests(unittest.TestCase):
    """Embalagem pela descrição (frases reais das lojas e da Odonto Master)."""

    def assertPackage(self, text, is_kit, count=None, unit="", contents=None):
        pkg = parse_package(text)
        self.assertIsNotNone(pkg, text)
        self.assertEqual(pkg.is_kit, is_kit, f"{text} → {pkg}")
        self.assertEqual(pkg.count, count, f"{text} → {pkg}")
        self.assertEqual(pkg.unit, unit, f"{text} → {pkg}")
        if contents is not None:
            self.assertEqual(pkg.contents, contents, f"{text} → {pkg}")

    def test_units(self):
        self.assertPackage("Embalagem com 1 seringa de 4g.", False, 1, "seringa", {"4g"})
        self.assertPackage("1 Seringa com 4g.", False, 1, "seringa", {"4g"})
        self.assertPackage("Frasco com 6 ml.", False, 1, "frasco", {"6ml"})
        self.assertPackage("Embalagem com 1 unidade", False, 1, "unidade")
        self.assertPackage("Embalagem com 1 seringa com 2,5g", False, 1, "seringa", {"2.5g"})
        self.assertPackage("50 carpules de 1,8 mL cada.", False, 50, "tubete", {"1.8ml"})
        self.assertPackage("Tira de Lixa Microcut 3032 Refil 5un TDV", False, 5, "unidade")

    def test_accessories_are_not_kit(self):
        self.assertPackage(
            "Embalagem com 1 seringa de 3g + 1 ponteira para aplicação", False, 1, "seringa"
        )
        self.assertPackage("60 unidades + mandril.", False, 60, "unidade")
        self.assertFalse(parse_package("Embalagem com 5g na cor A3 + acessórios.").is_kit)

    def test_kits(self):
        self.assertPackage(
            "Kit com 2 Seringas Adesivo Ortodôntico Transbond XT de 4g cada + 1 Primer de 6ml.",
            True, 2, "seringa",
        )
        for text in (
            "Kit com 02 Transbond XT com 4g cada, 1 Primer de 6ml.",
            "Embalagem com: * 2 Transbond XT 4g; * 1 Primer 6ml; * 1 Cabo de pincel",
            "Embalagem com 1 pó 12g + 1 líquido 10ml.",
            "Embalagem com 2 frascos de 5ml",
            "Embalagem com 1 Kit.",
        ):
            self.assertTrue(parse_package(text).is_kit, text)

    def test_marketing_only(self):
        self.assertIsNone(parse_package(""))
        self.assertIsNone(parse_package("O padrão ouro mundial para colagem de braquetes."))

    def test_excerpt_cuts_marketing(self):
        self.assertEqual(
            package_excerpt("Embalagem com 1 seringa de 4g. APRESENTAÇÃO: O Transbond XT é…"),
            "Embalagem com 1 seringa de 4g.",
        )

    def test_score_package(self):
        one = parse_package("Embalagem com 1 seringa de 4g.")
        self.assertIs(score_package(one, parse_package("1 Seringa com 4g."), False, False)[2], True)

        factor, reasons, match = score_package(
            parse_package("60 unidades"), parse_package("Embalagem com 30 unidades."), False, False
        )
        self.assertLess(factor, 1)
        self.assertEqual(reasons, ["30 unidades × 60 unidades"])
        self.assertIs(match, False)

        factor, _, match = score_package(one, parse_package("Seringa com 2,5g"), False, False)
        self.assertLess(factor, 1)
        self.assertIs(match, False)
        self.assertIsNone(score_package(one, None, False, False)[2])

    def test_same_title_kit_by_description(self):
        # Dental Cremer: kit e unidade do Transbond XT com o mesmo título
        name = "Adesivo Ortodôntico 3M™ Transbond™ XT - Solventum"
        ref = with_package(parse_name(name), parse_package("Embalagem com 1 seringa de 4g."))
        kit = with_package(
            parse_name(name),
            parse_package("Kit com 2 Seringas … de 4g cada + 1 Primer de 6ml."),
            ref.is_kit,
        )
        self.assertTrue(kit.is_kit)
        self.assertLess(score(ref, kit).value, PROBABLE_THRESHOLD)

    def test_multipack_does_not_decide_kit(self):
        # "Kit Disco Praxis 60unid." = "60 unidades + mandril" da Odonto Master
        ref = parse_name("Disco De Lixa Praxis - TDV")
        cand = with_package(
            parse_name("Kit Disco de Lixa Praxis com Mandril - TDV"),
            parse_package("Embalagem com 60 unidades sortidas + Mandril."),
            ref.is_kit,
        )
        self.assertFalse(cand.is_kit)

    def test_combo_is_not_unit_against_kit(self):
        r = _score("Kit Resina Z350 XT + 30 Discos - 3M", "Resina Z350 XT + Grátis Discos - 3M")
        self.assertNotIn("kit × unidade", r.reasons)
        self.assertNotIn("unidade × kit", r.reasons)

    def test_query_with_content(self):
        queries = build_queries(
            "Adesivo Ortodôntico 3M Transbond XT - Solventum", "3M",
            package=parse_package("Embalagem com 1 seringa de 4g."),
        )
        self.assertEqual(queries[-1], "Adesivo Ortodôntico Transbond XT 4g")


if __name__ == "__main__":
    unittest.main()
