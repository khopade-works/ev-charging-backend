import pytest

from ev.domain import (
    Conflict,
    Connector,
    ConnectorStatus,
    ConnectorType,
    InvalidInput,
    Location,
    Station,
    Vehicle,
)

AC = ConnectorType.AC
AVAILABLE, OCCUPIED, OUT = ConnectorStatus.AVAILABLE, ConnectorStatus.OCCUPIED, ConnectorStatus.OUT_OF_SERVICE


def test_distance_known_values():
    assert Location(0, 0).distance_km(Location(1, 0)) == pytest.approx(111.195, abs=0.01)
    assert Location(60, 0).distance_km(Location(60, 1)) == pytest.approx(55.597, abs=0.01)
    here = Location(12.934, 77.623)
    assert here.distance_km(here) == 0


@pytest.mark.parametrize(
    ("status", "op"),
    [
        (OCCUPIED, "claim"),
        (OUT, "claim"),
        (AVAILABLE, "release"),
        (OUT, "release"),
        (OCCUPIED, "take_out_of_service"),
        (OUT, "take_out_of_service"),
        (AVAILABLE, "return_to_service"),
        (OCCUPIED, "return_to_service"),
    ],
)
def test_connector_invalid_transitions(status, op):
    connector = Connector(1, AC, 7.4, status)
    with pytest.raises(Conflict):
        getattr(connector, op)()
    assert connector.status is status


@pytest.mark.parametrize(
    "build",
    [
        lambda: Station(1, "s", Location(0, 0), []),
        lambda: Station(1, "s", Location(0, 0), [Connector(1, AC, 7.4), Connector(1, AC, 7.4)]),
        lambda: Connector(1, AC, 0),
        lambda: Connector(1, AC, -1),
        lambda: Vehicle("v", frozenset()),
        lambda: Location(91, 0),
        lambda: Location(0, 181),
    ],
)
def test_construction_invariants(build):
    with pytest.raises(InvalidInput):
        build()
