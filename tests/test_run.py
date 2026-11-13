import json
from pathlib import Path

import run

FIXTURE = Path(__file__).parent / "fixtures" / "prices_2026-11-12.json"


def test_only_supramax_legs_without_an_lseg_rate_are_flagged():
    routes = [dict(leg="RB_INDIA", freight_source="LSEG BASIFEMX6 TCE (2026-11-12)"),
              dict(leg="SCRAP_US_TR", freight_source="Market fixture TCE (2026-11-06)"),
              dict(leg="C5", freight_source="Market fixture $/t (2026-11-06)")]
    assert run.mark_licence_gaps(routes)
    assert "private_note" not in routes[0]
    assert routes[1]["private_note"] == run.LICENCE_NOTE
    assert "private_note" not in routes[2]
    assert not run.mark_licence_gaps(routes[:1])


def test_a_licensed_price_replaces_the_typed_in_one(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "freight.csv").write_text(
        "type,key,label,unit,value,ws_flat_rate,date,source,comment\n"
        "price,PETCOKE_USG,Petcoke FOB US Gulf,$/t,92.5,,2026-11-11,LSEG CPAAF00,\n", encoding="utf-8")
    out = tmp_path / "site"
    run.main(["--prices-file", str(FIXTURE), "--private", str(private), "--out", str(out), "--no-history"])
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    petcoke = next(p for p in snap["prices"] if p["key"] == "PETCOKE_USG")
    assert petcoke["value"] == 92.5
    assert petcoke["source"].startswith("LSEG CPAAF00")


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_a_live_run_builds_from_the_feed(tmp_path, monkeypatch):
    """The path the daily workflow takes: fronts and curves from the feed, nothing read from a file."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_root = {(c["exchange"], c["root"]): raw["curves"][key] for key, c in run.prices.CURVES.items()}
    bar = 1794438000                                   # 12 November session on CME

    def post(url, json=None, **kwargs):
        if "symbols" in json:                          # front months
            rows = [{"s": symbol, "d": [raw["prices"][key]["value"], None, bar, bar - 86400]}
                    for key, (symbol, _, _) in run.prices.TRADINGVIEW.items() if key in raw["prices"]]
            return Reply({"data": rows})
        exchange, pattern = json["filter"][0]["right"], json["filter"][1]["right"]
        root = pattern[1:pattern.index("[")]
        rows = [{"d": [c["contract"], c["close"], int(c["expiry"].replace("-", "")), c["close"], bar, c["close"],
                       bar - 86400]} for c in by_root[(exchange, root)]]
        return Reply({"data": rows})

    monkeypatch.setattr(run.prices.requests, "post", post)
    monkeypatch.setattr(run.prices, "fetch_yahoo", lambda: {"EURUSD": raw["prices"]["EURUSD"]})
    out = tmp_path / "site"
    run.main(["--offline", "--no-history", "--out", str(out)])
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    brent = next(p for p in snap["prices"] if p["key"] == "BRENT")
    assert brent["symbol"].startswith("BRN") and len(brent["symbol"]) == 8      # a named contract, not BRN1!
    assert all(r.get("margin") is not None for r in snap["routes"])
    for page in ("index.html", "bulk.html", "freight.html", "ideas.html"):
        assert (out / page).stat().st_size > 20_000
