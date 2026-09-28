from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ev.domain import ConnectorType, InvalidInput, PromoCode
from ev.pricing import TARIFFS, by_load, by_time, no_peak, price

DC = TARIFFS[ConnectorType.DC]
AC = TARIFFS[ConnectorType.AC]
T0 = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)


def promo(percent: str) -> PromoCode:
    return PromoCode("SAVE", Decimal(percent))


@pytest.mark.parametrize(
    "kwh, total, applied",
    [
        ("0", "150.00", True), ("5", "150.00", True), ("7.5", "150.00", False), ("10", "200.00", False),
        ("10.001", "200.01", False), ("10.5", "207.00", False), ("25", "410.00", False),
        ("25.01", "410.09", False), ("26", "419.00", False), ("100", "1085.00", False),
    ],
)
def test_dc_tariff(kwh, total, applied):
    bill = price(DC, Decimal(kwh))
    assert (bill.total, bill.minimum_applied) == (Decimal(total), applied)


@pytest.mark.parametrize(
    "kwh, total",
    [
        ("0", "100.00"), ("5", "100.00"), ("10", "150.00"), ("10.5", "155.00"), ("12.5", "175.00"),
        ("20", "250.00"), ("25", "300.00"), ("26", "306.00"), ("100", "750.00"),
    ],
)
def test_ac_tariff(kwh, total):
    assert price(AC, Decimal(kwh)).total == Decimal(total)


@pytest.mark.parametrize(
    "tariff, kwh, percent, total, discount",
    [
        (DC, "5", "10", "135.00", "15.00"),
        (DC, "20", "10", "306.00", "34.00"),
        (DC, "5", "100", "0.00", "150.00"),
        (AC, "12.5", "10", "157.50", "17.50"),
    ],
)
def test_promo_discount(tariff, kwh, percent, total, discount):
    bill = price(tariff, Decimal(kwh), promo=promo(percent))
    assert (bill.total, bill.discount) == (Decimal(total), Decimal(discount))


@pytest.mark.parametrize(
    "code, percent",
    [("SAVE", "0"), ("SAVE", "-5"), ("SAVE", "101"), ("SAVE", "NaN"), ("", "10"), ("   ", "10")],
)
def test_promo_validation(code, percent):
    PromoCode("SAVE", Decimal("100"))
    with pytest.raises(InvalidInput):
        PromoCode(code, Decimal(percent))


@pytest.mark.parametrize(
    "kwh, percent, total, applied",
    [
        ("4", None, "150.00", True),
        ("5", None, "150.00", False),
        ("20", None, "510.00", False),
        ("5", "10", "135.00", False),
    ],
)
def test_peak_before_minimum(kwh, percent, total, applied):
    bill = price(DC, Decimal(kwh), Decimal("1.5"), promo(percent) if percent else None)
    assert (bill.total, bill.minimum_applied) == (Decimal(total), applied)


def test_rounding():
    bill = price(DC, Decimal("7.50025"))
    assert (bill.energy_cost, bill.total) == (Decimal("150.01"), Decimal("150.01"))

    bill = price(DC, Decimal("7.5025"), promo=promo("10"))
    assert (bill.energy_cost, bill.total, bill.discount) == (Decimal("150.05"), Decimal("135.05"), Decimal("15.00"))
    assert bill.discount + bill.total == max(bill.energy_cost * bill.multiplier, DC.minimum)


def test_tariff_grid():
    grid = [Decimal(i) / 40 for i in range(4001)]
    dc = [price(DC, kwh).total for kwh in grid]
    ac = [price(AC, kwh).total for kwh in grid]
    assert dc == sorted(dc)
    assert ac == sorted(ac)
    assert all(a <= d for a, d in zip(ac, dc))


@pytest.mark.parametrize("kwh", ["-1", "1000.001", "NaN", "Infinity"])
def test_energy_bounds(kwh):
    price(DC, Decimal("0"))
    price(DC, Decimal("1000"))
    with pytest.raises(InvalidInput):
        price(DC, Decimal(kwh))


@pytest.mark.parametrize(
    "rule, now, load, expected",
    [
        (by_time, T0.replace(hour=12, minute=29), Decimal(0), Decimal("1")),
        (by_time, T0.replace(hour=12, minute=30), Decimal(0), Decimal("1.5")),
        (by_time, T0.replace(hour=16, minute=29), Decimal(0), Decimal("1.5")),
        (by_time, T0.replace(hour=16, minute=30), Decimal(0), Decimal("1")),
        (by_load, T0, Decimal(0), Decimal("1.00")),
        (by_load, T0, Decimal(1) / 2, Decimal("1.25")),
        (by_load, T0, Decimal(1), Decimal("1.50")),
        (by_load, T0, Decimal(1) / 3, Decimal("1.17")),
        (no_peak, T0, Decimal(0), Decimal("1")),
    ],
)
def test_peak_rules(rule, now, load, expected):
    assert rule(now, load) == expected
