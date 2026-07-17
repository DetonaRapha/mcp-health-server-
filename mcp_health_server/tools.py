"""Tools — the thin translation layer.

Each tool does exactly three things: validate the incoming arguments against the
domain contract (never trusting the model), delegate to ``data.py``, and return a
typed model. The ``@audited`` decorator sits closest to the function so every call
is logged with PII redacted; ``@mcp.tool`` sits on top and derives the JSON schema
from the (preserved) signature.
"""

from __future__ import annotations

from datetime import date, datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import data
from .models import Appointment, Patient, PatientSummary
from .safety import audited, validate_date_range, validate_patient_id


def register(mcp: FastMCP) -> None:
    """Register all tools on the given FastMCP instance."""

    @mcp.tool(
        annotations=ToolAnnotations(title="Search patients", readOnlyHint=True),
    )
    @audited
    def search_patients(query: str) -> list[PatientSummary]:
        """Search synthetic patients by name or condition. Returns lean summaries."""
        if not isinstance(query, str):
            from .safety import DomainError

            raise DomainError("query must be a string.")
        return data.search_patients(query, today=date.today())

    @mcp.tool(
        annotations=ToolAnnotations(title="Get patient", readOnlyHint=True),
    )
    @audited
    def get_patient(patient_id: str) -> Patient:
        """Return a patient's demographics and conditions by id."""
        pid = validate_patient_id(patient_id)
        return data.get_patient(pid)

    @mcp.tool(
        annotations=ToolAnnotations(title="List appointments", readOnlyHint=True),
    )
    @audited
    def list_appointments(
        patient_id: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[Appointment]:
        """List appointments for a patient, optionally filtered to a date range."""
        pid = validate_patient_id(patient_id)
        validate_date_range(from_date, to_date)
        return data.list_appointments(pid, from_date, to_date)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Book appointment",
            readOnlyHint=False,
            # Consequential write. The host should confirm with the user before
            # executing — this is the human-in-the-loop signal for the action.
            destructiveHint=True,
            idempotentHint=False,
        ),
    )
    @audited
    def book_appointment(patient_id: str, when: datetime, reason: str) -> Appointment:
        """Book a new appointment for a patient.

        CONSEQUENTIAL WRITE: this changes state. Marked destructive so the MCP
        host prompts the user for confirmation before it runs.
        """
        from .safety import DomainError

        pid = validate_patient_id(patient_id)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("reason must be a non-empty string.")
        return data.create_appointment(pid, when, reason.strip())
