"""v1.5: FHIR realism + clinical-code (anti-hallucination) validation."""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_health_server import fhir
from mcp_health_server.safety import DomainError
from mcp_health_server.server import build_server


async def test_patient_fhir_bundle_resource():
    async with connect(build_server()) as client:
        result = await client.read_resource("fhir://Patient/p-001")
        bundle = json.loads(result.contents[0].text)
        assert bundle["resourceType"] == "Bundle"
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert types[0] == "Patient"
        assert "Condition" in types and "Observation" in types
        # LOINC coding present on an observation.
        obs = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation")
        assert obs["code"]["coding"][0]["system"] == fhir.LOINC_SYSTEM


def test_validate_loinc_accepts_known_and_rejects_unknown():
    assert fhir.validate_loinc("4548-4") == "4548-4"  # HbA1c
    with pytest.raises(DomainError):
        fhir.validate_loinc("0000-0")  # not a real/known code
    with pytest.raises(DomainError):
        fhir.validate_loinc("'; DROP TABLE labs;--")


async def test_record_lab_observation_with_valid_loinc():
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "record_lab_observation",
            {"patient_id": "p-004", "loinc_code": "718-7", "value": "14.2 g/dL"},
        )
        assert not result.isError
        obs = json.loads(result.content[0].text)
        assert obs["resourceType"] == "Observation"
        assert obs["code"]["coding"][0]["code"] == "718-7"


async def test_record_lab_observation_rejects_hallucinated_code():
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "record_lab_observation",
            {"patient_id": "p-004", "loinc_code": "99999-9", "value": "5"},
        )
        assert result.isError
        assert "Unknown LOINC code" in result.content[0].text
