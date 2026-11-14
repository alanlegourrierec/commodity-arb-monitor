import csv
import json
import re
from datetime import date
from pathlib import Path

import pytest

import run
from arb import balance, calc, eia, flows, shipping
from arb.config import BULK_ROUTES, CURVES, ROUTES

ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "prices_2026-11-12.json"
TODAY = date(2026, 11, 14)


def _brent(closes, expiries=("2026-11-30", "2026-12-31", "2027-01-29", "2027-02-26", "2027-03-31", "2027-04-30")):
    codes = "FGHJKM"
    return [dict(contract=f"BRN{codes[i]}2027", close=c, expiry=expiries[i]) for i, c in enumerate(closes)]


def _ctx(curve):
    return calc.Context({"BRENT": dict(value=curve[0]["close"])}, {}, {"BRENT": curve}, TODAY)


def test_slope_is_read_from_the_loading_day_to_three_months_later():
    s = balance.slope(_ctx(_brent([100, 98, 96, 94, 92, 90])), "BRENT")
    assert s["state"] == "backwardation" and s["days"] == 90
    assert s["near"] == pytest.approx(99.10, abs=0.01)             # 14 December, between the first two contracts
    assert s["per_month"] == pytest.approx(-2.02, abs=0.01)
    assert s["pct"] == pytest.approx(-2.04, abs=0.01)


def test_a_curve_that_barely_moves_is_called_flat():
    assert balance.slope(_ctx(_brent([100, 100.1, 100.2, 100.3, 100.4, 100.5])), "BRENT")["state"] == "flat"
    assert balance.slope(_ctx(_brent([100, 101, 102, 103, 104, 105])), "BRENT")["state"] == "contango"


def test_a_curve_that_stops_too_early_gives_no_slope():
    assert balance.slope(_ctx(_brent([100, 98])), "BRENT") is None
    assert balance.slope(_ctx(_brent([100, 98])), "NOT_A_CURVE") is None


def test_a_section_takes_what_is_there_and_leaves_out_the_rest():
    curve = _brent([100, 98, 96, 94, 92, 90])
    notes = dict(written="2026-11-14", sections=[dict(
        id="crude", group="Energy", name="Crude", state="Tight", tone="tight", read="Short of oil. And more.",
        curves=["BRENT", "WTI"], flows=["GULF", "NOWHERE"], weekly=["crude_stocks", "missing"],
        routes=["R1", "R9"], rates=["TD3C", "FINANCING_RATE", "ABSENT"], chokepoints=["Hormuz", "Malacca"],
        signal={"weekly": "crude_stocks"}, facts={})])
    port_flows = {"GULF": dict(key="GULF", label="Gulf", measure="Tanker cargo loaded", calls=False, dark=True,
                               unit="Mt a week", week="2026-11-01", now=1.0, year_ago=9.0, change=-88.9)}
    weekly = dict(week="2026-11-06", series={"crude_stocks": dict(label="Commercial crude stocks", unit="Mbbl",
                                                                   week="2026-11-06", value=414.4, previous=409.8,
                                                                   year_ago=427.6)})
    routes = [dict(route_id="R1", route="A → B", status="Closed", margin=-1.0, unit="$/bbl", family="Crude",
                   vessel="VLCC", tce_used=300000.0, breakeven_tce=170000.0)]
    overrides = {"TD3C": dict(type="freight", label="VLCC MEG → China", value=1078714.0, unit="$/day",
                              date="2026-11-13", source="Baltic"),
                 "FINANCING_RATE": dict(type="param", label="Financing", value=5.0, unit="%", date="", source="")}
    straits = [dict(name="Hormuz", week="2026-11-08", tanker=1.0, tanker_chg=-97.7, tanker_index=2.8, bulk=1.6,
                    bulk_chg=-88.5, bulk_index=14.2)]
    out = balance.assemble(notes, _ctx(curve), {"BRENT": curve}, port_flows, weekly, routes, overrides, straits)
    s = out["sections"][0]
    assert [c["key"] for c in s["curves"]] == ["BRENT"] and [c["key"] for c in s["strips"]] == ["BRENT"]
    assert s["strips"][0]["points"][0] == dict(contract="BRNF2027", close=100, month="2027-01-01")
    assert [f["key"] for f in s["flows"]] == ["GULF"]
    assert [w["key"] for w in s["weekly"]] == ["crude_stocks"]
    assert [r["route_id"] for r in s["routes"]] == ["R1"] and s["routes"][0]["page"] == "energy.html"
    assert [r["key"] for r in s["rates"]] == ["TD3C"] and s["rates"][0]["licensed"]
    assert s["chokepoints"] == [dict(name="Hormuz", per_day=1.0, change=-97.7, index=2.8, week="2026-11-08")]
    assert s["signal"]["label"] == "US commercial crude stocks" and s["signal"]["change"] == pytest.approx(-3.09, abs=0.01)
    assert s["brief"] == "Short of oil."
    assert out["flow_week"] == "2026-11-01" and out["weekly_week"] == "2026-11-06"
    assert out["headline"]["tight"] == 1 and out["headline"]["steepest"]["key"] == "BRENT"
    assert out["headline"]["down"] is None                         # loadings only seen by AIS stay out of the headline


