"""
Comparador de Preços — Dental Odonto Master vs múltiplas dentais
=================================================================

Fluxo principal:
  1. Recebe um SKU da Odonto Master
  2. Consulta a API da Odonto Master → obtém nome e preço do produto
  3. Usa o nome para buscar em paralelo em 4 lojas concorrentes:
       - Dental Cremer  (SmartHint)
       - Dental Speed   (Magento 2 HTML)
       - Dental Shop    (VTEX API)
       - Surya Dental   (Adobe Commerce GraphQL)
  4. Retorna um ComparisonResult com os dados de todas as lojas

Uso básico::

    from comparator import PriceComparator

    comparator = PriceComparator()
    result = comparator.compare_by_sku("143026")
    print(result.to_dict())
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from scrapers.dentalcremer import DentalCremerProduct, DentalCremerScraper
from scrapers.dentalshop import DentalShopScraper
from scrapers.dentalspeed import DentalSpeedScraper
from scrapers.odontomaster import OdontoMasterProduct, OdontoMasterScraper
from scrapers.suryadental import SuryaDentalScraper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers de texto
# ---------------------------------------------------------------------------

_NOISE_WORDS = frozenset(
    {
        "com",
        "de",
        "da",
        "do",
        "para",
        "e",
        "em",
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "por",
        "ao",
        "na",
        "no",
        "se",
        "kit",
        "und",
        "un",
    }
)


def _clean_name(name: str) -> str:
    """Remove caracteres especiais, normaliza espaços e caixa baixa."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9àáâãéêíóôõúüçñ\s\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _relevance_score(reference_name: str, candidate_name: str) -> float:
    """
    Calcula uma pontuação de relevância entre 0 e 1.

    A pontuação é baseada na proporção de palavras significativas do
    produto de referência (Odonto Master) que aparecem no nome do
    candidato (loja concorrente).
    """
    ref_words = {
        w
        for w in _clean_name(reference_name).split()
        if w not in _NOISE_WORDS and len(w) > 2
    }
    cand_clean = _clean_name(candidate_name)

    if not ref_words:
        return 0.0

    matches = sum(1 for w in ref_words if w in cand_clean)
    return matches / len(ref_words)


def _extract_search_term(product_name: str) -> str:
    """
    Extrai um termo de busca compacto a partir do nome completo do produto.

    Estratégia:
      - Remove marcas/códigos entre parênteses e colchetes
      - Remove palavras de ruído
      - Mantém os primeiros termos mais descritivos
    """
    name = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", product_name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s+-\s+", " ", name)
    words = name.split()
    short = " ".join(words[:6])
    return short.strip()


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------


@dataclass
class PriceDelta:
    """Diferença de preço entre a Odonto Master e uma loja concorrente."""

    odonto_master_price: float
    competitor_price: float

    @property
    def absolute(self) -> float:
        """Diferença absoluta (concorrente - Odonto Master)."""
        return round(self.competitor_price - self.odonto_master_price, 2)

    @property
    def percentage(self) -> float:
        """Percentual de diferença em relação ao preço da Odonto Master."""
        if self.odonto_master_price == 0:
            return 0.0
        return round((self.absolute / self.odonto_master_price) * 100, 1)

    @property
    def competitor_is_cheaper(self) -> bool:
        return self.competitor_price < self.odonto_master_price

    @property
    def label(self) -> str:
        """Rótulo textual da comparação."""
        if self.competitor_is_cheaper:
            return f"{abs(self.percentage):.1f}% mais barata"
        elif self.absolute > 0:
            return f"{self.percentage:.1f}% mais cara"
        else:
            return "Preços equivalentes"

    def to_dict(self) -> dict:
        return {
            "odonto_master_price": self.odonto_master_price,
            "competitor_price": self.competitor_price,
            "absolute": self.absolute,
            "percentage": self.percentage,
            "competitor_is_cheaper": self.competitor_is_cheaper,
            "label": self.label,
            # Aliases retrocompatíveis
            "dental_cremer_price": self.competitor_price,
            "dental_cremer_is_cheaper": self.competitor_is_cheaper,
        }


@dataclass
class ComparisonItem:
    """Um par (produto Odonto Master ↔ produto de uma loja concorrente)."""

    odonto_master: OdontoMasterProduct
    concorrente: DentalCremerProduct
    relevance_score: float  # 0.0 – 1.0

    @property
    def delta(self) -> PriceDelta:
        return PriceDelta(
            odonto_master_price=self.odonto_master.price,
            competitor_price=self.concorrente.preco_final,
        )

    def to_dict(self) -> dict:
        return {
            "odonto_master": self.odonto_master.to_dict(),
            "concorrente": self.concorrente.to_dict(),
            # Alias retrocompatível
            "dental_cremer": self.concorrente.to_dict(),
            "relevance_score": round(self.relevance_score, 3),
            "delta": self.delta.to_dict(),
        }


