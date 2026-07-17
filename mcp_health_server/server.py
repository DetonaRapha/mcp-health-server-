"""FastMCP server instance and primitive registration.

``build_server`` wires the tools, resource, and prompt onto a fresh FastMCP
instance. Keeping it a factory (rather than a module-level singleton) lets tests
build an isolated server, while ``mcp`` remains available for the ``fastmcp``/
Inspector tooling that imports a module-level object.

Transport: stdio by default (see ``__main__``). A Streamable HTTP variant is
documented in the README as the network path but intentionally not implemented
in v0.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import prompts, resources, tools
from .safety import configure_audit_logging

INSTRUCTIONS = (
    "Health-domain MCP server over 100% synthetic data. Provides read tools "
    "(search_patients, get_patient, list_appointments), a consequential write "
    "tool (book_appointment), a labs resource, and a triage prompt. All inputs "
    "are validated server-side and every call is audit-logged with PII redacted."
)


def build_server() -> FastMCP:
    """Create a FastMCP instance with all primitives registered."""
    configure_audit_logging()
    mcp = FastMCP(name="mcp-health-server", instructions=INSTRUCTIONS)
    tools.register(mcp)
    resources.register(mcp)
    prompts.register(mcp)
    return mcp


# Module-level instance for `fastmcp`/Inspector entry points and `mcp.run()`.
mcp = build_server()
