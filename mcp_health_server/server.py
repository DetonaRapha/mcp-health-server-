"""FastMCP server instance and primitive registration.

``build_server`` wires the tools, resource, and prompt onto a fresh FastMCP
instance. It optionally accepts a token verifier + auth settings so the HTTP
entrypoint can run as an OAuth 2.1 Resource Server; without them (the stdio/dev
default) the server runs unauthenticated and ``require_scope`` is a no-op.

Transport: stdio by default; Streamable HTTP (with auth) via ``__main__``.
"""

from __future__ import annotations

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from . import prompts, resources, tools
from .safety import configure_audit_logging
from .telemetry import configure_tracing

INSTRUCTIONS = (
    "Health-domain MCP server over 100% synthetic data. Provides read tools "
    "(search_patients, get_patient, list_appointments), a consequential write "
    "tool (book_appointment), a labs resource, and a triage prompt. All inputs "
    "are validated server-side; every call is audit-logged and traced with PII "
    "redacted; when auth is enabled the server acts as an OAuth 2.1 Resource "
    "Server enforcing per-tool scopes."
)


def build_server(
    *,
    token_verifier: TokenVerifier | None = None,
    auth_settings: AuthSettings | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create a FastMCP instance with all primitives registered.

    ``host``/``port`` apply only to the Streamable HTTP transport; stdio ignores
    them.
    """
    configure_audit_logging()
    configure_tracing()
    mcp = FastMCP(
        name="mcp-health-server",
        instructions=INSTRUCTIONS,
        token_verifier=token_verifier,
        auth=auth_settings,
        host=host,
        port=port,
    )
    tools.register(mcp)
    resources.register(mcp)
    prompts.register(mcp)
    return mcp


# Module-level instance for `fastmcp`/Inspector entry points and `mcp.run()`.
mcp = build_server()
