"""Server-rendered confirmation UI (MCP App precursor).

v2 concept. The 2026-07-28 spec adds **MCP Apps**: server-rendered UIs the host
displays in a sandboxed iframe. SDK 1.28 has no native App type yet, so this
exposes the same idea as far as the stable SDK allows — an HTML fragment served
as a resource (``ui://appointment/confirm/{patient_id}``) that a host could render
to confirm the consequential ``book_appointment`` write.

Two deliberate constraints:
* The fragment is **PII-free** — it references the patient by id only, never the
  name or DOB — so nothing sensitive rides in the (audited) resource payload.
* It is a *precursor*: the genuine interactive human-in-the-loop path available
  today is elicitation (``Context.elicit``); native server-rendered Apps arrive
  with the SDK v2 migration.
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
        """A PII-free HTML confirmation card for a consequential booking."""
        pid = validate_patient_id(patient_id)
        return _CONFIRM_HTML.format(patient_id=pid)
