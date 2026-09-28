from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from ev.domain import Connector, Station


@dataclass(frozen=True)
class Candidate:
    station: Station
    connector: Connector
    distance_km: float
    multiplier: Decimal  # peak multiplier this station would charge right now


Strategy = Callable[[Candidate], tuple]  # a sort key; the service picks min(candidates, key=strategy)


def nearest(c: Candidate) -> tuple:
    return (c.distance_km, c.station.id, c.connector.id)


def cheapest(c: Candidate) -> tuple:
    return (c.multiplier, c.distance_km, c.station.id, c.connector.id)


def highest_power(c: Candidate) -> tuple:
    return (-c.connector.power_kw, c.distance_km, c.station.id, c.connector.id)


STRATEGIES: dict[str, Strategy] = {"nearest": nearest, "cheapest": cheapest, "highest_power": highest_power}
