"""
Comparador de Preços — Dental Odonto Master vs múltiplas dentais
=================================================================

Fluxo principal:
  1. Recebe um SKU da Odonto Master
  2. Consulta a API da Odonto Master → obtém nome, marca e preço do produto
  3. Gera termos de busca (matching.build_queries) e busca em paralelo em 6 lojas:
       - Dental Cremer  (SmartHint)
       - Dental Speed   (SmartHint, fallback HTML Magento 2)
       - Dental Shop    (VTEX API — aceita busca por EAN)
       - Surya Dental   (Adobe Commerce GraphQL)
       - Dental Paiva   (SmartHint)
       - Dental MedSul  (HTML da busca do Magento 1)
     Em cada loja a busca é em cascata: se o primeiro termo não trouxer um
     produto "exato", tenta o próximo. Nas lojas que buscam por EAN, o EAN da
     Odonto Master é o primeiro termo.
  4. Pontua cada produto (matching.score, com o nome + variação da Odonto
     Master) e confere a embalagem pela descrição (matching.score_package:
     kit × unidade, 1 × 2 seringas). EAN idêntico = exato; EAN válido e
     diferente nunca é "exato".
  5. Separa itens da mesma marca (matches) dos de outra marca (similares),
     escolhe o melhor par de cada loja (_rank_items) e retorna um
     ComparisonResult.

Os resultados de cada busca (loja + termo) ficam 15 min em cache.

Uso básico::

    from comparator import PriceComparator

    comparator = PriceComparator()
    result = comparator.compare_by_sku("143026")
    print(result.to_dict())
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence

from matching import (
    EXACT_THRESHOLD,
    MatchScore,
    PackageInfo,
    ProductSignature,
    build_queries,
    normalize_gtin,
    parse_name,
    parse_package,
    price_distance,
    same_kind,
    score,
    score_package,
    with_package,
)
from scrapers.base import ScraperError
from scrapers.dentalcremer import DentalCremerProduct, DentalCremerScraper, SmartHintScraper
from scrapers.dentalmedsul import DentalMedSulScraper
from scrapers.dentalpaiva import DentalPaivaScraper
from scrapers.dentalshop import DentalShopScraper
from scrapers.dentalspeed import DentalSpeedScraper
from scrapers.odontomaster import OdontoMasterProduct, OdontoMasterScraper
from scrapers.suryadental import SuryaDentalScraper

logger = logging.getLogger(__name__)

# Diferença de nota abaixo da qual o preço mais próximo desempata o melhor match
_TIE_MARGIN = 0.05

# Razão de preço (concorrente / Odonto Master) fora da qual o item é sinalizado
# e, se houver opção de preço plausível, deixa de ser a melhor opção (±60%)
_OUTLIER_HIGH = 1.6
_OUTLIER_LOW = 1 / _OUTLIER_HIGH

# Nota mínima de um produto do mesmo tipo para substituir, pelo preço, uma
# melhor opção com preço muito diferente. Fica abaixo do relevance_threshold:
# um erro no nome ("Radii-Call" × "Radii Cal") ou "1 kit" × unidade derrubam a
# nota do produto certo para 0,25–0,4.
_PRICE_PICK_FLOOR = 0.25
_PRICE_PICK_REASON = "escolhido pelo preço próximo ao da Odonto Master"

# Lojas cuja busca encontra o produto pelo EAN (verificado em 10/2026).
# SmartHint (Cremer/Speed) e Surya não retornam nada para um EAN.
_EAN_SEARCH_STORES = frozenset({"Dental Shop"})

# Nota máxima quando os dois lados têm EAN válido e diferente
_EAN_MISMATCH_CAP = round(EXACT_THRESHOLD - 0.01, 3)


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
    match_level: str = "similar"  # "exato" | "provavel" | "similar"
    brand_match: Optional[bool] = None  # None = marca desconhecida
    reasons: list[str] = field(default_factory=list)
    ean_match: Optional[bool] = None  # None = EAN ausente em um dos lados
    package_match: Optional[bool] = None  # None = embalagem não comparável
    price_pick: bool = False  # escolhido pelo preço (a de maior nota destoava)

    @property
    def delta(self) -> PriceDelta:
        return PriceDelta(
            odonto_master_price=self.odonto_master.price,
            competitor_price=self.concorrente.preco_final,
        )

    @property
    def price_outlier(self) -> bool:
        """Preço muito distante do da Odonto Master (provável item diferente)."""
        om_price = self.odonto_master.price
        if om_price <= 0 or self.concorrente.preco_final <= 0:
            return False
        ratio = self.concorrente.preco_final / om_price
        return ratio > _OUTLIER_HIGH or ratio < _OUTLIER_LOW

    def to_dict(self) -> dict:
        return {
            "odonto_master": self.odonto_master.to_dict(),
            "concorrente": self.concorrente.to_dict(),
            # Alias retrocompatível
            "dental_cremer": self.concorrente.to_dict(),
            "relevance_score": round(self.relevance_score, 3),
            "match_level": self.match_level,
            "brand_match": self.brand_match,
            "ean_match": self.ean_match,
            "package_match": self.package_match,
            "reasons": self.reasons,
            "price_outlier": self.price_outlier,
            "price_pick": self.price_pick,
            "delta": self.delta.to_dict(),
        }


@dataclass
class StoreResult:
    """Resultado de busca em uma loja específica."""

    fonte: str
    matches: list[ComparisonItem] = field(default_factory=list)
    similares: list[ComparisonItem] = field(default_factory=list)
    all_produtos: list[DentalCremerProduct] = field(default_factory=list)
    total: int = 0
    termos_usados: list[str] = field(default_factory=list)
    erro: Optional[str] = None

    @property
    def sucesso(self) -> bool:
        return self.erro is None

    @property
    def best_match(self) -> Optional[ComparisonItem]:
        # ``matches`` já vem ordenado por _rank_items
        return self.matches[0] if self.matches else None

    def to_dict(self) -> dict:
        return {
            "fonte": self.fonte,
            "total": self.total,
            "erro": self.erro,
            "termos_usados": self.termos_usados,
            "matches": [m.to_dict() for m in self.matches],
            "similares": [m.to_dict() for m in self.similares],
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
    # A API da Odonto Master falhou (≠ SKU inexistente)
    service_error: bool = False

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
            "service_error": self.service_error,
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
# Helpers de ranqueamento
# ---------------------------------------------------------------------------

_LEVEL_ORDER = {"exato": 0, "provavel": 1, "similar": 2}


def _rank_items(
    items: list[ComparisonItem],
    om_price: float,
    alternatives: Sequence[ComparisonItem] = (),
) -> list[ComparisonItem]:
    """
    Ordena por nota e põe na frente o melhor par, escolhido nesta ordem:
      1. EAN idêntico;
      2. o maior nível disponível (exato > provável > similar);
      3. dentro do nível, preço plausível (sem "preço muito diferente");
      4. entre notas praticamente iguais à maior (até _TIE_MARGIN): mesma
         embalagem (pela descrição), marca confirmada, em estoque e preço
         mais próximo do da Odonto Master.

    Se o escolhido ainda tiver preço muito diferente e não for exato nem o
    mesmo EAN (nome e EAN conferem: a diferença de preço é real), a melhor das
    ``alternatives`` — produtos do mesmo tipo, sem conflito no nome e com preço
    plausível, mesmo com nota abaixo do corte — assume o lugar e entra na lista.
    """
    if not items:
        return items
    items = sorted(items, key=lambda i: i.relevance_score, reverse=True)

    pool = [i for i in items if i.ean_match] or items
    best_level = min(_LEVEL_ORDER.get(i.match_level, 2) for i in pool)
    pool = [i for i in pool if _LEVEL_ORDER.get(i.match_level, 2) == best_level]
    pool = [i for i in pool if not i.price_outlier] or pool
    top = max(i.relevance_score for i in pool)
    tied = [i for i in pool if top - i.relevance_score <= _TIE_MARGIN]
    best = min(
        tied,
        key=lambda i: (
            i.package_match is not True,
            i.brand_match is not True,
            not i.concorrente.disponivel,
            price_distance(om_price, i.concorrente.preco_final),
        ),
    )
    if (
        best.price_outlier
        and not best.ean_match
        and best.match_level != "exato"
        and alternatives
    ):
        best = replace(
            max(
                alternatives,
                key=lambda i: (
                    i.relevance_score,
                    -price_distance(om_price, i.concorrente.preco_final),
                ),
            ),
            price_pick=True,
        )
        best.reasons = best.reasons + [_PRICE_PICK_REASON]
    items = [i for i in items if i.concorrente is not best.concorrente]
    return [best] + items


def _product_key(prod: DentalCremerProduct) -> str:
    # Variantes podem dividir a URL do produto pai (Surya, SmartHint): o SKU
    # diferencia cada uma.
    if prod.url or prod.sku:
        return f"{prod.url}|{prod.sku}"
    return prod.nome


def _score_product(
    ref_sig: ProductSignature,
    ref_ean: str,
    prod: DentalCremerProduct,
    ref_pkg: Optional[PackageInfo] = None,
) -> tuple[MatchScore, Optional[bool], Optional[bool]]:
    """
    Nota do produto concorrente, se o EAN confere e se a embalagem confere
    (None = sem dados em um dos lados).

    O título nem sempre diferencia kit de unidade (na Cremer o kit e a
    seringa avulsa do Transbond XT têm o mesmo nome): a descrição corrige a
    nota. EAN idêntico é o mesmo produto, seja qual for o nome; EAN
    diferente indica outra variante/embalagem e limita a nota a "provável".
    """
    name_sig = parse_name(prod.nome, prod.marca or None)
    cand_pkg = parse_package(prod.descricao)
    cand_sig = with_package(name_sig, cand_pkg, ref_sig.is_kit)
    if ref_pkg and ref_pkg.is_kit and cand_sig.is_kit and cand_sig.is_combo != ref_sig.is_combo:
        # A Odonto Master vende o kit ("Embalagem com 1 kit"): o "+" do
        # nome ("Kit Endofill Pó + Líquido") lista o conteúdo, não um brinde
        cand_sig = replace(cand_sig, is_combo=ref_sig.is_combo)
    ms = score(ref_sig, cand_sig)
    if cand_sig.is_kit != name_sig.is_kit and ref_sig.is_kit != cand_sig.is_kit:
        ms.reasons = [
            r + " (descrição)" if r == "kit × unidade" else r for r in ms.reasons
        ]
    factor, pkg_reasons, package_match = score_package(
        ref_pkg, cand_pkg, ref_sig.is_kit, cand_sig.is_kit
    )
    if ms.value > 0:
        ms.value = round(ms.value * factor, 3)
        ms.reasons.extend(pkg_reasons)

    cand_ean = normalize_gtin(prod.ean)
    if not ref_ean or not cand_ean:
        return ms, None, package_match
    if cand_ean == ref_ean:
        return MatchScore(1.0, True, ["mesmo EAN"]), True, package_match
    ms.value = min(ms.value, _EAN_MISMATCH_CAP)
    ms.reasons.append("EAN diferente")
    return ms, False, package_match


class _TTLCache:
    """
    Resultados de busca por (loja, termo) durante ``ttl`` segundos: evita
    repetir a mesma consulta (e cair no limite de taxa/WAF das lojas) quando
    o mesmo produto é comparado de novo.
    """

    _MAX_ENTRIES = 2000

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._data: dict[tuple[str, str], tuple[float, list]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> Optional[list]:
        if self.ttl <= 0:
            return None
        with self._lock:
            hit = self._data.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                return list(hit[1])
            self._data.pop(key, None)
            return None

    def set(self, key: tuple[str, str], value: list) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._data) >= self._MAX_ENTRIES:
                now = time.monotonic()
                self._data = {
                    k: v for k, v in self._data.items() if now - v[0] < self.ttl
                }
                while len(self._data) >= self._MAX_ENTRIES:
                    self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic(), list(value))


# ---------------------------------------------------------------------------
# Comparador principal
# ---------------------------------------------------------------------------


class PriceComparator:
    """
    Orquestra a busca em todas as lojas concorrentes e consolida os resultados.

    Parâmetros
    ----------
    relevance_threshold : float
        Nota mínima (0–1) para um produto entrar em matches/similares.
        Padrão: 0.5
    max_results : int
        Quantidade máxima de produtos buscados por termo em cada loja. Padrão: 20
    cache_ttl : float
        Segundos que o resultado de uma busca (loja + termo) fica em cache.
        0 desliga o cache. Padrão: 900 (15 min)
    """

    # Nomes canônicos das lojas (usados como chaves em stores)
    STORE_NAMES = [
        "Dental Cremer",
        "Dental Speed",
        "Dental Shop",
        "Surya Dental",
        "Dental Paiva",
        "Dental MedSul",
    ]

    def __init__(
        self,
        relevance_threshold: float = 0.5,
        max_results: int = 20,
        # Parâmetros legados (para não quebrar código existente)
        max_dc_results: int = 0,
        cache_ttl: float = 900,
    ) -> None:
        self.relevance_threshold = relevance_threshold
        self.max_results = max_results or max_dc_results or 20
        self._cache = _TTLCache(cache_ttl)

        self._om_scraper = OdontoMasterScraper()
        self._dc_scraper = DentalCremerScraper()
        self._ds_scraper = DentalSpeedScraper()
        self._dsh_scraper = DentalShopScraper()
        self._su_scraper = SuryaDentalScraper()
        self._pv_scraper = DentalPaivaScraper()
        self._ms_scraper = DentalMedSulScraper()

        self._fetchers: dict[str, Callable[[str], list[DentalCremerProduct]]] = {
            "Dental Cremer": lambda t: self._fetch_smarthint(self._dc_scraper, t),
            "Dental Speed": lambda t: self._ds_scraper.buscar(t, tamanho=self.max_results),
            "Dental Shop": lambda t: self._dsh_scraper.buscar(t, tamanho=self.max_results),
            "Surya Dental": lambda t: self._su_scraper.buscar(t, tamanho=self.max_results),
            "Dental Paiva": lambda t: self._fetch_smarthint(self._pv_scraper, t),
            "Dental MedSul": lambda t: self._ms_scraper.buscar(t, tamanho=self.max_results),
        }

    # ------------------------------------------------------------------
    # Método público
    # ------------------------------------------------------------------

    def compare_by_sku(self, sku: str, use_cache: bool = True) -> ComparisonResult:
        """
        Ponto de entrada principal.

        1. Busca o produto na Odonto Master pelo SKU.
        2. Gera os termos de busca e consulta as lojas em paralelo.
        3. Pontua, separa matches/similares e calcula o delta de preço.
        4. Retorna um ComparisonResult.

        ``use_cache=False`` refaz as buscas nas lojas (preços atualizados).
        """
        sku = sku.strip()
        logger.info("Iniciando comparação para SKU: %s", sku)

        # ── Etapa 1: Odonto Master ──────────────────────────────────────
        try:
            om_product = self._om_scraper.get_by_sku(sku)
        except ScraperError as exc:
            logger.error("Odonto Master indisponível ao buscar '%s': %s", sku, exc)
            return ComparisonResult(
                sku_consultado=sku,
                odonto_master_product=None,
                error=f"Não foi possível consultar a Odonto Master: {exc} Tente novamente.",
                service_error=True,
            )

        if om_product is None:
            logger.warning("SKU '%s' não encontrado na Odonto Master.", sku)
            return ComparisonResult(
                sku_consultado=sku,
                odonto_master_product=None,
                error=f"Produto com SKU '{sku}' não encontrado na Odonto Master.",
            )

        logger.info(
            "Odonto Master → '%s' (%s) | R$ %.2f",
            om_product.name,
            om_product.brand or "marca ?",
            om_product.price,
        )

        # ── Etapa 2: Busca paralela nas lojas concorrentes ──────────────
        # Termos sem a variação (as lojas nem sempre a indexam); a nota usa
        # o nome com a variação ("Sonda Exploradora Nº47 - ICE").
        ref_pkg = parse_package(om_product.package_text)
        queries = build_queries(
            om_product.name, om_product.brand or None, package=ref_pkg
        )
        # "Embalagem com 1 kit" faz o produto contar como kit mesmo sem
        # "Kit" no nome
        ref_sig = with_package(
            parse_name(om_product.match_name, om_product.brand or None), ref_pkg
        )
        ref_ean = normalize_gtin(om_product.ean)
        logger.info(
            "Termos de busca: %s | variação: %s | EAN: %s | embalagem: %s",
            queries,
            om_product.variation or "-",
            ref_ean or "-",
            om_product.package_text or "-",
        )

        stores: dict[str, StoreResult] = {}
        with ThreadPoolExecutor(max_workers=len(self._fetchers)) as executor:
            future_to_store = {
                executor.submit(
                    self._search_store,
                    nome,
                    fetch,
                    [om_product.ean.strip()] + queries
                    if ref_ean and nome in _EAN_SEARCH_STORES
                    else queries,
                    om_product,
                    ref_sig,
                    ref_ean,
                    use_cache,
                    ref_pkg,
                ): nome
                for nome, fetch in self._fetchers.items()
            }
            for future in as_completed(future_to_store):
                store_name = future_to_store[future]
                try:
                    stores[store_name] = future.result()
                except Exception as exc:
                    logger.exception("Erro inesperado em '%s': %s", store_name, exc)
                    stores[store_name] = StoreResult(
                        fonte=store_name,
                        erro=f"Erro inesperado: {exc}",
                    )

        # Mantém a ordem de exibição fixa
        stores = {n: stores[n] for n in self._fetchers if n in stores}

        return ComparisonResult(
            sku_consultado=sku,
            odonto_master_product=om_product,
            stores=stores,
            search_term_used=queries[0] if queries else "",
        )

    def search_odonto_master(self, query: str, limit: int = 12) -> list[OdontoMasterProduct]:
        """
        Busca produtos da Odonto Master pelo nome (texto livre).

        A API devolve um item por produto (uma das variações); itens
        repetidos pelo mesmo SKU são descartados.

        Raises:
            ScraperError: a API da Odonto Master não respondeu.
        """
        query = (query or "").strip()
        if not query:
            return []
        seen: set[str] = set()
        produtos: list[OdontoMasterProduct] = []
        for prod in self._om_scraper.search(query, first=limit):
            if prod.sku and prod.sku not in seen:
                seen.add(prod.sku)
                produtos.append(prod)
        return produtos

    # ------------------------------------------------------------------
    # Busca em cascata por loja
    # ------------------------------------------------------------------

    def _fetch(
        self,
        store_name: str,
        fetch: Callable[[str], list[DentalCremerProduct]],
        query: str,
        use_cache: bool,
    ) -> list[DentalCremerProduct]:
        key = (store_name, query.strip().lower())
        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                logger.info("%s → '%s' do cache (%d produtos).", store_name, query, len(cached))
                return cached
        found = fetch(query)
        self._cache.set(key, found)
        return found

    def _search_store(
        self,
        store_name: str,
        fetch: Callable[[str], list[DentalCremerProduct]],
        queries: list[str],
        om_product: OdontoMasterProduct,
        ref_sig: ProductSignature,
        ref_ean: str = "",
        use_cache: bool = True,
        ref_pkg: Optional[PackageInfo] = None,
    ) -> StoreResult:
        produtos: dict[str, DentalCremerProduct] = {}
        scores: dict[str, MatchScore] = {}
        ean_matches: dict[str, Optional[bool]] = {}
        package_matches: dict[str, Optional[bool]] = {}
        termos: list[str] = []
        last_error: Optional[str] = None

        for query in queries:
            try:
                found = self._fetch(store_name, fetch, query, use_cache)
            except ScraperError as exc:
                logger.warning("%s → falha com '%s': %s", store_name, query, exc)
                last_error = str(exc)
                continue

            termos.append(query)
            for prod in found:
                key = _product_key(prod)
                if key in produtos:
                    continue
                produtos[key] = prod
                scores[key], ean_matches[key], package_matches[key] = _score_product(
                    ref_sig, ref_ean, prod, ref_pkg
                )

            if any(
                s.value >= EXACT_THRESHOLD and s.brand_match is not False
                for s in scores.values()
            ):
                break

        if not termos:
            return StoreResult(
                fonte=store_name,
                erro=last_error or "Não foi possível consultar a loja.",
            )

        matches: list[ComparisonItem] = []
        similares: list[ComparisonItem] = []
        # Mesmo tipo de produto, sem conflito no nome (com × sem, tamanho…) e
        # preço plausível: substituem uma melhor opção com preço muito
        # diferente (_rank_items)
        alternativas: list[ComparisonItem] = []
        for key, prod in produtos.items():
            ms = scores[key]
            if ms.value < min(self.relevance_threshold, _PRICE_PICK_FLOOR):
                continue
            item = ComparisonItem(
                odonto_master=om_product,
                concorrente=prod,
                relevance_score=ms.value,
                match_level=ms.level,
                brand_match=ms.brand_match,
                reasons=ms.reasons,
                ean_match=ean_matches[key],
                package_match=package_matches[key],
            )
            if (
                ms.value >= _PRICE_PICK_FLOOR
                and ms.brand_match is not False
                and not ms.conflict
                and not item.price_outlier
                and same_kind(ref_sig, parse_name(prod.nome, prod.marca or None))
            ):
                alternativas.append(item)
            if ms.value < self.relevance_threshold:
                continue
            (similares if ms.brand_match is False else matches).append(item)

        result = StoreResult(
            fonte=store_name,
            matches=_rank_items(matches, om_product.price, alternativas),
            similares=_rank_items(similares, om_product.price),
            all_produtos=list(produtos.values()),
            total=len(produtos),
            termos_usados=termos,
        )

        logger.info(
            "%s → %d produto(s) em %d busca(s) | %d match(es) | %d similar(es).",
            store_name,
            result.total,
            len(termos),
            len(result.matches),
            len(result.similares),
        )
        return result

    def _fetch_smarthint(
        self, scraper: SmartHintScraper, term: str
    ) -> list[DentalCremerProduct]:
        result = scraper.buscar(term, tamanho=self.max_results)
        if not result.sucesso:
            raise ScraperError(result.erro or f"Falha na {scraper.fonte}.")
        return result.produtos


# ---------------------------------------------------------------------------
# Execução standalone:  python comparator.py <SKU>
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
        print(f"    Marca : {om.brand or '-'}")
        print(f"    Var.  : {om.variation or '-'}  |  EAN: {om.ean or '-'}")
        print(f"    Emb.  : {om.package_text or '-'}")
        print(f"    SKU   : {om.sku}")
        print(f"    Preco : R$ {om.price:.2f}  (lista: R$ {om.list_price:.2f})")
        print(f"    URL   : {om.url}")

    for store_name, store_result in result.stores.items():
        print(f"\n{'─'*70}")
        icon = "[OK]" if store_result.matches else ("[--]" if store_result.all_produtos else "[X]")
        print(
            f"{icon}  {store_name.upper()}  (produtos={store_result.total} | "
            f"matches={len(store_result.matches)} | similares={len(store_result.similares)})"
        )
        print(f"    Termos: {store_result.termos_usados}")
        if store_result.erro:
            print(f"    Erro: {store_result.erro}")
        for i, match in enumerate(store_result.matches[:3], 1):
            dc = match.concorrente
            delta = match.delta
            arrow = "↓" if delta.competitor_is_cheaper else "↑"
            pix = f" | Pix R$ {dc.preco_pix:.2f}" if dc.preco_pix else ""
            alerta = "  ⚠ preço muito diferente" if match.price_outlier else ""
            ean = "  [EAN ok]" if match.ean_match else ""
            print(f"\n    [{i}] {dc.nome}  ({match.match_level}, {match.relevance_score:.0%}){ean}")
            print(f"        SKU   : {dc.sku}")
            if dc.descricao:
                print(f"        Emb.  : {dc.descricao}")
            if match.reasons:
                print(f"        Motivo: {', '.join(match.reasons)}")
            print(f"        Preço : R$ {dc.preco_final:.2f}{pix}{alerta}")
            print(f"        Delta : {arrow} {delta.label}")
            print(f"        URL   : {dc.url}")
        for sim in store_result.similares[:2]:
            print(
                f"    ~ similar: {sim.concorrente.nome} — R$ {sim.concorrente.preco_final:.2f}"
                f" ({sim.relevance_score:.0%})"
            )
