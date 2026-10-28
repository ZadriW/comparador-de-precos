"""
Scraper da Dental Cremer usando a API SmartHint (pública, sem autenticação).

Endpoint:
  GET https://searches.smarthint.co/v3/Search/GetPrimarySearch
      ?shcode=SH-743969&term={query}&from=0&size={size}&sort=0

A mesma API atende outras lojas do grupo (ex.: Dental Speed, SH-176187),
por isso a lógica fica na classe genérica ``SmartHintScraper``.

Produtos com variações (``Specifications``: tamanho, numeração, cor…) viram
um item por variação, com SKU, preço e estoque próprios.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Optional

import requests

from matching import append_variation, normalize, package_excerpt

from .base import html_to_text, retrying_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SMARTHINT_SEARCH_URL = "https://searches.smarthint.co/v3/Search/GetPrimarySearch"
SMARTHINT_SHCODE = "SH-743969"
DENTAL_CREMER_BASE = "https://www.dentalcremer.com.br"

DEFAULT_TIMEOUT = 15  # segundos
DEFAULT_PAGE_SIZE = 20

# Atributos do feed que não são variação do produto (a Dental Paiva manda
# "Gênero: unisex" em todos os itens)
_IGNORED_VARIATIONS = frozenset({"genero"})


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------


@dataclass
class DentalCremerProduct:
    """Representa um produto encontrado em uma loja concorrente."""

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
    ean: str = ""  # código de barras (quando a loja informa)
    variacao: str = ""  # tamanho, numeração, cor… já incluída em ``nome``
    descricao: str = ""  # o que vem na embalagem ("Embalagem com 1 seringa de 4g.")

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
            "ean": self.ean,
            "variacao": self.variacao,
            "descricao": self.descricao,
        }


@dataclass
class DentalCremerSearchResult:
    """Resultado completo de uma busca em uma loja SmartHint."""

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
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _variation_labels(spec: dict) -> list[str]:
    """Valores das variações de uma ``Specification`` ("47", "G"), sem os ignorados."""
    return [
        str(v.get("Value") or "").strip()
        for v in spec.get("variations") or []
        if isinstance(v, dict)
        and str(v.get("Value") or "").strip()
        and normalize(str(v.get("Name") or "")) not in _IGNORED_VARIATIONS
    ]


def _montar_precos(price, sale_price, pix) -> tuple[float, float, Optional[float]]:
    """
    (preco_original, preco_final, preco_pix) a partir dos campos da SmartHint.
    O "de" nunca fica abaixo do "por" e o Pix só vale se for menor que o "por".
    """
    preco_original = _to_float(price)
    preco_final = _to_float(sale_price) or preco_original
    preco_original = max(preco_original, preco_final)
    preco_pix: Optional[float] = _to_float(pix)
    if not 0 < preco_pix < preco_final:
        preco_pix = None
    return preco_original, preco_final, preco_pix


# ---------------------------------------------------------------------------
# Scraper genérico SmartHint
# ---------------------------------------------------------------------------


class SmartHintScraper:
    """
    Busca produtos em qualquer loja que use a API SmartHint.

    Uso:
        scraper = SmartHintScraper("SH-176187", "https://www.dentalspeed.com", "Dental Speed")
        resultado = scraper.buscar(nome_produto)
    """

    def __init__(
        self,
        shcode: str,
        base_url: str,
        fonte: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.shcode = shcode
        self.base_url = base_url.rstrip("/")
        self.fonte = fonte
        self.timeout = timeout
        self.session = retrying_session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
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
            total = int(dados_brutos.get("TotalResult") or len(produtos))

            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=total,
                produtos=produtos,
            )

        except requests.Timeout:
            logger.error("Timeout ao buscar '%s' na %s.", termo, self.fonte)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Tempo limite excedido ao acessar a {self.fonte}.",
            )
        except requests.ConnectionError as exc:
            logger.error("Erro de conexão na %s: %s", self.fonte, exc)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Não foi possível conectar à {self.fonte}.",
            )
        except requests.HTTPError as exc:
            logger.error(
                "HTTP %s ao buscar na %s.", exc.response.status_code, self.fonte
            )
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Erro HTTP {exc.response.status_code} da {self.fonte}.",
            )
        except ValueError as exc:  # JSON inválido ou fora do formato esperado
            logger.error("Resposta inesperada da %s: %s", self.fonte, exc)
            return DentalCremerSearchResult(
                termo_buscado=termo,
                total_encontrado=0,
                erro=f"Resposta inesperada da {self.fonte}: {exc}",
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Erro inesperado ao buscar na %s: %s", self.fonte, exc)
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
            "shcode": self.shcode,
            "term": termo,
            "from": pagina * tamanho,
            "size": min(tamanho, 48),  # SmartHint limita a 48
            "sort": sort,
        }
        logger.debug(
            "%s SmartHint GET %s params=%s", self.fonte, SMARTHINT_SEARCH_URL, params
        )

        response = self.session.get(
            SMARTHINT_SEARCH_URL,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        dados = response.json()
        # Um shcode inválido também responde 200 com lista vazia; só uma
        # busca reconhecida pela SmartHint traz o SearchId.
        if not isinstance(dados, dict) or not isinstance(dados.get("Products"), list):
            raise ValueError("resposta sem a lista 'Products' (a API pode ter mudado)")
        if not dados.get("SearchId"):
            raise ValueError(f"a SmartHint não reconheceu a loja (shcode {self.shcode})")
        return dados

    def _parsear_produtos(self, dados: dict) -> list[DentalCremerProduct]:
        """Converte a lista bruta de produtos da API em objetos tipados."""
        produtos_brutos: list[dict] = dados.get("Products", [])
        produtos: list[DentalCremerProduct] = []

        for item in produtos_brutos:
            try:
                produto = self._parsear_produto(item)
                produtos.extend(self._expandir_variacoes(produto, item))
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Falha ao parsear produto '%s': %s", item.get("Title"), exc
                )

        return produtos

    def _expandir_variacoes(
        self, produto: DentalCremerProduct, item: dict
    ) -> list[DentalCremerProduct]:
        """
        Gera um produto por variação (tamanho, numeração, cor…).

        O título da SmartHint não traz a variação: "Sonda Exploradora - Ice"
        pode ser a Nº 5 ou a Nº 47, cada uma com SKU, preço e estoque próprios
        em ``Specifications``. A variação entra no nome antes do " - Marca".
        """
        specs = [s for s in item.get("Specifications") or [] if isinstance(s, dict)]
        if not any(_variation_labels(s) for s in specs):
            return [produto]

        variantes: list[DentalCremerProduct] = []
        for spec in specs:
            labels = _variation_labels(spec)

            changes: dict = {}
            for change in spec.get("changes") or []:
                if isinstance(change, dict):
                    for key in ("valueDouble", "valueString", "valueInt"):
                        if key in change:
                            changes[change.get("name")] = change[key]
                            break

            if changes.get("Price") or changes.get("SalePrice"):
                preco_original, preco_final, preco_pix = _montar_precos(
                    changes.get("Price"),
                    changes.get("SalePrice"),
                    changes.get("PriceWithDiscountPix"),
                )
            else:
                preco_original = produto.preco_original
                preco_final = produto.preco_final
                preco_pix = produto.preco_pix
            if preco_final <= 0:
                continue

            pct = 0
            if preco_original > preco_final:
                pct = round((1 - preco_final / preco_original) * 100)

            imagem_url = str(changes.get("Img") or produto.imagem_url)
            if imagem_url.startswith("//"):
                imagem_url = f"https:{imagem_url}"

            availability = spec.get("availability")
            variantes.append(
                replace(
                    produto,
                    nome=append_variation(produto.nome, labels),
                    sku=str(spec.get("sku") or produto.sku).strip(),
                    preco_original=preco_original,
                    preco_final=preco_final,
                    preco_pix=preco_pix,
                    percentual_desconto=pct,
                    imagem_url=imagem_url,
                    disponivel=(
                        str(availability).lower() == "in stock"
                        if availability
                        else produto.disponivel
                    ),
                    variacao=" / ".join(labels),
                )
            )

        return variantes or [produto]

    def _parsear_produto(self, item: dict) -> DentalCremerProduct:
        """Parseia um único produto retornado pela API SmartHint."""

        # ---- Preços (o Pix vem como string em AditionalFeatures) ----
        features: dict = item.get("AditionalFeatures") or {}
        preco_original, preco_final, preco_pix = _montar_precos(
            item.get("Price"),
            item.get("SalePrice") or item.get("FinalPrice"),
            features.get("PriceWithDiscountPix"),
        )

        # ---- URL do produto ----
        link_raw: str = item.get("Link") or ""
        if link_raw.startswith("//"):
            url = f"https:{link_raw}"
        elif link_raw.startswith("http"):
            url = link_raw
        else:
            url = f"{self.base_url}/{link_raw.lstrip('/')}"

        # ---- Imagem ----
        imagem_url: str = item.get("ImageLink") or ""
        if imagem_url.startswith("//"):
            imagem_url = f"https:{imagem_url}"

        # ---- Disponibilidade ----
        disponivel = str(item.get("Availability") or "").lower() == "in stock"

        # ---- Categoria ----
        categoria: str = item.get("ProductType") or ""
        categorias_lista: list = item.get("Categories") or []
        if not categoria and categorias_lista:
            categoria = categorias_lista[-1]

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
            fonte=self.fonte,
            descricao=package_excerpt(html_to_text(item.get("Description"))),
        )


# ---------------------------------------------------------------------------
# Dental Cremer
# ---------------------------------------------------------------------------


class DentalCremerScraper(SmartHintScraper):
    """
    Busca produtos na Dental Cremer via API SmartHint.

    Uso:
        scraper = DentalCremerScraper()
        resultado = scraper.buscar(nome_produto)
        for produto in resultado.produtos:
            print(produto.nome, produto.preco_formatado)
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        super().__init__(
            shcode=SMARTHINT_SHCODE,
            base_url=DENTAL_CREMER_BASE,
            fonte="Dental Cremer",
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# Execução standalone para testes rápidos:  python -m scrapers.dentalcremer
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
