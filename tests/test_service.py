from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ev.domain import (
    Conflict,
    ConnectorStatus,
    ConnectorType,
    InvalidInput,
    Location,
    NoConnectorAvailable,
    NotFound,
    SessionStatus,
    Vehicle,
)
from ev.pricing import by_load, by_time, no_peak
from ev.selection import cheapest, highest_power, nearest
from ev.service import ChargingService
from ev.store import InMemoryStore

AC, DC = ConnectorType.AC, ConnectorType.DC
T0 = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)
HERE = Location(12.9340, 77.6230)


def make_service(strategy=nearest, peak=no_peak):
    now = [T0]
    return ChargingService(InMemoryStore(), strategy, peak, clock=lambda: now[0]), now


def add_station(svc, k, *connectors):
    return svc.register_station(f"S{k}", Location(HERE.lat + k * 0.01, HERE.lon), list(connectors))


def add_driver(svc, *types):
    return svc.register_driver("driver", Vehicle("car", frozenset(types)))


def test_start_picks_nearest_free_compatible():
    svc, _ = make_service()
    add_station(svc, 1, (AC, 7.4))
    add_station(svc, 2, (AC, 7.4))
    svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    s = svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    assert (s.station_id, s.connector_id) == (2, 1)

    svc, _ = make_service()
    add_station(svc, 1, (AC, 7.4))
    add_station(svc, 2, (AC, 7.4))
    s = svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    assert (s.station_id, s.connector_id) == (1, 1)


@pytest.mark.parametrize("factor, ok", [(1, True), (0.999, False)])
def test_radius_boundary(factor, ok):
    svc, _ = make_service()
    station = add_station(svc, 1, (AC, 7.4))
    driver = add_driver(svc, AC)
    r = HERE.distance_km(station.location) * factor
    if ok:
        assert svc.start_session(driver.id, HERE, r, AC).station_id == station.id
    else:
        with pytest.raises(NoConnectorAvailable):
            svc.start_session(driver.id, HERE, r, AC)


def test_out_of_service_never_offered_until_returned():
    svc, _ = make_service()
    station = add_station(svc, 1, (AC, 7.4), (AC, 7.4))
    driver = add_driver(svc, AC)
    svc.take_out_of_service(station.id, 1)
    svc.take_out_of_service(station.id, 2)
    with pytest.raises(NoConnectorAvailable):
        svc.start_session(driver.id, HERE, 5, AC)
    svc.return_to_service(station.id, 2)
    assert svc.start_session(driver.id, HERE, 5, AC).connector_id == 2


def test_take_out_while_occupied_rejected():
    svc, _ = make_service()
    station = add_station(svc, 1, (AC, 7.4))
    s = svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    with pytest.raises(Conflict):
        svc.take_out_of_service(station.id, 1)
    assert svc.end_session(s.id, Decimal(10)).status is SessionStatus.COMPLETED
    assert station.connector(1).status is ConnectorStatus.AVAILABLE
    assert svc.take_out_of_service(station.id, 1).status is ConnectorStatus.OUT_OF_SERVICE


@pytest.mark.parametrize(
    "requested, types, ac, dc, expected",
    [
        (AC, {AC, DC}, "free", "free", (1, AC)),
        (AC, {AC, DC}, "occupied", "free", (1, DC)),
        (AC, {AC, DC}, "out", "free", (1, DC)),
        (AC, {AC, DC}, "occupied", "out", NoConnectorAvailable),
        (AC, {AC}, "occupied", "free", NoConnectorAvailable),
        (DC, {AC, DC}, "free", "occupied", NoConnectorAvailable),
        (DC, {AC}, "free", "free", InvalidInput),
        (AC, {AC, DC}, "far", "near", (2, AC)),
    ],
)
def test_type_selection_and_fallback(requested, types, ac, dc, expected):
    svc, _ = make_service()
    if ac == "far":
        add_station(svc, 1, (DC, 50))
        add_station(svc, 2, (AC, 7.4))
    else:
        station = add_station(svc, 1, (AC, 7.4), (DC, 50))
        for t, state in ((AC, ac), (DC, dc)):
            if state == "occupied":
                svc.start_session(add_driver(svc, t).id, HERE, 5, t)
        for i, state in ((1, ac), (2, dc)):
            if state == "out":
                svc.take_out_of_service(station.id, i)
    driver = add_driver(svc, *types)
    if isinstance(expected, type):
        with pytest.raises(expected):
            svc.start_session(driver.id, HERE, 5, requested)
    else:
        s = svc.start_session(driver.id, HERE, 5, requested)
        assert (s.station_id, s.connector_type) == expected
        assert s.billed_type is requested


def test_fallback_billed_at_ac_with_promo():
    svc, _ = make_service()
    add_station(svc, 1, (AC, 7.4), (DC, 50))
    svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    svc.add_promo("SAVE10", Decimal(10))
    s = svc.start_session(add_driver(svc, AC, DC).id, HERE, 5, AC, "SAVE10")
    s = svc.end_session(s.id, Decimal(20))
    assert s.bill.total == Decimal("225.00")
    assert (s.billed_type, s.connector_type) == (AC, DC)


@pytest.mark.parametrize(
    "case, error",
    [("unknown promo", InvalidInput), ("deleted promo", InvalidInput), ("already active", Conflict)],
)
def test_failed_start_claims_nothing(case, error):
    svc, _ = make_service()
    station = add_station(svc, 1, (AC, 7.4), (AC, 7.4))
    driver = add_driver(svc, AC)
    code = None
    if case == "unknown promo":
        code = "NOPE"
    elif case == "deleted promo":
        svc.add_promo("GONE", Decimal(10))
        svc.delete_promo("GONE")
        code = "GONE"
    else:
        svc.start_session(driver.id, HERE, 5, AC)
    with pytest.raises(error):
        svc.start_session(driver.id, HERE, 5, AC, code)
    held = {s.connector_id for s in svc.station_history(station.id)["active"]}
    assert all(c.status is ConnectorStatus.AVAILABLE for c in station.connectors if c.id not in held)
    assert svc.start_session(add_driver(svc, AC).id, HERE, 5, AC).status is SessionStatus.ACTIVE