def test_nothing_to_assemble_without_the_notes():
    assert balance.assemble(None, _ctx(_brent([100, 98])), {}, {}, None, [], {}, []) is None
    assert balance.load_notes(ROOT / "data" / "no_such_file.json") is None


# ---- the notes file itself: typed by hand, so checked here

NOTES = json.loads((ROOT / "data" / "balance.json").read_text(encoding="utf-8"))


def _rate_keys():
    with open(ROOT / "data" / "freight_assumptions.csv", encoding="utf-8", newline="") as f:
        return {row["key"] for row in csv.DictReader(f) if row["type"] == "freight" and row["value"]}


def test_notes_point_at_things_that_exist():
    route_ids = {r["id"] for r in ROUTES + BULK_ROUTES}
    rates = _rate_keys()
    ids = [s["id"] for s in NOTES["sections"]]
    assert len(ids) == len(set(ids))
    for s in NOTES["sections"]:
        assert s["tone"] in ("tight", "long", "mixed"), s["id"]
        assert s["group"] in ("Energy", "Dry bulk", "Metals", "Freight"), s["id"]
        assert set(s.get("curves", [])) <= set(CURVES), s["id"]
        assert set(s.get("flows", [])) <= set(flows.GROUPS), s["id"]
        assert set(s.get("weekly", [])) <= set(eia.SERIES) | {"gas_storage"}, s["id"]
        assert set(s.get("routes", [])) <= route_ids, s["id"]
        assert set(s.get("rates", [])) <= rates, s["id"]
        assert set(s.get("chokepoints", [])) <= set(shipping.CHOKEPOINTS), s["id"]
        kind, key = next(iter(s["signal"].items()))
        known = dict(flow=flows.GROUPS, weekly=set(eia.SERIES) | {"gas_storage"}, route=route_ids,
                     chokepoint=shipping.CHOKEPOINTS)
        assert key in known[kind], s["id"]
        assert s.get("curves") or s.get("rates"), s["id"]          # every market has a curve or a rate to open on


def test_every_fact_is_a_sentence_with_a_source_when_it_has_a_link():
    for s in NOTES["sections"]:
        assert s["facts"], s["id"]
        for title, lines in s["facts"].items():
            assert lines, (s["id"], title)
            for f in lines:
                assert f["text"].endswith("."), f["text"]
                if f["url"]:
                    assert f["url"].startswith("https://") and f["source"], f["text"]


def test_the_prose_keeps_to_plain_punctuation():
    """The site is written without colons, semicolons or long dashes. Ratios such as 80:20 are fine."""
    for s in NOTES["sections"]:
        texts = [s["read"], s["state"], s.get("flows_note", "")] + [f["text"] for lines in s["facts"].values() for f in lines]
        for text in texts:
            assert not re.search(r"[;—–]", text), text
            assert ":" not in re.sub(r"\d{1,2}:\d{2}", "", text), text


# ---- the pages

def test_the_site_builds_with_the_new_pages(tmp_path):
    out = tmp_path / "site"
    run.main(["--prices-file", str(FIXTURE), "--out", str(out), "--no-history"])
    page = (out / "balance.html").read_text(encoding="utf-8")
    for s in NOTES["sections"]:
        assert f'<section class="desk market" id="{s["id"]}">' in page
        assert s["read"].replace("&", "&amp;") in page
    assert "<h3>Ports behind the lines</h3>" in page and "<h3>US weekly balance</h3>" in page
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Every arb, by desk" in home and 'href="balance.html#crude"' in home
    assert (out / "energy.html").stat().st_size > 20_000
    for name in ("index.html", "energy.html", "bulk.html", "freight.html", "balance.html", "ideas.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert 'class="subnav"' in html, name
        assert f'href="{name}" class="on"' in html, name          # the menu marks the page it is on


def test_the_page_still_builds_when_the_measured_data_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(run.flows, "load_flows", lambda path: None)
    monkeypatch.setattr(run.eia, "load_weekly", lambda path: None)
    out = tmp_path / "site"
    run.main(["--prices-file", str(FIXTURE), "--out", str(out), "--no-history"])
    page = (out / "balance.html").read_text(encoding="utf-8")
    assert 'id="crude"' in page and "Forward curve" in page
    assert "<h3>Ports behind the lines</h3>" not in page and "<h3>US weekly balance</h3>" not in page
