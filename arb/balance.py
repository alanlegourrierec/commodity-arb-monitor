"""Supply and demand, one section per market, ships included.

Each section stacks three kinds of evidence. The forward curve is read from the morning's prices.
Port loadings, the US weekly balance and chokepoint traffic are measured, and refreshed by the run.
The dated facts are typed into data/balance.json with a source on every line, and stay as written
until someone edits them. The state of a market (tight, long, split) is a judgment written with the
facts. It is not computed.
"""

import json
from datetime import timedelta
from pathlib import Path

from . import eia, flows
from .config import CURVES, LICENSED, LOAD_LEAD_DAYS
from .prices import contract_month

SLOPE_DAYS = 90            # the curve is read from the loading day to three months later
FLAT = 0.15                # % a month under which a curve is called flat
ENERGY = ("Crude", "Products", "LNG")


def load_notes(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def slope(ctx, key):
    """What a curve gains or loses a month over the three months after loading."""
    if key not in CURVES:
        return None
    points = ctx.points(key)
    if len(points) < 2:
        return None
    start = ctx.today + timedelta(days=LOAD_LEAD_DAYS)
    days = (min(start + timedelta(days=SLOPE_DAYS), points[-1][0]) - start).days
    if days < 20:                              # the curve stops too early to say anything
        return None
    near, far = ctx.at(key, LOAD_LEAD_DAYS), ctx.at(key, LOAD_LEAD_DAYS + days)
    if not near or far is None:
        return None
    per_month = (far - near) / days * 30
    pct = per_month / near * 100
    state = "flat" if abs(pct) < FLAT else "backwardation" if pct < 0 else "contango"
    return dict(key=key, label=CURVES[key]["label"], unit=CURVES[key]["unit"], near=near, far=far, days=days,
                per_month=per_month, pct=pct, state=state)


def strip(curves, key):
    """The contracts of a curve by delivery month, for the chart."""
    rows = sorted(curves.get(key) or [], key=lambda c: c["expiry"])
    if len(rows) < 2 or key not in CURVES:
        return None
    points = [dict(contract=c["contract"], close=c["close"], month="%04d-%02d-01" % contract_month(c["contract"]))
              for c in rows]
    return dict(key=key, label=CURVES[key]["label"], points=points)


def _route(row):
    return dict(route_id=row["route_id"], route=row["route"], status=row["status"], margin=row.get("margin"),
                unit=row["unit"], vessel=row.get("vessel"), tce=row.get("tce_used"), breakeven=row.get("breakeven_tce"),
                page="energy.html" if row["family"] in ENERGY else "bulk.html")


def _rate(key, overrides):
    o = overrides.get(key)
    if not o or o.get("type") != "freight":
        return None
    return dict(key=key, label=o["label"], value=o["value"], unit=o["unit"], date=o["date"], source=o["source"],
                licensed=key in LICENSED)


def _signal(spec, port_flows, weekly, routes, straits, ship):
    """The one measured number shown next to a market in the summary table."""
    if not spec:
        return None
    kind, key = next(iter(spec.items()))
    if kind == "flow" and key in port_flows:
        f = port_flows[key]
        return dict(kind=kind, label=f"{f['measure']}, {f['label']}", value=f["now"], unit=f["unit"], change=f["change"],
                    calls=f["calls"], dark=f["dark"])
    if kind == "weekly":
        row = next(iter(eia.rows(weekly, [key])), None)
        return row and dict(kind=kind, label="US " + row["label"][0].lower() + row["label"][1:], value=row["value"],
                            unit=row["unit"], change=row["on_year"], change_unit=row["on_year_unit"])
    if kind == "route" and key in routes:
        return dict(kind=kind, **_route(routes[key]))
    if kind == "chokepoint" and key in straits:
        c = straits[key]
        return dict(kind=kind, label=f"{key}, {'bulk carriers' if ship == 'bulk' else 'tankers'}",
                    value=c[ship], change=c[ship + "_chg"], dark=key == "Hormuz")
    return None


def assemble(notes, ctx, curves, port_flows, weekly, routes, overrides, chokepoints):
    """Sections of the page, each with whatever of its evidence is available today.

    `port_flows` is flows.summary(), `weekly` the EIA file, `routes` every route of the site and
    `chokepoints` shipping.chokepoint_summary(). A missing source leaves its block out of the section.
    """
    if not notes:
        return None
    by_id = {r["route_id"]: r for r in routes}
    straits = {c["name"]: c for c in chokepoints or []}
    port_flows = port_flows or {}
    sections = []
    for note in notes["sections"]:
        ship = note.get("ship", "tanker")
        s = dict(note)
        s["curves"] = [c for c in (slope(ctx, k) for k in note.get("curves", [])) if c]
        s["strips"] = [c for c in (strip(curves, k) for k in note.get("curves", [])) if c]
        s["flows"] = [port_flows[k] for k in note.get("flows", []) if k in port_flows]
        s["weekly"] = eia.rows(weekly, note.get("weekly", []))
        s["routes"] = [_route(by_id[k]) for k in note.get("routes", []) if k in by_id]
        s["rates"] = [r for r in (_rate(k, overrides) for k in note.get("rates", [])) if r]
        s["chokepoints"] = [dict(name=n, per_day=straits[n][ship], change=straits[n][ship + "_chg"],
                                 index=straits[n].get(ship + "_index"), week=straits[n]["week"])
                            for n in note.get("chokepoints", []) if n in straits]
        s["ship"] = ship
        s["brief"] = note["read"].split(". ")[0].rstrip(".") + "."
        s["signal"] = _signal(note.get("signal"), port_flows, weekly, by_id, straits, ship)
        sections.append(s)
    flow_week = next((f["week"] for f in port_flows.values()), None)
    return dict(written=notes.get("written"), sections=sections, flow_week=flow_week,
                weekly_week=(weekly or {}).get("week"), headline=headline(sections, port_flows))


def headline(sections, port_flows):
    """What the page opens on. How many markets are tight, the steepest curve and the largest flow moves."""
    tones = [s.get("tone") for s in sections]
    steep = [c for s in sections if not s.get("seasonal") for c in s["curves"][:1] if c["state"] == "backwardation"]
    seen = [f for f in port_flows.values() if f.get("change") is not None and not f["dark"]]
    up = max(seen, key=lambda f: f["change"], default=None)
    down = min(seen, key=lambda f: f["change"], default=None)
    return dict(markets=len(sections), tight=tones.count("tight"), long=tones.count("long"),
                steepest=min(steep, key=lambda c: c["pct"], default=None),
                up=up if up and up["change"] > 0 else None, down=down if down and down["change"] < 0 else None)