@dataclass
class StoreResult:
    """Resultado de busca em uma loja específica."""

    fonte: str
    matches: list[ComparisonItem] = field(default_factory=list)
    all_produtos: list[DentalCremerProduct] = field(default_factory=list)
    total: int = 0
    erro: Optional[str] = None

    @property
    def sucesso(self) -> bool:
        return self.erro is None

    @property
    def best_match(self) -> Optional[ComparisonItem]:
        if not self.matches:
            return None
        return max(self.matches, key=lambda m: m.relevance_score)

    def to_dict(self) -> dict:
        return {
            "fonte": self.fonte,
            "total": self.total,
            "erro": self.erro,
            "matches": [m.to_dict() for m in self.matches],
            "all_produtos": [p.to_dict() for p in self.all_produtos],
            "best_match": self.best_match.to_dict() if self.best_match else None,
        }


@dataclass
class ComparisonResult:
    """
    Resultado completo de uma comparação iniciada por um SKU da Odonto Master.
    Contém resultados de todas as lojas concorrentes configuradas.
    """

    sku_consultado: str
    odonto_master_product: Optional[OdontoMasterProduct]
    stores: dict[str, StoreResult] = field(default_factory=dict)
    search_term_used: str = ""
    error: Optional[str] = None

    @property
    def found_on_odonto_master(self) -> bool:
        return self.odonto_master_product is not None

    @property
    def found_any_competitor(self) -> bool:
        return any(sr.all_produtos for sr in self.stores.values())

    @property
    def matches(self) -> list[ComparisonItem]:
        """Todos os matches de todas as lojas, ordenados por relevância."""
        all_m: list[ComparisonItem] = []
        for sr in self.stores.values():
            all_m.extend(sr.matches)
        return sorted(all_m, key=lambda m: m.relevance_score, reverse=True)

    @property
    def best_match(self) -> Optional[ComparisonItem]:
        m = self.matches
        return m[0] if m else None

    # Alias retrocompatível
    @property
    def dental_cremer_all(self) -> list[DentalCremerProduct]:
        dc = self.stores.get("Dental Cremer")
        return dc.all_produtos if dc else []

    def to_dict(self) -> dict:
        all_matches = self.matches
        return {
            "sku_consultado": self.sku_consultado,
            "search_term_used": self.search_term_used,
            "found_on_odonto_master": self.found_on_odonto_master,
            "found_any_competitor": self.found_any_competitor,
            "error": self.error,
            "odonto_master_product": (
                self.odonto_master_product.to_dict()
                if self.odonto_master_product
                else None
            ),
            "matches": [m.to_dict() for m in all_matches],
            "stores": {name: sr.to_dict() for name, sr in self.stores.items()},
            "best_match": self.best_match.to_dict() if self.best_match else None,
            # Alias retrocompatível
            "dental_cremer_all": [p.to_dict() for p in self.dental_cremer_all],
            "found_on_dental_cremer": bool(self.dental_cremer_all),
        }


# ---------------------------------------------------------------------------
# Comparador principal
# ---------------------------------------------------------------------------


