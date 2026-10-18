from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from ev.domain import Bill, ConnectorType, InvalidInput, PromoCode

CENT = Decimal("0.01")


def q(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)  # the default context is HALF_EVEN


@dataclass(frozen=True)
class Tariff:
    minimum: Decimal
    slabs: tuple[tuple[Decimal | None, Decimal], ...]

    def energy_cost(self, kwh: Decimal) -> Decimal:
        """Marginal slabs: each band's rate applies only to the energy inside it."""
        cost, lower = Decimal(0), Decimal(0)
        for upper, rate in self.slabs:
            if kwh <= lower:
                break
            top = kwh if upper is None else min(kwh, upper)
            cost += (top - lower) * rate
            lower = top
        return cost


TARIFFS: dict[ConnectorType, Tariff] = {
    ConnectorType.DC: Tariff(Decimal("150.00"), ((Decimal(10), Decimal(20)), (Decimal(25), Decimal(14)), (None, Decimal(9)))),
    ConnectorType.AC: Tariff(Decimal("100.00"), ((Decimal(10), Decimal(15)), (Decimal(25), Decimal(10)), (None, Decimal(6)))),
}
MAX_SESSION_KWH = Decimal(1000)
NO_SHOW_FEE = Decimal("50.00")


def price(tariff: Tariff, energy_kwh: Decimal, multiplier: Decimal = Decimal(1), promo: PromoCode | None = None) -> Bill:
    # is_finite first: comparing a NaN Decimal raises InvalidOperation
    if not (energy_kwh.is_finite() and 0 <= energy_kwh <= MAX_SESSION_KWH):
        raise InvalidInput(f"energy_kwh must be between 0 and {MAX_SESSION_KWH}")
    energy_cost = q(tariff.energy_cost(energy_kwh))
    priced = q(energy_cost * multiplier)
    minimum_applied = priced < tariff.minimum
    subtotal = max(priced, tariff.minimum)
    total = q(promo.apply(subtotal)) if promo else subtotal
    return Bill(energy_cost, multiplier, minimum_applied, subtotal - total, total)


def no_show_bill() -> Bill:
    return Bill(Decimal("0.00"), Decimal(1), False, Decimal("0.00"), NO_SHOW_FEE)


PeakMultiplier = Callable[[datetime, Decimal], Decimal]  # (now, station load 0..1) -> multiplier
IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset; avoids a tzdata dependency
PEAK_HOURS = range(18, 22)  # 18:00–21:59 IST
PEAK_FACTOR = Decimal("1.5")


def no_peak(now: datetime, load: Decimal) -> Decimal:
    return Decimal(1)


def by_time(now: datetime, load: Decimal) -> Decimal:
    return PEAK_FACTOR if now.astimezone(IST).hour in PEAK_HOURS else Decimal(1)


def by_load(now: datetime, load: Decimal) -> Decimal:
    return q(Decimal(1) + load / 2)  # 1.00 (idle) … 1.50 (full)


PEAKS: dict[str, PeakMultiplier] = {"none": no_peak, "time": by_time, "load": by_load}
