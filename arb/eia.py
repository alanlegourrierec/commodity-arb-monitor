"""The US weekly balance, from the EIA.

The Weekly Petroleum Status Report comes out on Wednesday for the week to the previous Friday, as CSV
tables with no key. Table 1 holds stocks and the supply balance, table 2 refinery runs and table 4
Cushing. The gas storage report follows on Thursday as JSON. Together they are the one balance in
this market that is measured every week instead of estimated.

The last report is kept in data/us_weekly.json so a failed fetch still leaves a page.
"""

import csv
import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

WPSR = "https://ir.eia.gov/wpsr/{}.csv"
STORAGE = "https://ir.eia.gov/ngs/wngsr.json"

# key: (table, block, pattern of the row label, label on the page, unit)
# Stock tables carry one label column. The supply block and table 2 carry two, the block and the line.
SERIES = {
    "crude_stocks": ("table1", "", r"^Commercial", "Commercial crude stocks", "Mbbl"),
    "spr": ("table1", "", r"^Strategic Petroleum Reserve", "Strategic reserve", "Mbbl"),
    "gasoline_stocks": ("table1", "", r"^Total Motor Gasoline", "Gasoline stocks", "Mbbl"),
    "distillate_stocks": ("table1", "", r"^Distillate Fuel Oil", "Distillate stocks", "Mbbl"),
    "jet_stocks": ("table1", "", r"^Kerosene-Type Jet Fuel", "Jet fuel stocks", "Mbbl"),
    "cushing": ("table4", "", r"^Cushing", "Cushing crude stocks", "Mbbl"),
    "crude_production": ("table1", "Crude Oil Supply", r"Domestic Production$", "Crude production", "kb/d"),
    "crude_imports": ("table1", "Crude Oil Supply", r"\)\s+Imports$", "Crude imports", "kb/d"),
    "crude_exports": ("table1", "Crude Oil Supply", r"\)\s+Exports$", "Crude exports", "kb/d"),
    "refinery_input": ("table1", "Crude Oil Supply", r"Crude Oil Input to Refineries$", "Crude runs", "kb/d"),
    "product_exports": ("table1", "Other Supply", r"\)\s+Exports$", "Product exports", "kb/d"),
    "gasoline_supplied": ("table1", "Products Supplied", r"Finished Motor Gasoline$", "Gasoline supplied", "kb/d"),
    "distillate_supplied": ("table1", "Products Supplied", r"Distillate Fuel Oil$", "Distillate supplied", "kb/d"),
    "jet_supplied": ("table1", "Products Supplied", r"Kerosene-Type Jet Fuel$", "Jet fuel supplied", "kb/d"),
    "utilisation": ("table2", "Refiner Inputs and Utilization", r"^Percent Utilization", "Refinery utilisation", "%"),
}


def _num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def _day(text):
    """'11/6/26' -> '2026-11-06', or None."""
    try:
        return datetime.strptime(text.strip(), "%m/%d/%y").date().isoformat()
    except ValueError:
        return None


def read_table(text):
    """Rows of a report table with the header that applies to each: [(header, row), ...]."""
    out, header = [], None
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        if row[0].strip() == "STUB_1":
            header = row
        elif header:
            out.append((header, row))
    return out


def pick(rows, block, pattern):
    """The current week, the week before, a year ago and the four-week averages of one row."""
    for header, row in rows:
        two = len(header) > 1 and header[1].strip() == "STUB_2"
        if two != bool(block) or len(row) < 3:
            continue
        if two and (row[0].strip() != block or not re.search(pattern, row[1].strip())):
            continue
        if not two and not re.search(pattern, row[0].strip()):
            continue
        cols = [i for i, h in enumerate(header) if _day(h) and i < len(row)]
        if len(cols) < 3:
            continue
        out = dict(week=_day(header[cols[0]]), value=_num(row[cols[0]]), previous=_num(row[cols[1]]),
                   year_ago=_num(row[cols[2]]))
        again = [i for i in cols[1:] if header[i] == header[cols[0]]]     # the week again heads the four-week average
        if again and cols.index(again[0]) + 1 < len(cols):
            out.update(four_week=_num(row[again[0]]), four_week_year_ago=_num(row[cols[cols.index(again[0]) + 1]]))
        if out["value"] is not None:
            return out
    return None


