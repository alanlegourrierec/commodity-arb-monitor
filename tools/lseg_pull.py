"""Pull licensed rates from LSEG Workspace into private/freight.csv.

Run it on the PC where Workspace is open and signed in. It uses the LSEG Data Library
desktop session, so the data never leaves the machine and stays under your licence.

    pip install lseg-data
    setx LSEG_APP_KEY <key from the App Key Generator in Workspace>
    python tools/lseg_pull.py --check      # which codes return data, writes nothing
    python tools/lseg_pull.py              # updates private/freight.csv
    python tools/lseg_pull.py --wait 600   # keeps trying for 10 hours if Workspace is not open yet
    python tools/lseg_pull.py --find "Baltic Supramax 10TC"   # look up a code

The codes live in data/lseg_map.csv. {M}{Y} in a code is replaced by the current month
and year (BATMM{M}{Y} -> BATMMX6 in November 2026), so monthly FFAs roll by themselves.
A code that returns nothing, or a value older than STALE_DAYS, keeps the last value and
date in the rate file, so the page shows its age.
"""

import argparse
import csv
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "data" / "lseg_map.csv"
OUT = ROOT / "private" / "freight.csv"
COLUMNS = ["type", "key", "label", "unit", "value", "ws_flat_rate", "date", "source", "comment"]
MONTH_CODES = "FGHJKMNQUVXZ"
FIELDS = ["CF_LAST", "CF_CLOSE", "CF_DATE"]
STALE_DAYS = 10
RETRY_SECONDS = 300


def resolve(ric, today):
    return ric.strip().replace("{M}", MONTH_CODES[today.month - 1]).replace("{Y}", str(today.year % 10))


def read_map(path=MAP):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if (row.get("ric") or "").strip()]


def read_rows(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{c: row.get(c, "") or "" for c in COLUMNS} for row in csv.DictReader(f)]


def write_rows(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _day(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")
        except ValueError:          # NaT
            return None
    text = str(v)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def fetch_lseg(rics):
    """{ric: (value, date)} from a Workspace desktop session. Snapshot first, last close as fallback."""
    import lseg.data as ld

    ld.open_session(app_key=os.environ.get("LSEG_APP_KEY"))
    try:
        out = {}
        table = ld.get_data(universe=list(rics), fields=FIELDS)
        for _, row in table.iterrows():
            value = next((_number(row[f]) for f in ("CF_LAST", "CF_CLOSE") if f in row and _number(row[f]) is not None),
                         None)
            if value is not None:
                out[row["Instrument"]] = (value, _day(row.get("CF_DATE")))
        for ric in rics:
            if ric in out:
                continue
            try:
                hist = ld.get_history(universe=ric, count=1)
            except Exception as exc:        # the library raises its own error types for unknown codes
                print(f"  {ric}: {exc}", file=sys.stderr)
                continue
            if hist is None or hist.empty:
                continue
            numbers = hist.select_dtypes("number").iloc[-1].dropna()
            if len(numbers):
                out[ric] = (float(numbers.iloc[0]), _day(hist.index[-1]))
        return out
    finally:
        ld.close_session()


def fetch_when_ready(fetch, rics, wait_minutes=0, sleep=time.sleep, clock=time.monotonic):
    """Rates from Workspace, tried again every few minutes while it is closed.

    The morning task can start before Workspace is open. Rather than fail, it waits for the session,
    for `wait_minutes` at most. Without a wait it tries once.
    """
    deadline = clock() + wait_minutes * 60
    while True:
        try:
            fetched, reason = fetch(rics), "it returned no data"
        except Exception as exc:        # the library raises its own error types when Workspace is closed
            fetched, reason = {}, str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
        if fetched:
            return fetched
        if clock() >= deadline:
            print(f"Workspace did not answer ({reason})", flush=True)
            return {}
        print(f"{datetime.now():%H:%M} Workspace did not answer, next try in {RETRY_SECONDS // 60} min", flush=True)
        sleep(RETRY_SECONDS)


def find(query, top=25):
    """Print the codes LSEG search returns for a query (needs the same desktop session)."""
    import lseg.data as ld

    ld.open_session(app_key=os.environ.get("LSEG_APP_KEY"))
    try:
        table = ld.discovery.search(query=query, select="RIC,DocumentTitle", top=top)
        for _, row in table.iterrows():
            print(f"{row.get('RIC', ''):<20} {row.get('DocumentTitle', '')}")
    finally:
        ld.close_session()


def is_stale(day, today):
    try:
        return (today - datetime.strptime(day, "%Y-%m-%d").date()).days > STALE_DAYS
    except (TypeError, ValueError):
        return False


def merge(rows, mapping, fetched, today):
    """Write fetched values into the rate rows. Rows that are not in the map are left alone."""
    by_key = {r["key"].strip().upper(): r for r in rows}
    report = []
    for m in mapping:
        key = m["key"].strip().upper()
        ric = resolve(m["ric"], today)
        row = by_key.get(key)
        if row is None:
            row = {c: "" for c in COLUMNS}
            row.update(type=m["type"], key=key, label=m["label"], unit=m["unit"], comment=m.get("comment", ""))
            rows.append(row)
            by_key[key] = row
        hit = fetched.get(ric)
        if hit and is_stale(hit[1], today):
            report.append((key, ric, f"{hit[0]:,.2f}", f"stale since {hit[1]}, kept the previous value"))
            continue
        if hit:
            value, day = hit
            row.update(value=f"{value:g}", unit=m["unit"], ws_flat_rate="", date=day or today.isoformat(),
                       source=f"LSEG {ric}")
            report.append((key, ric, f"{value:,.2f}", "ok"))
        else:
            report.append((key, ric, row.get("value") or "-", f"no data, kept the value of {row.get('date') or 'never'}"))
    return rows, report


def main(argv=None, fetch=fetch_lseg, today=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="show what each code returns, write nothing")
    ap.add_argument("--map", default=MAP, help="code map (default data/lseg_map.csv)")
    ap.add_argument("--out", default=OUT, help="rate file to update (default private/freight.csv)")
    ap.add_argument("--find", metavar="QUERY", help="search LSEG for a code and stop")
    ap.add_argument("--wait", type=float, default=0, metavar="MINUTES",
                    help="keep trying this long while Workspace is closed (default: try once)")
    args = ap.parse_args(argv)
    today = today or date.today()
    if args.find:
        find(args.find)
        return 0

    mapping = read_map(args.map)
    rics = sorted({resolve(m["ric"], today) for m in mapping})
    fetched = fetch_when_ready(fetch, rics, args.wait)
    if not fetched and not args.check:
        return 1                        # nothing came back, the rate file stays as it is
    rows, report = merge(read_rows(args.out), mapping, fetched, today)
    for key, ric, value, state in report:
        print(f"{key:<12} {ric:<14} {value:>16}  {state}")
    if not args.check:
        write_rows(args.out, rows)
        print(f"wrote {args.out}")
    return 0 if fetched else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")    # Windows logs are cp1252, some LSEG titles are not
    sys.exit(main())
