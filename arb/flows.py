"""Seaborne flows by port, from IMF PortWatch.

PortWatch estimates how many tonnes each ship class loads and discharges in some 2,000 ports every
day, from AIS positions and draughts. Ports are grouped here by the trade they are known for, so dry
bulk leaving the Pilbara reads as iron ore supply and dry bulk landing in Indian coal ports as coal
demand. It is an estimate. A ship that sails with its transponder off is not seen at all, which
matters in the Gulf. An LNG carrier hardly changes draught, so Ras Laffan is counted in tanker calls.

The weekly totals are kept in data/port_flows.json so a failed fetch still leaves a page.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PORTS_URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
             "Daily_Ports_Data/FeatureServer/0/query")
WINDOW_DAYS = 430          # a year back for the comparison, plus the weeks before it for the average
AVERAGE_WEEKS = 4
TRIES = 3
CALLS = "portcalls_tanker"
MEASURES = {"export_tanker": "Tanker cargo loaded", "export_dry_bulk": "Dry bulk loaded",
            "import_dry_bulk": "Dry bulk discharged", CALLS: "Tanker calls"}

# Each group sums one PortWatch field over its ports. Ports are keyed by their PortWatch id.
GROUPS = {
    "GULF_INSIDE": dict(label="Gulf terminals inside Hormuz", field="export_tanker", dark=True,
                        ports={1091: "Ras Tanura", 526: "Juaymah", 24: "Jubail", 743: "Mina Al Ahmadi",
                               2479: "Basrah Oil Terminal", 512: "Ruwais", 2237: "Das Island", 2164: "Kharg Island"}),
    "OUTSIDE_HORMUZ": dict(label="Yanbu and Fujairah", field="export_tanker", dark=True,
                           ports={570: "Yanbu", 362: "Fujairah"}),
    "US_GULF": dict(label="US Gulf Coast", field="export_tanker",
                    ports={481: "Houston", 264: "Corpus Christi", 134: "Beaumont", 933: "Port Arthur"}),
    "RUSSIA_WEST": dict(label="Russian Baltic and Black Sea ports", field="export_tanker", dark=True,
                        ports={1020: "Primorsk", 1095: "Ust-Luga", 833: "Novorossiysk"}),
    "LNG_QATAR": dict(label="Ras Laffan", field=CALLS, dark=True, ports={1090: "Ras Laffan"}),
    "COAL_IDN": dict(label="Indonesian coal ports", field="export_dry_bulk",
                     ports={112: "Banjarmasin", 1137: "Samarinda", 2294: "Bunati", 102: "Balikpapan",
                            1270: "Tanjung Sangata", 2306: "Jetty Coalindo", 875: "Palembang"}),
    "COAL_NEWC": dict(label="Newcastle", field="export_dry_bulk", ports={816: "Newcastle"}),
    "COAL_RB": dict(label="Richards Bay", field="export_dry_bulk", ports={1099: "Richards Bay"}),
    "COAL_COL": dict(label="Colombian coal ports", field="export_dry_bulk",
                     ports={1054: "Puerto Nuevo", 1027: "Puerto Bolivar"}),
    "COAL_USEC": dict(label="US East Coast coal ports", field="export_dry_bulk",
                      ports={103: "Baltimore", 826: "Norfolk", 818: "Newport News"}),
    "INDIA_DRY_IMP": dict(label="Indian coal ports", field="import_dry_bulk",
                          ports={777: "Mundra", 599: "Krishnapatnam", 883: "Paradip", 1367: "Visakhapatnam",
                                 540: "Kandla", 290: "Dhamra", 534: "Kamarajar"}),
    "ARA_DRY_IMP": dict(label="Rotterdam and Amsterdam", field="import_dry_bulk",
                        ports={1114: "Rotterdam", 45: "Amsterdam"}),
    "COKING_QLD": dict(label="Queensland coal ports", field="export_dry_bulk",
                       ports={458: "Hay Point", 398: "Gladstone", 0: "Abbot Point"}),
    "ORE_PILBARA": dict(label="Pilbara ore ports", field="export_dry_bulk",
                        ports={955: "Port Hedland", 276: "Dampier", 981: "Port Walcott"}),
    "ORE_BRAZIL": dict(label="Brazilian ore ports", field="export_dry_bulk",
                       ports={506: "Itaqui", 1328: "Tubarao", 2045: "Itaguai", 422: "Guaiba Island", 166: "Acu"}),
    "ORE_CHINA_IMP": dict(label="Northern Chinese ore ports", field="import_dry_bulk",
                          ports={1195: "Shougang Jingtang", 1266: "Tangshan Jingtang", 1105: "Rizhao", 632: "Lanqiao",
                                 1069: "Qingdao", 644: "Lianyungang"}),
}


def fetch_port(port_id, start, fields, session=requests):
    """Daily rows of one port since `start`."""
    where = f"portid='port{port_id}' AND date >= DATE '{start:%Y-%m-%d}'"
    rows, offset = [], 0
    while True:
        params = dict(where=where, outFields="date," + ",".join(fields), orderByFields="date", returnGeometry="false",
                      resultOffset=offset, resultRecordCount=1000, f="json")
        payload = _get(session, params)
        batch = [f["attributes"] for f in payload.get("features", [])]
        rows += batch
        if not payload.get("exceededTransferLimit") or not batch:
            return rows
        offset += len(batch)


def _get(session, params):
    """One query, tried again after a pause. The service drops a request now and then."""
    for attempt in range(TRIES):
        try:
            r = session.get(PORTS_URL, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if "error" in payload:
                raise ValueError(payload["error"].get("message", "PortWatch error"))
            return payload
        except (requests.RequestException, ValueError):
            if attempt == TRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))


def _day(value):
    """PortWatch dates come as text or as epoch milliseconds, depending on the layer."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def weekly(rows, fields):
    """Sum daily rows into weeks ending on Sunday, by field. Incomplete weeks are dropped."""
    weeks = {}
    for row in rows:
        if not row.get("date"):
            continue
        d = date.fromisoformat(_day(row["date"]))
        end = (d + timedelta(days=6 - d.weekday())).isoformat()
        w = weeks.setdefault(end, dict(days=0, **{f: 0 for f in fields}))
        w["days"] += 1
        for f in fields:
            w[f] += row.get(f) or 0
    return {end: w for end, w in weeks.items() if w["days"] == 7}


