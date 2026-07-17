"""End-to-end tests over the in-memory MCP client (no transport, no subprocess).

The SDK lets a client session connect directly to the server object in memory,
so these run fast and clean in CI. The most important test here is the one that
proves the server rejects bad input — we do not trust the caller.
"""

from __future__ import annotations

import json
import logging

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_health_server.safety import AUDIT_LOGGER_NAME, redact
from mcp_health_server.server import build_server

# The ``fresh_store`` autouse fixture lives in conftest.py.


def _structured(result):
    """Pull the structured payload out of a CallToolResult."""
    assert not result.isError, f"tool returned an error: {result.content}"
    return result.structuredContent


# --------------------------------------------------------------------------- #
# 1. The server responds — read tools return the expected models
# --------------------------------------------------------------------------- #


async def test_search_patients_returns_summaries():
    async with connect(build_server()) as client:
        result = await client.call_tool("search_patients", {"query": "diabetes"})
        payload = _structured(result)
        ids = {row["id"] for row in payload["result"]}
        # p-001 and p-005 both have type 2 diabetes in the synthetic set.
        assert {"p-001", "p-005"} <= ids
        assert all({"id", "name", "age"} == set(row) for row in payload["result"])


async def test_get_patient_returns_full_record():
    async with connect(build_server()) as client:
        result = await client.call_tool("get_patient", {"patient_id": "p-002"})
        patient = _structured(result)
        assert patient["id"] == "p-002"
        assert patient["conditions"] == ["asthma"]
        assert "birth_date" in patient


async def test_list_appointments_respects_date_range():
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "list_appointments",
            {"patient_id": "p-001", "from_date": "2026-05-01", "to_date": "2026-12-31"},
        )
        appts = _structured(result)["result"]
        assert [a["id"] for a in appts] == ["a-002", "a-003"]


# --------------------------------------------------------------------------- #
# 2. THE MOST IMPORTANT TEST — bad input is rejected, not crashed on
# --------------------------------------------------------------------------- #


async def test_unknown_patient_is_a_clean_error():
    async with connect(build_server()) as client:
        result = await client.call_tool("get_patient", {"patient_id": "p-999"})
        assert result.isError
        assert "No patient found" in result.content[0].text


async def test_malformed_patient_id_is_rejected():
    async with connect(build_server()) as client:
        result = await client.call_tool("get_patient", {"patient_id": "not-an-id"})
        assert result.isError
        assert "Malformed patient_id" in result.content[0].text


async def test_inverted_date_range_is_rejected():
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "list_appointments",
            {"patient_id": "p-001", "from_date": "2026-12-31", "to_date": "2026-01-01"},
        )
        assert result.isError
        assert "Invalid date range" in result.content[0].text


async def test_missing_required_argument_is_rejected():
    """A hallucinated/absent argument must be caught by schema validation."""
    async with connect(build_server()) as client:
        result = await client.call_tool("get_patient", {})  # no patient_id
        assert result.isError


async def test_hallucinated_parameter_name_is_rejected():
    """If the model invents a wrong parameter name, the required one is absent
    and schema validation rejects the call — the model cannot smuggle its typo
    'patinet_id' through in place of 'patient_id'."""
    async with connect(build_server()) as client:
        result = await client.call_tool("get_patient", {"patinet_id": "p-001"})
        assert result.isError


# --------------------------------------------------------------------------- #
# 3. PII redaction — the audit line masks the name, never logs it in the clear
# --------------------------------------------------------------------------- #


async def test_audit_log_redacts_pii(caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        async with connect(build_server()) as client:
            await client.call_tool("get_patient", {"patient_id": "p-001"})

    audit_lines = [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER_NAME]
    assert audit_lines, "expected at least one audit log line"
    joined = "\n".join(audit_lines)
    # The real name must never appear; its masked form must.
    assert "Rafaela Almeida" not in joined
    assert "R******" in joined
    # The birth date must be masked too.
    assert "1979-03-12" not in joined


def test_redact_helper_masks_known_pii_keys():
    raw = {"name": "Rafaela Almeida", "birth_date": "1979-03-12", "age": 47, "id": "p-001"}
    out = redact(raw)
    assert out["name"] != "Rafaela Almeida"
    assert out["birth_date"] != "1979-03-12"
    assert out["age"] == 47  # non-PII passes through untouched
    assert out["id"] == "p-001"


# --------------------------------------------------------------------------- #
# 4. The write tool carries the consequential (destructive) annotation
# --------------------------------------------------------------------------- #


async def test_book_appointment_is_marked_consequential():
    async with connect(build_server()) as client:
        tools = (await client.list_tools()).tools
        book = next(t for t in tools if t.name == "book_appointment")
        assert book.annotations is not None
        assert book.annotations.readOnlyHint is False
        assert book.annotations.destructiveHint is True


async def test_book_appointment_creates_and_persists():
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "book_appointment",
            {"patient_id": "p-004", "when": "2026-12-01T10:00:00", "reason": "Follow-up"},
        )
        appt = _structured(result)
        assert appt["patient_id"] == "p-004"
        assert appt["reason"] == "Follow-up"

        # It should now show up in the patient's appointment list.
        listed = await client.call_tool("list_appointments", {"patient_id": "p-004"})
        assert appt["id"] in {a["id"] for a in _structured(listed)["result"]}


# --------------------------------------------------------------------------- #
# 5. The resource returns labs by URI
# --------------------------------------------------------------------------- #


async def test_patient_labs_resource():
    async with connect(build_server()) as client:
        result = await client.read_resource("patient://p-006/labs")
        labs = json.loads(result.contents[0].text)
        names = {lab["name"] for lab in labs}
        assert {"TSH", "Free T4"} <= names


# --------------------------------------------------------------------------- #
# 6. The prompt renders a structured triage template
# --------------------------------------------------------------------------- #


async def test_triage_prompt_renders():
    async with connect(build_server()) as client:
        result = await client.get_prompt("triage_summary", {"patient_id": "p-003"})
        text = result.messages[0].content.text
        assert "p-003" in text
        assert "triage" in text.lower()
