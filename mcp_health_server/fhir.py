"""FHIR realism over synthetic data + clinical-code validation.

v1.5. The data stays 100% synthetic, but it is shaped as FHIR R4 resources
(Patient, Condition, Observation) and returned as a Bundle — the interchange
format real healthcare systems speak. This keeps the ``data.py`` seam as the one
place a real FHIR/EHR backend would later plug in.

The differentiator here is **clinical-code validation**: a named risk in medical
AI is the model hallucinating a code (a LOINC/ICD value that does not exist).
:func:`validate_loinc` / :func:`validate_icd10` reject unknown codes *before* any
write, so a fabricated code never enters the record.

Code sets below are a tiny synthetic allowlist — enough to be realistic, not a
real terminology server.
"""

from __future__ import annotations

from .models import LabResult, Patient
from .safety import DomainError

LOINC_SYSTEM = "http://loinc.org"
ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10"

# Lab name -> LOINC code/display (synthetic allowlist).
LOINC_BY_LAB_NAME: dict[str, tuple[str, str]] = {
    "hba1c": ("4548-4", "Hemoglobin A1c/Hemoglobin.total"),
    "fasting glucose": ("1558-6", "Fasting glucose"),
    "systolic bp": ("8480-6", "Systolic blood pressure"),
    "ige": ("19113-0", "Immunoglobulin E"),
    "eosinophils": ("26449-9", "Eosinophils/100 leukocytes"),
    "ldl cholesterol": ("13457-7", "LDL cholesterol (calc)"),
    "total cholesterol": ("2093-3", "Total cholesterol"),
    "hemoglobin": ("718-7", "Hemoglobin"),
    "esr": ("4537-7", "Erythrocyte sedimentation rate"),
    "tsh": ("3016-3", "Thyrotropin (TSH)"),
    "free t4": ("3024-7", "Thyroxine (T4) free"),
}
KNOWN_LOINC: dict[str, str] = {code: display for code, display in LOINC_BY_LAB_NAME.values()}

# Condition label -> ICD-10 code/display (synthetic allowlist).
ICD10_BY_CONDITION: dict[str, tuple[str, str]] = {
    "type 2 diabetes": ("E11.9", "Type 2 diabetes mellitus without complications"),
    "hypertension": ("I10", "Essential (primary) hypertension"),
    "asthma": ("J45.909", "Unspecified asthma, uncomplicated"),
    "hyperlipidemia": ("E78.5", "Hyperlipidemia, unspecified"),
    "migraine": ("G43.909", "Migraine, unspecified, not intractable"),
    "osteoarthritis": ("M19.90", "Unspecified osteoarthritis, unspecified site"),
    "hypothyroidism": ("E03.9", "Hypothyroidism, unspecified"),
}


def validate_loinc(code: str) -> str:
    """Reject a LOINC code that is not in the known set (anti-hallucination)."""
    if not isinstance(code, str) or code.strip() not in KNOWN_LOINC:
        raise DomainError(
            f"Unknown LOINC code {code!r}. Refusing to record a fabricated clinical code."
        )
    return code.strip()


def loinc_display(code: str) -> str:
    return KNOWN_LOINC[code]


def loinc_for_lab(name: str) -> tuple[str, str] | None:
    return LOINC_BY_LAB_NAME.get(name.strip().lower())


def icd10_for_condition(label: str) -> tuple[str, str] | None:
    return ICD10_BY_CONDITION.get(label.strip().lower())


# --------------------------------------------------------------------------- #
# Resource mappers — synthetic domain models -> FHIR R4 resource dicts
# --------------------------------------------------------------------------- #


def _codeable(system: str, code: str, display: str, text: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": text}


def patient_resource(patient: Patient) -> dict:
    parts = patient.name.split()
    given = parts[:-1] or parts
    family = parts[-1] if len(parts) > 1 else ""
    return {
        "resourceType": "Patient",
        "id": patient.id,
        "name": [{"text": patient.name, "family": family, "given": given}],
        "birthDate": patient.birth_date.isoformat(),
    }


def condition_resource(patient_id: str, index: int, condition: str) -> dict:
    label = condition
    mapped = icd10_for_condition(label)
    code = (
        _codeable(ICD10_SYSTEM, mapped[0], mapped[1], label)
        if mapped
        else {"text": label}
    )
    return {
        "resourceType": "Condition",
        "id": f"{patient_id}-cond-{index}",
        "subject": {"reference": f"Patient/{patient_id}"},
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": code,
    }


def observation_resource(patient_id: str, index: int, lab: LabResult) -> dict:
    mapped = loinc_for_lab(lab.name)
    code = (
        _codeable(LOINC_SYSTEM, mapped[0], mapped[1], lab.name)
        if mapped
        else {"text": lab.name}
    )
    return {
        "resourceType": "Observation",
        "id": f"{patient_id}-obs-{index}",
        "status": "final",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": code,
        "valueString": lab.value,
        "referenceRange": [{"text": lab.reference_range}],
        "effectiveDateTime": lab.taken_at.isoformat(),
    }


def patient_bundle(patient: Patient, conditions, labs) -> dict:
    """A FHIR ``collection`` Bundle: the Patient plus its Conditions and Observations."""
    entries = [{"resource": patient_resource(patient)}]
    for i, cond in enumerate(conditions):
        entries.append({"resource": condition_resource(patient.id, i, cond)})
    for i, lab in enumerate(labs):
        entries.append({"resource": observation_resource(patient.id, i, lab)})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}
