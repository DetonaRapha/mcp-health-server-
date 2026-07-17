# DESIGN — mcp-health-server (v0)

A light RFC. It records why the server is shaped the way it is, and — the part that
signals seniority — why it is *not* shaped the other plausible ways.

## Context

An AI model that can only talk is of limited use in an operational setting; it becomes
useful when it can *act* against real systems. There are two ways to give it that reach:

- **Provider-specific function calling** — define tools inside one vendor's API. Portable
  to nothing; re-implemented per host.
- **MCP** — a host-agnostic protocol (JSON-RPC 2.0) with a shared vocabulary of tools,
  resources, and prompts. One server, every compatible host.

We chose MCP because portability is the whole point of an integration layer, and because
MCP is consolidating as the industry standard for it.

The domain — health — changes the engineering bar. In a regulated domain, an unvalidated
argument or a name leaking into a log file is not a bug, it is an incident. So the design
treats **validation and audit as primitives**, present from the first tool, not features
bolted on later.

## Decisions

1. **Thin translation layer.** `data.py` holds the "business logic" (the synthetic data
   source) and imports nothing from MCP. `tools.py`/`resources.py`/`prompts.py` only
   validate input, delegate to `data.py`, and return typed models. A REST `GET
   /patient/{id}` maps one-to-one to a `get_patient(id)` tool. This keeps the server
   portable and makes a future FHIR/EHR backend a swap behind one seam.

2. **stdio transport in v0.** The simplest thing that a local host (Claude Desktop, the
   Inspector) can drive, and the easiest to demonstrate. Streamable HTTP is documented as
   the network path but not built — see trade-offs.

3. **Security as a primitive.**
   - Pydantic models + explicit validators (`validate_patient_id`, `validate_date_range`)
     reject bad input server-side. The model is never trusted.
   - An `@audited` decorator wraps every tool and logs each call with PII redacted. It sits
     *beneath* `@mcp.tool` so FastMCP still derives the schema from the real signature.
   - Config/secrets come only from environment variables.
   - The one write tool carries `destructiveHint=True` so the host asks for confirmation.

4. **Synthetic data only.** The dataset is fictional and labelled as such at the top of the
   JSON and in the README. No real patient data, ever.

5. **SDK version pinned to the v1 line (`mcp>=1.28,<2.0`).** The MCP Python SDK is moving to
   a v2 that tracks the 2026-07-28 spec and carries breaking changes. Pinning to `<2.0` is a
   conscious decision to build on the stable line and prevents a `pip install` on the wrong
   day from silently breaking the repo. Upgrading to v2 will be a deliberate, guided
   migration, not a drift.

## Alternatives considered

- **Use an off-the-shelf MCP server.** Rejected: the value here is precisely the
  regulated-domain rigor (validation + PII-redacted audit + consequential-write signalling).
  A generic server does not demonstrate that.
- **Expose the data as a plain REST API.** Rejected: REST is not host-agnostic for AI
  models — each host would need bespoke glue. MCP is the shared contract. (The thin layer
  means a REST facade could still be added later without touching the tools.)
- **Forbid unknown arguments at the schema layer (`extra="forbid"`).** Considered.
  The SDK's generated argument model ignores extra fields (tolerant JSON-RPC). Rather than
  monkeypatch SDK internals, we rely on the fact that the *meaningful* failure mode — the
  model hallucinating a parameter name — surfaces as a missing required argument and is
  rejected. Documented and tested as such.
- **Log to stdout / a file by default.** Rejected: stdout is the JSON-RPC channel under
  stdio and must stay clean; a default log file risks persisting operational data. Audit
  goes to stderr; a file sink is an opt-in deployment concern.

## Trade-offs

**Gained:** portability across MCP hosts; a security posture that maps to ISO/IEC 42001;
clarity from the thin-layer seam; fast, transport-free tests via the in-memory client.

**Given up (on purpose, for v0):** no network transport (stdio only), no auth, no real
data backend, and tolerant handling of extra arguments rather than strict rejection. Each
is a scoped v0 boundary, not an oversight — the "why not now" is recorded above so the next
increment starts from an intentional baseline.
