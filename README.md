# mcp-health-server

A Claude-compatible **Model Context Protocol (MCP)** server that exposes health-domain
tools over **100% synthetic data**, built with the rigor a regulated domain demands:
strict server-side validation, a PII-redacting audit log, OAuth 2.1 authorization with
per-tool scopes, PII-safe tracing, and a **red-team suite that proves the guardrails
hold in CI**. The write action is marked consequential so the host confirms with the user.

Any MCP host — Claude Desktop, Cursor, VS Code, the MCP Inspector — connects over stdio
(local) or Streamable HTTP (remote).

## Why this exists

MCP is the standard way to connect AI models to real systems (donated to the Linux
Foundation in Dec 2025; ~10k public servers; production adoption across major hosts).
The scarce skill is doing it *well* where mistakes matter. This repo demonstrates MCP
integration with the discipline of a regulated domain — **security is a primitive, and it
is verifiable, not merely asserted**. It targets exactly where the ecosystem is weak:
only ~8.5% of MCP servers implement the mandatory OAuth 2.1, and tool-poisoning success
rates exceed 60% in the wild.

It is the operational half of a two-repo story: `llm-guardrails` governs *what the model
says*; this server governs *what the model can execute through tools*.

## What it exposes

| Primitive | Name | Scope | Notes |
|-----------|------|-------|-------|
| Tool (read) | `search_patients(query)` | `patients:read` | Search by name or condition. |
| Tool (read) | `get_patient(patient_id)` | `patients:read` | Demographics and conditions. |
| Tool (read) | `list_appointments(patient_id, from_date?, to_date?)` | `patients:read` | Optional date range. |
| Tool (**write**) | `book_appointment(patient_id, when, reason)` | `appointments:write` | **Consequential** — host confirms. |
| Tool (**write**) | `record_lab_observation(patient_id, loinc_code, value, …)` | `appointments:write` | **Consequential**; LOINC code validated (anti-hallucination). |
| Tool (task) | `start_cohort_report(condition)` / `get_cohort_report(task_id)` | `patients:read` | Long-running aggregate via the Tasks pattern. |
| Resource | `patient://{patient_id}/labs` | `patients:read` | Lab results, addressable by URI. |
| Resource | `fhir://Patient/{patient_id}` | `patients:read` | Patient as a FHIR R4 Bundle. |
| Resource | `ui://appointment/confirm/{patient_id}` | `patients:read` | Server-rendered HTML confirmation (MCP App precursor). |
| Prompt | `triage_summary(patient_id)` | — | Structured triage template. |

## Run it

### Local (stdio, no auth) — one command

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     |  macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m mcp_health_server
```

### Remote (Streamable HTTP, OAuth 2.1 Resource Server)

```bash
MCP_TRANSPORT=streamable-http python -m mcp_health_server
```

It prints a dev bearer token (read+write) to stderr and serves at `http://127.0.0.1:8000/mcp`.
Requests without a valid token get **HTTP 401**. Enforcement is real:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# => 401
```

Configuration comes from the environment:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `8000` | HTTP bind address. |
| `MCP_HEALTH_DATA_PATH` | bundled `data/patients.json` | Synthetic dataset path. |
| `MCP_HEALTH_LOG_LEVEL` | `INFO` | Audit log verbosity. |
| `MCP_AUTH_ENABLED` | off | Enable per-tool scope enforcement (HTTP sets it automatically). |
| `MCP_AUTH_ISSUER` / `MCP_AUTH_AUDIENCE` | dev defaults | OAuth issuer and audience (RFC 8707). |
| `MCP_OTEL_EXPORTER` | `none` | `none` \| `console` \| `otlp` tracing exporter. |

> The dev token is minted by an in-process **mock Authorization Server** for dev/CI only.
> A real deployment verifies against a real issuer's JWKS and never uses the mock.

Extra HTTP knobs: `MCP_HTTP_STATELESS=1` runs the server without a session
(`Mcp-Session-Id`), so it can sit behind a plain round-robin load balancer — the
direction the 2026-07-28 spec formalises.

### Container (Docker)

```bash
docker compose up                          # server only, console tracing
docker compose --profile observability up  # server + OTLP collector
```

The container runs the Streamable HTTP transport as a non-root user and prints a dev
bearer token to its logs on startup.

### Connect from the MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m mcp_health_server
```

