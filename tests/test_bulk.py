import json
from datetime import date
from pathlib import Path

import pytest

from arb import bulk, terms
from arb.config import BULK_PARAMS, LB_PER_TONNE

FIXTURE = Path(__file__).parent / "fixtures" / "prices_2026-11-12.json"


@pytest.fixture
def prices():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {k: dict(v, value=float(v["value"])) for k, v in raw["prices"].items()}


def override(value, unit, kind="freight"):
    return dict(type=kind, value=value, unit=unit, flat_rate=None, date="2026-11-12", source="test", label="")


def by_id(result):
    return {r["route_id"]: r for r in result["routes"]}


def test_iron_ore_freight_is_charged_on_wet_tonnes(prices):
    ore = by_id(bulk.evaluate(prices, {"C5": override(10.0, "$/t")}))["ORE_AUS_CHINA"]
    moisture = BULK_PARAMS["IRON_ORE_MOISTURE"][0]
    assert ore["freight"] == pytest.approx(10.0 / (1 - moisture), abs=1e-3)
    assert ore["netback"] == pytest.approx(ore["dest_price"] - ore["freight"] - ore["insurance"], abs=1e-3)


def test_brazil_pays_more_freight_than_australia(prices):
    rows = by_id(bulk.evaluate(prices, {}))
    assert rows["ORE_BRA_CHINA"]["freight"] > rows["ORE_AUS_CHINA"]["freight"]
    assert rows["ORE_BRA_CHINA"]["netback"] < rows["ORE_AUS_CHINA"]["netback"]


def test_coal_japan_is_valued_off_newcastle_plus_freight(prices):
    result = bulk.evaluate(prices, {"NEWC_JAPAN": override(12.0, "$/t")})
    japan = by_id(result)["COAL_RB_JAPAN"]
    discount = BULK_PARAMS["COAL_SA_DISCOUNT_JAPAN"][0]
    assert japan["dest_price"] == pytest.approx(prices["NEWC"]["value"] + 12.0 - discount, abs=1e-3)
    assert japan["margin"] == pytest.approx(japan["breakeven_freight"] - japan["freight"], abs=1e-3)


def test_aluminium_tariff_is_on_metal_value(prices):
    alu = by_id(bulk.evaluate(prices, {}))["ALU_LME_MIDWEST"]
    assert alu["tariff"] == pytest.approx(BULK_PARAMS["ALU_US_TARIFF"][0] * prices["LME_ALU"]["value"], abs=1e-2)
    assert alu["gross"] == pytest.approx(prices["MIDWEST_PREMIUM"]["value"] * LB_PER_TONNE, abs=1e-2)


def test_metal_costs_add_up(prices):
    for r in by_id(bulk.evaluate(prices, {})).values():
        if r["family"] != "Metals" or r.get("margin") is None:
            continue
        costs = ["origin_premium", "freight", "tariff", "loadout", "financing", "insurance"]
        assert r["gross"] - sum(r[c] for c in costs) == pytest.approx(r["margin"], abs=1e-2)


def test_rotterdam_premium_comes_from_the_cme_future(prices):
    row = by_id(bulk.evaluate(prices, {}))["ALU_LME_JAPAN"]
    assert row["origin_premium"] == pytest.approx(prices["ALU_DUP_RDM"]["value"])
    del prices["ALU_DUP_RDM"]
    row = by_id(bulk.evaluate(prices, {}))["ALU_LME_JAPAN"]
    assert row["origin_premium"] == BULK_PARAMS["ALU_ORIGIN_PREMIUM"][0]


def test_rotterdam_premium_override_moves_aluminium_margin_one_for_one(prices):
    base = by_id(bulk.evaluate(prices, {}))["ALU_LME_JAPAN"]
    cheap = by_id(bulk.evaluate(prices, {"ALU_ORIGIN_PREMIUM": override(100.0, "$/t", "param")}))["ALU_LME_JAPAN"]
    assert cheap["margin"] - base["margin"] == pytest.approx(base["origin_premium"] - 100.0, abs=1e-2)


def test_copper_ignores_the_aluminium_premium(prices):
    copper = by_id(bulk.evaluate(prices, {}))["COPPER_LME_COMEX"]
    assert copper["origin_premium"] == 0


def test_missing_price_is_flagged(prices):
    del prices["API4"]
    rows = by_id(bulk.evaluate(prices, {}))
    assert rows["COAL_RB_NWE"]["status"] == "Missing price"
    assert rows["COAL_RB_JAPAN"]["status"] == "Missing price"


def test_india_fuel_margin_is_room_against_the_next_cheapest(prices):
    rows = [r for r in bulk.evaluate(prices, {})["routes"] if r["family"] == "India fuel"]
    fresh = [r for r in rows if r.get("per_mkcal") is not None and not r["stale"]]
    cheapest = min(fresh, key=lambda r: r["per_mkcal"])
    second = sorted(r["per_mkcal"] for r in fresh)[1]
    assert cheapest["margin"] == pytest.approx((second - cheapest["per_mkcal"]) * cheapest["mkcal_per_t"], abs=1e-3)


