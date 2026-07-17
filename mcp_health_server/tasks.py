"""Long-running operations as tasks (handle-based, poll-by-id).

v2 concept, built on the stable SDK. The 2026-07-28 spec adds a native *Tasks*
protocol (``tasks/get``, ``tasks/result``, status ``working``/``completed``/…),
whose types already ship in ``mcp.types`` — but the high-level FastMCP API does
not yet expose task-mode tools. So this implements the same *shape* at the
application level: a start tool returns a task handle + status, and a get tool
polls it. The status strings are the protocol's own constants, so migrating to
native Tasks under SDK v2 is a rename, not a redesign.

Use case: a cohort report over the synthetic population — the kind of aggregate a
host should not block a turn on.
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
    """Test hook: drop all in-flight/known tasks."""
    _tasks.clear()


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def start_cohort_report(condition: str) -> dict:
        """Start a cohort report over synthetic patients matching a condition.

        Returns a task handle to poll with ``get_cohort_report`` — models the
        Tasks pattern for a long-running aggregate.
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
        """Poll a cohort-report task. Status is one of the protocol's task states."""
        future = _tasks.get(task_id)
        if future is None:
            raise DomainError(f"Unknown task_id {task_id!r}.")
        if not future.done():
            return {"task_id": task_id, "status": TASK_STATUS_WORKING}
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 — surface a clean, typed failure
            return {"task_id": task_id, "status": TASK_STATUS_FAILED, "error_type": type(exc).__name__}
        return {"task_id": task_id, "status": TASK_STATUS_COMPLETED, "result": result}
