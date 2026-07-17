"""Red-team meta-test: prove the scope gate is load-bearing.

A green security test is worthless if it would pass even with the guardrail
removed. Here we show the outcome *flips* when the guardrail is loosened: with
auth enabled the consequential write is denied; with auth disabled the identical
call succeeds. So the gate — not something incidental — is what blocks it.
"""

from __future__ import annotations

import pytest

from mcp_health_server.auth import AuthorizationError
from mcp_health_server.server import build_server

_WRITE = {"patient_id": "p-001", "when": "2026-12-01T10:00:00", "reason": "check"}


def test_gate_blocks_when_enabled(monkeypatch, get_fn):
    monkeypatch.setenv("MCP_AUTH_ENABLED", "1")
    mcp = build_server()
    with pytest.raises(AuthorizationError):
        get_fn(mcp, "book_appointment")(**_WRITE)


def test_loosening_gate_flips_outcome(monkeypatch, get_fn):
    # Loosen the guardrail (auth off) — the same call must now go through,
    # demonstrating the gate has teeth.
    monkeypatch.delenv("MCP_AUTH_ENABLED", raising=False)
    mcp = build_server()
    appt = get_fn(mcp, "book_appointment")(**_WRITE)
    assert appt.patient_id == "p-001"
