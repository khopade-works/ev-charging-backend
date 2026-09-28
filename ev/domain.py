import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ConnectorType(StrEnum):
    AC = "AC"
    DC = "DC"


class ConnectorStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# Which connector types may serve a request, after the requested type itself. DC never falls back.
FALLBACK: dict[ConnectorType, tuple[ConnectorType, ...]] = {ConnectorType.AC: (ConnectorType.DC,)}


class DomainError(Exception): ...
class NotFound(DomainError): ...
class Conflict(DomainError): ...
class NoConnectorAvailable(Conflict): ...
class InvalidInput(DomainError): ...


EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):
            raise InvalidInput(f"invalid coordinates ({self.lat}, {self.lon})")

    def distance_km(self, other: "Location") -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (self.lat, self.lon, other.lat, other.lon))
        a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Vehicle:
    model: str
    supported_types: frozenset[ConnectorType]

    def __post_init__(self) -> None:
        if not self.supported_types:
            raise InvalidInput("vehicle must support at least one connector type")


@dataclass(frozen=True)
class PromoCode:
    code: str
    percent_off: Decimal

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise InvalidInput("promo code must not be blank")
        # is_finite first: comparing a NaN Decimal raises.
        if not (self.percent_off.is_finite() and 0 < self.percent_off <= 100):
            raise InvalidInput(f"percent_off must be in (0, 100], got {self.percent_off}")

    def apply(self, amount: Decimal) -> Decimal:
        return amount * (100 - self.percent_off) / 100


@dataclass(frozen=True)
class Bill:
    energy_cost: Decimal
    multiplier: Decimal
    minimum_applied: bool
    discount: Decimal
    total: Decimal


@dataclass
class Connector:
    id: int
    type: ConnectorType
    power_kw: float
    status: ConnectorStatus = ConnectorStatus.AVAILABLE

    def __post_init__(self) -> None:
        if not self.power_kw > 0:
            raise InvalidInput(f"power_kw must be positive, got {self.power_kw}")

    def _move(self, expected: ConnectorStatus, new: ConnectorStatus) -> None:
        if self.status is not expected:
            raise Conflict(f"connector {self.id} is {self.status}, expected {expected}")
        self.status = new

    def claim(self) -> None:
        self._move(ConnectorStatus.AVAILABLE, ConnectorStatus.OCCUPIED)

    def release(self) -> None:
        self._move(ConnectorStatus.OCCUPIED, ConnectorStatus.AVAILABLE)

    def take_out_of_service(self) -> None:
        self._move(ConnectorStatus.AVAILABLE, ConnectorStatus.OUT_OF_SERVICE)

    def return_to_service(self) -> None:
        self._move(ConnectorStatus.OUT_OF_SERVICE, ConnectorStatus.AVAILABLE)


@dataclass
class Station:
    id: int
    name: str
    location: Location
    connectors: list[Connector]

    def __post_init__(self) -> None:
        if not self.connectors:
            raise InvalidInput("station needs at least one connector")
        if len({c.id for c in self.connectors}) != len(self.connectors):
            raise InvalidInput("connector ids must be unique")

    def free_connectors(self, type: ConnectorType) -> list[Connector]:
        return [c for c in self.connectors if c.type is type and c.status is ConnectorStatus.AVAILABLE]

    def connector(self, connector_id: int) -> Connector:
        for c in self.connectors:
            if c.id == connector_id:
                return c
        raise NotFound(f"connector {connector_id} not found at station {self.id}")

    def load(self) -> Decimal:
        in_service = sum(c.status is not ConnectorStatus.OUT_OF_SERVICE for c in self.connectors)
        if not in_service:
            return Decimal(1)
        occupied = sum(c.status is ConnectorStatus.OCCUPIED for c in self.connectors)
        return Decimal(occupied) / Decimal(in_service)


@dataclass
class Driver:
    id: int
    name: str
    vehicle: Vehicle


@dataclass
class ChargingSession:
    id: int
    driver_id: int
    station_id: int
    connector_id: int
    billed_type: ConnectorType
    connector_type: ConnectorType
    promo: PromoCode | None
    multiplier: Decimal
    started_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE
    ended_at: datetime | None = None
    energy_kwh: Decimal | None = None
    bill: Bill | None = None

    def complete(self, energy_kwh: Decimal, bill: Bill, now: datetime) -> None:
        self._end(SessionStatus.COMPLETED, bill, now)
        self.energy_kwh = energy_kwh

    def cancel(self, bill: Bill, now: datetime) -> None:
        self._end(SessionStatus.CANCELLED, bill, now)

    def _end(self, status: SessionStatus, bill: Bill, now: datetime) -> None:
        if self.status is not SessionStatus.ACTIVE:
            raise Conflict(f"session {self.id} is {self.status}")
        self.status = status
        self.bill = bill
        self.ended_at = now
