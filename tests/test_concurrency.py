"""Removing the store lock turns this red (the loser gets Conflict from claim()).

With Postgres it also runs as two replicas: two stores, two connection pools, one database."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from ev.domain import ConnectorStatus, ConnectorType, DomainError, Location, Vehicle
from ev.selection import nearest
from ev.service import ChargingService
from tests.stores import DATABASE_URL, new_store

T0 = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)
HERE = Location(12.9340, 77.6230)


def slow_nearest(c):
    time.sleep(0.05)  # widens the scan→claim window
    return nearest(c)


@pytest.mark.parametrize("replicas", [1, 2] if DATABASE_URL else [1])
@pytest.mark.parametrize("free", [1, 2])
def test_last_connector_race(free, replicas):
    store = new_store()
    services = [ChargingService(s, strategy=slow_nearest, clock=lambda: T0)
                for s in [store, *(new_store(fresh=False) for _ in range(replicas - 1))]]
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
    with store.transaction():
        connectors = store.get_station(station.id).connectors
    assert all(c.status is ConnectorStatus.OCCUPIED for c in connectors)
