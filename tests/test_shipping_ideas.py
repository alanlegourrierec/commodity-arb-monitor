import json
from datetime import date
from pathlib import Path

import pytest

from arb import bulk, calc, ideas, shipping


def _day(d, name="Strait of Hormuz", tanker=10, bulk=3):
    return dict(date=d, portname=name, n_tanker=tanker, n_dry_bulk=bulk, n_total=tanker + bulk, capacity_tanker=1000)


def test_weekly_keeps_only_complete_weeks():
    rows = [_day(f"2026-11-{d:02d}") for d in range(2, 10)]      # Mon 2 Nov to Mon 9 Nov
    weeks = shipping.weekly(rows)["Hormuz"]
    assert [w["week"] for w in weeks] == ["2026-11-08"]
    assert weeks[0]["n_tanker"] == 70


def test_summary_compares_with_the_same_week_last_year():
    data = dict(weeks={"Hormuz": [dict(week="2025-11-09", n_tanker=350, n_dry_bulk=70, n_total=420, capacity_tanker=1),
                                  dict(week="2026-11-08", n_tanker=7, n_dry_bulk=14, n_total=21, capacity_tanker=1)]})
    row = shipping.chokepoint_summary(data)[0]
    assert row["tanker"] == 1 and row["tanker_ly"] == 50
    assert round(row["tanker_chg"]) == -98


def test_baseline_is_kept_once_computed():
    weeks = {"Hormuz": [dict(week=f"2025-12-{d:02d}", n_tanker=70, n_dry_bulk=7, n_total=77, capacity_tanker=1)
                        for d in (7, 14, 21, 28)] +
                       [dict(week=f"2026-01-{d:02d}", n_tanker=70, n_dry_bulk=7, n_total=77, capacity_tanker=1)
                        for d in (4, 11, 18, 25)]}
    first = shipping.baseline(weeks)
    assert first["values"]["Hormuz"]["n_tanker"] == 70
    later = shipping.baseline({"Hormuz": weeks["Hormuz"][-2:]}, first)
    assert later["values"]["Hormuz"]["n_tanker"] == 70


def test_carry_idea_locks_the_backwardation(tmp_path):
    curves = {"WTI": [dict(contract="CLZ2026", close=86.0, expiry="2026-11-20"),
                      dict(contract="CLF2027", close=85.0, expiry="2026-12-21"),
                      dict(contract="CLG2027", close=84.0, expiry="2027-01-20")]}
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "Crude", "title": "t",'
                    ' "economics": {"curve": "WTI", "near": "Z2026", "far": "G2027", "volume": 1000000, "unit": "$/bbl"},'
                    ' "good": {"delta": 1, "case": ""}, "bad": {"delta": -1, "case": ""}}]')
    e = ideas.load_commodity_ideas(path, [], curves)[0]["economics"]
    assert e["margin"] == 2.0
    assert e["pnl"] == 2.0
    assert (e["pnl_good"], e["pnl_bad"]) == (3.0, 1.0)


def _routes():
    raw = json.loads((Path(__file__).parent / "fixtures" / "prices_2026-11-12.json").read_text(encoding="utf-8"))
    px = {k: dict(v, value=float(v["value"])) for k, v in raw["prices"].items()}
    return calc.evaluate(px, {})["routes"] + bulk.evaluate(px, {})["routes"]


def test_a_deal_sheet_adds_up_to_the_route_margin():
    checked = 0
    for route in _routes():
        if route["status"] not in ("Open", "Marginal", "Closed") or route["family"] == "India fuel":
            continue
        eco = ideas._route_economics(route)
        assert eco["margin"] == pytest.approx(route["margin"], abs=2e-3), route["route_id"]
        assert eco["lines"][-1]["label"].startswith("Sell")
        checked += 1
    assert checked >= 12


