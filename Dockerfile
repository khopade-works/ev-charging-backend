# Base image pinned by digest: rebuilds are reproducible.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
LABEL org.opencontainers.image.source="https://github.com/khopade-works/ev-charging-backend"
WORKDIR /app
COPY pyproject.toml .
COPY ev ev
# pip goes after the install: the app never needs it, and its vendored libraries carry CVEs.
RUN pip install --no-cache-dir . && pip uninstall -y pip
ARG GIT_SHA=unknown
# Tracing is configuration only: opentelemetry-instrument instruments FastAPI and psycopg; the chart sets the endpoint.
ENV EV_GIT_SHA=$GIT_SHA \
    EV_METRICS_PORT=9000 \
    OTEL_SERVICE_NAME=ev-charging \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=version,readyz
USER 10001:10001
CMD ["opentelemetry-instrument", "uvicorn", "ev.api:app", "--host", "0.0.0.0", "--port", "8000"]
