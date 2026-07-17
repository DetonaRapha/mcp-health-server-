"""Fonte de dados de saúde sintéticos — a "lógica de negócio", isolada do MCP.

Nada neste módulo importa MCP. Ele carrega ``data/patients.json`` uma vez e
expõe funções de acesso quase puras que retornam modelos de domínio. Trocar isto
por um cliente FHIR/EHR real mais tarde não tocaria na camada de tools — essa
separação é o que mantém o servidor MCP um tradutor enxuto e portável.

Os dados são 100% fictícios. Veja o aviso no topo do arquivo JSON.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from itertools import count

from .models import Appointment, LabResult, Patient, PatientSummary
from .safety import DomainError, get_config


class _Store:
    """Visão em memória do dataset sintético."""

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
        # Novos ids de consulta continuam a partir do maior id semeado.
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
    """Descarta o store em cache para que um novo caminho de dados / mutações sejam relidos. Test hook."""
    _store.cache_clear()


def _age(birth: date, *, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


# --------------------------------------------------------------------------- #
# Funções de acesso — alvos de tradução puros para a camada de tools
# --------------------------------------------------------------------------- #


def search_patients(query: str, *, today: date) -> list[PatientSummary]:
    """Correspondência case-insensitive contra o nome do paciente ou qualquer rótulo de condição."""
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
    get_patient(patient_id)  # levanta DomainError se o paciente não existir
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
    """Anexa uma observação laboratorial ao registro de um paciente (usado pelo caminho de escrita FHIR)."""
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
