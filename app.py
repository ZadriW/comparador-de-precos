"""
Aplicativo Flask — Comparador de Preços
Dental Odonto Master vs Dental Cremer, Dental Speed, Dental Shop e Surya Dental

Rotas:
  GET  /                      → Página inicial com o campo de busca
  GET  /compare?q=<texto>     → Busca por SKU ou nome: compara direto (SKU / 1 resultado)
                                ou lista os produtos da Odonto Master para escolher
  GET  /compare?sku=<SKU>     → Página de resultados HTML de um SKU
  GET  /api/search?q=<texto>  → Produtos da Odonto Master em JSON (sugestões da busca)
  GET  /api/compare?sku=<SKU> → Resultado em JSON (para integrações)
  GET  /health                → Health-check
  GET  /manifest.webmanifest  → Manifesto do app (instalação / atalho na área de trabalho)
  GET  /sw.js                 → Service worker (sem cache; só habilita a instalação)
  GET  /icons/<arquivo>       → Ícones do app
  GET  /atalho.url            → Atalho de internet (.url) para navegadores sem instalação

Em /compare e /api/compare, ``refresh=1`` ignora o cache de buscas (15 min).
"""

from __future__ import annotations

import logging
import os
import re
import time
from functools import wraps
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from comparator import ComparisonResult, PriceComparator
from scrapers.base import ScraperError

# ---------------------------------------------------------------------------
# Configuração da aplicação
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.json.ensure_ascii = False  # Preserva caracteres acentuados no JSON (Flask ≥ 2.3)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True

# Instância única do comparador (reutiliza as sessões HTTP e o cache de buscas)
_comparator = PriceComparator(relevance_threshold=0.5, max_results=20)

SEARCH_PAGE_LIMIT = 24     # produtos listados na página de resultados da busca
SUGGESTION_LIMIT = 6       # sugestões exibidas enquanto o usuário digita
_SKU_RE = re.compile(r"[A-Za-z0-9._-]{2,30}")


def _looks_like_sku(text: str) -> bool:
    """Uma palavra só, com ao menos um dígito (ex.: '143026', 'MIL38226A')."""
    return bool(_SKU_RE.fullmatch(text)) and any(ch.isdigit() for ch in text)


# ---------------------------------------------------------------------------
# Decorador de tempo
# ---------------------------------------------------------------------------


