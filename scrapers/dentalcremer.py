"""
Scraper da Dental Cremer usando a API SmartHint (pública, sem autenticação).

Endpoint:
  GET https://searches.smarthint.co/v3/Search/GetPrimarySearch
      ?shcode=SH-743969&term={query}&from=0&size={size}&sort=0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SMARTHINT_SEARCH_URL = "https://searches.smarthint.co/v3/Search/GetPrimarySearch"
SMARTHINT_SHCODE = "SH-743969"
DENTAL_CREMER_BASE = "https://www.dentalcremer.com.br"

DEFAULT_TIMEOUT = 15  # segundos
DEFAULT_PAGE_SIZE = 20


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------


@dataclass
class DentalCremerProduct:
    """Representa um produto encontrado na Dental Cremer."""

    nome: str
    sku: str
    preco_original: float  # preço "de"
    preco_final: float  # preço "por" (pode ser igual ao original)
    preco_pix: Optional[float]  # preço à vista no Pix
    percentual_desconto: int  # 0 se não houver desconto
    url: str
    imagem_url: str
    disponivel: bool
    marca: str
    categoria: str
    fonte: str = "Dental Cremer"

    @property
    def preco_exibido(self) -> float:
        """Retorna o menor preço disponível (Pix > final > original)."""
        if self.preco_pix and self.preco_pix > 0:
            return self.preco_pix
        return self.preco_final

    @property
    def preco_formatado(self) -> str:
        return (
            f"R$ {self.preco_final:,.2f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    @property
    def preco_pix_formatado(self) -> Optional[str]:
        if self.preco_pix and self.preco_pix > 0:
            return (
                f"R$ {self.preco_pix:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )
        return None

    def to_dict(self) -> dict:
        return {
            "nome": self.nome,
            "sku": self.sku,
            "preco_original": self.preco_original,
            "preco_final": self.preco_final,
            "preco_pix": self.preco_pix,
            "preco_formatado": self.preco_formatado,
            "preco_pix_formatado": self.preco_pix_formatado,
            "percentual_desconto": self.percentual_desconto,
            "url": self.url,
            "imagem_url": self.imagem_url,
            "disponivel": self.disponivel,
            "marca": self.marca,
            "categoria": self.categoria,
            "fonte": self.fonte,
        }


@dataclass
class DentalCremerSearchResult:
    """Resultado completo de uma busca na Dental Cremer."""

    termo_buscado: str
    total_encontrado: int
    produtos: list[DentalCremerProduct] = field(default_factory=list)
    erro: Optional[str] = None

    @property
    def sucesso(self) -> bool:
        return self.erro is None

    def to_dict(self) -> dict:
        return {
            "termo_buscado": self.termo_buscado,
            "total_encontrado": self.total_encontrado,
            "sucesso": self.sucesso,
            "erro": self.erro,
            "produtos": [p.to_dict() for p in self.produtos],
        }


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------


class DentalCremerScraper:
    """
    Busca produtos na Dental Cremer via API SmartHint.

    Uso:
        scraper = DentalCremerScraper()
        resultado = scraper.buscar(nome_produto)
        for produto in resultado.produtos:
            print(produto.nome, produto.preco_formatado)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Origin": DENTAL_CREMER_BASE,
                "Referer": f"{DENTAL_CREMER_BASE}/",
            }
        )

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def buscar(
        self,
        termo: str,
        tamanho: int = DEFAULT_PAGE_SIZE,
        pagina: int = 0,
        sort: int = 0,
    ) -> DentalCremerSearchResult:
        """
        Busca produtos pelo nome/termo informado.

        Args:
            termo:   Texto de busca (nome do produto).
            tamanho: Quantidade máxima de resultados por página (máx. 48).
            pagina:  Índice da página (0-based).
            sort:    Ordenação (0=relevância, 1=menor preço, 2=maior preço…).

        Returns:
            DentalCremerSearchResult com a lista de produtos encontrados.
        """
        if not termo or not termo.strip():
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro="Termo de busca não pode ser vazio.",
            )

        try:
            dados_brutos = self._chamar_api(termo.strip(), tamanho, pagina, sort)
            produtos = self._parsear_produtos(dados_brutos)
            total = dados_brutos.get("TotalResult", len(produtos))

            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=total,
                produtos=produtos,
            )

        except requests.Timeout:
            logger.error("Timeout ao buscar '%s' na Dental Cremer.", termo)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro="Tempo limite excedido ao acessar a Dental Cremer.",
            )
        except requests.ConnectionError as exc:
            logger.error("Erro de conexão na Dental Cremer: %s", exc)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro="Não foi possível conectar à Dental Cremer.",
            )
        except requests.HTTPError as exc:
            logger.error(
                "HTTP %s ao buscar na Dental Cremer.", exc.response.status_code
            )
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Erro HTTP {exc.response.status_code} da Dental Cremer.",
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Erro inesperado ao buscar na Dental Cremer: %s", exc)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Erro inesperado: {exc}",
            )

    # ------------------------------------------------------------------
    # Métodos privados
    # ------------------------------------------------------------------

    def _chamar_api(
        self,
        termo: str,
        tamanho: int,
        pagina: int,
        sort: int,
    ) -> dict:
        """Chama o endpoint SmartHint e retorna o JSON bruto."""
        params = {
            "shcode": SMARTHINT_SHCODE,
            "term": termo,
            "from": pagina * tamanho,
            "size": min(tamanho, 48),  # SmartHint limita a 48
            "sort": sort,
        }
        logger.debug(
            "Dental Cremer SmartHint GET %s params=%s", SMARTHINT_SEARCH_URL, params
        )

        response = self.session.get(
            SMARTHINT_SEARCH_URL,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _parsear_produtos(self, dados: dict) -> list[DentalCremerProduct]:
        """Converte a lista bruta de produtos da API em objetos tipados."""
        produtos_brutos: list[dict] = dados.get("Products", [])
        produtos: list[DentalCremerProduct] = []

        for item in produtos_brutos:
            try:
                produto = self._parsear_produto(item)
                produtos.append(produto)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Falha ao parsear produto '%s': %s", item.get("Title"), exc
                )

        return produtos

    def _parsear_produto(self, item: dict) -> DentalCremerProduct:
        """Parseia um único produto retornado pela API SmartHint."""

        # ---- Preços ----
        preco_original = float(item.get("Price") or 0)
        preco_final = float(
            item.get("SalePrice") or item.get("FinalPrice") or preco_original
        )
        if preco_final <= 0:
            preco_final = preco_original

        # Preço no Pix (vem como string no campo AditionalFeatures)
        preco_pix: Optional[float] = None
        features: dict = item.get("AditionalFeatures") or {}
        pix_raw = features.get("PriceWithDiscountPix")
        if pix_raw:
            try:
                preco_pix = float(pix_raw)
            except (ValueError, TypeError):
                pass

        # ---- URL do produto ----
        link_raw: str = item.get("Link") or ""
        if link_raw.startswith("//"):
            url = f"https:{link_raw}"
        elif link_raw.startswith("http"):
            url = link_raw
        else:
            url = f"{DENTAL_CREMER_BASE}/{link_raw.lstrip('/')}"

        # ---- Imagem ----
        imagem_url: str = item.get("ImageLink") or ""

        # ---- Disponibilidade ----
        disponivel = str(item.get("Availability") or "").lower() == "in stock"

        # ---- Categoria ----
        categoria: str = item.get("ProductType") or ""
        categorias_lista: list = item.get("Categories") or []
        if not categoria and categorias_lista:
            categoria = categorias_lista[-1] if categorias_lista else ""

        return DentalCremerProduct(
            nome=str(item.get("Title") or "").strip(),
            sku=str(item.get("Sku") or "").strip(),
            preco_original=preco_original,
            preco_final=preco_final,
            preco_pix=preco_pix,
            percentual_desconto=int(item.get("Discount") or 0),
            url=url,
            imagem_url=imagem_url,
            disponivel=disponivel,
            marca=str(item.get("Brand") or "").strip(),
            categoria=categoria,
        )


# ---------------------------------------------------------------------------
# Execução standalone para testes rápidos
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.DEBUG)

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "sonda exploradora"
    scraper = DentalCremerScraper()
    resultado = scraper.buscar(termo_teste, tamanho=5)

    print(f"\n=== Dental Cremer — Busca por: '{resultado.termo_buscado}' ===")
    print(f"Total encontrado: {resultado.total_encontrado}")

    if not resultado.sucesso:
        print(f"ERRO: {resultado.erro}")
        sys.exit(1)

    for i, p in enumerate(resultado.produtos, 1):
        print(f"\n[{i}] {p.nome}")
        print(f"     SKU      : {p.sku}")
        print(f"     Marca    : {p.marca}")
        print(f"     Preço    : {p.preco_formatado}", end="")
        if p.preco_pix_formatado:
            print(f"  |  Pix: {p.preco_pix_formatado}", end="")
        if p.percentual_desconto:
            print(f"  ({p.percentual_desconto}% OFF)", end="")
        print()
        print(f"     Estoque  : {'Disponível' if p.disponivel else 'Indisponível'}")
        print(f"     URL      : {p.url}")
