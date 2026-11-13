from arb import history


def test_same_day_rerun_replaces_rows(tmp_path):
    path = tmp_path / "history.csv"
    route = dict(route_id="A", route="A", family="Crude", unit="$/bbl", status="Open", margin=1.0,
                 freight=2.0, freight_source="Model", insurance=0.1, war_risk=0.0, tce_used=40000)
    history.update(path, [route], "2026-11-12")
    history.update(path, [dict(route, margin=1.5)], "2026-11-12")
    rows = history.update(path, [route], "2026-11-13")
    assert [(r["date"], float(r["margin"])) for r in rows] == [("2026-11-12", 1.5), ("2026-11-13", 1.0)]
