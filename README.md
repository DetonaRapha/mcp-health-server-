# mcp-health-server

A Claude-compatible **Model Context Protocol (MCP)** server that exposes health-domain
tools over **100% synthetic data**, built with the rigor a regulated domain demands:
strict server-side validation, a PII-redacting audit log, and a write action explicitly
marked as consequential so the host asks the user to confirm.

Any MCP host — Claude Desktop, Cursor, VS Code, the MCP Inspector — can connect over
stdio and immediately use the tools.

## Why this exists

MCP is becoming the standard way to connect AI models to real systems, and the scarce
skill is doing it *well* in a domain where mistakes matter. This repo is not "just an MCP
server" — it is a demonstration of MCP integration done with the discipline of a regulated
domain: **security is treated as a primitive, not a patch.**

It is the operational half of a two-repo story:
[`llm-guardrails`](#) governs *what the model says*; this server governs *what the model
can execute through tools*.

## What it exposes

| Primitive | Name | What it does |
|-----------|------|--------------|
| Tool (read) | `search_patients(query)` | Search synthetic patients by name or condition. |
| Tool (read) | `get_patient(patient_id)` | Return a patient's demographics and conditions. |
| Tool (read) | `list_appointments(patient_id, from_date?, to_date?)` | List appointments in an optional date range. |
| Tool (**write**) | `book_appointment(patient_id, when, reason)` | Book a new appointment. **Consequential** — host confirms. |
| Resource | `patient://{patient_id}/labs` | Lab results, addressable by URI, loaded on demand. |
| Prompt | `triage_summary(patient_id)` | A structured template that guides the model through a triage summary. |

Tools return small typed payloads; the larger lab data is a **resource** so the host
decides when to pull it into context.

## Run it (stdio, one command)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     |  macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m mcp_health_server
```

The server speaks JSON-RPC over stdio. Configuration comes from the environment:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_HEALTH_DATA_PATH` | bundled `data/patients.json` | Path to the synthetic dataset. |
| `MCP_HEALTH_LOG_LEVEL` | `INFO` | Audit log verbosity. |

### Connect from the MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m mcp_health_server
```

Open the printed URL, connect, and you can list and call every tool, read the labs
resource, and render the triage prompt — the manual verification path for the DoD.

### Connect from Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "health": {
      "command": "python",
      "args": ["-m", "mcp_health_server"],
      "env": { "MCP_HEALTH_LOG_LEVEL": "INFO" }
    }
  }
}
```

Restart Claude Desktop; the tools appear under the connector. Because `book_appointment`
is annotated as a consequential write, the host prompts for confirmation before running it.

## Security & compliance — the core of this repo

Treat this as the point of the project, not an appendix.

1. **Strict server-side validation on every tool (Pydantic + explicit validators).**
   The model is never trusted to send well-formed arguments. Unknown patients, malformed
   ids, and inverted date ranges return clean errors, not raw exceptions. A hallucinated
   parameter name fails because the required argument is then absent.
2. **Audit log of every invocation, with PII redacted.** Tool name, timestamp, arguments,
   and result are logged — names are masked (`Rafaela Almeida` → `R****** A******`) and
   dates of birth become `****-**-**`. PII never lands in the log in the clear. Logs go to
   **stderr** so they never corrupt the stdio JSON-RPC channel.
3. **Config and secrets come only from environment variables** — never from a tool schema
   or a resource payload. `.env` is git-ignored.
4. **The write tool is marked consequential** (`destructiveHint=True`), the MCP signal for
   the host to require human confirmation (human-in-the-loop).
5. **Synthetic data only.** No real patient data, in any version, ever.

### Mapping to ISO/IEC 42001 (AI management systems)

| Control area (ISO/IEC 42001) | How this server addresses it |
|------------------------------|------------------------------|
| **A.6.2.4 — System operation & monitoring** | Every tool call is audit-logged with timestamp, inputs, and outcome. |
| **A.7.4 — Data quality & minimization** | Lean `PatientSummary` returns only what search needs; PII minimized in transit and logs. |
| **A.8.3 — Information for interested parties** | Consequential writes are declared to the host so a human can intervene. |
| **A.9.2 — Responsible use & human oversight** | `destructiveHint` enforces human-in-the-loop on state-changing actions. |
| **A.10.4 — Third-party & data provenance** | 100% synthetic, clearly labelled data; no real subjects processed. |
| **B.6.2.6 — Security of AI system** | Strict input validation; secrets confined to environment, absent from schemas/payloads. |

> The mapping is illustrative of an ISO/IEC 42001-aligned control posture, not a formal
> certification.

## Synthetic data

Everything under [`data/patients.json`](data/patients.json) is invented. Six fictional
patients, each with appointments and lab results. Any resemblance to a real person is
coincidental. **Never** point `MCP_HEALTH_DATA_PATH` at real patient data.

## Tests

```bash
pytest
```

Tests connect a client to the server **in memory** (no transport, no subprocess), so they
run cleanly in CI. They cover: the server responding with the expected models; bad input
being rejected rather than crashing (the most important test); the audit line masking the
patient name; and the write tool carrying its consequential annotation.

## Design decisions & trade-offs

See [DESIGN.md](DESIGN.md) — thin-layer architecture, stdio in v0, security as a primitive,
synthetic-only data, and the SDK version decision, with the "why not the other way" for each.

## Future (intentionally out of scope for v0)

- **Streamable HTTP transport** for network access. The code path is
  `mcp.run(transport="streamable-http")` with host/port configured on the `FastMCP`
  instance — documented here, not implemented in v0.
- **OAuth / remote authentication** and cloud deployment.
- **Real FHIR / EHR integration** behind the `data.py` seam (the thin layer makes this a
  drop-in replacement, but it is deliberately not built in v0).
