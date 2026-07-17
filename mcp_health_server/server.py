"""Instância do servidor FastMCP e registro das primitivas.

``build_server`` conecta as tools, o resource e o prompt a uma instância FastMCP
nova. Ele aceita opcionalmente um token verifier + auth settings para que o
entrypoint HTTP possa rodar como um Resource Server OAuth 2.1; sem eles (o padrão
stdio/dev) o servidor roda sem autenticação e ``require_scope`` é um no-op.

Transporte: stdio por padrão; Streamable HTTP (com auth) via ``__main__``.
"""

from __future__ import annotations

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from . import apps, prompts, resources, tasks, tools
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
    stateless_http: bool = False,
) -> FastMCP:
    """Cria uma instância FastMCP com todas as primitivas registradas.

    ``host``/``port``/``stateless_http`` aplicam-se apenas ao transporte Streamable
    HTTP; o stdio os ignora. ``stateless_http=True`` roda sem uma sessão (sem
    ``Mcp-Session-Id``), a direção que a spec 2026-07-28 formaliza, para que o
    servidor possa ficar atrás de um load balancer round-robin simples.
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
        stateless_http=stateless_http,
    )
    tools.register(mcp)
    resources.register(mcp)
    prompts.register(mcp)
    tasks.register(mcp)
    apps.register(mcp)
    return mcp


# Instância a nível de módulo para os entry points `fastmcp`/Inspector e `mcp.run()`.
mcp = build_server()
