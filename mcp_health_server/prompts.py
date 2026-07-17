"""Prompts — reusable, structured starting points for multi-step flows.

A prompt is a template the host can surface to the user. ``triage_summary``
gives the model a disciplined scaffold for summarising a patient for triage,
reducing the chance of an unstructured, error-prone free-form answer.

The template only references the patient by id and instructs the model to pull
data via the tools/resource — it never embeds patient data itself.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .safety import validate_patient_id


def register(mcp: FastMCP) -> None:
    @mcp.prompt(title="Triage summary")
    def triage_summary(patient_id: str) -> str:
        """A prompt template instructing the model to summarise a patient for triage."""
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
