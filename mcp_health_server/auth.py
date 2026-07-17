"""Suporte a Resource Server OAuth 2.1 + aplicação de scope por tool.

Conforme a spec de autorização do MCP, um servidor MCP é um *Resource Server*:
ele não autentica usuários nem gera tokens — ele **valida** bearer tokens
(assinatura, ``exp``, ``iss`` e, crucialmente, ``aud`` para prevenir replay de
token entre resources, RFC 8707) e **aplica scopes** por tool.

Regras de design (da spec e de todo guia de segurança MCP):
* Não implemente crypto ou parsing de token à mão — use uma biblioteca testada
  (PyJWT aqui).
* A validação de token é local (verifica a assinatura RS256 contra a chave
  pública do issuer). Em produção, a chave vem do endpoint JWKS do issuer, em
  cache; aqui um :class:`MockAuthorizationServer` a fornece somente para dev/CI.
* Config e chaves vêm do ambiente / do AS, nunca de um schema de tool.

Auth está *desligada por padrão* para que stdio/dev continue funcionando; ela é
habilitada explicitamente (``MCP_AUTH_ENABLED=1``), o que o entrypoint HTTP faz.
Quando habilitada, :func:`require_scope` protege cada tool.
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

# Scopes. Leituras precisam de SCOPE_READ; a escrita consequente precisa de SCOPE_WRITE.
SCOPE_READ = "patients:read"
SCOPE_WRITE = "appointments:write"


class AuthorizationError(DomainError):
    """Falha de authn/authz — exposta ao host como um erro limpo e auditada.

    Subclasse de :class:`DomainError` para que o wrapper ``@audited`` a registre
    como uma rejeição (nunca vazando o conteúdo do token) e a relance de forma limpa.
    """


# --------------------------------------------------------------------------- #
# Configuração
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
# Mock Authorization Server — SOMENTE DEV / CI
# --------------------------------------------------------------------------- #


class MockAuthorizationServer:
    """Emite tokens RS256 e expõe a chave pública de verificação.

    SOMENTE DEV/TEST. Uma implantação real não usa isto — ela aponta o verifier
    para o JWKS de um Authorization Server real (Auth0, Keycloak, WorkOS, Entra, ...).
    Isto existe para que o caminho de auth seja exercitado no CI sem serviços
    externos e sem commitar nenhum segredo.
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
# Token verifier — o trabalho do Resource Server
# --------------------------------------------------------------------------- #


class JWTVerifier(TokenVerifier):
    """Valida bearer tokens RS256 localmente contra a chave pública do issuer."""

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
                audience=self._audience,   # RFC 8707: rejeita tokens gerados para outro resource
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
    """AuthSettings para o modo Resource Server.

    ``required_scopes=[]`` significa "um token válido é obrigatório, mas a
    autorização é decidida por tool" — a separação de responsabilidades que a
    spec exige.
    """
    return AuthSettings(
        issuer_url=AnyHttpUrl(cfg.issuer),
        resource_server_url=AnyHttpUrl(cfg.resource_url),
        required_scopes=[],
    )


# --------------------------------------------------------------------------- #
# Aplicação de scope por tool
# --------------------------------------------------------------------------- #

F = TypeVar("F", bound=Callable[..., Any])


def require_scope(scope: str) -> Callable[[F], F]:
    """Protege uma tool com um scope OAuth obrigatório.

    No-op quando a auth está desabilitada (stdio/dev). Quando habilitada, um token
    ausente ou um token sem ``scope`` levanta :class:`AuthorizationError`, que
    ``@audited`` registra como uma rejeição. Aplicado abaixo de ``@audited`` para
    que a negação seja auditada.
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
    """Helper de teste: executa o bloco como um principal autenticado com ``scopes``.

    Define a auth context var do SDK (a mesma que o middleware HTTP popula), para
    que :func:`require_scope` veja o token sem um transporte real.
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
