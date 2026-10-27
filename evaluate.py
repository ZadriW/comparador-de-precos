"""
Avaliação da busca automática
=============================

Usa as confirmações manuais (``data/curadoria.json``) como gabarito: para
cada SKU com produto confirmado em alguma loja, roda a comparação **sem** a
curadoria e verifica se a "Melhor opção" automática é o produto confirmado.

Resultados por loja:
  acerto     — a melhor opção automática é o produto confirmado
  erro       — a melhor opção é outro produto (ou um que você rejeitou)
  sem par    — a busca automática não escolheu nenhum produto
  fora       — o produto confirmado nem apareceu na busca (problema nos termos)

Uso::

    python evaluate.py                     # todos os SKUs confirmados
    python evaluate.py 143026 140748       # só estes SKUs
    python evaluate.py --min 0.9           # código de saída 1 se acertos < 90%
    python evaluate.py --arquivo outro.json

Rode depois de mudar regras em matching.py para ver se a taxa de acerto
subiu ou caiu. Quanto mais SKUs confirmados, mais confiável a medida.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict

from comparator import PriceComparator
from curation import DEFAULT_PATH, CurationStore, same_product

OUTCOMES = ("acerto", "erro", "sem par", "fora")


def _desc(prod: dict) -> str:
    """'Nome' (SKU x, R$ y): nomes iguais são comuns entre variantes."""
    extra = [f"SKU {prod['sku']}"] if prod.get("sku") else []
    if prod.get("preco_final"):
        extra.append(f"R$ {prod['preco_final']:.2f}")
    return f"'{prod.get('nome')}'" + (f" ({', '.join(extra)})" if extra else "")


def evaluate(skus: list[str], store: CurationStore) -> tuple[dict, list[str]]:
    """Retorna (contagem por loja e resultado, linhas de detalhe das falhas)."""
    comparator = PriceComparator(curation=None)
    counts: dict[str, Counter] = defaultdict(Counter)
    failures: list[str] = []
    gabarito = store.all()

    for sku in skus:
        decisions = {
            loja: d for loja, d in gabarito.get(sku, {}).items() if d.get("confirmado")
        }
        if not decisions:
            continue
        result = comparator.compare_by_sku(sku)
        if not result.found_on_odonto_master:
            failures.append(f"[{sku}] {result.error}")
            continue
        print(f"· {sku}  {result.odonto_master_product.name}")

        for loja, decision in decisions.items():
            confirmed = decision["confirmado"]
            sr = result.stores.get(loja)
            if sr is None or sr.erro:
                failures.append(f"[{sku}] {loja}: loja indisponível ({sr.erro if sr else '-'})")
                continue
            best = sr.best_match
            if best and same_product(confirmed, best.concorrente.to_dict()):
                outcome = "acerto"
            elif best:
                outcome = "erro"
                rejected = any(
                    same_product(r, best.concorrente.to_dict())
                    for r in decision.get("rejeitados") or []
                )
                failures.append(
                    f"[{sku}] {loja}: escolheu {'um REJEITADO ' if rejected else ''}"
                    f"{_desc(best.concorrente.to_dict())} [{best.match_level} {best.relevance_score:.0%}]"
                    f" — confirmado: {_desc(confirmed)}"
                )
            elif any(same_product(confirmed, p.to_dict()) for p in sr.all_produtos):
                outcome = "sem par"
                failures.append(
                    f"[{sku}] {loja}: nenhum par escolhido; o confirmado "
                    f"'{confirmed.get('nome')}' veio na busca com nota baixa"
                )
            else:
                outcome = "fora"
                failures.append(
                    f"[{sku}] {loja}: '{confirmed.get('nome')}' não apareceu nos termos "
                    f"{sr.termos_usados}"
                )
            counts[loja][outcome] += 1

    return counts, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Mede a busca automática contra as confirmações manuais.")
    parser.add_argument("skus", nargs="*", help="SKUs da Odonto Master (padrão: todos os confirmados)")
    parser.add_argument("--arquivo", default=DEFAULT_PATH, help="arquivo de curadoria (JSON)")
    parser.add_argument("--min", type=float, default=None, help="taxa mínima de acerto (0–1)")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra os logs das buscas")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    store = CurationStore(args.arquivo)
    skus = args.skus or sorted(
        sku for sku, lojas in store.all().items() if any(d.get("confirmado") for d in lojas.values())
    )
    if not skus:
        print(
            "Nenhum produto confirmado ainda. Abra comparações no site e use "
            "\"É este\" / \"Não é\" para montar o gabarito."
        )
        return 0

    print(f"Avaliando {len(skus)} SKU(s)...\n")
    counts, failures = evaluate(skus, store)

    total = Counter()
    print(f"\n{'Loja':<15}" + "".join(f"{o:>10}" for o in OUTCOMES) + f"{'taxa':>9}")
    for loja in PriceComparator.STORE_NAMES:
        c = counts.get(loja)
        if not c:
            continue
        total.update(c)
        n = sum(c.values())
        print(f"{loja:<15}" + "".join(f"{c[o]:>10}" for o in OUTCOMES) + f"{c['acerto'] / n:>9.0%}")
    n_total = sum(total.values())
    rate = total["acerto"] / n_total if n_total else 0.0
    print(f"{'TOTAL':<15}" + "".join(f"{total[o]:>10}" for o in OUTCOMES) + f"{rate:>9.0%}")

    if failures:
        print("\nFalhas:")
        for line in failures:
            print(f"  - {line}")

    if args.min is not None and n_total and rate < args.min:
        print(f"\nTaxa de acerto {rate:.0%} abaixo do mínimo {args.min:.0%}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
