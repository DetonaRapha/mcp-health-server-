"""Prompts — pontos de partida reutilizáveis e estruturados para fluxos de múltiplas etapas.

Um prompt é um template que o host pode apresentar ao usuário. ``triage_summary``
dá ao modelo um scaffold disciplinado para resumir um paciente para triagem,
reduzindo a chance de uma resposta livre, desestruturada e propensa a erros.

O template referencia o paciente apenas pelo id e instrui o modelo a buscar os
dados via as tools/resource — ele nunca embute os dados do paciente em si.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .safety import validate_patient_id


def register(mcp: FastMCP) -> None:
    @mcp.prompt(title="Triage summary")
    def triage_summary(patient_id: str) -> str:
        """Um template de prompt que instrui o modelo a resumir um paciente para triagem."""
        pid = validate_patient_id(patient_id)
        return (
            f"You are assisting a clinician with triage for patient {pid}.\n\n"
            "Use the available tools and resources to gather what you need:\n"
            f"1. Call `get_patient` with patient_id='{pid}' for demographics and conditions.\n"
            f"2. Call `list_appointments` with patient_id='{pid}' to see upcoming visits.\n"
            f"3. Read the resource `patient://{pid}/labs` for recent lab results.\n\n"
            "Then produce a concise triage summary with these sections:\n"
            "- **Patient**: age and active conditions.\n"
            "- **Recent labs**: any value outside its reference range, flagged clearly.\n"
            "- **Upcoming care**: next appointment and its reason.\n"
            "- **Suggested priority**: routine / soon / urgent, with a one-line justification.\n\n"
            "Do not invent values. If data is missing, say so explicitly. This is "
            "decision support over synthetic data, not a medical diagnosis."
        )
