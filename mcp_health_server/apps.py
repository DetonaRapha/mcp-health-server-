"""UI de confirmação renderizada no servidor (precursor de MCP App).

Conceito v2. A spec 2026-07-28 adiciona os **MCP Apps**: UIs renderizadas no
servidor que o host exibe em um iframe em sandbox. O SDK 1.28 ainda não tem um
tipo App nativo, então isto expõe a mesma ideia até onde o SDK estável permite —
um fragmento HTML servido como um resource (``ui://appointment/confirm/{patient_id}``)
que um host poderia renderizar para confirmar a escrita consequente ``book_appointment``.

Duas restrições deliberadas:
* O fragmento é **livre de PII** — ele referencia o paciente apenas pelo id, nunca
  o nome ou a DOB — para que nada sensível trafegue no payload (auditado) do resource.
* É um *precursor*: o caminho interativo genuíno de human-in-the-loop disponível
  hoje é a elicitation (``Context.elicit``); os Apps nativos renderizados no
  servidor chegam com a migração para o SDK v2.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .auth import SCOPE_READ, require_scope
from .safety import audited, validate_patient_id
from .telemetry import traced

_CONFIRM_HTML = """<!doctype html>
<meta charset="utf-8">
<div style="font-family:system-ui;max-width:22rem;padding:1rem;border:1px solid #ccc;border-radius:.5rem">
  <h3 style="margin:.2rem 0">Confirm appointment</h3>
  <p style="color:#555;margin:.4rem 0">You are about to book an appointment for
  patient <strong>{patient_id}</strong>. This is a consequential action.</p>
  <div style="display:flex;gap:.5rem;margin-top:.6rem">
    <button data-action="confirm" style="padding:.4rem .8rem">Confirm</button>
    <button data-action="cancel" style="padding:.4rem .8rem">Cancel</button>
  </div>
</div>"""


def register(mcp: FastMCP) -> None:
    @mcp.resource("ui://appointment/confirm/{patient_id}", mime_type="text/html")
    @traced
    @audited
    @require_scope(SCOPE_READ)
    def appointment_confirm_ui(patient_id: str) -> str:
        """Um card de confirmação HTML livre de PII para um agendamento consequente."""
        pid = validate_patient_id(patient_id)
        return _CONFIRM_HTML.format(patient_id=pid)
