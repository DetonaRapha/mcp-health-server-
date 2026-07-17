"""Ponto de entrada para ``python -m mcp_health_server``.

O transporte é selecionado por ``MCP_TRANSPORT`` (padrão ``stdio``):

* ``stdio``           — hosts locais (Claude Desktop, MCP Inspector). Sem auth.
* ``streamable-http`` — remoto. Executa como um Resource Server OAuth 2.1.

Para o caminho HTTP em dev/CI, um :class:`MockAuthorizationServer` gera a chave
de assinatura e um bearer token de conveniência (impresso no stderr). Uma
implantação real, em vez disso, validaria contra o JWKS de um issuer real e
nunca usaria o mock.
"""

from __future__ import annotations

import os
import sys

from .auth import (
    SCOPE_READ,
    SCOPE_WRITE,
    AuthConfig,
    JWTVerifier,
    MockAuthorizationServer,
    build_auth_settings,
)
from .server import build_server


def _run_stdio() -> None:
    from .server import mcp  # instância a nível de módulo sem autenticação

    mcp.run(transport="stdio")


def _run_http() -> None:
    # Habilitar auth para o caminho HTTP é explícito; o entrypoint a define para
    # que require_scope seja aplicado mesmo que o operador tenha esquecido a env var.
    os.environ["MCP_AUTH_ENABLED"] = "1"
    cfg = AuthConfig.from_env()

    mock = MockAuthorizationServer(cfg.issuer, cfg.audience)
    verifier = JWTVerifier(cfg.issuer, cfg.audience, mock.public_key_pem())
    settings = build_auth_settings(cfg)

    host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_HTTP_PORT", "8000"))
    stateless = os.environ.get("MCP_HTTP_STATELESS", "").strip().lower() in {"1", "true", "yes"}

    dev_token = mock.issue_token(scopes=[SCOPE_READ, SCOPE_WRITE])
    print(
        f"[dev] Resource Server up on http://{host}:{port}/mcp  audience={cfg.audience}"
        f"  stateless={stateless}\n"
        f"[dev] Bearer token (read+write): {dev_token}",
        file=sys.stderr,
    )

    mcp = build_server(
        token_verifier=verifier,
        auth_settings=settings,
        host=host,
        port=port,
        stateless_http=stateless,
    )
    mcp.run(transport="streamable-http")


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"http", "streamable-http"}:
        _run_http()
    else:
        _run_stdio()


if __name__ == "__main__":
    main()