def test_stale_manual_price_is_flagged(prices):
    prices["PETCOKE_USG"] = dict(value=79.0, unit="$/t", date="2026-06-03")
    rows = by_id(bulk.evaluate(prices, {}, today=date(2026, 11, 12)))
    assert rows["INDIA_PETCOKE"]["status"] == "Stale price"


def test_out_of_quota_duty_is_charged_on_the_cif_value(prices):
    rows = by_id(bulk.evaluate(prices, {}))
    q = rows["HRC_CN_EU_QUOTA"]
    cif = q["origin_price"] + q["freight"] + q["insurance"]
    gap = q["margin"] - rows["HRC_CN_EU_OUT"]["margin"]
    assert gap == pytest.approx(BULK_PARAMS["EU_STEEL_DUTY"][0] * cif, abs=1e-2)


def test_cbam_follows_the_eua_price(prices):
    base = by_id(bulk.evaluate(prices, {}))["HRC_CN_EU_QUOTA"]
    certs = 3.187 * 1.10 - 0.975 * 1.370
    assert base["cbam_certs"] == pytest.approx(certs, abs=1e-4)
    assert base["cbam"] == pytest.approx(certs * prices["EUA"]["value"] * prices["EURUSD"]["value"], rel=1e-3)
    prices["EUA"] = dict(prices["EUA"], value=prices["EUA"]["value"] * 2)
    assert by_id(bulk.evaluate(prices, {}))["HRC_CN_EU_QUOTA"]["cbam"] == pytest.approx(2 * base["cbam"], rel=1e-3)


def test_us_hrc_is_converted_from_short_tons(prices):
    row = by_id(bulk.evaluate(prices, {}))["HRC_EU_US"]
    assert row["dest_price"] == pytest.approx(prices["HRC_US"]["value"] * 1.10231, abs=1e-2)


def test_scrap_best_destination_is_marked_once_per_origin(prices):
    rows = [r for r in bulk.evaluate(prices, {})["routes"] if r["family"] == "Scrap"]
    origins = {r["route"].split(" → ")[0] for r in rows}
    assert sum(1 for r in rows if r.get("best_destination")) == len(origins)


def test_switching_price_equalises_coal_and_gas(prices):
    power = bulk.evaluate(prices, {})["power"]
    eua = power["switch_eua"]
    coal_co2 = power["coal_carbon"] / power["eua"]
    gas_co2 = power["gas_carbon"] / power["eua"]
    assert power["coal_fuel"] + coal_co2 * eua == pytest.approx(power["gas_fuel"] + gas_co2 * eua, abs=1e-3)


def test_us_coil_import_pays_the_discount_to_domestic(prices):
    row = by_id(bulk.evaluate(prices, {}))["HRC_EU_US"]
    discount = BULK_PARAMS["US_IMPORT_DISCOUNT_HRC"][0]
    assert row["margin"] == pytest.approx(row["dest_price"] - discount - row["delivered"] - row["financing"], abs=1e-3)


def test_european_coil_pays_to_reach_the_ship_and_duty_on_its_fob_value(prices):
    row = by_id(bulk.evaluate(prices, {}))["HRC_EU_US"]
    fob = prices["HRC_NWE"]["value"] + BULK_PARAMS["HRC_FOBBING_EU"][0]
    assert row["duty"] == pytest.approx(BULK_PARAMS["US_STEEL_TARIFF"][0] * fob, abs=1e-3)
    assert terms.ladder(row)["DDP"] == pytest.approx(row["delivered"], abs=1e-3)


def test_a_cfr_sale_leaves_the_insurance_with_the_buyer(prices):
    rows = by_id(bulk.evaluate(prices, {}))
    assert rows["COAL_RB_JAPAN"]["insurance"] == 0
    assert rows["COAL_RB_NWE"]["insurance"] > 0
    scrap = rows["SCRAP_US_TURKEY"]
    assert scrap["netback"] == pytest.approx(scrap["dest_price"] - scrap["freight"], abs=1e-3)


def test_ladder_adds_each_term_on_top_of_the_last(prices):
    row = by_id(bulk.evaluate(prices, {}))["COAL_RB_NWE"]
    steps = terms.ladder(row)["steps"]
    assert [s["term"] for s in steps] == ["FOB", "CFR", "CIF", "DAP", "DDP"]
    assert steps[0]["total"] == pytest.approx(row["origin_price"])
    assert steps[1]["total"] - steps[0]["total"] == pytest.approx(row["freight"], abs=1e-3)
    assert [s["seller"] for s in steps] == [True, True, True, False, False]      # sold CIF
    assert terms.beyond(row) == [("transit loss", pytest.approx(row["losses"], abs=1e-3))]


def test_netback_ladder_walks_back_from_cfr(prices):
    row = by_id(bulk.evaluate(prices, {}))["ORE_AUS_CHINA"]
    ladder = terms.ladder(row)
    assert ladder["CFR"] == row["dest_price"] and ladder["FOB"] == row["netback"]
