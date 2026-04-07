"""
Pacote de scrapers para o comparador de preços — Dental Odonto Master.

Módulos disponíveis:
    odontomaster  — API GraphQL Wake/Fbits
    dentalcremer  — API SmartHint (JSON público)
    dentalspeed   — HTML parsing Magento 2 (curl-cffi)
    dentalshop    — API VTEX Intelligent Search (JSON)
    suryadental   — GraphQL Adobe Commerce (curl-cffi)
"""

from .dentalcremer import (
    DentalCremerProduct,
    DentalCremerScraper,
    DentalCremerSearchResult,
)
from .dentalshop import DentalShopScraper
from .dentalspeed import DentalSpeedScraper
from .odontomaster import OdontoMasterProduct, OdontoMasterScraper
from .suryadental import SuryaDentalScraper

__all__ = [
    "OdontoMasterScraper",
    "OdontoMasterProduct",
    "DentalCremerScraper",
    "DentalCremerProduct",
    "DentalCremerSearchResult",
    "DentalSpeedScraper",
    "DentalShopScraper",
    "SuryaDentalScraper",
]
