import threading
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

from ev.domain import (
    FALLBACK,
    ChargingSession,
    Conflict,
    Connector,
    ConnectorType,
    Driver,
    InvalidInput,
    Location,
    NoConnectorAvailable,
    PromoCode,
    SessionStatus,
    Station,
    Vehicle,
)
from ev.pricing import TARIFFS, PeakMultiplier, no_peak, no_show_bill, price
from ev.selection import Candidate, Strategy, nearest
from ev.store import InMemoryStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChargingService:
    def __init__(self, store: InMemoryStore, strategy: Strategy = nearest,
                 peak: PeakMultiplier = no_peak, clock: Callable[[], datetime] = utc_now) -> None:
        self._store = store
        self._strategy = strategy
        self._peak = peak
        self._clock = clock
        # ponytail: one in-process lock = single-process ceiling. When persisted: partial UNIQUE indexes on sessions(connector_id) and sessions(driver_id) WHERE status='ACTIVE'; claim with UPDATE connectors SET status='OCCUPIED' WHERE id=? AND status='AVAILABLE' checking rowcount == 1, in the same transaction as the session INSERT; on 0 rows try the next candidate, else 409.
        self._lock = threading.Lock()

    def register_driver(self, name: str, vehicle: Vehicle) -> Driver:
        with self._lock:
            driver = Driver(self._store.next_id("driver"), name, vehicle)
            self._store.add_driver(driver)
            return driver

    def register_station(self, name: str, location: Location,
                         connectors: list[tuple[ConnectorType, float]]) -> Station:
        with self._lock:
            station = Station(
                self._store.next_id("station"), name, location,
                [Connector(i, type, power_kw) for i, (type, power_kw) in enumerate(connectors, start=1)],
            )
            self._store.add_station(station)
            return station

    def take_out_of_service(self, station_id: int, connector_id: int) -> Connector:
        with self._lock:
            connector = self._store.get_station(station_id).connector(connector_id)
            connector.take_out_of_service()
            return connector

    def return_to_service(self, station_id: int, connector_id: int) -> Connector:
        with self._lock:
            connector = self._store.get_station(station_id).connector(connector_id)
            connector.return_to_service()
            return connector

    def add_promo(self, code: str, percent_off: Decimal) -> PromoCode:
        with self._lock:
            promo = PromoCode(code, percent_off)
            self._store.add_promo(promo)
            return promo

    def delete_promo(self, code: str) -> None:
        with self._lock:
            self._store.delete_promo(code)

    def start_session(self, driver_id: int, here: Location, radius_km: float,
                      requested: ConnectorType, promo_code: str | None = None) -> ChargingSession:
        with self._lock:
            now = self._clock()
            driver = self._store.get_driver(driver_id)
            if any(s.status is SessionStatus.ACTIVE for s in self._store.sessions_for_driver(driver.id)):
                raise Conflict(f"driver {driver.id} already has an active session")
            if requested not in driver.vehicle.supported_types:
                raise InvalidInput(f"vehicle does not support {requested}")
            promo = self._store.find_promo(promo_code) if promo_code is not None else None
            if promo_code is not None and promo is None:
                raise InvalidInput(f"unknown promo code {promo_code!r}")
            if not radius_km > 0:
                raise InvalidInput("radius_km must be positive")
            cands: list[Candidate] = []
            for t in (requested, *FALLBACK.get(requested, ())):
                if t not in driver.vehicle.supported_types:
                    continue
                cands = self._candidates(here, radius_km, t, now)
                if cands:
                    break
            if not cands:
                raise NoConnectorAvailable(f"no free {requested} connector within {radius_km} km")
            pick = min(cands, key=self._strategy)
            pick.connector.claim()
            session = ChargingSession(
                self._store.next_id("session"), driver.id, pick.station.id, pick.connector.id,
                billed_type=requested, connector_type=pick.connector.type, promo=promo,
                multiplier=pick.multiplier, started_at=now,
            )
            self._store.add_session(session)
            return session

    def _candidates(self, here: Location, radius_km: float, type: ConnectorType,
                    now: datetime) -> list[Candidate]:
        # ponytail: linear scan over stations; geohash/PostGIS index when station count matters.
        cands = []
        for station in self._store.list_stations():
            distance = station.location.distance_km(here)
            free = station.free_connectors(type)
            if distance <= radius_km and free:
                multiplier = self._peak(now, station.load())
                cands += [Candidate(station, c, distance, multiplier) for c in free]
        return cands

    def end_session(self, session_id: int, energy_kwh: Decimal) -> ChargingSession:
        with self._lock:
            session = self._store.get_session(session_id)
            connector = self._store.get_station(session.station_id).connector(session.connector_id)
            bill = price(TARIFFS[session.billed_type], energy_kwh, session.multiplier, session.promo)
            session.complete(energy_kwh, bill, self._clock())
            connector.release()
            return session

    def cancel_session(self, session_id: int) -> ChargingSession:
        with self._lock:
            session = self._store.get_session(session_id)
            connector = self._store.get_station(session.station_id).connector(session.connector_id)
            session.cancel(no_show_bill(), self._clock())
            connector.release()
            return session

    def driver_history(self, driver_id: int) -> dict[str, list[ChargingSession]]:
        with self._lock:
            self._store.get_driver(driver_id)
            return self._history(self._store.sessions_for_driver(driver_id))

    def station_history(self, station_id: int) -> dict[str, list[ChargingSession]]:
        with self._lock:
            self._store.get_station(station_id)
            return self._history(self._store.sessions_for_station(station_id))

    def _history(self, sessions: list[ChargingSession]) -> dict[str, list[ChargingSession]]:
        return {
            key: [s for s in sessions if s.status is status]
            for key, status in (("active", SessionStatus.ACTIVE), ("completed", SessionStatus.COMPLETED),
                                ("cancelled", SessionStatus.CANCELLED))
        }
