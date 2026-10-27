"""
Curadoria manual das correspondências
=====================================

Guarda, por SKU da Odonto Master e por loja, o produto confirmado ("é este")
e os rejeitados ("não é este"). O comparador aplica essas decisões antes da
pontuação automática, e ``evaluate.py`` usa as confirmações como gabarito
para medir a taxa de acerto da busca automática.

Arquivo ``data/curadoria.json``::

    {
      "143026": {
        "Dental Cremer": {
          "confirmado": {"sku": "56260", "url": "...", "nome": "...", "em": "2026-10-27T13:00:00"},
          "rejeitados": [{"sku": "337077", "url": "...", "nome": "...", "em": "..."}]
        }
      }
    }

Um produto é identificado pelo SKU da loja; sem SKU, pela URL sem a query
string. Só usa a biblioteca padrão.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "curadoria.json")

ACTIONS = ("confirmar", "rejeitar", "limpar")


def product_ref(prod: dict[str, Any]) -> dict[str, str]:
    """Campos guardados de um produto concorrente (aceita o to_dict() do scraper)."""
    return {
        "sku": str(prod.get("sku") or "").strip(),
        "url": str(prod.get("url") or "").strip(),
        "nome": str(prod.get("nome") or "").strip(),
    }


def same_product(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Mesmo produto da loja: SKU igual ou, sem SKU, URL igual (sem query string)."""
    sku_a, sku_b = str(a.get("sku") or "").strip(), str(b.get("sku") or "").strip()
    if sku_a and sku_b:
        return sku_a == sku_b

    def _path(url: Any) -> str:
        parts = urlsplit(str(url or "").strip())
        return f"{parts.netloc}{parts.path}".rstrip("/").lower()

    return bool(_path(a.get("url"))) and _path(a.get("url")) == _path(b.get("url"))


class CurationStore:
    """Decisões manuais persistidas em JSON (seguro para várias threads)."""

    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict]] = self._load()

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def for_sku(self, om_sku: str) -> dict[str, dict]:
        """Decisões de um SKU da Odonto Master, por loja (cópia)."""
        with self._lock:
            return copy.deepcopy(self._data.get(str(om_sku).strip(), {}))

    def all(self) -> dict[str, dict[str, dict]]:
        with self._lock:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def apply(
        self, om_sku: str, store: str, action: str, prod: Optional[dict] = None
    ) -> dict:
        """
        Registra uma decisão e devolve o estado da loja para o SKU.

        - ``confirmar``: o produto passa a ser o par do SKU nesta loja
          (substitui a confirmação anterior e sai dos rejeitados);
        - ``rejeitar``: o produto nunca mais é usado como par do SKU;
        - ``limpar``: apaga as decisões do SKU nesta loja.
        """
        om_sku, store = str(om_sku).strip(), str(store).strip()
        if not om_sku or not store:
            raise ValueError("Informe o SKU da Odonto Master e a loja.")
        if action not in ACTIONS:
            raise ValueError(f"Ação inválida: {action!r} (use {', '.join(ACTIONS)}).")

        ref = None
        if action != "limpar":
            ref = product_ref(prod or {})
            if not ref["sku"] and not ref["url"]:
                raise ValueError("O produto precisa ter SKU ou URL.")
            ref["em"] = datetime.now().isoformat(timespec="seconds")

        with self._lock:
            stores = self._data.setdefault(om_sku, {})
            entry = stores.setdefault(store, {"confirmado": None, "rejeitados": []})
            rejected = [r for r in entry.get("rejeitados") or [] if not (ref and same_product(r, ref))]

            if action == "confirmar":
                entry["confirmado"] = ref
            elif action == "rejeitar":
                if entry.get("confirmado") and same_product(entry["confirmado"], ref):
                    entry["confirmado"] = None
                rejected.append(ref)
            else:
                stores.pop(store, None)
                if not stores:
                    self._data.pop(om_sku, None)
                self._save()
                return {"confirmado": None, "rejeitados": []}

            entry["rejeitados"] = rejected
            self._save()
            logger.info("Curadoria: %s / %s → %s %s", om_sku, store, action, ref.get("sku") or ref.get("url"))
            return copy.deepcopy(entry)

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("o conteúdo não é um objeto JSON")
            return data
        except (OSError, ValueError) as exc:
            # Nunca sobrescreve um arquivo ilegível: guarda uma cópia e recomeça.
            backup = f"{self.path}.corrompido-{datetime.now():%Y%m%d-%H%M%S}"
            try:
                os.replace(self.path, backup)
            except OSError:
                backup = "(não foi possível copiar)"
            logger.error("Curadoria ilegível (%s); cópia salva em %s.", exc, backup)
            return {}

    def _save(self) -> None:
        """Grava em arquivo temporário e troca, para não corromper em caso de falha."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)