def timed(fn):
    """Mede e loga o tempo de execução de uma rota."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        logger.info("Rota '%s' concluída em %.2fs", request.path, elapsed)
        return result

    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_brl(value: float | None) -> str:
    """Formata um float como moeda BRL (ex: 1234.5 → 'R$ 1.234,50')."""
    if value is None:
        return "—"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _format_pct(value: float | None, signed: bool = True) -> str:
    """Formata percentual no padrão brasileiro (ex: -8.44 → '−8,4%')."""
    if value is None:
        return "—"
    text = f"{abs(value):.1f}".replace(".", ",") + "%"
    if not signed or value == 0:
        return text
    return ("+" if value > 0 else "−") + text


# ---------------------------------------------------------------------------
# Apresentação (cores das lojas, rótulos, ranking e veredito)
# ---------------------------------------------------------------------------

OM_COLOR = "#0e167a"

STORE_META: dict[str, dict[str, str]] = {
    "Dental Cremer": {"color": "#0072bc", "icon": "bi-shop"},
    "Dental Speed": {"color": "#ea580c", "icon": "bi-lightning-charge"},
    "Dental Shop": {"color": "#7c3aed", "icon": "bi-bag"},
    "Surya Dental": {"color": "#0f766e", "icon": "bi-sun"},
}
_DEFAULT_STORE_META = {"color": "#64748b", "icon": "bi-shop"}

# Nível de correspondência → (rótulo, explicação curta)
LEVEL_INFO: dict[str, tuple[str, str]] = {
    "exato": ("Exato", "Mesmo produto"),
    "provavel": ("Provável", "Mesmo produto, com pequenas diferenças no nome"),
    "similar": ("Similar", "Pode ser outra versão, tamanho ou quantidade — confira"),
}

app.jinja_env.filters["brl"] = _format_brl
app.jinja_env.filters["pct"] = _format_pct
app.jinja_env.globals.update(
    STORE_META=STORE_META,
    LEVEL_INFO=LEVEL_INFO,
    OM_COLOR=OM_COLOR,
)


def _store_status(store: dict) -> tuple[str, str]:
    """Situação resumida de uma loja: (código, rótulo)."""
    if store.get("erro"):
        return "error", "Indisponível no momento"
    if not store.get("all_produtos"):
        return "empty", "Nenhum produto encontrado"
    n_matches = len(store.get("matches") or [])
    n_similares = len(store.get("similares") or [])
    if not n_matches and n_similares:
        return "similar_only", f"Só similares de outras marcas ({n_similares})"
    if not n_matches:
        return "no_match", "Nenhum produto equivalente"
    return "match", f"{n_matches} opç{'ão' if n_matches == 1 else 'ões'} equivalente{'' if n_matches == 1 else 's'}"


def _build_overview(result: dict) -> dict:
    """
    Monta o resumo exibido no topo do resultado:
      - ranking de preços (Odonto Master + melhor opção de cada loja)
      - estatísticas do mercado
      - veredito em linguagem simples

    Itens com preço muito distante do da Odonto Master (price_outlier)
    aparecem no ranking, mas sem posição e fora das estatísticas, porque
    provavelmente são outra versão/quantidade do produto.
    """
    om = result.get("odonto_master_product") or {}
    om_price = float(om.get("price") or 0)

    om_row = {
        "kind": "om",
        "store": "Odonto Master",
        "color": OM_COLOR,
        "icon": "bi-star-fill",
        "nome": om.get("name", ""),
        "price": om_price,
        "pix": None,
        "url": om.get("url"),
        "comparable": True,
    }
    store_rows: list[dict] = []
    missing_rows: list[dict] = []

    for name, store in (result.get("stores") or {}).items():
        meta = STORE_META.get(name, _DEFAULT_STORE_META)
        bm = store.get("best_match")
        if not bm:
            missing_rows.append(
                {
                    "kind": "missing",
                    "store": name,
                    "color": meta["color"],
                    "icon": meta["icon"],
                    "status": store["status"],
                    "status_label": store["status_label"],
                }
            )
            continue
        conc = bm["concorrente"]
        delta = bm["delta"]
        store_rows.append(
            {
                "kind": "store",
                "store": name,
                "color": meta["color"],
                "icon": meta["icon"],
                "nome": conc["nome"],
                "price": conc["preco_final"],
                "pix": conc.get("preco_pix"),
                "url": conc["url"],
                "level": bm["match_level"],
                "outlier": bm["price_outlier"],
                "comparable": not bm["price_outlier"],
                "delta_abs": delta["absolute"],
                "delta_pct": delta["percentage"],
                "cheaper": delta["competitor_is_cheaper"],
            }
        )

    ranked = sorted(
        [om_row] + [r for r in store_rows if r["comparable"]],
        key=lambda r: (r["price"], r["kind"] != "om"),
    )
    for row in ranked:
        # Empates dividem a mesma posição
        row["position"] = 1 + sum(1 for r in ranked if r["price"] < row["price"])
    outliers = [r for r in store_rows if not r["comparable"]]

    max_price = max([r["price"] for r in ranked + outliers] or [1]) or 1
    for row in ranked + outliers:
        row["bar"] = max(4.0, round(row["price"] / max_price * 100, 1))

    competitors = [r for r in ranked if r["kind"] == "store"]
    prices = [r["price"] for r in competitors]
    stats = {
        "found": len(store_rows),
        "total_stores": len(result.get("stores") or {}),
        "min_store": competitors[0]["store"] if competitors else None,
        "max_store": competitors[-1]["store"] if competitors else None,
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "avg": round(sum(prices) / len(prices), 2) if prices else None,
    }
    if stats["avg"]:
        stats["om_vs_avg_pct"] = round((om_price - stats["avg"]) / stats["avg"] * 100, 1)

    verdict = _build_verdict(om_row, competitors, len(ranked))

    return {
        "rows": ranked + outliers + missing_rows,
        "stats": stats,
        "verdict": verdict,
        "has_outliers": bool(outliers),
    }


def _build_verdict(om_row: dict, competitors: list[dict], total_ranked: int) -> dict:
    """Frase principal do resultado: onde a Odonto Master está no mercado."""
    om_price = om_row["price"]
    if not competitors:
        return {
            "tone": "neutral",
            "icon": "bi-search",
            "title": "Nenhuma loja tem um produto equivalente",
            "text": "Confira nos detalhes de cada loja os similares de outras marcas "
            "e todos os produtos encontrados na busca.",
        }

    cheapest = min(competitors, key=lambda r: r["price"])
    cheaper = [r for r in competitors if r["price"] < om_price]

    if not cheaper:
        diff = cheapest["price"] - om_price
        if diff == 0:
            text = f"Empatada com a {cheapest['store']}, que também vende por {_format_brl(om_price)}."
        else:
            pct = diff / om_price * 100 if om_price else 0
            text = (
                f"{_format_brl(diff)} abaixo da {cheapest['store']}, a concorrente mais barata "
                f"({_format_pct(pct, signed=False)} de vantagem)."
            )
        return {
            "tone": "good",
            "icon": "bi-trophy",
            "title": "A Odonto Master tem o menor preço",
            "text": text,
        }

    diff = om_price - cheapest["price"]
    pct = diff / om_price * 100 if om_price else 0
    cheapest_text = (
        f"A {cheapest['store']} vende por {_format_brl(cheapest['price'])}, "
        f"{_format_brl(diff)} a menos ({_format_pct(-pct)})."
    )
    if len(cheaper) == len(competitors):
        return {
            "tone": "bad",
            "icon": "bi-graph-up-arrow",
            "title": "A Odonto Master tem o maior preço",
            "text": f"Todas as lojas comparadas vendem mais barato. {cheapest_text}",
        }

    position = om_row["position"]
    lojas = "loja vende" if len(cheaper) == 1 else "lojas vendem"
    return {
        "tone": "warn",
        "icon": "bi-bar-chart-steps",
        "title": f"A Odonto Master está em {position}º lugar entre {total_ranked}",
        "text": f"{len(cheaper)} {lojas} mais barato. {cheapest_text}",
    }


def _enrich_result(result_dict: dict) -> dict:
    """
    Adiciona campos formatados ao dicionário de resultado para facilitar
    a renderização no template Jinja2.
    """
    # Formata preços da Odonto Master
    om = result_dict.get("odonto_master_product")
    if om:
        om["price_fmt"] = _format_brl(om.get("price"))
        om["list_price_fmt"] = _format_brl(om.get("list_price"))

    # Helper interno para enriquecer um produto concorrente
    def _enrich_produto(prod: dict) -> None:
        prod["preco_final_fmt"] = _format_brl(prod.get("preco_final"))
        prod["preco_pix_fmt"] = (
            _format_brl(prod.get("preco_pix")) if prod.get("preco_pix") else None
        )
        prod["preco_original_fmt"] = _format_brl(prod.get("preco_original"))

    def _enrich_match(match: dict) -> None:
        conc = match.get("concorrente") or match.get("dental_cremer") or {}
        _enrich_produto(conc)
        delta = match.get("delta", {})
        delta["absolute_fmt"] = _format_brl(abs(delta.get("absolute", 0)))

    # Formata todos os matches globais
    for match in result_dict.get("matches", []):
        _enrich_match(match)

    # Formata resultados por loja
    for name, store in result_dict.get("stores", {}).items():
        store["meta"] = STORE_META.get(name, _DEFAULT_STORE_META)
        store["status"], store["status_label"] = _store_status(store)
        for match in store.get("matches", []):
            _enrich_match(match)
        for match in store.get("similares", []):
            _enrich_match(match)
        for prod in store.get("all_produtos", []):
            _enrich_produto(prod)
        # Enriquece best_match da loja
        bm = store.get("best_match")
        if bm:
            _enrich_match(bm)

    if om:
        result_dict["overview"] = _build_overview(result_dict)

    return result_dict


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


def _render_comparison(result: ComparisonResult, query: str):
    if result.service_error:
        return render_template("index.html", query=query, error=result.error), 503
    return render_template(
        "index.html",
        result=_enrich_result(result.to_dict()),
        sku=result.sku_consultado,
        query=query,
    )


def _product_summary(prod) -> dict:
    """Campos de um produto da Odonto Master usados na busca e nas sugestões."""
    data = prod.to_dict()
    data["price_fmt"] = _format_brl(prod.price)
    data["list_price_fmt"] = _format_brl(prod.list_price)
    data["compare_url"] = url_for("compare", sku=prod.sku)
    return data


@app.route("/")
def index():
    """Página inicial com o campo de busca."""
    return render_template("index.html")


@app.route("/compare")
@timed
def compare():
    """
    Busca o produto e exibe a comparação ou a lista de produtos para escolher.

    Query params:
        sku (str): SKU exato do produto na Odonto Master → compara direto.
        q   (str): SKU ou nome do produto (campo de busca da página):
                   - parece um SKU e existe → compara direto;
                   - senão, busca pelo nome: 1 resultado → compara direto,
                     vários → lista para o usuário escolher.
    """
    sku: str = request.args.get("sku", "").strip()
    query: str = request.args.get("q", "").strip()
    use_cache = request.args.get("refresh") != "1"

    if sku:
        logger.info("Comparação — SKU: %s (cache=%s)", sku, use_cache)
        return _render_comparison(_comparator.compare_by_sku(sku, use_cache), query or sku)

    if not query:
        return render_template(
            "index.html",
            error="Digite o SKU ou o nome do produto para comparar.",
        )

    if _looks_like_sku(query):
        logger.info("Busca '%s' parece um SKU — tentando comparação direta.", query)
        result = _comparator.compare_by_sku(query, use_cache)
        if result.found_on_odonto_master or result.service_error:
            return _render_comparison(result, query)

    logger.info("Busca por nome na Odonto Master: '%s'", query)
    try:
        produtos = _comparator.search_odonto_master(query, limit=SEARCH_PAGE_LIMIT)
    except ScraperError as exc:
        return render_template(
            "index.html",
            query=query,
            error=f"Não foi possível consultar a Odonto Master: {exc} Tente novamente.",
        ), 503

    if len(produtos) == 1:
        return redirect(url_for("compare", sku=produtos[0].sku, q=query))

    return render_template(
        "index.html",
        query=query,
        search={
            "query": query,
            "products": [_product_summary(p) for p in produtos],
            "limit": SEARCH_PAGE_LIMIT,
        },
    )


@app.route("/api/search")
@timed
def api_search():
    """
    Produtos da Odonto Master que correspondem ao texto (sugestões da busca).

    Query params:
        q     (str): SKU ou nome (mínimo de 2 caracteres).
        limit (int): máximo de produtos (padrão 6, máx. 24).
    """
    query: str = request.args.get("q", "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", SUGGESTION_LIMIT)), SEARCH_PAGE_LIMIT))
    except ValueError:
        limit = SUGGESTION_LIMIT

    if len(query) < 2:
        return jsonify({"query": query, "products": []})

    try:
        produtos = _comparator.search_odonto_master(query, limit=limit)
    except ScraperError as exc:
        return jsonify({"query": query, "products": [], "error": str(exc)}), 503
    return jsonify({"query": query, "products": [_product_summary(p) for p in produtos]})


@app.route("/api/compare")
@timed
def api_compare():
    """
    Endpoint JSON para integrações externas.

    Query params:
        sku (str): SKU do produto na Odonto Master.

    Returns:
        JSON com o resultado completo da comparação.
    """
    sku: str = request.args.get("sku", "").strip()

    if not sku:
        return jsonify({"error": "Parâmetro 'sku' é obrigatório."}), 400

    logger.info("API — Comparação para SKU: %s", sku)
    result = _comparator.compare_by_sku(sku, request.args.get("refresh") != "1")
    return jsonify(result.to_dict()), (503 if result.service_error else 200)


@app.route("/health")
def health():
    """Health-check simples."""
    return jsonify({"status": "ok", "service": "comparador-precos"})


# ---------------------------------------------------------------------------
# Instalação como app (atalho na área de trabalho)
# ---------------------------------------------------------------------------
# Servidos por rotas próprias (e não pela pasta static/) para funcionar igual
# localmente e na Vercel.

PWA_DIR = os.path.join(app.root_path, "pwa")
BRAND_COLOR = "#0e167a"
BACKGROUND_COLOR = "#f4f6fa"


def _manifest_icon(name: str, size: str, purpose: str = "any") -> dict:
    return {
        "src": url_for("pwa_icon", filename=name),
        "sizes": size,
        "type": "image/png",
        "purpose": purpose,
    }


@app.route("/manifest.webmanifest")
def manifest():
    """Manifesto que permite instalar o comparador como app no Chrome/Edge."""
    response = jsonify({
        "name": "Comparador de Preços — Odonto Master",
        "short_name": "Comparador",
        "description": "Compara os preços da Dental Odonto Master com os concorrentes.",
        "lang": "pt-BR",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": BRAND_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": [
            _manifest_icon("icon-192.png", "192x192"),
            _manifest_icon("icon-512.png", "512x512"),
            _manifest_icon("icon-maskable-512.png", "512x512", "maskable"),
        ],
    })
    response.mimetype = "application/manifest+json"
    return response


@app.route("/sw.js")
def service_worker():
    """Service worker na raiz, para ter escopo sobre o site inteiro."""
    response = send_from_directory(PWA_DIR, "sw.js", mimetype="text/javascript", max_age=0)
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/icons/<path:filename>")
def pwa_icon(filename: str):
    return send_from_directory(os.path.join(PWA_DIR, "icons"), filename, max_age=86400)


@app.route("/atalho.url")
def desktop_shortcut():
    """Atalho de internet do Windows apontando para o comparador."""
    body = f"[InternetShortcut]\r\nURL={request.host_url}\r\n"
    return Response(
        body,
        mimetype="application/internet-shortcut",
        headers={
            "Content-Disposition": "attachment; filename=\"Comparador de Precos.url\"; "
                                   "filename*=UTF-8''Comparador%20de%20Pre%C3%A7os.url",
        },
    )


# ---------------------------------------------------------------------------
# Tratamento de erros
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(e: Any):
    return render_template("index.html", error="Página não encontrada."), 404


@app.errorhandler(500)
def server_error(e: Any):
    logger.exception("Erro interno: %s", e)
    return render_template(
        "index.html",
        error="Erro interno no servidor. Tente novamente.",
    ), 500


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"

    logger.info("Iniciando Comparador de Preços na porta %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
