"""Resources — URI-addressable data loaded on demand by the host.

Lab results are exposed as a resource rather than a tool because the payload can
grow large and is naturally addressable by a URI (``patient://{id}/labs``). The
host decides when to pull it into context, instead of it riding on every turn.

Note: a resource payload must never carry secrets or configuration — only the
domain data the URI names.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import data, fhir
from .auth import SCOPE_READ, require_scope
from .models import LabResult
from .safety import audited, validate_patient_id
from .telemetry import traced


def register(mcp: FastMCP) -> None:
    @mcp.resource("patient://{patient_id}/labs")
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def patient_labs(patient_id: str) -> list[LabResult]:
        """Lab results for a patient, exposed as a resource URI."""
        pid = validate_patient_id(patient_id)
        return data.get_labs(pid)

    @mcp.resource("fhir://Patient/{patient_id}", mime_type="application/fhir+json")
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def patient_fhir_bundle(patient_id: str) -> dict:
        """The patient as a FHIR R4 collection Bundle (Patient + Conditions + Observations).

        FHIR is the interchange format real healthcare systems speak; exposing it
        as a resource lets a host pull structured clinical data on demand. Data
        remains 100% synthetic.
        """
        pid = validate_patient_id(patient_id)
        patient = data.get_patient(pid)
        return fhir.patient_bundle(patient, patient.conditions, data.get_labs(pid))