### Connect from Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "health": { "command": "python", "args": ["-m", "mcp_health_server"] }
  }
}
```

Because `book_appointment` is annotated consequential, the host prompts for confirmation.

## Security & compliance — the core of this repo

1. **Strict server-side validation on every tool.** The model is never trusted. Unknown
   patients, malformed ids, injection strings, and inverted date ranges return clean
   errors, not raw exceptions.
2. **Audit log of every invocation, PII redacted.** Names masked (`Rafaela Almeida` →
   `R****** A******`), DOB → `****-**-**`. Logs go to **stderr** (stdout is the JSON-RPC
   channel under stdio).
3. **Config and secrets come only from the environment** — never a tool schema or resource
   payload. `.env` is git-ignored.
4. **The write tool is consequential** (`destructiveHint=True`) — human-in-the-loop.
5. **OAuth 2.1 as Resource Server.** Local RS256 JWT validation, `aud` enforcement
   (RFC 8707, anti-replay), and **per-tool scopes** (`patients:read` vs
   `appointments:write`) — least privilege in both directions.
6. **PII-safe observability.** One OpenTelemetry span per call recording tool name, scope,
   outcome, and latency — never arguments or results.
7. **Verifiable guardrails.** A red-team suite (`tests/redteam/`) reproduces tool poisoning,
   authorization escalation, and PII-leak attacks. It runs as a **CI gate**: any successful
   attack fails the build. A meta-test proves the gate is load-bearing (loosening it flips
   the outcome).

### Mapping to ISO/IEC 42001 + HIPAA + LGPD

| Control | How this server addresses it |
|---|---|
| **HIPAA — audit trail of every PHI access** | `@audited` + a span per call, PII redacted. |
| **HIPAA — minimum necessary** | Per-tool scopes; lean `PatientSummary`. |
| **HIPAA — access control** | OAuth 2.1 Resource Server, per-tool scope enforcement. |
| **LGPD — minimization & purpose** | PII redaction in logs/traces; synthetic data; scope per tool. |
| **ISO/IEC 42001 — human oversight (A.9.2)** | Consequential write (HITL) + red-team gate. |
| **ISO/IEC 42001 — system security (B.6.2.6)** | Auth, strict validation, poisoning resistance verified in CI. |

> Illustrative of an ISO/IEC 42001-aligned control posture, not a formal certification.
> There is no "HIPAA-certified AI" — compliance is an operational state, which is exactly
> what the controls around the model demonstrate.

## Synthetic data

Everything under [`data/patients.json`](data/patients.json) is invented — six fictional
patients with appointments and labs. **Never** point `MCP_HEALTH_DATA_PATH` at real data.

## Tests

```bash
pytest                    # full suite (in-memory client, no transport)
pytest tests/redteam -q   # the adversarial security gate on its own
```

Coverage: the server responds with the expected models; bad input is rejected rather than
crashing; the audit line masks the patient name; token verification rejects wrong-audience,
expired, wrong-issuer, and forged tokens; per-tool scopes enforce least privilege; and the
red-team suite proves poisoning/escalation/PII-leak resistance.

## Design decisions & trade-offs

See [DESIGN.md](DESIGN.md) — thin layer, transports, security as a *verifiable* primitive,
Resource-Server auth, synthetic-only data, and the SDK version decision, each with its
"why not the other way".

## FHIR realism & v2 concepts (built)

- **FHIR (v1.5):** `fhir://Patient/{id}` returns a FHIR R4 Bundle (Patient + Conditions +
  Observations). The write tool `record_lab_observation` **validates the LOINC code against
  a known set and rejects fabricated codes** — the named anti-hallucination control for
  clinical AI. Data stays synthetic; `data.py` is the one seam a real FHIR/EHR backend
  would replace.
- **Stateless HTTP (v2):** `MCP_HTTP_STATELESS=1` — real, using the SDK's `stateless_http`.
- **Tasks pattern (v2):** `start_cohort_report` returns a handle; `get_cohort_report` polls
  it, using the protocol's own status strings (`working`/`completed`/…).
- **MCP App precursor (v2):** `ui://appointment/confirm/{id}` serves a PII-free HTML
  confirmation card.

> **Honesty note on v2.** The MCP SDK v2 (native stateless core, native Tasks tools,
> native server-rendered MCP Apps) is not published yet — this repo pins the stable v1
> line (`mcp>=1.28,<2.0`). The items above implement the v2 *concepts* on the stable SDK;
> the native migration is a deliberate future step once v2 ships. Today's supported
> interactive HITL path is elicitation (`Context.elicit`); native Apps arrive with v2.

## Still future (not built)

- Migrate to the MCP SDK v2 once published (native stateless core / Tasks / MCP Apps).
- Multi-agent (agent-to-agent) coordination.
- Real FHIR/EHR backend behind the `data.py` seam; real external Authorization Server / IdP;
  JWKS fetched+cached from a live issuer instead of the in-process mock.
