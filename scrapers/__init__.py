"""
Pacote de scrapers para o comparador de preços — Dental Odonto Master.

Módulos disponíveis:
    base          — ScraperError (falha ao consultar uma loja)
    odontomaster  — API GraphQL Wake/Fbits
    dentalcremer  — API SmartHint (JSON público) + SmartHintScraper genérico
    dentalspeed   — API SmartHint, com fallback para HTML Magento 2 (curl-cffi)
    dentalshop    — API VTEX Intelligent Search (JSON)
    suryadental   — GraphQL Adobe Commerce (curl-cffi)
    dentalpaiva   — API SmartHint (feed Wbuy: SKU em Mpn, Pix em BankSlipPrice)
    dentalmedsul  — HTML da busca do Magento 1 + dataLayer (curl-cffi)

Teste rápido de cada scraper:  python -m scrapers.<modulo> "termo"
"""

from .base import ScraperError
from .dentalcremer import (
    DentalCremerProduct,
    DentalCremerScraper,
    DentalCremerSearchResult,
    SmartHintScraper,
)
from .dentalmedsul import DentalMedSulScraper
from .dentalpaiva import DentalPaivaScraper
from .dentalshop import DentalShopScraper
from .dentalspeed import DentalSpeedScraper
from .odontomaster import OdontoMasterProduct, OdontoMasterScraper
from .suryadental import SuryaDentalScraper

__all__ = [
    "ScraperError",
    "OdontoMasterScraper",
    "OdontoMasterProduct",
    "SmartHintScraper",
    "DentalCremerScraper",
    "DentalCremerProduct",
    "DentalCremerSearchResult",
    "DentalSpeedScraper",
    "DentalShopScraper",
    "SuryaDentalScraper",
    "DentalPaivaScraper",
    "DentalMedSulScraper",
]