def parse_petroleum(tables):
    """{key: reading} from the text of the report tables, {name: csv text}."""
    parsed = {name: read_table(text) for name, text in tables.items()}
    out = {}
    for key, (table, block, pattern, title, unit) in SERIES.items():
        reading = pick(parsed.get(table, []), block, pattern)
        if reading:
            out[key] = dict(label=title, unit=unit, **reading)
    return out


def parse_storage(payload):
    """Working gas in the Lower 48, in bcf."""
    series = next(s for s in payload["series"] if "lower 48" in s["name"].lower())
    levels = dict((d, v) for d, v in series["data"])
    week = payload["current_week"]
    return dict(label="Working gas in storage", unit="bcf", week=week, value=levels[week],
                previous=levels.get(payload.get("week_ago")), year_ago=levels.get(payload.get("year_ago")),
                five_year=series["calculated"].get("5yr-avg"))


def _read(session, url):
    """Text of a report. The storage file starts with a byte order mark, which r.json() chokes on."""
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.content.decode("utf-8-sig")


def update_weekly(path, today=None, session=None):
    """Refresh data/us_weekly.json. A report that fails keeps its last reading."""
    path = Path(path)
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0"
    old = load_weekly(path) or {}
    data = dict(source="EIA", fetched=(today or date.today()).isoformat(), series=dict(old.get("series") or {}),
                gas=old.get("gas"), history=dict(old.get("history") or {}))
    try:
        tables = {}
        for name in sorted({t for t, *_ in SERIES.values()}):
            tables[name] = _read(session, WPSR.format(name))
        fresh = parse_petroleum(tables)
        if "crude_stocks" not in fresh:
            raise ValueError("no stocks in table 1")
        data["series"] = fresh
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        log.warning("EIA petroleum update failed, keeping the last report: %s", exc)
    try:
        data["gas"] = parse_storage(json.loads(_read(session, STORAGE)))
    except (requests.RequestException, ValueError, KeyError, StopIteration) as exc:
        log.warning("EIA gas storage update failed, keeping the last report: %s", exc)
    if not data["series"] and not data["gas"]:
        return old or None
    week = (data["series"].get("crude_stocks") or {}).get("week")
    data["week"] = week or old.get("week")
    if week:                                   # one line a week, so a chart can be drawn once enough have built up
        data["history"][week] = {k: v["value"] for k, v in data["series"].items()}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def load_weekly(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def rows(data, keys):
    """Readings for a page table, with the change on the week and on the year."""
    out = []
    for key in keys:
        r = (data or {}).get("gas") if key == "gas_storage" else ((data or {}).get("series") or {}).get(key)
        if not r or r.get("value") is None:
            continue
        row = dict(r, key=key)
        base, prior = r.get("four_week_year_ago"), r.get("four_week")
        row["on_week"] = None if r.get("previous") is None else r["value"] - r["previous"]
        if r["unit"] == "%":
            row["on_year"] = None if r.get("year_ago") is None else r["value"] - r["year_ago"]
            row["on_year_unit"] = "pts"
        elif base and prior:                   # flows are noisy week to week, so compare four-week averages
            row["on_year"] = (prior / base - 1) * 100
            row["on_year_unit"] = "% (4-week)"
        else:
            row["on_year"] = None if not r.get("year_ago") else (r["value"] / r["year_ago"] - 1) * 100
            row["on_year_unit"] = "%"
        if r.get("five_year"):
            row["vs_five_year"] = (r["value"] / r["five_year"] - 1) * 100
        out.append(row)
    return out