class PriceComparator:
    """
    Orquestra a busca em todas as lojas concorrentes e consolida os resultados.

    Parâmetros
    ----------
    relevance_threshold : float
        Pontuação mínima de relevância (0–1) para incluir um produto na lista
        de matches. Padrão: 0.35
    max_results : int
        Quantidade máxima de produtos buscados em cada loja. Padrão: 20
    """

    # Nomes canônicos das lojas (usados como chaves em stores)
    STORE_NAMES = ["Dental Cremer", "Dental Speed", "Dental Shop", "Surya Dental"]

    def __init__(
        self,
        relevance_threshold: float = 0.35,
        max_results: int = 20,
        # Parâmetros legados (para não quebrar código existente)
        max_dc_results: int = 0,
    ) -> None:
        self.relevance_threshold = relevance_threshold
        self.max_results = max_results or max_dc_results or 20

        self._om_scraper = OdontoMasterScraper()
        self._dc_scraper = DentalCremerScraper()
        self._ds_scraper = DentalSpeedScraper()
        self._dsh_scraper = DentalShopScraper()
        self._su_scraper = SuryaDentalScraper()

    # ------------------------------------------------------------------
    # Método público
    # ------------------------------------------------------------------

    def compare_by_sku(self, sku: str) -> ComparisonResult:
        """
        Ponto de entrada principal.

        1. Busca o produto na Odonto Master pelo SKU.
        2. Usa o nome para buscar em paralelo nas 4 lojas concorrentes.
        3. Calcula relevância e delta de preço.
        4. Retorna um ComparisonResult.
        """
        sku = sku.strip()
        logger.info("Iniciando comparação para SKU: %s", sku)

        # ── Etapa 1: Odonto Master ──────────────────────────────────────
        om_product = self._om_scraper.get_by_sku(sku)

        if om_product is None:
            logger.warning("SKU '%s' não encontrado na Odonto Master.", sku)
            return ComparisonResult(
                sku_consultado=sku,
                odonto_master_product=None,
                error=f"Produto com SKU '{sku}' não encontrado na Odonto Master.",
            )

        logger.info(
            "Odonto Master → '%s' | R$ %.2f", om_product.name, om_product.price
        )

        # ── Etapa 2: Busca paralela nas lojas concorrentes ──────────────
        search_term = _extract_search_term(om_product.name)
        logger.info("Buscando em todas as lojas por: '%s'", search_term)

        stores: dict[str, StoreResult] = {}
        search_tasks = {
            "Dental Cremer": lambda: self._search_cremer(search_term),
            "Dental Speed": lambda: self._search_speed(search_term),
            "Dental Shop": lambda: self._search_shop(search_term),
            "Surya Dental": lambda: self._search_surya(search_term),
        }

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_store = {
                executor.submit(fn): nome
                for nome, fn in search_tasks.items()
            }
            for future in as_completed(future_to_store):
                store_name = future_to_store[future]
                try:
                    result = future.result()
                    stores[store_name] = result
                except Exception as exc:
                    logger.error("Erro inesperado em '%s': %s", store_name, exc)
                    stores[store_name] = StoreResult(
                        fonte=store_name,
                        erro=f"Erro inesperado: {exc}",
                    )

        # ── Etapa 3: Calcular relevância e montar pares ─────────────────
        for store_name, store_result in stores.items():
            if store_result.erro or not store_result.all_produtos:
                continue

            matches: list[ComparisonItem] = []
            for prod in store_result.all_produtos:
                score = _relevance_score(om_product.name, prod.nome)
                if score >= self.relevance_threshold:
                    matches.append(
                        ComparisonItem(
                            odonto_master=om_product,
                            concorrente=prod,
                            relevance_score=score,
                        )
                    )

            matches.sort(key=lambda m: m.relevance_score, reverse=True)
            store_result.matches = matches

            logger.info(
                "%s → %d produto(s) | %d match(es) (≥%.0f%%).",
                store_name,
                store_result.total,
                len(matches),
                self.relevance_threshold * 100,
            )

        return ComparisonResult(
            sku_consultado=sku,
            odonto_master_product=om_product,
            stores=stores,
            search_term_used=search_term,
        )

    # ------------------------------------------------------------------
    # Buscas individuais por loja
    # ------------------------------------------------------------------

    def _search_cremer(self, term: str) -> StoreResult:
        result = self._dc_scraper.buscar(term, tamanho=self.max_results)
        if not result.sucesso:
            return StoreResult(fonte="Dental Cremer", erro=result.erro)
        return StoreResult(
            fonte="Dental Cremer",
            all_produtos=result.produtos,
            total=result.total_encontrado,
        )

    def _search_speed(self, term: str) -> StoreResult:
        produtos = self._ds_scraper.buscar(term, tamanho=self.max_results)
        return StoreResult(
            fonte="Dental Speed",
            all_produtos=produtos,
            total=len(produtos),
        )

    def _search_shop(self, term: str) -> StoreResult:
        produtos = self._dsh_scraper.buscar(term, tamanho=self.max_results)
        return StoreResult(
            fonte="Dental Shop",
            all_produtos=produtos,
            total=len(produtos),
        )

    def _search_surya(self, term: str) -> StoreResult:
        produtos = self._su_scraper.buscar(term, tamanho=self.max_results)
        return StoreResult(
            fonte="Surya Dental",
            all_produtos=produtos,
            total=len(produtos),
        )


# ---------------------------------------------------------------------------
# Execução standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    sku_input = sys.argv[1] if len(sys.argv) > 1 else "143026"

    comparator = PriceComparator()
    result = comparator.compare_by_sku(sku_input)

    print(f"\n{'=' * 70}")
    print(f"  COMPARAÇÃO DE PREÇOS — SKU: {result.sku_consultado}")
    print(f"{'=' * 70}")

    if result.error and not result.found_on_odonto_master:
        print(f"\n[X]  {result.error}")
        sys.exit(1)

    if result.odonto_master_product:
        om = result.odonto_master_product
        print(f"\n[OM]  ODONTO MASTER")
        print(f"    Nome  : {om.name}")
        print(f"    SKU   : {om.sku}")
        print(f"    Preco : R$ {om.price:.2f}  (lista: R$ {om.list_price:.2f})")
        print(f"    URL   : {om.url}")

    for store_name, store_result in result.stores.items():
        print(f"\n{'─'*70}")
        icon = "[OK]" if store_result.matches else ("[--]" if store_result.all_produtos else "[X]")
        print(f"{icon}  {store_name.upper()}  (total={store_result.total} | matches={len(store_result.matches)})")
        if store_result.erro:
            print(f"    Erro: {store_result.erro}")
        for i, match in enumerate(store_result.matches[:3], 1):
            dc = match.concorrente
            delta = match.delta
            arrow = "↓" if delta.competitor_is_cheaper else "↑"
            print(f"\n    [{i}] {dc.nome}  (score={match.relevance_score:.0%})")
            print(f"        SKU   : {dc.sku}")
            print(f"        Preço : R$ {dc.preco_final:.2f}")
            print(f"        Delta : {arrow} {delta.label}")
            print(f"        URL   : {dc.url}")
