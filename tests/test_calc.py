import json
from datetime import date
from pathlib import Path

import pytest

from arb import calc
from arb.config import BBL_PER_TONNE

FIXTURE = Path(__file__).parent / "fixtures" / "prices_2026-11-12.json"


@pytest.fixture
def prices():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {k: dict(v, value=float(v["value"])) for k, v in raw["prices"].items()}


def override(value, unit, flat_rate=None, kind="freight"):
    return dict(type=kind, value=value, unit=unit, flat_rate=flat_rate, date="2026-11-12", source="test", label="")


def by_id(result):
    return {r["route_id"]: r for r in result["routes"]}


def test_lng_components_add_up_to_delivered_price(prices):
    for r in by_id(calc.evaluate(prices, {})).values():
        if r["family"] != "LNG":
            continue
        parts = ["netback", "hire", "boil_off", "canal", "port", "ballast_fuel", "ets", "time_spread", "insurance",
                 "financing"]
        assert sum(r[p] for p in parts) == pytest.approx(r["dest_price"], abs=1e-3)


def test_margin_is_breakeven_minus_freight(prices):
    for r in by_id(calc.evaluate(prices, {})).values():
        if r.get("margin") is None:
            continue
        assert r["margin"] == pytest.approx(r["breakeven_freight"] - r["freight"], abs=1e-3)


def test_lump_sum_override(prices):
    routes = by_id(calc.evaluate(prices, {"TD22": override(37_687_500, "$/voyage")}))
    expected = 37_687_500 / (270_000 * BBL_PER_TONNE["wti"])
    assert routes["CRUDE_USGC_ASIA"]["freight"] == pytest.approx(expected, rel=1e-4)
    assert routes["CRUDE_USGC_ASIA"]["freight_source"].startswith("test lump sum")


def test_worldscale_override(prices):
    routes = by_id(calc.evaluate(prices, {"TD25": override(185, "WS", flat_rate=19.5)}))
    td25 = routes["CRUDE_USGC_NWE"]
    assert td25["freight"] - td25["ets"] == pytest.approx(185 / 100 * 19.5 / BBL_PER_TONNE["wti"], rel=1e-4)


def test_war_risk_on_td3c_raises_murban_delivered(prices):
    base = by_id(calc.evaluate(prices, {}))["CRUDE_USGC_ASIA"]["dest_price"]
    war = by_id(calc.evaluate(prices, {"WAR_TD3C": override(1.5, "% of hull", kind="param")}))
    bump = 0.015 * 140e6 / (270_000 * BBL_PER_TONNE["murban"])
    assert war["CRUDE_USGC_ASIA"]["dest_price"] - base == pytest.approx(bump, abs=1e-3)


def test_missing_eurobob_flags_the_gasoline_route(prices):
    del prices["EUROBOB"]
    assert by_id(calc.evaluate(prices, {}))["GASOLINE_NWE_USAC"]["status"] == "Missing price"


def test_usgc_diesel_diff_comes_from_the_up_down_future(prices):
    route = by_id(calc.evaluate(prices, {}))["DIESEL_USGC_NWE"]
    gal_t = BBL_PER_TONNE["diesel"] * 42
    pipeline = (prices["ULSD_NYH"]["value"] + prices["ULSD_USGC_UPDOWN"]["value"]) * gal_t
    assert route["origin_price"] + route["rvo_credit"] == pytest.approx(pipeline, abs=1e-2)


def test_wti_into_rotterdam_is_valued_off_dated_brent(prices):
    route = by_id(calc.evaluate(prices, {}))["CRUDE_USGC_NWE"]
    assert route["dest_price"] == pytest.approx(prices["DATED_BRENT"]["value"])
    del prices["DATED_BRENT"]
    assert by_id(calc.evaluate(prices, {}))["CRUDE_USGC_NWE"]["dest_price"] == pytest.approx(prices["BRENT"]["value"])


def test_breakeven_tce_gives_zero_margin(prices):
    first = by_id(calc.evaluate(prices, {}))["CRUDE_USGC_NWE"]
    again = by_id(calc.evaluate(prices, {"TD25": override(first["breakeven_tce"], "$/day")}))
    assert again["CRUDE_USGC_NWE"]["margin"] == pytest.approx(0, abs=1e-3)


def test_rin_obligation_follows_the_epa_standards(prices):
    ctx = calc.Context(prices, {}, today=date(2026, 11, 12))
    expected = (0.1550 - 0.0642) * 2.03 + 0.0079 * 2.265 + 0.0524 * 2.095 + (0.0642 - 0.0524 - 0.0079) * 2.045
    assert calc.rvo_per_gallon(ctx) == pytest.approx(expected, abs=1e-6)
    del prices["RIN_D3"]
    assert calc.rvo_per_gallon(calc.Context(prices, {})) is None


def test_an_export_cargo_is_worth_the_pipeline_price_less_rins(prices):
    route = by_id(calc.evaluate(prices, {}, today=date(2026, 11, 12)))["DIESEL_USGC_NWE"]
    ctx = calc.Context(prices, {}, today=date(2026, 11, 12))
    assert route["rvo_credit"] == pytest.approx(calc.rvo_per_gallon(ctx) * BBL_PER_TONNE["diesel"] * 42, abs=1e-3)


def test_an_importer_into_new_york_pays_duty_and_rins(prices):
    r = by_id(calc.evaluate(prices, {}, today=date(2026, 11, 12)))["GASOLINE_NWE_USAC"]
    assert r["duty"] == pytest.approx(0.525 * BBL_PER_TONNE["gasoline"], abs=1e-3)
    assert r["rvo"] > 100
    costs = r["freight"] + r["insurance"] + r["losses"] + r["duty"] + r["rvo"] + r["seller_costs"] + r["time_spread"]
    assert r["margin"] == pytest.approx(r["dest_price"] - r["origin_price"] - costs, abs=1e-3)


def test_a_cif_sale_leaves_the_transit_loss_with_the_buyer(prices):
    rows = by_id(calc.evaluate(prices, {}))
    for rid, pays_loss in (("CRUDE_USGC_NWE", False), ("CRUDE_USGC_ASIA", True)):
        r = rows[rid]
        costs = r["fobbing"] + r["freight"] + r["insurance"] + r["seller_costs"] + r["time_spread"]
        costs += r["losses"] if pays_loss else 0.0
        assert r["margin"] == pytest.approx(r["dest_price"] - r["origin_price"] - costs, abs=1e-3)


def test_murban_from_fujairah_is_costed_at_the_houston_daily_hire(prices):
    row = by_id(calc.evaluate(prices, {"TD22": override(37_687_500, "$/voyage")}))["CRUDE_USGC_ASIA"]
    ctx = calc.Context(prices, {})
    voyage = calc.round_voyage(ctx, "TD3C", tce=row["tce_used"])
    freight = voyage["cost"] / 270_000 / BBL_PER_TONNE["murban"]
    assert row["dest_price"] == pytest.approx(prices["MURBAN"]["value"] + freight, abs=1e-2)
    assert row["tce_used"] > 250_000          # the lump sum implies a daily hire far above a normal market
