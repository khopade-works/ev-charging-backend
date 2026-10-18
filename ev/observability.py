"""Logs, metrics and traces. Imported only by api.py; the domain and service stay stdlib-only."""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

UNOBSERVED = {"/healthz", "/readyz", "/version"}  # probes would drown the request metrics

REQUESTS = Counter("http_requests", "HTTP requests served", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5))
SESSIONS_STARTED = Counter("ev_charging_sessions_started", "Charging sessions started",
                           ["connector_type", "billed_type"])
START_REJECTED = Counter("ev_charging_session_start_rejected", "Session starts rejected", ["reason"])
SESSIONS_ENDED = Counter("ev_charging_sessions_ended", "Charging sessions ended", ["outcome"])
BUILD_INFO = Gauge("ev_build_info", "Running build", ["version", "git_sha"])

log = logging.getLogger("ev")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:  # lets a log line be pasted into Jaeger, and a trace be found from a log line
            doc["trace_id"], doc["span_id"] = f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error"):  # uvicorn's own logs go through JSON too; its access log stays off
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("psycopg.pool").setLevel(logging.WARNING)  # it logs every connection checkout at INFO


def setup_tracing(app: FastAPI, version: str) -> None:
    """Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set; service name and attributes come from OTEL_* env vars."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.version": version}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,version")
    PsycopgInstrumentor().instrument(enable_commenter=False)


def observe_requests(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route else "unmatched"  # the template, so ids don't explode label cardinality
            if path not in UNOBSERVED:
                elapsed = time.perf_counter() - started
                REQUESTS.labels(request.method, path, str(status)).inc()
                LATENCY.labels(request.method, path).observe(elapsed)
                log.info("request", extra={"fields": {
                    "method": request.method, "route": path, "path": request.url.path,
                    "status": status, "duration_ms": round(elapsed * 1000, 2),
                }})
