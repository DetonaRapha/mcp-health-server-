"""OAuth 2.1 Resource Server support + per-tool scope enforcement.

Per the MCP authorization spec, an MCP server is a *Resource Server*: it does not
authenticate users or mint tokens — it **validates** bearer tokens (signature,
``exp``, ``iss``, and crucially ``aud`` to prevent token replay across resources,
RFC 8707) and **enforces scopes** per tool.

Design rules (from the spec, and from every MCP security guide):
* Do not hand-roll crypto or token parsing — use a tested library (PyJWT here).
* Token validation is local (verify the RS256 signature against the issuer's
  public key). In production, the key comes from the issuer's JWKS endpoint,
  cached; here a :class:`MockAuthorizationServer` provides it for dev/CI only.
* Config and keys come from the environment / the AS, never from a tool schema.

Auth is *off by default* so stdio/dev keeps working; it is enabled explicitly
(``MCP_AUTH_ENABLED=1``), which the HTTP entrypoint does. When enabled,
:func:`require_scope` gates each tool.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.middleware.auth_context import (
    AuthenticatedUser,
    auth_context_var,
    get_access_token,
)
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from .safety import DomainError

ALGORITHM = "RS256"

# Scopes. Reads need SCOPE_READ; the consequential write needs SCOPE_WRITE.
SCOPE_READ = "patients:read"
SCOPE_WRITE = "appointments:write"


class AuthorizationError(DomainError):
    """Authn/authz failure — surfaced to the host as a clean error and audited.

    Subclasses :class:`DomainError` so the ``@audited`` wrapper logs it as a
    rejection (never leaking token contents) and re-raises it cleanly.
    """


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    issuer: str
    audience: str
    resource_url: str

    @classmethod
    def from_env(cls) -> "AuthConfig":
        issuer = os.environ.get("MCP_AUTH_ISSUER", "https://auth.local/mock")
        audience = os.environ.get("MCP_AUTH_AUDIENCE", "https://health.local/mcp")
        resource = os.environ.get("MCP_AUTH_RESOURCE_URL", audience)
        enabled = os.environ.get("MCP_AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        return cls(enabled=enabled, issuer=issuer, audience=audience, resource_url=resource)


def auth_config() -> AuthConfig:
    return AuthConfig.from_env()


def auth_enabled() -> bool:
    return auth_config().enabled


# --------------------------------------------------------------------------- #
# Mock Authorization Server — DEV / CI ONLY
# --------------------------------------------------------------------------- #


class MockAuthorizationServer:
    """Issues RS256 tokens and exposes the verifying public key.

    DEV/TEST ONLY. A real deployment does not use this — it points the verifier
    at a real Authorization Server's JWKS (Auth0, Keycloak, WorkOS, Entra, ...).
    This exists so the auth path is exercised in CI without external services and
    without committing any secret.
    """

    def __init__(self, issuer: str, audience: str) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.issuer = issuer
        self.audience = audience

    def _private_pem(self) -> bytes:
        return self._key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_key_pem(self) -> bytes:
        return self._key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def issue_token(
        self,
        *,
        subject: str = "clinician-1",
        scopes: Sequence[str] = (SCOPE_READ,),
        audience: str | None = None,
        issuer: str | None = None,
        ttl_seconds: int = 3600,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer or self.issuer,
            "aud": audience or self.audience,
            "sub": subject,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + ttl_seconds,
        }
        return jwt.encode(claims, self._private_pem(), algorithm=ALGORITHM)


# --------------------------------------------------------------------------- #
# Token verifier — the Resource Server's job
# --------------------------------------------------------------------------- #


class JWTVerifier(TokenVerifier):
    """Validates RS256 bearer tokens locally against the issuer's public key."""

    def __init__(self, issuer: str, audience: str, public_key_pem: bytes) -> None:
        self._issuer = issuer
        self._audience = audience
        self._public_key = public_key_pem

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._public_key,
                algorithms=[ALGORITHM],
                audience=self._audience,   # RFC 8707: reject tokens minted for another resource
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            return None
        scopes = str(claims.get("scope", "")).split()
        return AccessToken(
            token=token,
            client_id=str(claims.get("sub", "")),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=self._audience,
            subject=claims.get("sub"),
            claims=claims,
        )


def build_auth_settings(cfg: AuthConfig) -> AuthSettings:
    """AuthSettings for Resource Server mode.

    ``required_scopes=[]`` means "a valid token is required, but authorization is
    decided per tool" — the separation of concerns the spec calls for.
    """
    return AuthSettings(
        issuer_url=AnyHttpUrl(cfg.issuer),
        resource_server_url=AnyHttpUrl(cfg.resource_url),
        required_scopes=[],
    )


# --------------------------------------------------------------------------- #
# Per-tool scope enforcement
# --------------------------------------------------------------------------- #

F = TypeVar("F", bound=Callable[..., Any])


def require_scope(scope: str) -> Callable[[F], F]:
    """Gate a tool on a required OAuth scope.

    No-op when auth is disabled (stdio/dev). When enabled, a missing token or a
    token lacking ``scope`` raises :class:`AuthorizationError`, which ``@audited``
    logs as a rejection. Applied beneath ``@audited`` so the denial is audited.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if auth_enabled():
                token = get_access_token()
                if token is None:
                    raise AuthorizationError("Authentication required: no valid access token.")
                if scope not in (token.scopes or []):
                    raise AuthorizationError(
                        f"Access denied: token is missing required scope '{scope}'."
                    )
            return func(*args, **kwargs)

        wrapper.__required_scope__ = scope  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


@contextlib.contextmanager
def principal(scopes: Sequence[str], *, subject: str = "test-user") -> Iterator[None]:
    """Test helper: run the block as an authenticated principal with ``scopes``.

    Sets the SDK's auth context var (the same one the HTTP middleware populates),
    so :func:`require_scope` sees the token without a real transport.
    """
    token = AccessToken(
        token="in-memory-test-token",
        client_id=subject,
        scopes=list(scopes),
        subject=subject,
        claims={},
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)
