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
    parse_name,
    score,
    split_brand_suffix,
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


if __name__ == "__main__":
    unittest.main()
