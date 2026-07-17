"""Red-team: a hallucinated clinical code must never enter the record.

Fabricated medical codes are a named risk for clinical AI. The write path
validates LOINC against a known set; any unknown/injected code is rejected. A
success here would fail the build.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_health_server.server import build_server

FABRICATED_CODES = ["99999-9", "0000-0", "LOINC-ROCKS", "'; DROP TABLE labs;--", "4548-4-extra"]


@pytest.mark.parametrize("code", FABRICATED_CODES)
async def test_hallucinated_loinc_is_rejected(code):
    async with connect(build_server()) as client:
        result = await client.call_tool(
            "record_lab_observation",
            {"patient_id": "p-001", "loinc_code": code, "value": "1.0"},
        )
        assert result.isError, f"fabricated code accepted: {code!r}"
        assert "Unknown LOINC code" in result.content[0].text
