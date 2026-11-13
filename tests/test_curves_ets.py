import json
from datetime import date
from pathlib import Path

import pytest

from arb import calc
from arb.calc import Context

FIXTURE = Path(__file__).parent / "fixtures" / "prices_2026-11-12.json"
TODAY = date(2026, 11, 12)


@pytest.fixture
def data():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prices = {k: dict(v, value=float(v["value"])) for k, v in raw["prices"].items()}
    return prices, raw["curves"]


def by_id(result):
    return {r["route_id"]: r for r in result["routes"]}


def test_delivery_curve_uses_the_arrival_month(data):
    prices, curves = data
    ctx = Context(prices, {}, curves, TODAY)
    assert ctx.forward("JKM", 10) == (31.325, 31.325)          # lands in November: front (December) contract
    assert ctx.forward("JKM", 55)[1] == 28.665                # lands in January
    assert ctx.forward("API2", 25)[1] == 137.45               # December contract


def test_expiry_curve_uses_the_front_slope(data):
    prices, curves = data
    ctx = Context(prices, {}, curves, TODAY)
    front, later = ctx.forward("BRENT", 30)
    assert front == 93.48
    assert later == pytest.approx(93.48 + (91.33 - 93.48) / 30 * 30)


def test_backwardation_is_a_cost_on_the_margin(data):
    prices, curves = data
    flat = by_id(calc.evaluate(prices, {}, {}, TODAY))["CRUDE_USGC_NWE"]
    curved = by_id(calc.evaluate(prices, {}, curves, TODAY))["CRUDE_USGC_NWE"]
    assert curved["time_spread"] > 0
    assert flat["margin"] - curved["margin"] == pytest.approx(curved["time_spread"], abs=1e-3)


def test_eu_ets_only_on_european_legs(data):
    prices, _ = data
    rows = by_id(calc.evaluate(prices, {}))
    assert rows["CRUDE_USGC_NWE"]["ets"] > 0
    assert rows["CRUDE_USGC_ASIA"]["ets"] == 0


def test_eu_ets_scales_with_the_allowance_price(data):
    prices, _ = data
    base = by_id(calc.evaluate(prices, {}))["CRUDE_USGC_NWE"]["ets"]
    prices["EUA"] = dict(prices["EUA"], value=prices["EUA"]["value"] * 2)
    assert by_id(calc.evaluate(prices, {}))["CRUDE_USGC_NWE"]["ets"] == pytest.approx(2 * base, rel=1e-3)


def test_trip_legs_have_no_ballast(data):
    prices, _ = data
    ctx = Context(prices, {})
    trip = calc.round_voyage(ctx, "USG_INDIA")
    assert trip["sea_days"] == pytest.approx(trip["laden_days"])
    round_trip = calc.round_voyage(ctx, "TD25")
    assert round_trip["sea_days"] == pytest.approx(2 * round_trip["laden_days"])
