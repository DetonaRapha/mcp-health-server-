"""Typed domain models.

These are the contracts between the thin MCP layer (``tools.py``, ``resources.py``)
and the business logic (``data.py``). FastMCP derives the tool JSON schemas from the
type hints, so keeping the domain modelled here is what gives clients a precise,
machine-readable interface for free.

Fields that carry PII are annotated below; ``safety.py`` uses that knowledge to
redact them from the audit log.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Patient(BaseModel):
    """Full patient record: demographics and clinical conditions."""

    id: str = Field(description="Stable synthetic patient identifier, e.g. 'p-001'.")
    name: str = Field(description="Patient full name. PII — redacted in audit logs.")
    birth_date: date = Field(description="Date of birth. PII — redacted in audit logs.")
    conditions: list[str] = Field(
        default_factory=list,
        description="Active clinical conditions (free-text labels).",
    )


class PatientSummary(BaseModel):
    """Lean patient view returned by search — no birth date, age instead of DOB."""

    id: str
    name: str = Field(description="Patient full name. PII — redacted in audit logs.")
    age: int = Field(description="Age in years, derived from birth date.")


class Appointment(BaseModel):
    """A scheduled appointment for a patient."""

    id: str
    patient_id: str
    when: datetime = Field(description="Appointment date and time (ISO 8601).")
    reason: str = Field(description="Reason for the visit.")


class LabResult(BaseModel):
    """A single laboratory measurement."""

    name: str = Field(description="Test name, e.g. 'HbA1c'.")
    value: str = Field(description="Measured value with unit, e.g. '6.1 %'.")
    reference_range: str = Field(description="Normal reference range for the test.")
    taken_at: date = Field(description="Date the sample was taken.")
