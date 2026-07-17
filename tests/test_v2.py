"""v2 concepts on the stable SDK: Tasks pattern, stateless HTTP, MCP App precursor."""

from __future__ import annotations

import json
import time

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_health_server import tasks
from mcp_health_server.server import build_server


@pytest.fixture(autouse=True)
def fresh_tasks():
    tasks.reset()
    yield
    tasks.reset()


def _structured(result):
    assert not result.isError, result.content
    # Tools returning a plain dict (no Pydantic schema) carry the JSON in the text
    # content rather than structuredContent.
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


# --------------------------------------------------------------------------- #
# Tasks pattern — start returns a handle; poll until completed
# --------------------------------------------------------------------------- #


async def test_cohort_report_task_completes():
    async with connect(build_server()) as client:
        started = _structured(await client.call_tool("start_cohort_report", {"condition": "diabetes"}))
        assert started["status"] == "working"
        task_id = started["task_id"]

        # Poll until completed (bounded).
        deadline = time.monotonic() + 5
        report = None
        while time.monotonic() < deadline:
            polled = _structured(await client.call_tool("get_cohort_report", {"task_id": task_id}))
            if polled["status"] == "completed":
                report = polled["result"]
                break
        assert report is not None, "task did not complete in time"
        # p-001 and p-005 have diabetes in the synthetic set.
        assert report["count"] >= 2
        assert {"p-001", "p-005"} <= {p["id"] for p in report["patients"]}


async def test_get_unknown_task_is_clean_error():
    async with connect(build_server()) as client:
        result = await client.call_tool("get_cohort_report", {"task_id": "task-does-not-exist"})
        assert result.isError
        assert "Unknown task_id" in result.content[0].text


# --------------------------------------------------------------------------- #
# MCP App precursor — server-rendered HTML confirmation, PII-free
# --------------------------------------------------------------------------- #


async def test_confirmation_ui_resource_is_html_and_pii_free():
    async with connect(build_server()) as client:
        result = await client.read_resource("ui://appointment/confirm/p-001")
        html = result.contents[0].text
        assert "<div" in html and "Confirm appointment" in html
        assert "p-001" in html
        # No patient name/DOB in the server-rendered fragment.
        assert "Rafaela" not in html and "1979" not in html


# --------------------------------------------------------------------------- #
# Stateless HTTP — the app builds in stateless mode without error
# --------------------------------------------------------------------------- #


def test_server_builds_stateless():
    mcp = build_server(stateless_http=True)
    assert mcp.settings.stateless_http is True
