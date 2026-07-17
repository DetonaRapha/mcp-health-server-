"""Safety primitives: configuration, input validation, and PII-redacting audit.

This module is the project's differentiator. In a regulated domain you cannot
trust the model to send well-formed arguments, and you cannot let identifying
data leak into logs. So:

* Configuration and any secret come *only* from environment variables — never
  from a tool schema or a resource payload.
* Every tool invocation is wrapped by :func:`audited`, which records the tool
  name, a timestamp, the arguments, and the result — with PII redacted.
* Reusable validators (:func:`validate_patient_id`, :func:`validate_date_range`)
  raise :class:`DomainError`, which tools translate into clean tool errors
  instead of leaking raw stack traces to the model.
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
# Configuration — environment only
# --------------------------------------------------------------------------- #

# Field names treated as PII. Matching is case-insensitive and substring-based,
# so "patient_name", "birthDate", "cpf_number", etc. are all caught.
_PII_FIELDS = ("name", "birth_date", "birthdate", "dob", "document", "cpf", "rg", "ssn")

_DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "patients.json"


@dataclass(frozen=True)
class Config:
    """Runtime configuration, sourced exclusively from the environment."""

    data_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            data_path=Path(os.environ.get("MCP_HEALTH_DATA_PATH", str(_DEFAULT_DATA_PATH))),
            log_level=os.environ.get("MCP_HEALTH_LOG_LEVEL", "INFO").upper(),
        )


def get_config() -> Config:
    """Read configuration from the environment on each call (test-friendly)."""
    return Config.from_env()


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #

AUDIT_LOGGER_NAME = "mcp_health_server.audit"
_audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)


def configure_audit_logging() -> logging.Logger:
    """Attach a stderr handler to the audit logger (idempotent).

    stderr is used on purpose: stdio transport reserves stdout for the JSON-RPC
    channel, so anything printed to stdout would corrupt the protocol.
    """
    level = get_config().log_level
    _audit_logger.setLevel(level)
    if not _audit_logger.handlers:
        handler = logging.StreamHandler()  # defaults to stderr
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
    """'Rafaela Almeida' -> 'R**** A*****' — keeps initials, hides the rest."""
    parts = value.split()
    masked = [p[0] + "*" * max(len(p) - 1, 1) if p else p for p in parts]
    return " ".join(masked) if masked else "****"


def _mask_scalar(key: str, value: Any) -> Any:
    """Mask a single leaf value known to be PII by its key."""
    if isinstance(value, str) and ("name" in key.lower()):
        return _mask_name(value)
    if isinstance(value, (date, datetime)):
        return "****-**-**"
    if isinstance(value, str):
        # Dates-as-strings, documents, etc.: keep length signal, hide content.
        return "*" * len(value) if value else "****"
    return "****"


def redact(obj: Any, *, _key: str = "") -> Any:
    """Return a deep copy of ``obj`` with any PII-keyed leaves masked.

    Handles Pydantic models, dataclasses-as-dicts, plain dicts, and sequences.
    A key is considered PII by name (see ``_PII_FIELDS``); the value is masked
    regardless of type so a mislabelled string date is still hidden.
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
# Domain errors & validators
# --------------------------------------------------------------------------- #


class DomainError(ValueError):
    """A clean, client-safe error. Tools surface its message; the host shows it."""


def validate_patient_id(patient_id: Any) -> str:
    """Enforce the 'p-NNN' id shape before any lookup. Never trust the caller."""
    if not isinstance(patient_id, str) or not patient_id.strip():
        raise DomainError("patient_id must be a non-empty string.")
    pid = patient_id.strip()
    prefix, _, digits = pid.partition("-")
    if prefix != "p" or not digits.isdigit():
        raise DomainError(f"Malformed patient_id {pid!r}; expected format 'p-001'.")
    return pid


def validate_date_range(from_date: date | None, to_date: date | None) -> None:
    """Reject an inverted [from, to] window."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise DomainError(
            f"Invalid date range: from_date ({from_date}) is after to_date ({to_date})."
        )


# --------------------------------------------------------------------------- #
# The audit decorator
# --------------------------------------------------------------------------- #

F = TypeVar("F", bound=Callable[..., Any])


def audited(func: F) -> F:
    """Wrap a tool so every call is logged with arguments and result redacted.

    Applied *beneath* ``@mcp.tool`` (i.e. closest to the function) so FastMCP
    still derives the schema from the original signature — ``functools.wraps``
    preserves ``__wrapped__``, which ``inspect.signature`` follows.

    On :class:`DomainError` the failure is logged and re-raised so the host sees
    a clean message; on unexpected errors the exception type is logged (never
    its message, which could contain PII) and a generic error is raised.
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
        except Exception as exc:  # noqa: BLE001 — log type only, never the message
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