def build(by_port, groups=GROUPS):
    """Group series over the weeks every port has, and each port's last four weeks against a year ago.

    Tonnages are kept in thousand tonnes a week and calls as counts.
    """
    seen = [w for w in by_port.values() if w]
    if not seen:
        return None
    first = max(min(w) for w in seen)
    last = min(max(w) for w in seen)
    weeks, d = [], date.fromisoformat(first)
    while d <= date.fromisoformat(last):
        weeks.append(d.isoformat())
        d += timedelta(days=7)
    if len(weeks) < AVERAGE_WEEKS:
        return None
    recent = weeks[-AVERAGE_WEEKS:]
    earlier = [(date.fromisoformat(w) - timedelta(weeks=52)).isoformat() for w in recent]
    series, detail = {}, {}
    for key, g in groups.items():
        scale = 1 if g["field"] == CALLS else 1000
        value = lambda port, week: by_port.get(port, {}).get(week, {}).get(g["field"], 0) / scale
        series[key] = [round(sum(value(p, w) for p in g["ports"]), 1) for w in weeks]
        compare = earlier[0] in weeks
        detail[key] = [dict(name=name, now=round(sum(value(p, w) for w in recent) / AVERAGE_WEEKS, 1),
                            year_ago=round(sum(value(p, w) for w in earlier) / AVERAGE_WEEKS, 1) if compare else None)
                       for p, name in g["ports"].items()]
    return dict(weeks=weeks, groups=series, ports=detail)


def update_flows(path, today=None, session=None, groups=GROUPS):
    """Refresh data/port_flows.json from PortWatch. Keeps the old file if the fetch fails."""
    path = Path(path)
    today = today or date.today()
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0"
    fields = sorted({g["field"] for g in groups.values()})
    ports = sorted({p for g in groups.values() for p in g["ports"]})
    try:
        by_port, latest = {}, ""
        for port in ports:
            rows = fetch_port(port, today - timedelta(days=WINDOW_DAYS), fields, session)
            by_port[port] = weekly(rows, fields)
            latest = max([latest] + [_day(r["date"]) for r in rows if r.get("date")])
        built = build(by_port, groups)
        if not built:
            raise ValueError("no complete week")
        data = dict(source="IMF PortWatch", fetched=today.isoformat(), latest_day=latest, **built)
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return data
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.warning("PortWatch port update failed, keeping %s: %s", path.name, exc)
        return load_flows(path)


def load_flows(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def summary(data, groups=GROUPS):
    """Each group's last four weeks against the same four weeks a year earlier."""
    if not data or not data.get("weeks"):
        return {}
    weeks = data["weeks"]
    out = {}
    for key, g in groups.items():
        values = data["groups"].get(key)
        if not values or len(values) != len(weeks):
            continue
        now = sum(values[-AVERAGE_WEEKS:]) / AVERAGE_WEEKS
        then = sum(values[-52 - AVERAGE_WEEKS:-52]) / AVERAGE_WEEKS if len(values) >= 52 + AVERAGE_WEEKS else None
        calls = g["field"] == CALLS
        out[key] = dict(key=key, label=g["label"], measure=MEASURES[g["field"]], calls=calls, dark=bool(g.get("dark")),
                        unit="calls a week" if calls else "Mt a week", week=weeks[-1],
                        direction="imports" if g["field"].startswith("import") else "exports",
                        now=now if calls else now / 1000,
                        year_ago=then if calls or then is None else then / 1000,
                        change=None if not then else (now / then - 1) * 100,
                        ports=data.get("ports", {}).get(key, []), weeks=weeks, series=values)
    return out
