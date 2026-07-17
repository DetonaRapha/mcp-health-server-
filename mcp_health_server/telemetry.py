"""Observability: one OpenTelemetry span per tool call, with PII-safe attributes.

Production agent observability means structured tracing across tool invocations.
``@traced`` opens a span per call and records only non-sensitive attributes —
tool name, required scope, outcome, latency. It never records arguments or
results, so PII cannot leak into the telemetry backend (the same discipline the
audit log applies). The span's ``outcome`` mirrors the audit line, so a trace and
its log entry tell the same story.

Exporter selection is env-driven:
* ``MCP_OTEL_EXPORTER=console`` — print spans to stderr (dev default when tracing on).
* ``MCP_OTEL_EXPORTER=otlp`` — export via OTLP (endpoint from standard OTEL_* vars).
* unset / ``none`` — no-op tracer (zero overhead, default).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Status, StatusCode

_CONFIGURED = False
_SERVICE_NAME = "mcp-health-server"

# Module-owned provider so we don't depend on OpenTelemetry's global (which can
# only be set once per process — awkward for tests). ``_tracer`` prefers it.
_provider: TracerProvider | None = None


def configure_tracing() -> None:
    """Install a tracer provider based on ``MCP_OTEL_EXPORTER`` (idempotent)."""
    global _CONFIGURED, _provider
    if _CONFIGURED:
        return
    _CONFIGURED = True

    exporter = os.environ.get("MCP_OTEL_EXPORTER", "none").strip().lower()
    if exporter in {"", "none"}:
        return  # leave the default no-op tracer in place

    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    if exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "otlp":
        # Imported lazily so the OTLP extra is only needed when actually used.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _provider = provider
    trace.set_tracer_provider(provider)


def install_span_exporter(exporter: SpanExporter) -> None:
    """Test hook: route spans to ``exporter`` via a fresh module-owned provider."""
    global _provider, _CONFIGURED
    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _provider = provider
    _CONFIGURED = True


def _tracer() -> trace.Tracer:
    if _provider is not None:
        return _provider.get_tracer(_SERVICE_NAME)
    return trace.get_tracer(_SERVICE_NAME)


F = TypeVar("F", bound=Callable[..., Any])


def traced(func: F) -> F:
    """Wrap a tool in a span recording only PII-safe attributes.

    Composed above ``@audited`` so the span brackets the whole call. Records the
    tool name, its required scope (if any), outcome, and latency — never the
    arguments or the result.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        span_name = f"tool.{func.__name__}"
        started = time.perf_counter()
        with _tracer().start_as_current_span(span_name) as span:
            span.set_attribute("mcp.tool.name", func.__name__)
            required_scope = getattr(func, "__required_scope__", None)
            if required_scope:
                span.set_attribute("mcp.tool.required_scope", required_scope)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — record type only, never message/PII
                span.set_attribute("mcp.tool.outcome", "rejected_or_error")
                span.set_attribute("mcp.error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                span.set_attribute("mcp.tool.duration_ms", round((time.perf_counter() - started) * 1000, 2))
            span.set_attribute("mcp.tool.outcome", "ok")
            return result

    return wrapper  # type: ignore[return-value]
