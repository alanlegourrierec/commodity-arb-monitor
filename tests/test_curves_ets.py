import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from arb import bulk, calc
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


def test_a_monthly_index_is_pinned_to_the_middle_of_its_month(data):
    prices, curves = data
    ctx = Context(prices, {}, curves, TODAY)
    assert ctx.at("JKM", 10) == 31.365                 # before 15 December, the nearest contract
    assert ctx.at("JKM", 33) == 31.365                 # 15 December is the December contract
    assert ctx.at("JKM", 64) == 30.495                 # 15 January is the January contract
    # 3 January is 19 of the 31 days between the two
    assert ctx.at("JKM", 52) == pytest.approx(31.365 + (30.495 - 31.365) * 19 / 31)
    assert ctx.at("API2", 3) == pytest.approx(137.4)   # 15 November, the November contract
    assert ctx.at("API2", 18) == pytest.approx(137.7)  # 30 November, halfway to December at 138


def test_a_future_is_interpolated_between_expiries(data):
    prices, curves = data
    ctx = Context(prices, {}, curves, TODAY)
    assert ctx.at("WTI_HOUSTON", 5) == 89.34           # before the December contract expires on the 23rd
    # 30 days out is 12 December, 19 of the 29 days between the December and January expiries
    assert ctx.at("WTI_HOUSTON", 30) == pytest.approx(89.34 + (88.78 - 89.34) * 19 / 29)
    assert ctx.at("WTI_HOUSTON", 900) == 83.94         # past the last contract


def test_a_future_that_just_expired_stays_as_the_left_end(data):
    prices, curves = data
    gone = [dict(contract="ULSX2026", close=1341.25, expiry="2026-11-10")]
    ctx = Context(prices, {}, dict(curves, GASOIL_ICE=gone + curves["GASOIL_ICE"]), TODAY)
    # 25 November sits halfway between the November expiry and the December one
    assert ctx.at("GASOIL_ICE", 13) == pytest.approx((1341.25 + 1249.75) / 2)
    assert ctx.at("GASOIL_ICE", 30) == Context(prices, {}, curves, TODAY).at("GASOIL_ICE", 30)
    old = [dict(contract="ULSV2026", close=1500.0, expiry="2026-10-12")]
    assert Context(prices, {}, dict(curves, GASOIL_ICE=old + curves["GASOIL_ICE"]), TODAY).at("GASOIL_ICE", 0) == 1249.75


def test_a_monthly_contract_still_prices_its_month_after_it_expires(data):
    prices, curves = data
    later = date(2026, 11, 28)                         # December TTF stopped trading on the 27th
    ctx = Context(prices, {}, curves, later)
    assert ctx.at("TTF_EUR", 17) == 92.259             # 15 December
    without = dict(curves, TTF=curves["TTF"][1:])
    assert Context(prices, {}, without, later).at("TTF_EUR", 17) == 92.081


def test_margins_do_not_step_when_contracts_roll(data):
    """With every price frozen, moving the date only slides each cargo along its curve."""
    prices, curves = data
    limit = {"$/bbl": 0.3, "$/MMBtu": 0.15, "$/t": 3.0}
    last = {}
    for n in range(75):
        today = TODAY + timedelta(days=n)
        rows = calc.evaluate(prices, {}, curves, today)["routes"] + bulk.evaluate(prices, {}, curves, today)["routes"]
        for r in rows:
            if r.get("margin") is None:
                continue
            if r["route_id"] in last:
                # the Midwest aluminium premium falls about 170 $/t in a month, so metals slide faster
                most = 8.0 if r["family"] == "Metals" else limit.get(r["unit"], 3.0)
                assert abs(r["margin"] - last[r["route_id"]]) < most, (r["route_id"], today)
            last[r["route_id"]] = r["margin"]


def test_a_benchmark_without_a_curve_keeps_its_front_price(data):
    prices, curves = data
    assert Context(prices, {}, curves, TODAY).at("SCRAP_TURKEY", 40) == prices["SCRAP_TURKEY"]["value"]


def test_purchase_and_sale_are_priced_for_loading_and_landing(data):
    prices, curves = data
    ctx = Context(prices, {}, curves, TODAY)
    row = by_id(calc.evaluate(prices, {}, curves, TODAY))["DIESEL_USGC_NWE"]
    assert (row["load_date"], row["land_date"]) == ("2026-12-12", "2026-12-28")
    land = 30 + row["laden_days"]
    assert row["dest_price"] == pytest.approx(ctx.at("GASOIL_ICE", 30), abs=1e-3)
    assert row["time_spread"] == pytest.approx(ctx.at("GASOIL_ICE", 30) - ctx.at("GASOIL_ICE", land), abs=1e-3)
    assert row["time_spread"] > 0                      # gasoil is backwardated, so time at sea costs money


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
