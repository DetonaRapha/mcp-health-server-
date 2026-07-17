"""Modelos de domínio tipados.

Estes são os contratos entre a camada MCP enxuta (``tools.py``, ``resources.py``)
e a lógica de negócio (``data.py``). O FastMCP deriva os schemas JSON das tools a
partir dos type hints, então manter o domínio modelado aqui é o que dá aos clientes,
de graça, uma interface precisa e legível por máquina.

Os campos que carregam PII estão anotados abaixo; ``safety.py`` usa esse
conhecimento para redigi-los no audit log.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Patient(BaseModel):
    """Registro completo do paciente: dados demográficos e condições clínicas."""

    id: str = Field(description="Stable synthetic patient identifier, e.g. 'p-001'.")
    name: str = Field(description="Patient full name. PII — redacted in audit logs.")
    birth_date: date = Field(description="Date of birth. PII — redacted in audit logs.")
    conditions: list[str] = Field(
        default_factory=list,
        description="Active clinical conditions (free-text labels).",
    )


class PatientSummary(BaseModel):
    """Visão enxuta do paciente retornada pela busca — sem data de nascimento, idade no lugar da DOB."""

    id: str
    name: str = Field(description="Patient full name. PII — redacted in audit logs.")
    age: int = Field(description="Age in years, derived from birth date.")


class Appointment(BaseModel):
    """Uma consulta agendada para um paciente."""

    id: str
    patient_id: str
    when: datetime = Field(description="Appointment date and time (ISO 8601).")
    reason: str = Field(description="Reason for the visit.")


class LabResult(BaseModel):
    """Uma única medição laboratorial."""

    name: str = Field(description="Test name, e.g. 'HbA1c'.")
    value: str = Field(description="Measured value with unit, e.g. '6.1 %'.")
    reference_range: str = Field(description="Normal reference range for the test.")
    taken_at: date = Field(description="Date the sample was taken.")
