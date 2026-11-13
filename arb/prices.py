"""Market prices: Yahoo Finance first, TradingView's scanner for what Yahoo lacks,
then manual inputs (data/market_inputs.csv) for anything still missing.
Forward curves (first few monthly contracts) also come from the scanner.

Every price carries its source so the dashboard can say where a number came from.
"""

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import CURVE_DEPTH, CURVES, MWH_PER_MMBTU, PRICE_BOUNDS, PRICE_CURVE, STALE_DAYS
from .terms import BASIS

log = logging.getLogger(__name__)

YAHOO = {
    "WTI_CUSHING": ("CL=F", "$/bbl", "WTI Cushing (NYMEX)"),
    "BRENT": ("BZ=F", "$/bbl", "Brent (ICE)"),
    "RBOB": ("RB=F", "$/gal", "RBOB NY Harbor (NYMEX)"),
    "ULSD_NYH": ("HO=F", "$/gal", "ULSD NY Harbor (NYMEX)"),
    "HENRY_HUB": ("NG=F", "$/MMBtu", "Henry Hub (NYMEX)"),
    "TTF_EUR": ("TTF=F", "EUR/MWh", "Dutch TTF (ICE Endex)"),
    "EURUSD": ("EURUSD=X", "USD per EUR", "EUR/USD"),
    "COMEX_COPPER": ("HG=F", "$/lb", "Copper (COMEX)"),
}

# The scanner endpoint is unofficial. If a symbol stops returning data, check the
# ticker on tradingview.com; the manual inputs file takes over in the meantime.
TRADINGVIEW = {
    "WTI_HOUSTON": ("ICEEUR:HOU1!", "$/bbl", "WTI Midland Gulf Coast (Houston)"),
    "HOU_DIFF": ("NYMEX:HTT1!", "$/bbl", "WTI Houston (Argus) vs WTI trade month"),
    "MURBAN": ("ICEAD:ADM1!", "$/bbl", "Murban (ICE Futures Abu Dhabi)"),
    "DATED_BRENT": ("NYMEX:AUB1!", "$/bbl", "Dated Brent (Platts), CME financial"),
    "GASOIL_ICE": ("ICEEUR:ULS1!", "$/t", "Low sulphur gasoil (ICE)"),
    "ULSD_USGC_UPDOWN": ("NYMEX:LT1!", "$/gal", "Gulf Coast ULSD (Platts) vs NY Harbor, up-down"),
    "EUROBOB": ("NYMEX:B7H1!", "$/t", "Eurobob oxy NWE barges (Argus), CME"),
    "JKM": ("NYMEX:JKM1!", "$/MMBtu", "JKM (Platts)"),
    "IRON_ORE_62": ("COMEX:TIO1!", "$/dmt", "Iron ore 62% CFR China"),
    "API2": ("ICEEUR:ATW1!", "$/t", "API2 Rotterdam coal"),
    "API4": ("ICEEUR:AFR1!", "$/t", "API4 Richards Bay coal"),
    "NEWC": ("ICEEUR:NCF1!", "$/t", "globalCOAL Newcastle"),
    "LME_COPPER": ("LME:CA1!", "$/t", "Copper grade A (LME)"),
    "LME_ALU": ("LME:AH1!", "$/t", "Aluminium high grade (LME)"),
    "MIDWEST_PREMIUM": ("COMEX:AUP1!", "$/lb", "Aluminium US Midwest premium"),
    "JAPAN_PREMIUM": ("COMEX:MJP1!", "$/t", "Aluminium Japan premium"),
    "ALU_DUP_RDM": ("COMEX:AEP1!", "$/t", "Aluminium duty-unpaid premium Rotterdam (Fastmarkets), CME"),
    "COKING_COAL": ("SGX:ACF1!", "$/t", "Premium hard coking coal FOB Australia (SGX TSI)"),
    "INDO_4200": ("SGX:M42F1!", "$/t", "Indonesian 4,200 GAR FOB (SGX M42)"),
    "EUA": ("ICEENDEX:ECF1!", "EUR/t", "EU carbon allowance (ICE EUA)"),
    "DE_POWER": ("EEX:DEBM1!", "EUR/MWh", "German power base, front month (EEX)"),
    "VLSFO_SG": ("NYMEX:S5F1!", "$/t", "VLSFO 0.5% FOB Singapore"),
    "VLSFO_RDM": ("NYMEX:R5F1!", "$/t", "VLSFO 0.5% barges FOB Rotterdam"),
    "HSFO_SG": ("NYMEX:SE1!", "$/t", "HSFO 380 cst Singapore"),
    "TC20_FUT": ("NYMEX:TF21!", "$/t", "TC20 MEG → UK Continent LR2 (Baltic), CME futures"),
    "HRC_CHINA": ("LME:HC1!", "$/t", "HRC FOB China (LME)"),
    "HRC_NWE": ("LME:HN1!", "$/t", "HRC NW Europe (LME, Argus)"),
    "HRC_US": ("COMEX:HRC1!", "$/st", "HRC US Midwest (CME, CRU), per short ton"),
    "SCRAP_TURKEY": ("LME:SC1!", "$/t", "Scrap HMS 80:20 CFR Turkey (LME)"),
    "SCRAP_INDIA": ("LME:SI1!", "$/t", "Scrap CFR India (LME)"),
    "SCRAP_TAIWAN": ("LME:ST1!", "$/t", "Scrap CFR Taiwan (LME)"),
    "REBAR_TURKEY": ("LME:SR1!", "$/t", "Rebar FOB Turkey (LME)"),
}
TV_URL = "https://scanner.tradingview.com/futures/scan"
SESSION_SHIFT_HOURS = 13


