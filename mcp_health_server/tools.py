"""Tools — a camada de tradução enxuta.

Cada tool: aplica o scope OAuth obrigatório (quando a auth está ligada), valida
os argumentos recebidos contra o contrato de domínio (nunca confiando no modelo),
delega para ``data.py`` e retorna um modelo tipado.

Pilha de decorators (de cima para baixo = de fora para dentro):
    @mcp.tool          deriva o schema JSON da assinatura preservada
    @traced            abre um span PII-safe em torno da chamada inteira
    @audited           loga a chamada/outcome com PII redigida (audita negações)
    @require_scope     autoriza antes que qualquer outra coisa rode
O marcador ``__required_scope__`` definido por ``require_scope`` propaga-se pela
cadeia de ``functools.wraps`` para que ``traced`` possa registrá-lo no span.
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
    """Registra todas as tools na instância FastMCP fornecida."""

    @mcp.tool(annotations=ToolAnnotations(title="Search patients", readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def search_patients(query: str) -> list[PatientSummary]:
        """Busca pacientes sintéticos por nome ou condição. Retorna resumos enxutos."""
        if not isinstance(query, str):
            raise DomainError("query must be a string.")
        return data.search_patients(query, today=date.today())

    @mcp.tool(annotations=ToolAnnotations(title="Get patient", readOnlyHint=True))
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def get_patient(patient_id: str) -> Patient:
        """Retorna os dados demográficos e condições de um paciente pelo id."""
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
        """Lista as consultas de um paciente, opcionalmente filtradas por um intervalo de datas."""
        pid = validate_patient_id(patient_id)
        validate_date_range(from_date, to_date)
        return data.list_appointments(pid, from_date, to_date)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Book appointment",
            readOnlyHint=False,
            # Escrita consequente. O host deve confirmar com o usuário antes de
            # executar — este é o sinal de human-in-the-loop para a ação.
            destructiveHint=True,
            idempotentHint=False,
        ),
    )
    @traced
    @audited
    @require_scope(SCOPE_WRITE)
    def book_appointment(patient_id: str, when: datetime, reason: str) -> Appointment:
        """Agenda uma nova consulta para um paciente.

        ESCRITA CONSEQUENTE: isto altera o estado. Requer o scope 'appointments:write'
        e é marcada como destrutiva para que o host MCP solicite ao usuário a
        confirmação antes de executar.
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
        """Registra um resultado de laboratório para um paciente e o retorna como uma FHIR Observation.

        ESCRITA CONSEQUENTE (requer 'appointments:write'). O código LOINC é validado
        primeiro contra o conjunto de códigos conhecidos: um código alucinado/desconhecido
        é rejeitado para que um código clínico fabricado nunca entre no registro.
        """
        pid = validate_patient_id(patient_id)
        code = fhir.validate_loinc(loinc_code)
        if not isinstance(value, str) or not value.strip():
            raise DomainError("value must be a non-empty string.")
        display = fhir.loinc_display(code)
        lab = data.record_observation(
            pid, name=display, value=value.strip(), reference_range=reference_range, taken_at=_date.today()
        )
        # Retorna a FHIR Observation do lab recém-registrado (último da lista).
        index = len(data.get_labs(pid)) - 1
        return fhir.observation_resource(pid, index, lab)
