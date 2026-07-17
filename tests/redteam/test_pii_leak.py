"""Red-team: PII must never leak into logs or traces.

Tool *results* legitimately contain patient data (the caller is authorized). The
observability plane — audit log and traces — must not. This scans both for any
cleartext name or date of birth from the dataset after exercising the tools.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session as connect
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mcp_health_server import telemetry
from mcp_health_server.safety import AUDIT_LOGGER_NAME
from mcp_health_server.server import build_server

_DATA = Path(__file__).resolve().parents[2] / "data" / "patients.json"


def _pii_values() -> set[str]:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    values: set[str] = set()
    for p in raw["patients"]:
        values.add(p["name"])
        values.update(p["name"].split())  # each name part, e.g. surname
        values.add(p["birth_date"])
    # Only scan for tokens long enough to be unambiguous PII.
    return {v for v in values if len(v) > 3}


async def test_pii_never_appears_in_audit_log(caplog):
    pii = _pii_values()
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        async with connect(build_server()) as client:
            await client.call_tool("search_patients", {"query": ""})
            await client.call_tool("get_patient", {"patient_id": "p-001"})
            await client.call_tool("list_appointments", {"patient_id": "p-005"})

    blob = "\n".join(r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER_NAME)
    assert blob, "expected audit output"
    leaked = sorted(v for v in pii if v in blob)
    assert not leaked, f"PII leaked into audit log: {leaked}"


def test_pii_never_appears_in_spans(get_fn):
    exporter = InMemorySpanExporter()
    telemetry.install_span_exporter(exporter)

    mcp = build_server()
    get_fn(mcp, "get_patient")(patient_id="p-001")
    get_fn(mcp, "search_patients")(query="diabetes")

    spans = exporter.get_finished_spans()
    assert spans, "expected spans to be recorded"

    pii = _pii_values()
    for span in spans:
        for key, value in (span.attributes or {}).items():
            rendered = str(value)
            hit = [p for p in pii if p in rendered]
            assert not hit, f"PII {hit} leaked into span attribute {key!r}"