def test_promo_snapshot():
    svc, _ = make_service()
    add_station(svc, 1, (DC, 50))
    svc.add_promo("SAVE10", Decimal(10))
    s = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC, "SAVE10")
    svc.delete_promo("SAVE10")
    svc.add_promo("SAVE10", Decimal(50))
    assert svc.end_session(s.id, Decimal(20)).bill.total == Decimal("306.00")


def test_driver_single_active_session():
    svc, _ = make_service()
    add_station(svc, 1, (AC, 7.4), (AC, 7.4))
    driver = add_driver(svc, AC)
    svc.start_session(driver.id, HERE, 5, AC)
    with pytest.raises(Conflict):
        svc.start_session(driver.id, HERE, 5, AC)


def test_end_prices_and_releases():
    svc, now = make_service()
    add_station(svc, 1, (DC, 50))
    s = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    now[0] = T0 + timedelta(hours=1)
    s = svc.end_session(s.id, Decimal(10))
    assert s.status is SessionStatus.COMPLETED
    assert s.bill.total == Decimal("200.00")
    assert s.energy_kwh == Decimal(10)
    assert s.ended_at == now[0]
    nxt = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    assert (nxt.station_id, nxt.connector_id) == (s.station_id, s.connector_id)


def test_end_invalid_energy_keeps_session_active():
    svc, _ = make_service()
    station = add_station(svc, 1, (DC, 50))
    s = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    with pytest.raises(InvalidInput):
        svc.end_session(s.id, Decimal(-1))
    assert s.status is SessionStatus.ACTIVE
    assert station.connector(1).status is ConnectorStatus.OCCUPIED
    assert svc.end_session(s.id, Decimal(10)).bill.total == Decimal("200.00")


OPS = {
    "end": lambda svc, sid: svc.end_session(sid, Decimal(10)),
    "cancel": lambda svc, sid: svc.cancel_session(sid),
}


@pytest.mark.parametrize("first", OPS)
@pytest.mark.parametrize("second", OPS)
def test_terminal_session_rejects_end_and_cancel(first, second):
    svc, _ = make_service()
    station = add_station(svc, 1, (DC, 50))
    s1 = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    OPS[first](svc, s1.id)
    s2 = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    with pytest.raises(Conflict):
        OPS[second](svc, s1.id)
    assert station.connector(1).status is ConnectorStatus.OCCUPIED
    assert svc.station_history(station.id)["active"] == [s2]


def test_history_grouping():
    svc, _ = make_service()
    st1 = add_station(svc, 1, (AC, 7.4))
    st2 = add_station(svc, 2, (AC, 7.4))
    driver = add_driver(svc, AC)
    assert svc.driver_history(driver.id) == {"active": [], "completed": [], "cancelled": []}
    done = svc.end_session(svc.start_session(driver.id, HERE, 5, AC).id, Decimal(10))
    cancelled = svc.cancel_session(svc.start_session(driver.id, HERE, 5, AC).id)
    active = svc.start_session(driver.id, HERE, 5, AC)
    other = svc.start_session(add_driver(svc, AC).id, HERE, 5, AC)
    assert other.station_id == st2.id
    expected = {"active": [active], "completed": [done], "cancelled": [cancelled]}
    assert svc.driver_history(driver.id) == expected
    assert svc.station_history(st1.id) == expected
    with pytest.raises(NotFound):
        svc.driver_history(999)
    with pytest.raises(NotFound):
        svc.station_history(999)


def test_cancel_charges_fee_and_releases():
    svc, _ = make_service()
    station = add_station(svc, 1, (AC, 7.4))
    s = svc.cancel_session(svc.start_session(add_driver(svc, AC).id, HERE, 5, AC).id)
    assert s.status is SessionStatus.CANCELLED
    assert s.bill.total == Decimal("50.00")
    assert station.connector(1).status is ConnectorStatus.AVAILABLE


@pytest.mark.parametrize(
    "minute, multiplier, total",
    [(29, Decimal(1), Decimal("340.00")), (30, Decimal("1.5"), Decimal("510.00"))],
)
def test_peak_uses_start_time(minute, multiplier, total):
    svc, now = make_service(peak=by_time)
    add_station(svc, 1, (DC, 50))
    now[0] = datetime(2026, 9, 28, 12, minute, tzinfo=timezone.utc)
    s = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    now[0] = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    s = svc.end_session(s.id, Decimal(20))
    assert s.multiplier == multiplier
    assert s.bill.total == total


@pytest.mark.parametrize(
    "strategy, peak, expected",
    [(nearest, no_peak, "A"), (highest_power, no_peak, "B"), (cheapest, by_load, "C")],
)
def test_strategy_switch_changes_station(strategy, peak, expected):
    svc, _ = make_service(strategy, peak)
    a = add_station(svc, 1, (DC, 50), (DC, 50))
    b = add_station(svc, 2, (DC, 150), (DC, 150))
    c = add_station(svc, 3, (DC, 60))
    a.connectors[0].claim()
    b.connectors[0].claim()
    s = svc.start_session(add_driver(svc, DC).id, HERE, 5, DC)
    assert s.station_id == {"A": a.id, "B": b.id, "C": c.id}[expected]
