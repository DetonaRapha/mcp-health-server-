"""Operações de longa duração como tasks (baseadas em handle, poll-by-id).

Conceito v2, construído sobre o SDK estável. A spec 2026-07-28 adiciona um
protocolo *Tasks* nativo (``tasks/get``, ``tasks/result``, status
``working``/``completed``/…), cujos tipos já vêm em ``mcp.types`` — mas a API de
alto nível do FastMCP ainda não expõe tools em modo task. Então isto implementa o
mesmo *formato* a nível de aplicação: uma tool de start retorna um handle de task
+ status, e uma tool de get faz o poll dela. As strings de status são as próprias
constantes do protocolo, então migrar para os Tasks nativos no SDK v2 é uma
renomeação, não um redesign.

Caso de uso: um relatório de coorte sobre a população sintética — o tipo de
agregação em que um host não deveria bloquear um turno.
"""

from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date

from mcp.server.fastmcp import FastMCP
from mcp.types import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_WORKING,
    ToolAnnotations,
)

from . import data
from .auth import SCOPE_READ, require_scope
from .safety import DomainError, audited
from .telemetry import traced

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-task")
_tasks: dict[str, Future] = {}


def _run_cohort_report(condition: str) -> dict:
    summaries = data.search_patients(condition, today=date.today())
    ages = [s.age for s in summaries]
    return {
        "condition": condition,
        "count": len(summaries),
        "patients": [{"id": s.id, "age": s.age} for s in summaries],
        "average_age": round(sum(ages) / len(ages), 1) if ages else None,
    }


def reset() -> None:
    """Test hook: descarta todas as tasks em andamento/conhecidas."""
    _tasks.clear()


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def start_cohort_report(condition: str) -> dict:
        """Inicia um relatório de coorte sobre pacientes sintéticos que correspondem a uma condição.

        Retorna um handle de task para fazer poll com ``get_cohort_report`` —
        modela o padrão Tasks para uma agregação de longa duração.
        """
        if not isinstance(condition, str) or not condition.strip():
            raise DomainError("condition must be a non-empty string.")
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        _tasks[task_id] = _executor.submit(_run_cohort_report, condition.strip())
        return {"task_id": task_id, "status": TASK_STATUS_WORKING}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def get_cohort_report(task_id: str) -> dict:
        """Faz poll de uma task de relatório de coorte. O status é um dos estados de task do protocolo."""
        future = _tasks.get(task_id)
        if future is None:
            raise DomainError(f"Unknown task_id {task_id!r}.")
        if not future.done():
            return {"task_id": task_id, "status": TASK_STATUS_WORKING}
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 — expõe uma falha limpa e tipada
            return {"task_id": task_id, "status": TASK_STATUS_FAILED, "error_type": type(exc).__name__}
        return {"task_id": task_id, "status": TASK_STATUS_COMPLETED, "result": result}
