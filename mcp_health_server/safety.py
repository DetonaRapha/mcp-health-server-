"""Primitivas de segurança: configuração, validação de entrada e auditoria com redação de PII.

Este módulo é o diferencial do projeto. Em um domínio regulado você não pode
confiar que o modelo enviará argumentos bem-formados, e não pode deixar dados
identificáveis vazarem para os logs. Então:

* A configuração e qualquer segredo vêm *apenas* de variáveis de ambiente — nunca
  de um schema de tool ou de um payload de resource.
* Cada invocação de tool é envolvida por :func:`audited`, que registra o nome da
  tool, um timestamp, os argumentos e o resultado — com a PII redigida.
* Validadores reutilizáveis (:func:`validate_patient_id`, :func:`validate_date_range`)
  levantam :class:`DomainError`, que as tools traduzem em erros de tool limpos em
  vez de vazar stack traces cruas para o modelo.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Configuração — somente ambiente
# --------------------------------------------------------------------------- #

# Nomes de campos tratados como PII. A correspondência é case-insensitive e por
# substring, então "patient_name", "birthDate", "cpf_number", etc. são todos capturados.
_PII_FIELDS = ("name", "birth_date", "birthdate", "dob", "document", "cpf", "rg", "ssn")

_DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "patients.json"


@dataclass(frozen=True)
class Config:
    """Configuração de runtime, obtida exclusivamente do ambiente."""

    data_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            data_path=Path(os.environ.get("MCP_HEALTH_DATA_PATH", str(_DEFAULT_DATA_PATH))),
            log_level=os.environ.get("MCP_HEALTH_LOG_LEVEL", "INFO").upper(),
        )


def get_config() -> Config:
    """Lê a configuração do ambiente a cada chamada (test-friendly)."""
    return Config.from_env()


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #

AUDIT_LOGGER_NAME = "mcp_health_server.audit"
_audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)


def configure_audit_logging() -> logging.Logger:
    """Anexa um handler de stderr ao audit logger (idempotente).

    O stderr é usado de propósito: o transporte stdio reserva o stdout para o
    canal JSON-RPC, então qualquer coisa impressa no stdout corromperia o protocolo.
    """
    level = get_config().log_level
    _audit_logger.setLevel(level)
    if not _audit_logger.handlers:
        handler = logging.StreamHandler()  # usa stderr por padrão
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        _audit_logger.addHandler(handler)
    _audit_logger.propagate = False
    return _audit_logger


# --------------------------------------------------------------------------- #
# PII redaction
# --------------------------------------------------------------------------- #


def _is_pii_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _PII_FIELDS)


def _mask_name(value: str) -> str:
    """'Rafaela Almeida' -> 'R**** A*****' — mantém as iniciais, esconde o resto."""
    parts = value.split()
    masked = [p[0] + "*" * max(len(p) - 1, 1) if p else p for p in parts]
    return " ".join(masked) if masked else "****"


def _mask_scalar(key: str, value: Any) -> Any:
    """Mascara um único valor folha conhecido como PII pela sua chave."""
    if isinstance(value, str) and ("name" in key.lower()):
        return _mask_name(value)
    if isinstance(value, (date, datetime)):
        return "****-**-**"
    if isinstance(value, str):
        # Datas-como-strings, documentos, etc.: mantém o sinal de comprimento, esconde o conteúdo.
        return "*" * len(value) if value else "****"
    return "****"


def redact(obj: Any, *, _key: str = "") -> Any:
    """Retorna uma cópia profunda de ``obj`` com quaisquer folhas de chave PII mascaradas.

    Lida com modelos Pydantic, dataclasses-como-dicts, dicts simples e sequências.
    Uma chave é considerada PII pelo nome (veja ``_PII_FIELDS``); o valor é
    mascarado independentemente do tipo, então uma data em string rotulada
    incorretamente ainda fica escondida.
    """
    if isinstance(obj, BaseModel):
        return redact(obj.model_dump(mode="python"), _key=_key)
    if isinstance(obj, dict):
        return {
            k: (_mask_scalar(k, v) if _is_pii_key(str(k)) else redact(v, _key=str(k)))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(item, _key=_key) for item in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


# --------------------------------------------------------------------------- #
# Erros de domínio e validadores
# --------------------------------------------------------------------------- #


class DomainError(ValueError):
    """Um erro limpo e seguro para o cliente. As tools expõem sua mensagem; o host a exibe."""


def validate_patient_id(patient_id: Any) -> str:
    """Impõe o formato de id 'p-NNN' antes de qualquer lookup. Nunca confie no chamador."""
    if not isinstance(patient_id, str) or not patient_id.strip():
        raise DomainError("patient_id must be a non-empty string.")
    pid = patient_id.strip()
    prefix, _, digits = pid.partition("-")
    if prefix != "p" or not digits.isdigit():
        raise DomainError(f"Malformed patient_id {pid!r}; expected format 'p-001'.")
    return pid


def validate_date_range(from_date: date | None, to_date: date | None) -> None:
    """Rejeita uma janela [from, to] invertida."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise DomainError(
            f"Invalid date range: from_date ({from_date}) is after to_date ({to_date})."
        )


# --------------------------------------------------------------------------- #
# O decorator de auditoria
# --------------------------------------------------------------------------- #

F = TypeVar("F", bound=Callable[..., Any])


def audited(func: F) -> F:
    """Envolve uma tool para que cada chamada seja logada com argumentos e resultado redigidos.

    Aplicado *abaixo* de ``@mcp.tool`` (ou seja, mais próximo da função) para que
    o FastMCP ainda derive o schema da assinatura original — ``functools.wraps``
    preserva ``__wrapped__``, que ``inspect.signature`` segue.

    Em :class:`DomainError` a falha é logada e relançada para que o host veja uma
    mensagem limpa; em erros inesperados o tipo da exceção é logado (nunca sua
    mensagem, que poderia conter PII) e um erro genérico é levantado.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger = configure_audit_logging()
        redacted_args = _safe_json(redact(dict(kwargs)))
        started = datetime.now().isoformat(timespec="seconds")
        try:
            result = func(*args, **kwargs)
        except DomainError as exc:
            logger.warning(
                "tool=%s ts=%s args=%s outcome=rejected reason=%s",
                func.__name__, started, redacted_args, exc,
            )
            raise
        except Exception as exc:  # noqa: BLE001 — loga apenas o tipo, nunca a mensagem
            logger.error(
                "tool=%s ts=%s args=%s outcome=error error_type=%s",
                func.__name__, started, redacted_args, type(exc).__name__,
            )
            raise DomainError(
                f"{func.__name__} failed due to an internal error ({type(exc).__name__})."
            ) from exc
        logger.info(
            "tool=%s ts=%s args=%s outcome=ok result=%s",
            func.__name__, started, redacted_args, _safe_json(redact(result)),
        )
        return result

    return wrapper  # type: ignore[return-value]
