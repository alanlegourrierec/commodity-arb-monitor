"""Freight page: chokepoint traffic, the daily cost of a ship, curve structure and freight headroom.

Chokepoint transits come from IMF PortWatch (daily AIS counts by vessel type, free, no key).
The weekly totals are kept in data/chokepoints.json so a failed fetch still leaves a page.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .calc import Context
from .config import CO2_PER_T_FUEL, CURVES, PARAMS, VESSELS

log = logging.getLogger(__name__)

PORTWATCH = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
             "Daily_Chokepoints_Data/FeatureServer/0/query")
CHOKEPOINTS = {            # name shown on the page: pattern matched against PortWatch's portname
    "Hormuz": "Hormuz",
    "Bab el-Mandeb": "Bab el",
    "Suez": "Suez",
    "Cape of Good Hope": "Good Hope",
    "Panama": "Panama",
    "Malacca": "Malacca",
    "Bosporus": "Bosporus",
}
FIELDS = ["n_tanker", "n_dry_bulk", "n_total", "capacity_tanker"]
# Weeks before the Gulf war closed Hormuz (early March 2026). Kept in the file once computed,
# because the rolling 430-day window will eventually drop these weeks.
BASELINE = ("2025-11-01", "2026-02-28")


def baseline(weeks, old=None):
    out = dict((old or {}).get("values", {}))
    for name, rows in weeks.items():
        sample = [w for w in rows if BASELINE[0] <= w["week"] <= BASELINE[1]]
        if len(sample) >= 8 and name not in out:
            out[name] = {f: sum(w[f] for w in sample) / len(sample) for f in FIELDS}
    return dict(period=list(BASELINE), values=out)


# ---- PortWatch

def fetch_portwatch(start, session=requests):
    """Daily rows since `start` for the tracked chokepoints."""
    like = " OR ".join(f"portname LIKE '%{p}%'" for p in CHOKEPOINTS.values())
    where = f"date >= DATE '{start:%Y-%m-%d}' AND ({like})"
    rows, offset = [], 0
    while True:
        params = dict(where=where, outFields="date,portname," + ",".join(FIELDS), orderByFields="date",
                      resultOffset=offset, resultRecordCount=2000, f="json")
        r = session.get(PORTWATCH, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        batch = [f["attributes"] for f in payload.get("features", [])]
        rows += batch
        if not payload.get("exceededTransferLimit") or not batch:
            return rows
        offset += len(batch)


def _name(portname):
    for name, pattern in CHOKEPOINTS.items():
        if pattern.lower() in (portname or "").lower():
            return name
    return None


def weekly(rows):
    """Sum daily rows into weeks ending on Sunday. Incomplete weeks are dropped."""
    weeks = {}
    for row in rows:
        name = _name(row.get("portname"))
        if not name or not row.get("date"):
            continue
        day = row["date"]
        if not isinstance(day, str):            # some layers return epoch milliseconds
            day = datetime.fromtimestamp(day / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        d = date.fromisoformat(day[:10])
        end = d + timedelta(days=6 - d.weekday())
        w = weeks.setdefault(name, {}).setdefault(end.isoformat(), dict(days=0, **{f: 0 for f in FIELDS}))
        w["days"] += 1
        for f in FIELDS:
            w[f] += row.get(f) or 0
    out = {}
    for name, by_week in weeks.items():
        out[name] = [dict(week=k, **v) for k, v in sorted(by_week.items()) if v["days"] == 7]
    return out


def update_chokepoints(path, today=None):
    """Refresh data/chokepoints.json from PortWatch. Keeps the old file if the fetch fails."""
    path = Path(path)
    today = today or date.today()
    try:
        rows = fetch_portwatch(today - timedelta(days=430))
        weeks = weekly(rows)
        if not weeks:
            raise ValueError("no rows")
        old = load_chokepoints(path) or {}
        data = dict(source="IMF PortWatch", fetched=today.isoformat(),
                    latest_day=max(r["date"][:10] for r in rows if isinstance(r.get("date"), str)), weeks=weeks,
                    baseline=baseline(weeks, old.get("baseline")))
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return data
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.warning("PortWatch update failed, keeping %s: %s", path.name, exc)
        return load_chokepoints(path)


def load_chokepoints(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def chokepoint_summary(data):
    """Last full week against the same week a year earlier, in transits per day."""
    if not data:
        return []
    out = []
    for name in CHOKEPOINTS:
        weeks = data["weeks"].get(name)
        if not weeks:
            continue
        last = weeks[-1]
        target = (date.fromisoformat(last["week"]) - timedelta(weeks=52)).isoformat()
        year_ago = next((w for w in weeks if w["week"] == target), None)

        def per_day(w, f):
            return None if w is None else w[f] / 7

        row = dict(name=name, week=last["week"])
        for key, field in (("total", "n_total"), ("tanker", "n_tanker"), ("bulk", "n_dry_bulk")):
            now, then = per_day(last, field), per_day(year_ago, field)
            row[key] = now
            row[key + "_ly"] = then
            row[key + "_chg"] = None if not then else (now / then - 1) * 100
        row["tanker_dwt"] = last["capacity_tanker"] / 7
        base = (data.get("baseline") or {}).get("values", {}).get(name)
        if base:
            row["tanker_index"] = last["n_tanker"] / base["n_tanker"] * 100 if base["n_tanker"] else None
            row["bulk_index"] = last["n_dry_bulk"] / base["n_dry_bulk"] * 100 if base["n_dry_bulk"] else None
        out.append(row)
    return out


# ---- cost of a day at sea

def vessel_costs(prices, overrides, routes):
    """For each vessel class: hire, bunkers and EU allowances for one day at sea."""
    ctx = Context(prices, overrides)
    eua = ctx.eua_usd()
    hsfo = ctx.price("HSFO_SG")
    vlsfo_sg = ctx.price("VLSFO_SG")
    out = []
    for name, ship in VESSELS.items():
        used = [r["tce_used"] for r in routes if r.get("vessel") == name and r.get("tce_used")]
        tce = sorted(used)[len(used) // 2] if used else ship["default_tce"]
        if ship.get("cargo_m3"):
            fuel = PARAMS["LNG_BOIL_OFF"][0] * ship["cargo_m3"] * 0.45      # boil-off burnt as fuel, t/day
            bunkers = None
            co2 = fuel * 2.75
        else:
            fuel = ship["fuel_t_day"]
            bunkers = fuel * ctx.bunker_price("RDM")
            co2 = fuel * CO2_PER_T_FUEL
        ets = co2 * 0.5 * eua
        scrubber = None if ship.get("cargo_m3") or hsfo is None or vlsfo_sg is None else fuel * (vlsfo_sg - hsfo)
        total = tce + (bunkers or 0)
        out.append(dict(vessel=name, tce=tce, tce_source="median of routes" if used else "model default",
                        fuel_t_day=fuel, bunkers=bunkers, ets=ets, scrubber=scrubber, total=total,
                        bunker_share=None if bunkers is None else bunkers / total * 100))
    return out


# ---- curves

def curve_table(curves):
    rows = []
    for key, curve in curves.items():
        if key not in CURVES or len(curve) < 2 or CURVES[key].get("hidden"):
            continue
        c = CURVES[key]
        m1, m2 = curve[0]["close"], curve[1]["close"]
        m3 = curve[2]["close"] if len(curve) > 2 else None
        days = (date.fromisoformat(curve[1]["expiry"]) - date.fromisoformat(curve[0]["expiry"])).days or 30
        per_month = (m1 - m2) / days * 30
        state = "backwardation" if m1 > m2 else "contango" if m1 < m2 else "flat"
        rows.append(dict(key=key, label=c["label"], unit=c["unit"], m1=m1, m2=m2, m3=m3,
                         m1_contract=curve[0]["contract"], m2_contract=curve[1]["contract"],
                         spread=m1 - m2, per_month=per_month, pct_month=per_month / m1 * 100 if m1 else None,
                         state=state, points=curve))
    return rows


def storage_check(curves, prices, months=6):
    """Floating storage on a VLCC: does the Brent contango pay for the ship, financing and insurance?"""
    curve = curves.get("BRENT")
    if not curve or len(curve) <= months - 1:
        return None
    near, far = curve[0], curve[min(months - 1, len(curve) - 1)]
    vlcc = VESSELS["VLCC"]
    bbl = vlcc["cargo_t"] * 7.33
    days = (date.fromisoformat(far["expiry"]) - date.fromisoformat(near["expiry"])).days
    hire = vlcc["default_tce"] * days / bbl
    financing = near["close"] * PARAMS["FINANCING_RATE"][0] * days / 360
    insurance = near["close"] * PARAMS["CARGO_INSURANCE"][0]
    carry = far["close"] - near["close"]
    return dict(near=near, far=far, days=days, carry=carry, hire=hire, financing=financing, insurance=insurance,
                result=carry - hire - financing - insurance, tce=vlcc["default_tce"],
                breakeven_tce=(carry - financing - insurance) * bbl / days if days else None)


# ---- headroom: which trades can pay more for ships

def headroom(routes):
    rows = []
    for r in routes:
        be, used = r.get("breakeven_tce"), r.get("tce_used")
        if be is None or used is None or r.get("status") in ("Missing price", "Netback", "Delivered"):
            continue
        rows.append(dict(route=r["route"], vessel=r.get("vessel"), family=r["family"], status=r["status"],
                         tce=used, breakeven=be, room=be - used))
    return sorted(rows, key=lambda x: x["room"], reverse=True)
