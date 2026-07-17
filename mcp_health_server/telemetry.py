"""Observabilidade: um span do OpenTelemetry por chamada de tool, com atributos PII-safe.

Observabilidade de agentes em produção significa tracing estruturado através das
invocações de tools. ``@traced`` abre um span por chamada e registra apenas
atributos não sensíveis — nome da tool, scope obrigatório, outcome, latência. Ele
nunca registra argumentos ou resultados, então PII não pode vazar para o backend
de telemetria (a mesma disciplina que o audit log aplica). O ``outcome`` do span
espelha a linha de auditoria, então um trace e sua entrada de log contam a mesma
história.

A seleção do exporter é dirigida por env:
* ``MCP_OTEL_EXPORTER=console`` — imprime spans no stderr (padrão de dev com tracing ligado).
* ``MCP_OTEL_EXPORTER=otlp`` — exporta via OTLP (endpoint das vars OTEL_* padrão).
* não definido / ``none`` — tracer no-op (overhead zero, padrão).
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

# Provider próprio do módulo para não dependermos do global do OpenTelemetry (que
# só pode ser definido uma vez por processo — inconveniente para testes). ``_tracer`` o prefere.
_provider: TracerProvider | None = None


def configure_tracing() -> None:
    """Instala um tracer provider com base em ``MCP_OTEL_EXPORTER`` (idempotente)."""
    global _CONFIGURED, _provider
    if _CONFIGURED:
        return
    _CONFIGURED = True

    exporter = os.environ.get("MCP_OTEL_EXPORTER", "none").strip().lower()
    if exporter in {"", "none"}:
        return  # deixa o tracer no-op padrão no lugar

    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    if exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "otlp":
        # Importado de forma lazy para que o extra OTLP só seja necessário quando de fato usado.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _provider = provider
    trace.set_tracer_provider(provider)


def install_span_exporter(exporter: SpanExporter) -> None:
    """Test hook: roteia spans para ``exporter`` via um novo provider próprio do módulo."""
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
    """Envolve uma tool em um span registrando apenas atributos PII-safe.

    Composto acima de ``@audited`` para que o span envolva a chamada inteira.
    Registra o nome da tool, seu scope obrigatório (se houver), outcome e
    latência — nunca os argumentos ou o resultado.
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
            except Exception as exc:  # noqa: BLE001 — registra apenas o tipo, nunca mensagem/PII
                span.set_attribute("mcp.tool.outcome", "rejected_or_error")
                span.set_attribute("mcp.error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                span.set_attribute("mcp.tool.duration_ms", round((time.perf_counter() - started) * 1000, 2))
            span.set_attribute("mcp.tool.outcome", "ok")
            return result

    return wrapper  # type: ignore[return-value]
