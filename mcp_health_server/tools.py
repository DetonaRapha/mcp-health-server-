"""Tools — the thin translation layer.

Each tool: enforce the required OAuth scope (when auth is on), validate the
incoming arguments against the domain contract (never trusting the model),
delegate to ``data.py``, and return a typed model.

Decorator stack (top-to-bottom = outer-to-inner):
    @mcp.tool          derives the JSON schema from the preserved signature
    @traced            opens a PII-safe span around the whole call
    @audited           logs the call/outcome with PII redacted (audits denials)
    @require_scope     authorizes before anything else runs
The ``__required_scope__`` marker set by ``require_scope`` propagates up the
``functools.wraps`` chain so ``traced`` can record it on the span.
"""

from __future__ import annotations

from datetime import date, datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from datetime import date as _date

from . import data, fhir
from .auth import SCOPE_READ, SCOPE_WRITE, require_scope
from .models import Appointment, Patient, PatientSummary
from .safety import DomainError, audited, validate_date_range, validate_patient_id
from .telemetry import traced


def register(mcp: FastMCP) -> None:
    """Register all tools on the given FastMCP instance."""

    @mcp.tool(annotations=ToolAnnotations(title="Search patients", readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def search_patients(query: str) -> list[PatientSummary]:
        """Search synthetic patients by name or condition. Returns lean summaries."""
        if not isinstance(query, str):
            raise DomainError("query must be a string.")
        return data.search_patients(query, today=date.today())

    @mcp.tool(annotations=ToolAnnotations(title="Get patient", readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def get_patient(patient_id: str) -> Patient:
        """Return a patient's demographics and conditions by id."""
        pid = validate_patient_id(patient_id)
        return data.get_patient(pid)

    @mcp.tool(annotations=ToolAnnotations(title="List appointments", readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
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
    @traced
    @audited
    @require_scope(SCOPE_WRITE)
    def book_appointment(patient_id: str, when: datetime, reason: str) -> Appointment:
        """Book a new appointment for a patient.

        CONSEQUENTIAL WRITE: this changes state. Requires the 'appointments:write'
        scope and is marked destructive so the MCP host prompts the user for
        confirmation before it runs.
        """
        pid = validate_patient_id(patient_id)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("reason must be a non-empty string.")
        return data.create_appointment(pid, when, reason.strip())

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Record lab observation",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
        ),
    )
    @traced
    @audited
    @require_scope(SCOPE_WRITE)
    def record_lab_observation(
        patient_id: str, loinc_code: str, value: str, reference_range: str = ""
    ) -> dict:
        """Record a lab result for a patient and return it as a FHIR Observation.

        CONSEQUENTIAL WRITE (requires 'appointments:write'). The LOINC code is
        validated against the known code set first: a hallucinated/unknown code is
        rejected so a fabricated clinical code never enters the record.
        """
        pid = validate_patient_id(patient_id)
        code = fhir.validate_loinc(loinc_code)
        if not isinstance(value, str) or not value.strip():
            raise DomainError("value must be a non-empty string.")
        display = fhir.loinc_display(code)
        lab = data.record_observation(
            pid, name=display, value=value.strip(), reference_range=reference_range, taken_at=_date.today()
        )
        # Return the FHIR Observation for the newly recorded lab (last in the list).
        index = len(data.get_labs(pid)) - 1
        return fhir.observation_resource(pid, index, lab)
