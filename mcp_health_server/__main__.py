"""Entry point: ``python -m mcp_health_server`` runs the server over stdio."""

from __future__ import annotations

from .server import mcp


def main() -> None:
    # stdio is the default transport for local hosts (Claude Desktop, Inspector).
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
