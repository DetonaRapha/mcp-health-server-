# mcp-health-server — Streamable HTTP (OAuth 2.1 Resource Server) container.
# Synthetic data only. See README "Run it" for the auth/token flow.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    MCP_OTEL_EXPORTER=console

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md LICENSE ./
COPY mcp_health_server ./mcp_health_server
COPY data ./data
RUN pip install .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# The server binds 0.0.0.0:8000 and enforces OAuth on the HTTP transport.
CMD ["python", "-m", "mcp_health_server"]
