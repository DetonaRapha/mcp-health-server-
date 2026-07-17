"""Shared test fixtures and helpers (auto-applied to tests/ and subdirectories)."""

from __future__ import annotations

import pytest

from mcp_health_server import data


@pytest.fixture(autouse=True)
def fresh_store():
    """Reset the cached data store so write tests don't leak between cases."""
    data.reset_cache()
    yield
    data.reset_cache()


@pytest.fixture
def enable_auth(monkeypatch):
    """Turn on Resource Server auth for the duration of a test."""
    monkeypatch.setenv("MCP_AUTH_ENABLED", "1")
    yield


@pytest.fixture
def get_fn():
    """Factory: return the underlying decorated callable for a registered tool.

    Calling it directly runs the full decorator stack (scope gate → audit →
    tracing) in the current task, so a ``principal()`` context is visible — the
    in-memory client session runs tools in a separate task where the auth context
    var does not propagate.
    """

    def _get(mcp, name):
        return mcp._tool_manager.get_tool(name).fn

    return _get


@pytest.fixture
def get_resource_fn():
    def _get(mcp, name):
        for res in mcp._resource_manager._templates.values():
            if res.name == name:
                return res.fn
        raise KeyError(name)

    return _get
