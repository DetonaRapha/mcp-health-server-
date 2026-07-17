"""Entry point for ``python -m mcp_health_server``.

Transport is selected by ``MCP_TRANSPORT`` (default ``stdio``):

* ``stdio``           — local hosts (Claude Desktop, MCP Inspector). No auth.
* ``streamable-http`` — remote. Runs as an OAuth 2.1 Resource Server.

For the HTTP path in dev/CI, a :class:`MockAuthorizationServer` mints the signing
key and a convenience bearer token (printed to stderr). A real deployment would
instead verify against a real issuer's JWKS and never use the mock.
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
    from .server import mcp  # unauthenticated module-level instance

    mcp.run(transport="stdio")


def _run_http() -> None:
    # Enabling auth for the HTTP path is explicit; the entrypoint sets it so
    # require_scope enforces even if the operator forgot the env var.
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
