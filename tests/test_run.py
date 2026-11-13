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
