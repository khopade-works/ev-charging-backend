import asyncio
import os
import time
from decimal import Decimal

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, start_http_server
from pydantic import BaseModel

from ev.domain import (
    ChargingSession,
    Conflict,
    Connector,
    ConnectorType,
    DomainError,
    Driver,
    InvalidInput,
    Location,
    NotFound,
    PromoCode,
    Station,
    Vehicle,
)
from ev.pgstore import PostgresStore
from ev.pricing import PEAKS
from ev.selection import STRATEGIES
from ev.service import ChargingService
from ev.store import InMemoryStore

REQUESTS = Counter("http_requests", "HTTP requests served", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"])
STARTED = Counter("ev_charging_sessions_started", "Charging sessions started")
REJECTED = Counter("ev_charging_session_start_rejected", "Charging session starts rejected", ["reason"])
PROBES = ("/version", "/readyz")  # kept out of request metrics


class VehicleIn(BaseModel):
    model: str
    connector_types: list[ConnectorType]


class DriverIn(BaseModel):
    name: str
    vehicle: VehicleIn


class ConnectorIn(BaseModel):
    type: ConnectorType
    power_kw: float


class StationIn(BaseModel):
    name: str
    lat: float
    lon: float
    connectors: list[ConnectorIn]


class PromoIn(BaseModel):
    code: str
    percent_off: Decimal


class StartIn(BaseModel):
    driver_id: int
    lat: float
    lon: float
    radius_km: float
    connector_type: ConnectorType
    promo_code: str | None = None


class EndIn(BaseModel):
    energy_kwh: Decimal


def _handler(status: int):
    def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": type(exc).__name__, "message": str(exc)})

    return handle


def create_app(service: ChargingService | None = None) -> FastAPI:
    if service is None:
        service = ChargingService(
            PostgresStore() if os.environ.get("EV_STORE") == "postgres" else InMemoryStore(),
            strategy=STRATEGIES[os.environ.get("EV_STRATEGY", "nearest")],
            peak=PEAKS[os.environ.get("EV_PEAK", "time")],  # a bad value fails startup, so a bad release never turns Ready
        )
    svc = service
    app = FastAPI()
    app.add_exception_handler(NotFound, _handler(404))
    app.add_exception_handler(Conflict, _handler(409))
    app.add_exception_handler(InvalidInput, _handler(422))

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started, status = time.perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route else "unmatched"  # the route template keeps label cardinality bounded
            if path not in PROBES:
                REQUESTS.labels(request.method, path, status).inc()
                LATENCY.labels(request.method, path).observe(time.perf_counter() - started)

    # Probes are async so they never queue behind requests in the threadpool: a busy pod is not a dead one.
    @app.get("/version")  # also the liveness probe: answers without touching the database
    async def version() -> dict[str, str]:
        return {"git_sha": os.environ.get("EV_GIT_SHA", "unknown")}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        try:
            await asyncio.to_thread(svc.ping)
        except psycopg.Error as exc:
            raise HTTPException(503, "database unreachable") from exc
        return {"status": "ready"}

    @app.post("/drivers", status_code=201)
    def register_driver(body: DriverIn) -> Driver:
        vehicle = Vehicle(body.vehicle.model, frozenset(body.vehicle.connector_types))
        return svc.register_driver(body.name, vehicle)

    @app.post("/stations", status_code=201)
    def register_station(body: StationIn) -> Station:
        connectors = [(c.type, c.power_kw) for c in body.connectors]
        return svc.register_station(body.name, Location(body.lat, body.lon), connectors)

    @app.post("/stations/{station_id}/connectors/{connector_id}/out-of-service")
    def take_out_of_service(station_id: int, connector_id: int) -> Connector:
        return svc.take_out_of_service(station_id, connector_id)

    @app.post("/stations/{station_id}/connectors/{connector_id}/in-service")
    def return_to_service(station_id: int, connector_id: int) -> Connector:
        return svc.return_to_service(station_id, connector_id)

    @app.post("/promos", status_code=201)
    def add_promo(body: PromoIn) -> PromoCode:
        return svc.add_promo(body.code, body.percent_off)

    @app.delete("/promos/{code}", status_code=204)
    def delete_promo(code: str) -> None:
        svc.delete_promo(code)

    @app.post("/sessions", status_code=201)
    def start_session(body: StartIn) -> ChargingSession:
        try:  # user success: a rejection (no free connector, bad promo) is a correct answer, but a driver didn't charge
            here = Location(body.lat, body.lon)
            session = svc.start_session(body.driver_id, here, body.radius_km, body.connector_type, body.promo_code)
        except DomainError as exc:
            REJECTED.labels(type(exc).__name__).inc()
            raise
        STARTED.inc()
        return session

    @app.post("/sessions/{session_id}/end")
    def end_session(session_id: int, body: EndIn) -> ChargingSession:
        return svc.end_session(session_id, body.energy_kwh)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: int) -> ChargingSession:
        return svc.cancel_session(session_id)

    @app.get("/drivers/{driver_id}/sessions")
    def driver_history(driver_id: int) -> dict[str, list[ChargingSession]]:
        return svc.driver_history(driver_id)

    @app.get("/stations/{station_id}/sessions")
    def station_history(station_id: int) -> dict[str, list[ChargingSession]]:
        return svc.station_history(station_id)

    return app


app = create_app()
if port := os.environ.get("EV_METRICS_PORT"):  # set in the image only; tests import this module too
    start_http_server(int(port))  # its own port, so the public Service never exposes /metrics