def _session(bar_time):
    """Trade date of a daily bar. CME and SGX open a day's session the evening before."""
    if not bar_time:
        return None
    return (datetime.fromtimestamp(bar_time, timezone.utc) + timedelta(hours=SESSION_SHIFT_HOURS)).date().isoformat()


def last_settlement(today, close, previous=None, bar_time=None, before=None, previous_time=None):
    """(price, trade date, change that day) at the last settlement of one contract.

    The feed's close is a live price while a session is open, and each contract trades at its own pace.
    Stepping back one bar for the contracts already trading today puts every price on a finished
    session, so a spread never mixes a live price with yesterday's settlement.
    """
    session = _session(bar_time)
    if session is None:
        return close, None, None
    if session >= today and previous is not None:
        return previous, _session(previous_time), None if before is None else previous - before
    return close, session, None if previous is None else close - previous


def align(rows):
    """Bring the contracts that missed the last session up to it.

    A thin contract keeps the settlement of the last day it traded while its neighbours move on, which
    puts a false kink in the curve. It is moved by the change of the nearest contract that did trade,
    the way a desk marks a quiet month off the liquid one next to it.
    """
    days = [r["day"] for r in rows if r["day"]]
    if not days:
        return rows
    last = max(days)
    fresh = [i for i, r in enumerate(rows) if r["day"] == last and r["move"] is not None]
    for i, r in enumerate(rows):
        if fresh and r["day"] and r["day"] < last:
            r["close"] += rows[min(fresh, key=lambda j: abs(j - i))]["move"]
            r["est"] = "the nearest contract that traded"
    return rows


def fetch_yahoo(tickers=YAHOO, today=None):
    import yfinance as yf

    today = today or datetime.now(timezone.utc).date().isoformat()
    out = {}
    for key, (symbol, unit, label) in tickers.items():
        try:
            close = yf.Ticker(symbol).history(period="5d", interval="1d")["Close"].dropna()
        except Exception as exc:  # network errors, delisted tickers, rate limits
            log.warning("Yahoo %s failed: %s", symbol, exc)
            continue
        if close.empty:
            log.warning("Yahoo %s returned no data", symbol)
            continue
        if len(close) > 1 and close.index[-1].strftime("%Y-%m-%d") >= today:
            close = close.iloc[:-1]                      # today's bar is still open
        out[key] = dict(value=float(close.iloc[-1]), unit=unit, label=label, symbol=symbol,
                        source="Yahoo Finance", date=close.index[-1].strftime("%Y-%m-%d"))
    return out


