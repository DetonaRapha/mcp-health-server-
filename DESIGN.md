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

---

# v1 — remote, authorized, and *verifiably* safe

v1 turns the v0 proof into a production-grade, hireable artifact by closing the gaps the
market both demands and where the ecosystem is weakest: transport, authorization,
observability, and — the differentiator — **security you can run in CI**.

## Decisions (v1)

1. **Two transports, one server.** `build_server` is transport-agnostic; the entrypoint
   selects stdio (dev, no auth) or Streamable HTTP (remote, auth on) by `MCP_TRANSPORT`.
   Nothing about the tools changes — the thin layer pays off.

2. **OAuth 2.1 as *Resource Server*, not Authorization Server.** The server validates
   tokens (RS256 signature, `exp`, `iss`, and `aud` per RFC 8707 to stop cross-resource
   replay) and enforces scopes; it never authenticates users or mints tokens. Validation
   uses PyJWT — no hand-rolled crypto. Only ~8.5% of MCP servers do this, so doing it
   correctly is a strong seniority signal.

3. **Per-tool scopes, enforced in-process.** `require_scope` reads the SDK's auth context
   and gates each tool (`patients:read` vs `appointments:write`). It sits beneath
   `@audited` so denials are audited, and it is a no-op when auth is disabled so stdio/dev
   still works. The `__required_scope__` marker propagates up the `functools.wraps` chain
   for the tracer to record.

4. **Security is *verifiable*, not asserted.** `tests/redteam/` reproduces the real 2026
   attacks — tool poisoning, authz escalation, PII leakage — and runs as a CI gate. A
   meta-test (`test_gate_is_real`) shows the outcome flips when the guardrail is loosened,
   so a green result can't be vacuous.

5. **PII-safe observability.** One span per call with non-sensitive attributes only,
   mirroring the audit outcome. The tracer uses a module-owned provider (not OTel's
   set-once global) so tests can capture spans deterministically.

## Alternatives considered (v1)

- **Roll our own token validation / Authorization Server.** Rejected — every MCP security
  guide says use tested libraries; we verify with PyJWT and defer issuance to a real IdP
  (a mock AS stands in for dev/CI only, never production).
- **Enforce auth at the transport layer only.** The SDK does gate HTTP requests (verified:
  unauthenticated → 401), but that alone can't express *per-tool* scopes, and it doesn't
  run under the in-memory test client. Per-tool `require_scope` gives both differentiated
  authorization and transport-independent testability.
- **Test auth end-to-end through the in-memory client.** The client session runs tools in a
  separate task where the auth context var doesn't propagate, so scope tests would be
  impossible to set up honestly. We test the verifier as a unit and the scope gate on the
  tool functions within a `principal()` context — both are real, hermetic checks.

## Trade-offs (v1)

**Gained:** remote deployability; a real Resource-Server auth posture; least-privilege
scopes; production-style tracing; and a security suite that *fails the build* on
regression — the difference between marketing and engineering.

**Given up (deferred by design):** JWKS is provided by an in-process mock rather than
fetched+cached from a live issuer (drop-in later); no FHIR realism or containerization yet
(v1.5); still on the v1 SDK line — the stateless v2 migration is a deliberate future step,
not a drift.
