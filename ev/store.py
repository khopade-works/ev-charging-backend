import itertools

from ev.domain import ChargingSession, Conflict, Driver, NotFound, PromoCode, Station


class InMemoryStore:
    def __init__(self) -> None:
        self._drivers: dict[int, Driver] = {}
        self._stations: dict[int, Station] = {}
        self._sessions: dict[int, ChargingSession] = {}
        self._promos: dict[str, PromoCode] = {}
        self._ids = {kind: itertools.count(1) for kind in ("driver", "station", "session")}

    def next_id(self, kind: str) -> int:
        return next(self._ids[kind])

    def add_driver(self, driver: Driver) -> None:
        self._drivers[driver.id] = driver

    def get_driver(self, driver_id: int) -> Driver:
        if driver_id not in self._drivers:
            raise NotFound(f"driver {driver_id} not found")
        return self._drivers[driver_id]

    def add_station(self, station: Station) -> None:
        self._stations[station.id] = station

    def get_station(self, station_id: int) -> Station:
        if station_id not in self._stations:
            raise NotFound(f"station {station_id} not found")
        return self._stations[station_id]

    def list_stations(self) -> list[Station]:
        return list(self._stations.values())

    def add_session(self, session: ChargingSession) -> None:
        self._sessions[session.id] = session

    def get_session(self, session_id: int) -> ChargingSession:
        if session_id not in self._sessions:
            raise NotFound(f"session {session_id} not found")
        return self._sessions[session_id]

    def sessions_for_driver(self, driver_id: int) -> list[ChargingSession]:
        return [s for s in self._sessions.values() if s.driver_id == driver_id]

    def sessions_for_station(self, station_id: int) -> list[ChargingSession]:
        return [s for s in self._sessions.values() if s.station_id == station_id]

    def add_promo(self, promo: PromoCode) -> None:
        if promo.code in self._promos:
            raise Conflict(f"promo {promo.code!r} already exists")
        self._promos[promo.code] = promo

    def find_promo(self, code: str) -> PromoCode | None:
        return self._promos.get(code)

    def delete_promo(self, code: str) -> None:
        if self._promos.pop(code, None) is None:
            raise NotFound(f"promo {code!r} not found")
