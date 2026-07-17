"""OAuth 2.1 Resource Server tests: token verification + per-tool scope gating."""

from __future__ import annotations

import pytest

from mcp_health_server.auth import (
    SCOPE_READ,
    SCOPE_WRITE,
    AuthorizationError,
    JWTVerifier,
    MockAuthorizationServer,
    principal,
)
from mcp_health_server.server import build_server

ISSUER = "https://auth.local/mock"
AUDIENCE = "https://health.local/mcp"


@pytest.fixture
def mock_as():
    return MockAuthorizationServer(ISSUER, AUDIENCE)


@pytest.fixture
def verifier(mock_as):
    return JWTVerifier(ISSUER, AUDIENCE, mock_as.public_key_pem())


# --------------------------------------------------------------------------- #
# Token verification — the Resource Server validates, never trusts
# --------------------------------------------------------------------------- #


async def test_valid_token_yields_scopes(mock_as, verifier):
    token = mock_as.issue_token(scopes=[SCOPE_READ, SCOPE_WRITE])
    access = await verifier.verify_token(token)
    assert access is not None
    assert set(access.scopes) == {SCOPE_READ, SCOPE_WRITE}


async def test_wrong_audience_is_rejected(mock_as, verifier):
    # A token minted for another resource must not be replayable here (RFC 8707).
    token = mock_as.issue_token(scopes=[SCOPE_READ], audience="https://evil.example/api")
    assert await verifier.verify_token(token) is None


async def test_expired_token_is_rejected(mock_as, verifier):
    token = mock_as.issue_token(scopes=[SCOPE_READ], ttl_seconds=-10)
    assert await verifier.verify_token(token) is None


async def test_wrong_issuer_is_rejected(mock_as, verifier):
    token = mock_as.issue_token(scopes=[SCOPE_READ], issuer="https://someone-else/")
    assert await verifier.verify_token(token) is None


async def test_garbage_token_is_rejected(verifier):
    assert await verifier.verify_token("not-a-jwt") is None


async def test_token_signed_by_other_key_is_rejected():
    """A token from a different AS (different key) must fail signature check."""
    good_as = MockAuthorizationServer(ISSUER, AUDIENCE)
    attacker = MockAuthorizationServer(ISSUER, AUDIENCE)
    verifier = JWTVerifier(ISSUER, AUDIENCE, good_as.public_key_pem())
    forged = attacker.issue_token(scopes=[SCOPE_READ, SCOPE_WRITE])
    assert await verifier.verify_token(forged) is None


# --------------------------------------------------------------------------- #
# Per-tool scope enforcement (auth enabled)
# --------------------------------------------------------------------------- #


def test_read_tool_allowed_with_read_scope(enable_auth, get_fn):
    mcp = build_server()
    get_patient = get_fn(mcp, "get_patient")
    with principal([SCOPE_READ]):
        assert get_patient(patient_id="p-001").id == "p-001"


def test_read_tool_denied_without_token(enable_auth, get_fn):
    mcp = build_server()
    get_patient = get_fn(mcp, "get_patient")
    with pytest.raises(AuthorizationError):
        get_patient(patient_id="p-001")


def test_write_tool_requires_write_scope(enable_auth, get_fn):
    mcp = build_server()
    book = get_fn(mcp, "book_appointment")
    # A read-only token cannot perform the consequential write.
    with pytest.raises(AuthorizationError), principal([SCOPE_READ]):
        book(patient_id="p-001", when="2026-12-01T10:00:00", reason="x")
    # With the write scope it succeeds.
    with principal([SCOPE_READ, SCOPE_WRITE]):
        appt = book(patient_id="p-001", when="2026-12-01T10:00:00", reason="Follow-up")
        assert appt.patient_id == "p-001"


def test_auth_off_by_default_no_token_needed(get_fn):
    """Without auth enabled (stdio/dev), tools run without any token."""
    mcp = build_server()
    assert get_fn(mcp, "get_patient")(patient_id="p-001").id == "p-001"
