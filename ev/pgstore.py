"""Postgres store: same methods and semantics as InMemoryStore, shared by every replica.

Each aggregate (driver, station with its connectors, session, promo) is one JSONB row. The service mutates
the objects it loads, so a transaction keeps an identity map and writes back every loaded object whose
encoding changed. No save() calls leak into the service.
"""

import dataclasses
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal

from prometheus_client import Histogram
from psycopg import Connection, DataError
from psycopg_pool import ConnectionPool

from ev.domain import (
    Bill,
    ChargingSession,
    Conflict,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Driver,
    InvalidInput,
    Location,
    NotFound,
    PromoCode,
    SessionStatus,
    Station,
    Vehicle,
)

# ponytail: one advisory lock serialises every service call across replicas, like the old in-process lock.
# Upgrade when write throughput matters: partial UNIQUE indexes on active sessions per driver/connector and a
# conditional claim (UPDATE ... WHERE status='AVAILABLE', rowcount == 1) in the same transaction as the INSERT.
SERVICE_LOCK = 7_734_201

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS driver_id_seq;
CREATE SEQUENCE IF NOT EXISTS station_id_seq;
CREATE SEQUENCE IF NOT EXISTS session_id_seq;
CREATE TABLE IF NOT EXISTS drivers  (id bigint PRIMARY KEY, doc jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS stations (id bigint PRIMARY KEY, doc jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id bigint PRIMARY KEY, driver_id bigint NOT NULL,
                                     station_id bigint NOT NULL, doc jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_driver_id ON sessions (driver_id);
CREATE INDEX IF NOT EXISTS sessions_station_id ON sessions (station_id);
CREATE TABLE IF NOT EXISTS promos   (code text PRIMARY KEY, doc jsonb NOT NULL);
"""

TABLES = {Driver: "drivers", Station: "stations", ChargingSession: "sessions", PromoCode: "promos"}
KEY = {"drivers": "id", "stations": "id", "sessions": "id", "promos": "code"}

TX_SECONDS = Histogram("ev_store_transaction_seconds", "Time inside one store transaction, including lock wait")


def _default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def encode(obj: object) -> str:
    return json.dumps(dataclasses.asdict(obj), default=_default, sort_keys=True)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _promo(d: dict) -> PromoCode:
    return PromoCode(d["code"], Decimal(d["percent_off"]))


def _driver(d: dict) -> Driver:
    v = d["vehicle"]
    return Driver(d["id"], d["name"], Vehicle(v["model"], frozenset(map(ConnectorType, v["supported_types"]))))


def _station(d: dict) -> Station:
    connectors = [Connector(c["id"], ConnectorType(c["type"]), c["power_kw"], ConnectorStatus(c["status"]))
                  for c in d["connectors"]]
    return Station(d["id"], d["name"], Location(**d["location"]), connectors)


def _session(d: dict) -> ChargingSession:
    b = d["bill"]
    bill = b and Bill(Decimal(b["energy_cost"]), Decimal(b["multiplier"]), b["minimum_applied"],
                      Decimal(b["discount"]), Decimal(b["total"]))
    return ChargingSession(
        d["id"], d["driver_id"], d["station_id"], d["connector_id"],
        ConnectorType(d["billed_type"]), ConnectorType(d["connector_type"]),
        d["promo"] and _promo(d["promo"]), Decimal(d["multiplier"]), datetime.fromisoformat(d["started_at"]),
        SessionStatus(d["status"]), d["ended_at"] and datetime.fromisoformat(d["ended_at"]),
        _dec(d["energy_kwh"]), bill,
    )


DECODERS = {"drivers": _driver, "stations": _station, "sessions": _session, "promos": _promo}


class PostgresStore:
    def __init__(self, conninfo: str = "") -> None:
        # Empty conninfo: libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD from the environment.
        # check_connection: after a database restart, stale pooled connections are replaced, not handed to a request.
        # idle_in_transaction_session_timeout: a client that vanishes mid-transaction (node loss, no TCP reset)
        # releases SERVICE_LOCK after 10s instead of blocking every replica until TCP keepalive gives up.
        settings = dict(check=ConnectionPool.check_connection, open=True,
                        kwargs={"options": "-c idle_in_transaction_session_timeout=10s"})
        self._pool = ConnectionPool(conninfo, min_size=1, max_size=10, timeout=5, **settings)
        # Readiness has its own connection: requests waiting on SERVICE_LOCK hold pool connections, and a busy
        # pod must not look like a broken one.
        self._probe_pool = ConnectionPool(conninfo, min_size=1, max_size=1, timeout=1, **settings)
        self._tx = threading.local()  # FastAPI runs sync routes on a threadpool: one transaction per thread
        self._schema_ready = False

    def close(self) -> None:
        self._pool.close()
        self._probe_pool.close()

    @contextmanager
    def _connection(self, pool: ConnectionPool) -> Iterator[Connection]:
        with pool.connection() as conn:
            if not self._schema_ready:  # lazily: the app boots and reports not-ready while Postgres is down
                with conn.transaction():
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (SERVICE_LOCK,))  # replicas may boot together
                    conn.execute(SCHEMA)
                self._schema_ready = True
            yield conn

    def ping(self) -> None:
        with self._connection(self._probe_pool) as conn:
            conn.execute("SELECT 1")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        started = time.perf_counter()
        try:
            with self._connection(self._pool) as conn, conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (SERVICE_LOCK,))
                self._tx.conn, self._tx.loaded = conn, {}
                try:
                    yield
                    self._flush()
                finally:
                    del self._tx.conn, self._tx.loaded
        except DataError as exc:  # e.g. a NUL byte in a name: the client's input can't be stored, not a server fault
            raise InvalidInput(f"value cannot be stored: {exc}") from exc
        finally:
            TX_SECONDS.observe(time.perf_counter() - started)

    def _flush(self) -> None:
        for (table, key), (obj, original) in self._tx.loaded.items():
            doc = encode(obj)
            if doc != original:
                self._tx.conn.execute(f"UPDATE {table} SET doc = %s WHERE {KEY[table]} = %s", (doc, key))

    def _track(self, table: str, obj: object) -> object:
        """Return the instance already loaded in this transaction, so every mutation lands on one object."""
        key = getattr(obj, KEY[table])
        return self._tx.loaded.setdefault((table, key), (obj, encode(obj)))[0]

    def _select(self, table: str, where: str, params: tuple) -> list:
        sql = f"SELECT doc FROM {table} WHERE {where} ORDER BY {KEY[table]}"  # insertion order, like the dicts
        rows = self._tx.conn.execute(sql, params).fetchall()
        return [self._track(table, DECODERS[table](doc)) for (doc,) in rows]

    def _one(self, table: str, key: int, label: str):
        found = self._select(table, "id = %s", (key,))
        if not found:
            raise NotFound(f"{label} {key} not found")
        return found[0]

    def _insert(self, obj: object, **columns: object) -> None:
        table = TABLES[type(obj)]
        columns = {KEY[table]: getattr(obj, KEY[table]), **columns, "doc": encode(obj)}
        sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
               " ON CONFLICT DO NOTHING")
        if self._tx.conn.execute(sql, list(columns.values())).rowcount == 0:
            raise Conflict(f"promo {obj.code!r} already exists")  # only promos have caller-chosen keys
        self._track(table, obj)

    def next_id(self, kind: str) -> int:
        return self._tx.conn.execute("SELECT nextval(%s::regclass)", (f"{kind}_id_seq",)).fetchone()[0]

    def add_driver(self, driver: Driver) -> None:
        self._insert(driver)

    def get_driver(self, driver_id: int) -> Driver:
        return self._one("drivers", driver_id, "driver")

    def add_station(self, station: Station) -> None:
        self._insert(station)

    def get_station(self, station_id: int) -> Station:
        return self._one("stations", station_id, "station")

    def list_stations(self) -> list[Station]:
        return self._select("stations", "TRUE", ())

    def add_session(self, session: ChargingSession) -> None:
        self._insert(session, driver_id=session.driver_id, station_id=session.station_id)

    def get_session(self, session_id: int) -> ChargingSession:
        return self._one("sessions", session_id, "session")

    def sessions_for_driver(self, driver_id: int) -> list[ChargingSession]:
        return self._select("sessions", "driver_id = %s", (driver_id,))

    def sessions_for_station(self, station_id: int) -> list[ChargingSession]:
        return self._select("sessions", "station_id = %s", (station_id,))

    def add_promo(self, promo: PromoCode) -> None:
        self._insert(promo)

    def find_promo(self, code: str) -> PromoCode | None:
        found = self._select("promos", "code = %s", (code,))
        return found[0] if found else None

    def delete_promo(self, code: str) -> None:
        if self._tx.conn.execute("DELETE FROM promos WHERE code = %s", (code,)).rowcount == 0:
            raise NotFound(f"promo {code!r} not found")
        self._tx.loaded.pop(("promos", code), None)
