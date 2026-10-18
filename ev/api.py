import asyncio
import os
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import start_http_server
from pydantic import BaseModel

from ev import __version__
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
from ev.observability import (
    BUILD_INFO,
    SESSIONS_ENDED,
    SESSIONS_STARTED,
    START_REJECTED,
    observe_requests,
    setup_logging,
    setup_tracing,
)
from ev.pricing import PEAKS
from ev.selection import STRATEGIES
from ev.service import ChargingService
from ev.store import InMemoryStore, Store


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


def _choice(name: str, options: dict, default: str):
    """Fail fast on a bad setting: the pod never turns Ready, so a rolling update stops and rolls back."""
    value = os.environ.get(name, default)
    if value not in options:
        raise SystemExit(f"invalid {name}={value!r}; expected one of {sorted(options)}")
    return options[value]


def _store() -> Store:
    if _choice("EV_STORE", {"memory": "memory", "postgres": "postgres"}, "memory") == "postgres":
        from ev.pgstore import PostgresStore  # psycopg is only needed when Postgres is selected

        return PostgresStore()
    return InMemoryStore()


def create_app(service: ChargingService | None = None) -> FastAPI:
    git_sha = os.environ.get("EV_GIT_SHA", "unknown")
    app = FastAPI(version=__version__)
    setup_tracing(app, git_sha)  # before the store opens connections, so database calls are traced
    if service is None:
        setup_logging()
        service = ChargingService(
            _store(),
            strategy=_choice("EV_STRATEGY", STRATEGIES, "nearest"),
            peak=_choice("EV_PEAK", PEAKS, "time"),
        )
        if port := os.environ.get("EV_METRICS_PORT"):
            start_http_server(int(port))  # a separate port, so the public Service never exposes /metrics
    svc = service
    BUILD_INFO.labels(__version__, git_sha).set(1)
    observe_requests(app)
    app.add_exception_handler(NotFound, _handler(404))
    app.add_exception_handler(Conflict, _handler(409))
    app.add_exception_handler(InvalidInput, _handler(422))

    # Probes are async so they never queue behind requests in the route threadpool: a busy pod is not a dead one.
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}  # liveness: the process serves HTTP; dependencies are readiness's job

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            await asyncio.to_thread(svc.ping)  # asyncio's executor, not the request threadpool
        except Exception as exc:  # any database failure means: take this pod out of the Service
            return JSONResponse(status_code=503, content={"status": "unavailable", "error": type(exc).__name__})
        return JSONResponse({"status": "ready"})

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": __version__, "git_sha": git_sha}

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
        try:
            here = Location(body.lat, body.lon)
            s = svc.start_session(body.driver_id, here, body.radius_km, body.connector_type, body.promo_code)
        except DomainError as exc:
            START_REJECTED.labels(type(exc).__name__).inc()
            raise
        SESSIONS_STARTED.labels(s.connector_type, s.billed_type).inc()
        return s

    @app.post("/sessions/{session_id}/end")
    def end_session(session_id: int, body: EndIn) -> ChargingSession:
        s = svc.end_session(session_id, body.energy_kwh)
        SESSIONS_ENDED.labels("completed").inc()
        return s

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: int) -> ChargingSession:
        s = svc.cancel_session(session_id)
        SESSIONS_ENDED.labels("cancelled").inc()
        return s

    @app.get("/drivers/{driver_id}/sessions")
    def driver_history(driver_id: int) -> dict[str, list[ChargingSession]]:
        return svc.driver_history(driver_id)

    @app.get("/stations/{station_id}/sessions")
    def station_history(station_id: int) -> dict[str, list[ChargingSession]]:
        return svc.station_history(station_id)

    return app


app = create_app()
