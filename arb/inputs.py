"""Reading the CSV inputs: freight overrides, assumptions and manual market prices."""

import csv
from pathlib import Path


def to_float(text):
    if text is None:
        return None
    t = str(text).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def read_overrides(*paths):
    """Merge one or more override files. Later files win, empty values are skipped.

    Columns: type, key, label, unit, value, ws_flat_rate, date, source, comment
    """
    merged = {}
    for path in paths:
        if not path or not Path(path).exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                key = row.get("key", "").upper()
                value = to_float(row.get("value"))
                if not key or value is None:
                    continue
                merged[key] = dict(type=row.get("type", "").lower(), value=value, unit=row.get("unit", ""),
                                   flat_rate=to_float(row.get("ws_flat_rate")), date=row.get("date", ""),
                                   source=row.get("source", "") or "Override", label=row.get("label", key))
    return merged


def read_manual_prices(path):
    """data/market_inputs.csv: key, value, unit, date, source, note."""
    out = {}
    if not path or not Path(path).exists():
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            value = to_float(row.get("value"))
            if value is None:
                continue
            out[row["key"].strip().upper()] = dict(value=value, unit=row.get("unit", ""), date=row.get("date", ""),
                                                   source=row.get("source", "") or "Manual",
                                                   label=row.get("note", "") or row["key"])
    return out