def test_a_spot_lng_cargo_earns_the_gap_between_outlets(tmp_path):
    by_id = {r["route_id"]: r for r in _routes()}
    asia, europe = by_id["LNG_US_ASIA_PANAMA"], by_id["LNG_US_NWE"]
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "LNG", "title": "t", "route_id": "LNG_US_ASIA_PANAMA", "compare_route_id": "LNG_US_NWE",'
                    ' "buy_at": "alternative", "good": {"delta": 2, "case": ""}, "bad": {"delta": -2.66, "case": ""}}]')
    e = ideas.load_commodity_ideas(path, list(by_id.values()), {})[0]["economics"]
    dearer = europe["netback"] / asia["buy_price"] - 1            # the cargo costs more, so does its interest
    assert e["margin"] == pytest.approx(asia["netback"] - europe["netback"] - asia["financing"] * dearer, abs=2e-3)
    assert e["compare"] is None
    assert e["lines"][0]["value"] == pytest.approx(europe["netback"])


def test_margin_per_day_uses_the_days_the_cargo_is_tied_up(tmp_path):
    curves = {"WTI": [dict(contract="CLZ2026", close=86.0, expiry="2026-11-20"),
                      dict(contract="CLF2027", close=85.0, expiry="2026-12-21"),
                      dict(contract="CLG2027", close=84.0, expiry="2027-01-20")]}
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "Crude", "title": "t",'
                    ' "economics": {"curve": "WTI", "near": "Z2026", "far": "G2027", "volume": 1000000, "unit": "$/bbl"},'
                    ' "good": {"delta": 1, "case": ""}, "bad": {"delta": -1, "case": ""}}]')
    e = ideas.load_commodity_ideas(path, [], curves)[0]["economics"]
    assert e["days"] == 61
    assert e["per_day"] == pytest.approx(2.0 / 61)
    assert e["pnl_day"] == pytest.approx(2.0 / 61 * 1e6)


def test_a_storage_idea_stays_on_its_contracts(tmp_path):
    curves = {"WTI": [dict(contract="CLF2027", close=85.0, expiry="2026-12-21"),
                      dict(contract="CLG2027", close=84.0, expiry="2027-01-20"),
                      dict(contract="CLH2027", close=83.0, expiry="2027-02-22")]}
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "Crude", "title": "t",'
                    ' "economics": {"curve": "WTI", "near": "Z2026", "far": "G2027", "volume": 1000000, "unit": "$/bbl"},'
                    ' "good": {"delta": 1, "case": ""}, "bad": {"delta": -1, "case": ""}}]')
    idea = ideas.load_commodity_ideas(path, [], curves)[0]
    assert idea["economics"] is None                  # December has expired, January does not take its place
    assert idea["expired"] == "December 2026"


def test_a_bad_case_can_reroute_the_cargo_or_count_days_of_delay(tmp_path):
    by_id = {r["route_id"]: r for r in _routes()}
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "LNG", "title": "t", "route_id": "LNG_US_ASIA_PANAMA", "compare_route_id": "LNG_US_NWE",'
                    ' "buy_at": "alternative", "good": {"delta": 1, "case": ""},'
                    ' "bad": {"route_id": "LNG_US_ASIA_CAPE", "case": ""}},'
                    ' {"desk": "Coal", "title": "t", "route_id": "COAL_RB_JAPAN", "good": {"delta": 1, "case": ""},'
                    ' "bad": {"delta": -5, "delay_days": 10, "case": ""}}]')
    lng, coal = [i["economics"] for i in ideas.load_commodity_ideas(path, list(by_id.values()), {})]
    cape = ideas._diversion(by_id["LNG_US_ASIA_CAPE"], by_id["LNG_US_NWE"])
    assert lng["bad"] == pytest.approx(cape["margin"])
    assert lng["bad"] < lng["margin"]
    assert coal["bad"] == pytest.approx(coal["margin"] - 5 - 10 * by_id["COAL_RB_JAPAN"]["delay_day"])


def test_an_idea_with_a_deadline_stops_being_priced_after_it(tmp_path):
    by_id = {r["route_id"]: r for r in _routes()}
    path = tmp_path / "ideas.json"
    path.write_text('[{"desk": "Base metals", "title": "t", "route_id": "COPPER_LME_COMEX", "until": "2026-11-17",'
                    ' "good": {"delta": 1, "case": ""}, "bad": {"delta": -1, "case": ""}}]')
    load = lambda day: ideas.load_commodity_ideas(path, list(by_id.values()), {}, day)[0]
    assert load(date(2026, 11, 17))["economics"] is not None
    late = load(date(2026, 11, 18))
    assert late["economics"] is None and late["missed"]