def parse_tradingview(payload, symbols=TRADINGVIEW, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    by_symbol = {row.get("s"): row.get("d") or [] for row in payload.get("data", [])}
    out = {}
    for key, (symbol, unit, label) in symbols.items():
        d = (by_symbol.get(symbol) or []) + [None] * 4
        if d[0] is not None:
            value, day, _ = last_settlement(today, d[0], d[1], d[2], previous_time=d[3])
            out[key] = dict(value=float(value), unit=unit, label=label, symbol=symbol,
                            source="TradingView (delayed)", date=day or today)
    return out


def fetch_tradingview(symbols=TRADINGVIEW):
    body = {"symbols": {"tickers": [s for s, _, _ in symbols.values()], "query": {"types": []}},
            "columns": ["close", "close[1]", "time", "time[1]"]}
    try:
        r = requests.post(TV_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return parse_tradingview(r.json(), symbols)
    except (requests.RequestException, ValueError) as exc:
        log.warning("TradingView scan failed: %s", exc)
        return {}


MONTH_CODES = "FGHJKMNQUVXZ"
SETTLED_DAYS = 75      # a Murban contract expires two months before its cargoes load


def contract_month(code):
    """'BRNF2027' -> (2027, 1)."""
    return int(code[-4:]), MONTH_CODES.index(code[-5]) + 1


CURVE_COLUMNS = ["name", "close", "expiration", "close[1]", "time", "close[2]", "time[1]"]


def read_curve(payload, today):
    """Every contract the feed lists for one curve, at its last settlement, nearest first."""
    rows = []
    for row in payload.get("data", []):
        name, close, expiry, previous, bar_time, before, previous_time = ((row.get("d") or []) + [None] * 7)[:7]
        if close is None or not expiry:
            continue
        expiry = str(expiry)
        price, day, move = last_settlement(today, close, previous, bar_time, before, previous_time)
        rows.append(dict(contract=name, close=float(price), expiry=f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}",
                         day=day, move=move))
    return align(sorted(rows, key=lambda r: r["expiry"]))


def follow(raw, curves=CURVES):
    """Move a thin curve that missed the last session by the change of the liquid curve it follows.

    Some swaps only get a price on the days they trade, so the whole curve can sit a session behind the
    rest of the market. Dated Brent, Eurobob and Rotterdam fuel oil are then moved by the percentage
    Brent futures moved that day. It is an estimate, and the price table shows the older date.
    """
    for key, rows in raw.items():
        lead = raw.get(curves[key].get("follows"))
        if not rows or not lead:
            continue
        ours = max((r["day"] for r in rows if r["day"]), default=None)
        theirs = max((r["day"] for r in lead if r["day"]), default=None)
        front = next((r for r in lead if r["day"] == theirs and r["move"] is not None), None)
        if ours and front and ours < theirs and front["close"] != front["move"]:
            pct = front["move"] / (front["close"] - front["move"])
            for r in rows:
                r["close"] *= 1 + pct
                r["est"] = curves[curves[key]["follows"]]["label"]


def split(rows, today):
    """(live contracts nearest first, contracts the feed still lists after their expiry)."""
    keep = ("contract", "close", "expiry", "day", "est")
    rows = [{k: round(r[k], 6) if k == "close" else r[k] for k in keep if r.get(k) is not None} for r in rows]
    return [r for r in rows if r["expiry"] >= today][:CURVE_DEPTH], [r for r in rows if r["expiry"] < today]


def parse_curve(payload, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    return split(read_curve(payload, today), today)


def fetch_curves(curves=CURVES, today=None):
    """(live curves, contracts just expired) by curve key."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    raw = {}
    for key, c in curves.items():
        body = {"filter": [{"left": "exchange", "operation": "equal", "right": c["exchange"]},
                           {"left": "name", "operation": "match", "right": f"^{c['root']}[FGHJKMNQUVXZ]\\d{{4}}$"}],
                "columns": CURVE_COLUMNS, "sort": {"sortBy": "expiration", "sortOrder": "asc"},
                "range": [0, CURVE_DEPTH + 2]}
        try:
            r = requests.post(TV_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            raw[key] = read_curve(r.json(), today)
        except (requests.RequestException, ValueError) as exc:
            log.warning("curve %s failed: %s", key, exc)
    follow(raw, curves)
    live, expired = {}, {}
    for key, rows in raw.items():
        rows, gone = split(rows, today)
        if len(rows) >= 2:
            live[key], expired[key] = rows, gone
    return live, expired


def with_settled(curves, expired, path, today, save=True):
    """Live curves plus the last close of contracts that stopped trading not long ago.

    A December TTF or Henry Hub contract expires in late November, yet a cargo delivered in December is
    still priced on it. The feed drops a contract soon after it expires, so every run writes the closes
    it saw to a small file and reads back the ones it no longer gets.
    """
    path = Path(path)
    seen = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.setdefault(row["curve"], {})[row["contract"]] = dict(
                    contract=row["contract"], close=float(row["close"]), expiry=row["expiry"])
    floor = (today - timedelta(days=SETTLED_DAYS)).isoformat()
    recent = lambda rows: sorted((c for c in rows if c["expiry"] >= floor), key=lambda c: c["expiry"])
    out = {}
    for key, rows in curves.items():
        known = seen.get(key, {})
        known.update({c["contract"]: c for c in expired.get(key, []) + rows})
        out[key] = recent(known.values())
    if save:
        # a curve the feed missed today keeps its file rows for tomorrow
        kept = {**{key: recent(rows.values()) for key, rows in seen.items()}, **out}
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["curve", "contract", "close", "expiry"])
            for key in sorted(kept):
                w.writerows([key, c["contract"], c["close"], c["expiry"]] for c in kept[key])
    return out


def name_fronts(prices, curves):
    """Show each benchmark as the nearest contract of its curve, under that contract's name.

    The feed's continuous symbols roll on their own calendar, days before the contract expires, so the
    same line can go from December to January overnight. Naming the contract makes the roll visible.
    A benchmark the front-month fetch missed is filled from its curve the same way.
    """
    for key, (_, unit, label) in {**YAHOO, **TRADINGVIEW}.items():
        curve = curves.get(PRICE_CURVE.get(key, key))
        if not curve:
            continue
        c = curve[0]
        source = "TradingView (delayed)"
        if c.get("est"):
            source += f", moved with {c['est']} on a day it did not trade"
        p = prices.setdefault(key, dict(unit=unit, label=label))
        p.update(value=c["close"], symbol=c["contract"], source=source, date=c.get("day") or p.get("date", ""))
    return prices


def load_prices_file(path):
    """Prices saved as JSON: {"asof": ..., "prices": {KEY: {...}}, "curves": {KEY: [{contract, close, expiry}]}}."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    prices = {k: dict(v, value=float(v["value"])) for k, v in raw["prices"].items() if v.get("value") is not None}
    return raw.get("asof"), prices, raw.get("curves", {})


def in_bounds(key, value):
    lo, hi = PRICE_BOUNDS.get(key, (None, None))
    return lo is None or lo <= value <= hi


def assemble(fetched, manual):
    """Fill the gaps in fetched prices with derived values, then manual levels.

    A fetched price outside its sanity bounds is dropped (a symbol that changed meaning
    returns a number, just not the right one), so the manual level takes over.
    """
    prices = {}
    for key, p in fetched.items():
        if in_bounds(key, p["value"]):
            prices[key] = p
        else:
            log.warning("%s = %s from %s is out of bounds, ignored", key, p["value"], p.get("symbol"))

    def derive(key, base, diff_key, label, unit):
        diff = prices.get(diff_key) or manual.get(diff_key)
        if key in prices or base not in prices or diff is None:
            return
        prices[key] = dict(value=prices[base]["value"] + diff["value"], unit=unit, label=label,
                           symbol=f"{base} + {diff.get('symbol') or 'diff'}",
                           source=f"Derived from {base} + {diff_key} ({diff.get('date') or 'no date'})",
                           date=diff.get("date", ""))

    derive("WTI_HOUSTON", "WTI_CUSHING", "HOU_DIFF", "WTI Houston (Cushing + diff)", "$/bbl")
    derive("MURBAN", "BRENT", "MURBAN_DIFF", "Murban (Brent + diff)", "$/bbl")

    if "TTF_USD" not in prices and "TTF_EUR" in prices and "EURUSD" in prices:
        value = prices["TTF_EUR"]["value"] * prices["EURUSD"]["value"] * MWH_PER_MMBTU
        prices["TTF_USD"] = dict(value=value, unit="$/MMBtu", label="TTF in USD", symbol="TTF x EURUSD",
                                 source="Derived from TTF and EUR/USD", date=prices["TTF_EUR"].get("date", ""))

    for key, m in manual.items():
        if key in PRICE_BOUNDS and key not in prices:
            prices[key] = dict(value=m["value"], unit=m["unit"], label=m["label"], symbol="manual",
                               source=f"Manual ({m['date'] or 'no date'})", date=m["date"])
    return prices


def price_table(prices, today=None):
    today = today or datetime.now(timezone.utc).date()
    rows = []
    for key, p in sorted(prices.items(), key=lambda kv: kv[1].get("label", kv[0])):
        lo, hi = PRICE_BOUNDS.get(key, (None, None))
        check = "ok" if lo is None or lo <= p["value"] <= hi else "check"
        try:
            age = (today - datetime.strptime(p.get("date", ""), "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        if check == "ok" and age is not None and age > STALE_DAYS:
            check = "stale"
        elif check == "ok" and "moved with" in p.get("source", ""):
            check = "estimate"
        rows.append(dict(key=key, label=p.get("label", key), symbol=p.get("symbol", ""), value=p["value"],
                         unit=p.get("unit", ""), source=p.get("source", ""), date=p.get("date", ""), check=check,
                         basis=BASIS.get(key, "")))
    return rows
