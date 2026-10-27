"""
Elementos compartilhados entre os scrapers.
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Respostas temporárias que valem nova tentativa (limite de taxa, instabilidade)
RETRY_STATUS = (429, 500, 502, 503, 504)


def html_to_text(raw: object) -> str:
    """Texto puro de um trecho HTML: sem tags, entidades decodificadas e espaços colapsados."""
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class ScraperError(Exception):
    """
    Falha ao consultar uma loja (rede, HTTP, bloqueio, resposta inválida).

    Os scrapers levantam esta exceção em vez de devolver uma lista vazia,
    para que o comparador consiga diferenciar "loja indisponível" de
    "nenhum produto encontrado".
    """


def retrying_session(retries: int = 2, backoff: float = 0.5) -> requests.Session:
    """
    Sessão ``requests`` que repete a chamada em falha de conexão, timeout de
    leitura (uma vez) e respostas 429/5xx, com espera crescente
    (0,5 s, 1 s…). Vale também para POST: as APIs usadas só fazem leitura.
    """
    retry = Retry(
        total=retries,
        connect=retries,
        read=1,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=RETRY_STATUS,
        allowed_methods=None,
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def call_with_retries(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    backoff: float = 0.5,
    label: str = "",
) -> Any:
    """
    Executa ``fn`` (uma chamada HTTP que devolve a resposta) até ``attempts``
    vezes, repetindo em exceção ou status 429/5xx. Usado com o curl-cffi, que
    não tem o mecanismo de retry do ``requests``. Na última tentativa, a
    exceção é propagada e a resposta é devolvida como veio.
    """
    for attempt in range(1, attempts + 1):
        try:
            resp = fn()
        except Exception as exc:  # pylint: disable=broad-except
            if attempt == attempts:
                raise
            logger.warning("%s tentativa %d falhou: %s", label, attempt, exc)
        else:
            status = getattr(resp, "status_code", 200)
            if status not in RETRY_STATUS or attempt == attempts:
                return resp
            logger.warning("%s tentativa %d: HTTP %s", label, attempt, status)
        time.sleep(backoff * 2 ** (attempt - 1))
    raise AssertionError("inalcançável")
