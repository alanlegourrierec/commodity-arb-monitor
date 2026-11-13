"""Market prices: Yahoo Finance first, TradingView's scanner for what Yahoo lacks,
then manual inputs (data/market_inputs.csv) for anything still missing.
Forward curves (first few monthly contracts) also come from the scanner.

Every price carries its source so the dashboard can say where a number came from.
"""

import json
import logging
from datetime import datetime, timezone

import requests

from .config import CURVE_DEPTH, CURVES, MWH_PER_MMBTU, PRICE_BOUNDS, STALE_DAYS
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


def fetch_yahoo(tickers=YAHOO):
    import yfinance as yf

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
        out[key] = dict(value=float(close.iloc[-1]), unit=unit, label=label, symbol=symbol,
                        source="Yahoo Finance", date=close.index[-1].strftime("%Y-%m-%d"))
    return out


def parse_tradingview(payload, symbols=TRADINGVIEW):
    by_symbol = {row.get("s"): row.get("d") or [] for row in payload.get("data", [])}
    out = {}
    for key, (symbol, unit, label) in symbols.items():
        d = by_symbol.get(symbol)
        if d and d[0] is not None:
            out[key] = dict(value=float(d[0]), unit=unit, label=label, symbol=symbol,
                            source="TradingView (delayed)", date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    return out


def fetch_tradingview(symbols=TRADINGVIEW):
    body = {"symbols": {"tickers": [s for s, _, _ in symbols.values()], "query": {"types": []}},
            "columns": ["close"]}
    try:
        r = requests.post(TV_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return parse_tradingview(r.json(), symbols)
    except (requests.RequestException, ValueError) as exc:
        log.warning("TradingView scan failed: %s", exc)
        return {}


MONTH_CODES = "FGHJKMNQUVXZ"


def contract_month(code):
    """'BRNF2027' -> (2027, 1)."""
    return int(code[-4:]), MONTH_CODES.index(code[-5]) + 1


def parse_curve(payload):
    rows = []
    for row in payload.get("data", []):
        name, close, expiry = (row.get("d") or [None, None, None])[:3]
        if close is None or not expiry:
            continue
        expiry = str(expiry)
        rows.append(dict(contract=name, close=float(close), expiry=f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"))
    return sorted(rows, key=lambda r: r["expiry"])[:CURVE_DEPTH]


def fetch_curves(curves=CURVES):
    out = {}
    for key, c in curves.items():
        body = {"filter": [{"left": "exchange", "operation": "equal", "right": c["exchange"]},
                           {"left": "name", "operation": "match", "right": f"^{c['root']}[FGHJKMNQUVXZ]\\d{{4}}$"}],
                "columns": ["name", "close", "expiration"], "sort": {"sortBy": "expiration", "sortOrder": "asc"},
                "range": [0, CURVE_DEPTH]}
        try:
            r = requests.post(TV_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            rows = parse_curve(r.json())
        except (requests.RequestException, ValueError) as exc:
            log.warning("curve %s failed: %s", key, exc)
            continue
        if len(rows) >= 2:
            out[key] = rows
    return out


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
        rows.append(dict(key=key, label=p.get("label", key), symbol=p.get("symbol", ""), value=p["value"],
                         unit=p.get("unit", ""), source=p.get("source", ""), date=p.get("date", ""), check=check,
                         basis=BASIS.get(key, "")))
    return rows
