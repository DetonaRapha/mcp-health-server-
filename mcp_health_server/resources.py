"""Resources — dados endereçáveis por URI carregados sob demanda pelo host.

Os resultados de laboratório são expostos como um resource em vez de uma tool
porque o payload pode crescer bastante e é naturalmente endereçável por uma URI
(``patient://{id}/labs``). O host decide quando trazê-lo para o contexto, em vez
de ele acompanhar cada turno.

Nota: um payload de resource nunca deve carregar segredos ou configuração —
apenas os dados de domínio que a URI nomeia.
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
        """Resultados de laboratório de um paciente, expostos como uma URI de resource."""
        pid = validate_patient_id(patient_id)
        return data.get_labs(pid)

    @mcp.resource("fhir://Patient/{patient_id}", mime_type="application/fhir+json")
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def patient_fhir_bundle(patient_id: str) -> dict:
        """O paciente como um Bundle de coleção FHIR R4 (Patient + Conditions + Observations).

        FHIR é o formato de intercâmbio que os sistemas de saúde reais falam;
        expô-lo como um resource permite que um host busque dados clínicos
        estruturados sob demanda. Os dados permanecem 100% sintéticos.
        """
        pid = validate_patient_id(patient_id)
        patient = data.get_patient(pid)
        return fhir.patient_bundle(patient, patient.conditions, data.get_labs(pid))
