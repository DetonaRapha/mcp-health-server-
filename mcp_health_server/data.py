"""Synthetic health data source — the "business logic", isolated from MCP.

Nothing in this module imports MCP. It loads ``data/patients.json`` once and
exposes pure-ish access functions that return domain models. Swapping this for a
real FHIR/EHR client later would not touch the tool layer — that separation is
what keeps the MCP server a thin, portable translator.

The data is 100% fictional. See the notice at the top of the JSON file.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from itertools import count

from .models import Appointment, LabResult, Patient, PatientSummary
from .safety import DomainError, get_config


class _Store:
    """In-memory view of the synthetic dataset."""

    def __init__(self, raw: dict) -> None:
        self.patients: dict[str, Patient] = {}
        self.appointments: dict[str, list[Appointment]] = {}
        self.labs: dict[str, list[LabResult]] = {}
        max_apt = 0
        for row in raw.get("patients", []):
            patient = Patient(
                id=row["id"],
                name=row["name"],
                birth_date=date.fromisoformat(row["birth_date"]),
                conditions=list(row.get("conditions", [])),
            )
            self.patients[patient.id] = patient
            self.appointments[patient.id] = [
                Appointment(
                    id=a["id"],
                    patient_id=patient.id,
                    when=datetime.fromisoformat(a["when"]),
                    reason=a["reason"],
                )
                for a in row.get("appointments", [])
            ]
            self.labs[patient.id] = [
                LabResult(
                    name=lab["name"],
                    value=lab["value"],
                    reference_range=lab["reference_range"],
                    taken_at=date.fromisoformat(lab["taken_at"]),
                )
                for lab in row.get("labs", [])
            ]
            for a in row.get("appointments", []):
                suffix = a["id"].rsplit("-", 1)[-1]
                if suffix.isdigit():
                    max_apt = max(max_apt, int(suffix))
        # New appointment ids continue past the highest seeded id.
        self._apt_counter = count(max_apt + 1)

    def next_appointment_id(self) -> str:
        return f"a-{next(self._apt_counter):03d}"


@lru_cache(maxsize=1)
def _store() -> _Store:
    path = get_config().data_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DomainError(f"Synthetic data file not found at {path}.") from exc
    except json.JSONDecodeError as exc:
        raise DomainError(f"Synthetic data file at {path} is not valid JSON.") from exc
    return _Store(raw)


def reset_cache() -> None:
    """Drop the cached store so a new data path / mutations are re-read. Test hook."""
    _store.cache_clear()


def _age(birth: date, *, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


# --------------------------------------------------------------------------- #
# Access functions — pure translation targets for the tool layer
# --------------------------------------------------------------------------- #


def search_patients(query: str, *, today: date) -> list[PatientSummary]:
    """Case-insensitive match against patient name or any condition label."""
    q = query.strip().lower()
    results: list[PatientSummary] = []
    for p in _store().patients.values():
        haystack = [p.name.lower(), *(c.lower() for c in p.conditions)]
        if not q or any(q in field for field in haystack):
            results.append(PatientSummary(id=p.id, name=p.name, age=_age(p.birth_date, today=today)))
    return results


def get_patient(patient_id: str) -> Patient:
    patient = _store().patients.get(patient_id)
    if patient is None:
        raise DomainError(f"No patient found with id {patient_id!r}.")
    return patient


def list_appointments(
    patient_id: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Appointment]:
    get_patient(patient_id)  # raises DomainError if the patient does not exist
    items = _store().appointments.get(patient_id, [])
    if from_date is not None:
        items = [a for a in items if a.when.date() >= from_date]
    if to_date is not None:
        items = [a for a in items if a.when.date() <= to_date]
    return sorted(items, key=lambda a: a.when)


def get_labs(patient_id: str) -> list[LabResult]:
    get_patient(patient_id)
    return list(_store().labs.get(patient_id, []))


def record_observation(
    patient_id: str,
    name: str,
    value: str,
    reference_range: str,
    taken_at: date,
) -> LabResult:
    """Append a lab observation to a patient's record (used by the FHIR write path)."""
    get_patient(patient_id)
    lab = LabResult(name=name, value=value, reference_range=reference_range, taken_at=taken_at)
    _store().labs.setdefault(patient_id, []).append(lab)
    return lab


def create_appointment(patient_id: str, when: datetime, reason: str) -> Appointment:
    get_patient(patient_id)
    appt = Appointment(
        id=_store().next_appointment_id(),
        patient_id=patient_id,
        when=when,
        reason=reason,
    )
    _store().appointments.setdefault(patient_id, []).append(appt)
    return appt
