"""Removing the service lock turns this red (the loser gets Conflict from claim()).

With EV_TEST_DATABASE_URL it also runs as 2 replicas on one Postgres: then only the advisory lock keeps it green."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg
import pytest

from ev.domain import ConnectorStatus, ConnectorType, DomainError, Location, Vehicle
from ev.selection import nearest
from ev.service import ChargingService
from ev.pgstore import PostgresStore
from ev.store import InMemoryStore

DATABASE_URL = os.environ.get("EV_TEST_DATABASE_URL")
T0 = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)
HERE = Location(12.9340, 77.6230)


def slow_nearest(c):
    time.sleep(0.05)  # widens the scan→claim window
    return nearest(c)


def new_store(fresh):
    if not DATABASE_URL:
        return InMemoryStore()
    if fresh:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("DROP TABLE IF EXISTS state")
    return PostgresStore(DATABASE_URL)


@pytest.mark.parametrize("replicas", [1, 2] if DATABASE_URL else [1])
@pytest.mark.parametrize("free", [1, 2])
def test_last_connector_race(free, replicas):
    services = [ChargingService(new_store(i == 0), strategy=slow_nearest, clock=lambda: T0) for i in range(replicas)]
    svc = services[0]
    station = svc.register_station("S", HERE, [(ConnectorType.DC, 50.0)] * free)
    vehicle = Vehicle("V", frozenset({ConnectorType.DC}))
    drivers = [svc.register_driver(name, vehicle).id for name in ("A", "B")]
    barrier = threading.Barrier(2, timeout=5)

    def start(driver_id, replica):
        barrier.wait()
        try:
            replica.start_session(driver_id, HERE, 5, ConnectorType.DC)
            return "OK"
        except DomainError as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start, d, services[i % replicas]) for i, d in enumerate(drivers)]
        outcomes = [f.result(timeout=10) for f in futures]

    active = svc.station_history(station.id)["active"]
    if free == 1:
        assert sorted(outcomes) == ["NoConnectorAvailable", "OK"]
        assert len(active) == 1
    else:
        assert outcomes == ["OK", "OK"]
        assert len({s.connector_id for s in active}) == 2
    with svc._store.transaction():  # with Postgres, `station` above is a snapshot
        assert all(c.status is ConnectorStatus.OCCUPIED for c in svc._store.get_station(station.id).connectors)
