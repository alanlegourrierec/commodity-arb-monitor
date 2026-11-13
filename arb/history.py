import csv
from pathlib import Path

FIELDS = ["date", "route_id", "route", "family", "unit", "status", "margin", "freight", "freight_source",
          "insurance", "war_risk", "tce"]


def load(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def update(path, routes, day):
    """Replace today's rows (re-runs on the same day are idempotent) and save."""
    rows = [r for r in load(path) if r["date"] != day]
    for r in routes:
        if r.get("margin") is None:
            continue
        rows.append(dict(date=day, route_id=r["route_id"], route=r["route"], family=r["family"], unit=r["unit"],
                         status=r["status"], margin=r["margin"], freight=r.get("freight"),
                         freight_source=r.get("freight_source", ""), insurance=r.get("insurance"),
                         war_risk=r.get("war_risk"), tce=r.get("tce_used")))
    rows.sort(key=lambda r: (r["date"], r["route_id"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows
