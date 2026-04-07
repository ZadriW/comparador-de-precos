"""
Aplicativo Flask — Comparador de Preços
Dental Odonto Master vs Dental Cremer, Dental Speed, Dental Shop e Surya Dental

Rotas:
  GET  /                     → Página inicial com formulário de busca por SKU
  GET  /compare?sku=<SKU>    → Página de resultados HTML
  GET  /api/compare?sku=<SKU>→ Resultado em JSON (para integrações)
  GET  /health               → Health-check
"""

from __future__ import annotations

import logging
import os
import time
from functools import wraps
from typing import Any

from flask import Flask, jsonify, render_template, request

from comparator import PriceComparator

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
app.config["JSON_AS_ASCII"] = False  # Preserva caracteres acentuados no JSON
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True

# Instância única do comparador (reutiliza as sessões HTTP)
_comparator = PriceComparator(relevance_threshold=0.35, max_results=20)


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
    for store in result_dict.get("stores", {}).values():
        for match in store.get("matches", []):
            _enrich_match(match)
        for prod in store.get("all_produtos", []):
            _enrich_produto(prod)
        # Enriquece best_match da loja
        bm = store.get("best_match")
        if bm:
            _enrich_match(bm)

    return result_dict


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """Página inicial com formulário de busca por SKU."""
    return render_template("index.html")


@app.route("/compare")
@timed
def compare():
    """
    Executa a comparação e exibe o resultado em HTML.

    Query params:
        sku (str): SKU do produto na Odonto Master.
    """
    sku: str = request.args.get("sku", "").strip()

    if not sku:
        return render_template(
            "index.html",
            error="Por favor, informe um SKU para comparar.",
        )

    logger.info("Requisição de comparação — SKU: %s", sku)

    result = _comparator.compare_by_sku(sku)
    result_dict = _enrich_result(result.to_dict())

    return render_template(
        "index.html",
        result=result_dict,
        sku=sku,
    )


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
    result = _comparator.compare_by_sku(sku)
    return jsonify(result.to_dict())


@app.route("/health")
def health():
    """Health-check simples."""
    return jsonify({"status": "ok", "service": "comparador-precos"})


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
