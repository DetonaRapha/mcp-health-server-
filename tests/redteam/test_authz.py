"""Red-team: authorization escalation.

Least privilege in both directions — no token does nothing; a read token cannot
write; a write-only token cannot read. Any escalation that succeeds fails the build.
"""

from __future__ import annotations

import pytest

from mcp_health_server.auth import SCOPE_READ, SCOPE_WRITE, AuthorizationError, principal
from mcp_health_server.server import build_server

READ_CALLS = {
    "search_patients": {"query": "x"},
    "get_patient": {"patient_id": "p-001"},
    "list_appointments": {"patient_id": "p-001"},
}
WRITE_CALL = ("book_appointment", {"patient_id": "p-001", "when": "2026-12-01T10:00:00", "reason": "x"})


def test_no_token_denies_every_read(enable_auth, get_fn):
    mcp = build_server()
    for name, kwargs in READ_CALLS.items():
        with pytest.raises(AuthorizationError):
            get_fn(mcp, name)(**kwargs)


def test_no_token_denies_write(enable_auth, get_fn):
    mcp = build_server()
    name, kwargs = WRITE_CALL
    with pytest.raises(AuthorizationError):
        get_fn(mcp, name)(**kwargs)


def test_read_token_cannot_write(enable_auth, get_fn):
    mcp = build_server()
    name, kwargs = WRITE_CALL
    with pytest.raises(AuthorizationError), principal([SCOPE_READ]):
        get_fn(mcp, name)(**kwargs)


def test_write_only_token_cannot_read(enable_auth, get_fn):
    """A token scoped only for writes must not be able to read patient data."""
    mcp = build_server()
    with pytest.raises(AuthorizationError), principal([SCOPE_WRITE]):
        get_fn(mcp, "get_patient")(patient_id="p-001")
