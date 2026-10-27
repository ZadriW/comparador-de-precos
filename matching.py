"""
Correspondência de produtos entre lojas
=======================================

Decide se um produto de uma loja concorrente é o mesmo item da Odonto Master.

Cada nome vira uma ``ProductSignature`` com:
  - words     : palavras significativas e códigos (z350, mcx5, 17s…)
  - numbers   : números, medidas e quantidades em forma canônica
                (5, 1.2, 20ml, 1000ml para "1L", q100 para "c/ 100" e "Cx 100")
  - brand     : marca canônica (API da loja ou sufixo " - Marca")
  - modifiers : "com X" / "sem X" / "não X" (ex.: com pó × sem pó); sem
                modificador, a palavra presente no nome conta como "com"
  - sizes     : tamanho canônico (PP, P, M, G, GG; "Grande" → G)
  - is_kit    : kit, combo, "+", grátis, brinde, "10 caixas"…

``score()`` combina essas partes em uma nota de 0 a 1. A marca não entra na
nota: ela só indica se o item é comparável (``brand_match``) ou um similar
de outra marca.

Só usa a biblioteca padrão.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

# ---------------------------------------------------------------------------
# Parâmetros
# ---------------------------------------------------------------------------

EXACT_THRESHOLD = 0.85     # "exato"
PROBABLE_THRESHOLD = 0.65  # "provável"

# Peso do recall (palavras da Odonto Master encontradas) sobre a precisão
# (palavras extras no concorrente). beta > 1 favorece o recall.
_BETA_SQ = 1.5 ** 2

_STOPWORDS = frozenset(
    {
        "de", "da", "do", "das", "dos", "para", "pra", "e", "em", "a", "o",
        "as", "os", "um", "uma", "por", "ao", "aos", "na", "no", "nas", "nos",
        "se", "und", "un", "unid", "x", "tam", "tamanho", "cx", "cxs", "pct",
    }
)

# Tamanhos (luvas, aventais, toucas…) → forma canônica.
# Letras soltas só valem como tamanho; "extra/ex pequeno" e "extra grande"
# são tratados em parse_name.
_SIZES = {
    "pp": "pp", "ep": "pp", "xp": "pp", "xs": "pp",
    "p": "p", "pequeno": "p", "pequena": "p",
    "m": "m", "medio": "m", "media": "m",
    "g": "g", "grande": "g",
    "gg": "gg", "xg": "gg", "eg": "gg", "xl": "gg",
}
_SIZE_PREFIXES = {"extra", "ex"}

# Indicam venda de mais de uma unidade/item. "kit"/"combo" saem das palavras do
# nome; "duopack"/"tripack" continuam nelas (DuoPack ≠ Kit Intro).
_KIT_WORDS = frozenset({"kit", "kits", "combo", "duopack", "tripack", "multipack"})
_KIT_WORDS_KEPT = frozenset({"duopack", "tripack", "multipack"})

# Indicam pacote com produtos diferentes ("Resina + Discos", "… grátis")
_COMBO_WORDS = frozenset({"+", "gratis", "brinde", "acompanha"})

# Palavras que não identificam uma marca sozinhas ("Dental Cremer", "Odonto Brasil")
_GENERIC_BRAND_WORDS = frozenset(
    {"dental", "odonto", "brasil", "produtos", "industria", "medical", "lab"}
)

# Pares de palavras que indicam itens diferentes quando aparecem cruzadas
_OPPOSITES = (
    ("infantil", "adulto"),
    ("reta", "curva"),
    ("reto", "curvo"),
    ("fino", "grosso"),
    ("fina", "grossa"),
    ("procedimento", "cirurgica"),
    ("procedimento", "cirurgico"),
    ("curta", "longa"),
    ("curto", "longo"),
    ("superior", "inferior"),
    ("direito", "esquerdo"),
    ("direita", "esquerda"),
    ("mesial", "distal"),
    ("macho", "femea"),
    ("latex", "nitrilo"),
    ("latex", "vinil"),
    ("nitrilo", "vinil"),
)

# Marca canônica → nomes alternativos (já normalizados: minúsculas, sem acento,
# pontos e hífens viram espaço). Amplie aqui quando surgirem novas grafias.
BRAND_ALIASES: dict[str, set[str]] = {
    "solventum": {"3m", "3m espe", "3m oral care", "3m do brasil"},
    "dentsply sirona": {"dentsply", "sirona", "dentsply maillefer", "maillefer"},
    "golgran": {"millennium", "millenium", "golgran millennium"},
    "hu friedy": {"hufriedy"},
    "kg sorensen": {"kg"},
    "ivoclar": {"ivoclar vivadent"},
    "supermax": {"super max"},
    "ss white": {"sswhite", "s s white"},
    "coltene": {"vigodent", "coltene vigodent", "vigodent coltene", "coltene whaledent"},
    "kulzer": {"heraeus kulzer", "heraeus"},
    "dfl": {"nova dfl"},
    "kerr": {"kavo kerr", "kerr dental"},
    "gc": {"gc america", "gc corporation"},
    "prevest denpro": {"prevest"},
    "ultradent": {"ultradent products"},
}

# Sinônimos aplicados em normalize() (depois de remover acentos)
_SYNONYMS = (
    (re.compile(r"\bpowder\s+free\b"), "sem po"),
    (re.compile(r"\btalco\b"), "po"),
    (re.compile(r"\bnitril(?:ica|ico|icas|icos|a|o)\b"), "nitrilo"),
    (re.compile(r"\bs\s*/\s*(?=[a-z])"), " sem "),
    (re.compile(r"\bc\s*/\s*(?=[a-z])"), " com "),
)

# Palavras que negam a seguinte: "sem pó", "não estéril"
_NEGATIONS = frozenset({"sem", "nao"})

_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in BRAND_ALIASES.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _alias in _aliases:
        _ALIAS_TO_CANON[_alias] = _canon

_DASHES = str.maketrans({"–": "-", "—": "-", "‐": "-", "‑": "-"})

_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)(ml|mg|g|kg|l|mm|cm|m)?$")
_QTY_RE = re.compile(r"^q(\d+)$")
_BOX_RE = re.compile(r"\b\d+\s*(?:caixas|caixa|cx|cxs|pacotes|frascos)\b")
# "Cx 100", "Caixa com 100", "Pacote c/ 50" → quantidade (q100), exceto medidas
_PACK_QTY_RE = re.compile(
    r"\b(cx|cxs|caixa|pct|pacote|embalagem|frasco)\s+(?:com\s+)?(\d+)\b"
    r"(?!\.\d|\s*(?:ml|mg|g|kg|l|mm|cm|m)\b)"
)


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize(text: str) -> str:
    """
    Normaliza um nome de produto para comparação.

    - minúsculas, sem acentos, sem ™ ® ©
    - "Nº 5", "N°5", "n.5" → "5"
    - "1,2" → "1.2";  "20 ml" → "20ml"
    - "c/ 100", "100un", "1 UN", "Cx 100" → "q100" / "q1" (quantidade)
    - "c/ pó" → "com po", "s/ pó" e "powder free" → "sem po", "talco" → "po",
      "nitrílica" → "nitrilo"
    - "+" vira token próprio; demais símbolos viram espaço
    """
    t = (text or "").translate(_DASHES).lower()
    t = re.sub(r"[™®©]", " ", t)
    # Antes de remover acentos: em NFKD o "º" vira "o".
    t = re.sub(r"\bn\s*[º°o]?\s*\.?\s*(?=\d)", " ", t)
    t = re.sub(r"[º°ª]", " ", t)
    t = _strip_accents(t)
    for pattern, repl in _SYNONYMS:
        t = pattern.sub(repl, t)
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    t = re.sub(r"\bc\s*/\s*(\d+)", r" q\1 ", t)
    t = re.sub(
        r"\b(\d+)\s*(?:un|und|unid|unidade|unidades|pcs|pecas)\b", r" q\1 ", t
    )
    t = _PACK_QTY_RE.sub(r"\1 q\2", t)
    t = re.sub(r"(\d(?:\.\d+)?)\s+(ml|mg|g|kg|l|mm|cm|m)\b", r"\1\2", t)
    t = t.replace("+", " + ")
    t = re.sub(r"[^a-z0-9.+]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def canonical_brand(raw: str) -> str:
    """Normaliza a marca e resolve aliases (ex.: '3M ESPE' → 'solventum')."""
    n = normalize(raw)
    if not n:
        return ""
    return _ALIAS_TO_CANON.get(n, n)


def _brand_words(brand_key: str) -> set[str]:
    """Todas as palavras que identificam a marca (canônica + aliases)."""
    if not brand_key:
        return set()
    words = set(brand_key.split())
    for alias in BRAND_ALIASES.get(brand_key, ()):
        words.update(alias.split())
    return words


def _brand_keys(raw: str) -> set[str]:
    """
    Chaves de comparação de uma marca: a forma canônica completa, aliases
    reconhecidos em qualquer trecho de 1–2 palavras e a primeira palavra
    (ex.: '3M Solventum' → {'3m solventum', 'solventum', '3m'}).
    """
    n = normalize(raw)
    if not n:
        return set()
    tokens = n.split()
    keys = {_ALIAS_TO_CANON.get(n, n)}
    for size in (1, 2):
        for i in range(len(tokens) - size + 1):
            window = " ".join(tokens[i:i + size])
            if window in _ALIAS_TO_CANON:
                keys.add(_ALIAS_TO_CANON[window])
    first = tokens[0]
    if len(first) >= 3 and first not in _GENERIC_BRAND_WORDS:
        keys.add(_ALIAS_TO_CANON.get(first, first))
    return keys


def split_brand_suffix(name: str) -> tuple[str, str]:
    """
    Separa o padrão 'Produto - Marca' usado por quase todas as lojas.
    Retorna (nome_sem_marca, marca). A marca fica vazia se o sufixo não
    parecer uma marca (mais de 3 palavras, ou números — salvo marcas
    conhecidas como "3M").
    """
    name = (name or "").translate(_DASHES).strip()
    # Hífen com espaço em pelo menos um lado ("A - B", "A- B"); "Sof-Lex" não conta
    seps = list(re.finditer(r"\s+-\s*|\s*-\s+", name))
    if seps:
        sep = seps[-1]
        head, tail = name[: sep.start()], name[sep.end():].strip()
        if (
            tail
            and len(tail.split()) <= 3
            and (not re.search(r"\d", tail) or normalize(tail) in _ALIAS_TO_CANON)
        ):
            return head.strip(), tail
    return name, ""


def append_variation(name: str, labels: list[str]) -> str:
    """
    Acrescenta a variação (tamanho, numeração, cor…) ao nome, antes do
    sufixo " - Marca": ('Luva Látex - Medix', ['P']) → 'Luva Látex P - Medix'.
    Partes do rótulo ("Nº 47 - Azul" → "Nº 47", "Azul") cujas palavras já
    estão no nome são ignoradas.
    """
    base, brand = split_brand_suffix(name)
    present = set(normalize(base).split())
    extra: list[str] = []
    for label in labels:
        for part in re.split(r"\s+-\s+", (label or "").translate(_DASHES)):
            part = part.strip()
            tokens = set(normalize(part).split())
            if tokens and not tokens <= present:
                extra.append(part)
                present |= tokens
    if not extra:
        return name
    base = f"{base.strip()} {' '.join(extra)}"
    return f"{base} - {brand}" if brand else base


def normalize_gtin(code: Optional[str]) -> str:
    """
    EAN/GTIN normalizado (só dígitos, sem zeros à esquerda) ou "" se o código
    for inválido — as lojas usam "0", "SEM GTIN" ou códigos internos.
    """
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) not in (8, 12, 13, 14):
        return ""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    if (10 - total % 10) % 10 != check:
        return ""
    return digits.lstrip("0")


def _fmt_number(value: float) -> str:
    return ("%.4f" % value).rstrip("0").rstrip(".")


def _canonical_measure(tok: str) -> str:
    """
    Número ou medida em forma canônica, ou "" se o token não for medida:
    "05" → "5", "1l" → "1000ml", "1kg" → "1000g", "1.0ml" → "1ml".

    "l" e "m" colados também são letras de código: só valem como litro até
    5 L ("18L" é o fórceps esquerdo) e como metro a partir de 5 m ("3M").
    """
    m = _NUMBER_RE.match(tok)
    if not m:
        return ""
    value, unit = float(m.group(1)), m.group(2) or ""
    if unit == "l":
        if value > 5 and value.is_integer():
            return ""
        return f"{_fmt_number(value * 1000)}ml"
    if unit == "m" and value < 5:
        return ""
    if unit == "kg":
        return f"{_fmt_number(value * 1000)}g"
    return f"{_fmt_number(value)}{unit}"


# ---------------------------------------------------------------------------
# Assinatura do produto
# ---------------------------------------------------------------------------


@dataclass
class ProductSignature:
    words: list[str]
    numbers: set[str]
    brand: str
    brand_keys: set[str] = field(default_factory=set)
    modifiers: dict[str, bool] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    sizes: set[str] = field(default_factory=set)
    is_kit: bool = False
    is_combo: bool = False


def parse_name(name: str, brand: Optional[str] = None) -> ProductSignature:
    """
    Gera a assinatura de comparação de um nome de produto.

    ``brand`` é a marca informada pela API da loja. Ela e o sufixo
    " - Marca" do nome contam para a comparação de marca, porque algumas
    APIs trazem a linha do produto no campo de marca (ex.: Dental Shop
    com brand="Z350 XT"). As palavras de marca só são removidas do nome
    quando a fonte é confiável: o sufixo, ou a API quando não há sufixo
    ou quando ela concorda com o sufixo.
    """
    base, suffix_brand = split_brand_suffix(name)
    suffix_key = canonical_brand(suffix_brand)
    api_key = canonical_brand(brand or "")

    brand_keys = _brand_keys(suffix_brand) | _brand_keys(brand or "")
    brand_key = suffix_key or api_key

    brand_words = _brand_words(suffix_key)
    if api_key and (not suffix_key or _brand_keys(brand or "") & _brand_keys(suffix_brand)):
        brand_words |= _brand_words(api_key)

    normalized = normalize(base)
    tokens = normalized.split()

    is_kit = any(t in _KIT_WORDS for t in tokens) or bool(_BOX_RE.search(normalized))
    is_combo = any(t in _COMBO_WORDS for t in tokens)

    modifiers: dict[str, bool] = {}
    for i, tok in enumerate(tokens):
        if tok == "com" or tok in _NEGATIONS:
            nxt = next(
                (t for t in tokens[i + 1:] if t not in _STOPWORDS), None
            )
            if nxt and nxt != "com" and nxt not in _NEGATIONS and nxt != "+":
                modifiers[nxt] = tok == "com"

    opposite_words = {w for pair in _OPPOSITES for w in pair}

    words: list[str] = []
    numbers: set[str] = set()
    flags: set[str] = set()
    sizes: set[str] = set()
    skip_next = False
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _SIZE_PREFIXES and _SIZES.get(nxt) in ("p", "g"):
            sizes.add(_SIZES[nxt] * 2)  # extra pequeno → pp, extra grande → gg
            skip_next = True
            continue
        if tok in _SIZES:
            sizes.add(_SIZES[tok])
            continue
        if tok in _STOPWORDS or tok == "com" or tok in _NEGATIONS or tok in _COMBO_WORDS:
            continue
        if tok in _KIT_WORDS and tok not in _KIT_WORDS_KEPT:
            continue
        if tok in brand_words:
            continue
        qty = _QTY_RE.match(tok)
        if qty:
            if qty.group(1) != "1":
                numbers.add(tok)
            continue
        measure = _canonical_measure(tok)
        if measure:
            numbers.add(measure)
            continue
        # "18L", "3M" etc. caem aqui e viram código (palavra com dígito)
        if len(tok) < 2:
            continue
        if tok in opposite_words:
            flags.add(tok)
        words.append(tok)

    return ProductSignature(
        words=words,
        numbers=numbers,
        brand=brand_key,
        brand_keys=brand_keys,
        modifiers=modifiers,
        flags=flags,
        sizes=sizes,
        is_kit=is_kit,
        is_combo=is_combo,
    )


# ---------------------------------------------------------------------------
# Pontuação
# ---------------------------------------------------------------------------


@dataclass
class MatchScore:
    value: float
    brand_match: Optional[bool]
    reasons: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        return match_level(self.value)


def match_level(value: float) -> str:
    if value >= EXACT_THRESHOLD:
        return "exato"
    if value >= PROBABLE_THRESHOLD:
        return "provavel"
    return "similar"


def _word_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    # Códigos com dígitos (z350, mcx5) só casam exatamente
    if any(ch.isdigit() for ch in a + b):
        return False
    if a.rstrip("s") == b.rstrip("s"):
        return True
    return min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.88



def score(ref: ProductSignature, cand: ProductSignature) -> MatchScore:
    """
    Nota de 0 a 1 indicando o quanto ``cand`` é o mesmo produto que ``ref``.
    """
    reasons: list[str] = []

    brand_match: Optional[bool] = None
    if ref.brand_keys and cand.brand_keys:
        brand_match = bool(ref.brand_keys & cand.brand_keys)
        if not brand_match:
            reasons.append("marca diferente")

    if not ref.words or not cand.words:
        return MatchScore(0.0, brand_match, reasons)

    # ── Palavras: F-score com peso maior no recall ──────────────────────
    used = [False] * len(cand.words)
    matched = 0
    for rw in ref.words:
        for j, cw in enumerate(cand.words):
            if not used[j] and _word_eq(rw, cw):
                used[j] = True
                matched += 1
                break

    if matched == 0:
        return MatchScore(0.0, brand_match, reasons)

    recall = matched / len(ref.words)
    precision = matched / len(cand.words)
    value = (1 + _BETA_SQ) * precision * recall / (_BETA_SQ * precision + recall)
    if precision < 1:
        reasons.append(f"{len(cand.words) - matched} palavra(s) a mais")
    if recall < 1:
        reasons.append(f"{len(ref.words) - matched} palavra(s) ausente(s)")

    # ── Códigos de modelo (z350 × z250, mcx5 × mcxl) ────────────────────
    ref_codes = {w for w in ref.words if any(ch.isdigit() for ch in w)}
    cand_codes = {w for w in cand.words if any(ch.isdigit() for ch in w)}
    if ref_codes - cand_codes and cand_codes - ref_codes:
        value *= 0.4
        reasons.append("código/modelo diferente")

    # ── Números, medidas e quantidades ──────────────────────────────────
    ref_numbers, cand_numbers = _align_quantities(ref.numbers, cand.numbers)
    if ref_numbers and cand_numbers:
        if ref_numbers != cand_numbers:
            if ref_numbers & cand_numbers:
                value *= 0.7
                reasons.append("medidas/números parcialmente diferentes")
            else:
                value *= 0.4
                reasons.append("medidas/números diferentes")
    elif ref_numbers:
        value *= 0.85
        reasons.append("sem a medida/número do produto")
    elif cand_numbers:
        value *= 0.9
        reasons.append("medida/número extra")

    # ── "com X" × "sem X" ───────────────────────────────────────────────
    # Sem modificador, a palavra presente no nome conta como "com":
    # "Luva Látex Pó" × "Luva Látex Sem Pó", "Estéril" × "Não Estéril".
    def _has(sig: ProductSignature, key: str) -> Optional[bool]:
        if key in sig.modifiers:
            return sig.modifiers[key]
        return True if key in sig.words else None

    for key in set(ref.modifiers) | set(cand.modifiers):
        r_has, c_has = _has(ref, key), _has(cand, key)
        if r_has is not None and c_has is not None and r_has != c_has:
            value *= 0.3
            reasons.append(f"{'com' if r_has else 'sem'} × {'com' if c_has else 'sem'} {key}")

    # ── Pares opostos (infantil × adulto…) ──────────────────────────────
    for a, b in _OPPOSITES:
        if (a in ref.flags and b in cand.flags) or (b in ref.flags and a in cand.flags):
            value *= 0.3
            reasons.append(f"{a} × {b}")

    # ── Tamanho (P × M) ─────────────────────────────────────────────────
    if ref.sizes and cand.sizes and not ref.sizes & cand.sizes:
        value *= 0.3
        reasons.append(
            f"tamanho {'/'.join(sorted(ref.sizes)).upper()} × {'/'.join(sorted(cand.sizes)).upper()}"
        )

    # ── Kit × unidade ───────────────────────────────────────────────────
    if ref.is_kit != cand.is_kit:
        value *= 0.55
        reasons.append("kit × unidade" if cand.is_kit else "unidade × kit")

    # ── Combo com outros produtos (Resina + Discos, … grátis) ───────────
    if ref.is_combo != cand.is_combo:
        value *= 0.55
        reasons.append("combo com outros itens" if cand.is_combo else "sem os itens do combo")

    return MatchScore(round(min(value, 1.0), 3), brand_match, reasons)


def _align_quantities(a: set[str], b: set[str]) -> tuple[set[str], set[str]]:
    """
    "100" de um lado e "q100" do outro são a mesma quantidade ("Cx 100" ×
    "c/ 100 un"): o número solto vira quantidade.
    """
    a_q = {n for n in a if n.startswith("q")}
    b_q = {n for n in b if n.startswith("q")}
    a = {f"q{n}" if f"q{n}" in b_q else n for n in a}
    b = {f"q{n}" if f"q{n}" in a_q else n for n in b}
    return a, b


def price_distance(ref_price: float, cand_price: float) -> float:
    """Distância relativa entre preços (0 = iguais), simétrica."""
    if ref_price <= 0 or cand_price <= 0:
        return math.inf
    return abs(math.log(cand_price / ref_price))


# ---------------------------------------------------------------------------
# Termos de busca
# ---------------------------------------------------------------------------


def _is_code(tok: str) -> bool:
    """Código de modelo/cor (Z350, A2, 18L): letras e dígitos, sem ser medida."""
    n = normalize(tok).replace(" ", "")
    return (
        any(c.isdigit() for c in n)
        and any(c.isalpha() for c in n)
        and not _canonical_measure(n)
        and not _QTY_RE.match(n)
    )


def build_queries(name: str, brand: Optional[str] = None, max_words: int = 6) -> list[str]:
    """
    Gera os termos de busca em ordem de tentativa:
      1. palavras significativas sem a marca (até ``max_words``); em nomes
         longos, os códigos de modelo/cor (Z350, A2) têm prioridade sobre
         as palavras do fim
      2. o mesmo termo + marca
      3. núcleo curto (3 primeiras palavras)
      4. primeira palavra + códigos + marca ("Resina Z350 A2 3M")

    Mantém a grafia original (com acentos) para não atrapalhar a busca das lojas.
    """
    base, suffix_brand = split_brand_suffix(name)
    brand_display = (brand or suffix_brand or "").strip()
    brand_words = _brand_words(canonical_brand(brand_display))

    base = re.sub(r"[\(\)\[\]\{\}]", " ", base)
    keep: list[str] = []
    for tok in base.split():
        n = normalize(tok)
        if not n or n in _STOPWORDS or n in ("com", "+", "-"):
            continue
        if _QTY_RE.match(n.replace(" ", "")):
            continue
        if n in brand_words:
            continue
        if len(n) < 2 and not n.isdigit():
            continue
        keep.append(tok.strip(",;:"))

    codes = [t for t in keep if _is_code(t)]
    if len(keep) > max_words:
        budget = max(max_words - len(codes), 2)
        chosen: list[str] = []
        for tok in keep:
            if tok in codes or budget > 0:
                if tok not in codes:
                    budget -= 1
                chosen.append(tok)
        main = chosen[: max_words + 2]
    else:
        main = keep

    queries: list[str] = []
    q1 = " ".join(main)
    if q1:
        queries.append(q1)
        if brand_display:
            queries.append(f"{q1} {brand_display}")
        if len(keep) > 3:
            short = keep[:3]
            if normalize(short[-1]) in _NEGATIONS:
                short = short[:-1]
            queries.append(" ".join(short))
        if codes:
            head = [keep[0]] if keep[0] not in codes else []
            queries.append(" ".join(head + codes + ([brand_display] if brand_display else [])))
    elif brand_display:
        queries.append(brand_display)

    seen: set[str] = set()
    return [q for q in queries if not (q.lower() in seen or seen.add(q.lower()))]
