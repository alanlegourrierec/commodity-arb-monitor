"""Build the commodity arbitrage dashboard.

    python run.py                          # live prices, public inputs, writes site/
    python run.py --prices-file prices.json
    python run.py --private private        # adds private/freight.csv and private/trade_ideas.json
"""

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from arb import bulk, calc, history, ideas, inputs, prices, report, shipping, terms
from arb.config import (FREIGHT_KEY, LICENCE_NOTE, LICENSED, LOAD_LEAD_DAYS, PRICE_BOUNDS, PRIVATE_NOTE,
                        SUPRAMAX_LEGS)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PRICE_GROUPS = {
    "energy": ["WTI_CUSHING", "WTI_HOUSTON", "BRENT", "DATED_BRENT", "MURBAN", "RBOB", "ULSD_NYH",
               "ULSD_USGC_UPDOWN", "GASOIL_ICE", "EUROBOB", "HENRY_HUB", "TTF_EUR", "TTF_USD", "EURUSD", "JKM",
               "RIN_D6", "RIN_D4", "RIN_D5", "RIN_D3"],
    "bulk": ["IRON_ORE_62", "API2", "API4", "NEWC", "COKING_COAL", "INDO_4200", "PETCOKE_USG", "EUA", "DE_POWER",
             "TTF_EUR", "EURUSD", "HRC_CHINA", "HRC_NWE", "HRC_US", "SCRAP_TURKEY", "SCRAP_INDIA", "SCRAP_TAIWAN",
             "REBAR_TURKEY", "LME_COPPER", "COMEX_COPPER", "LME_ALU", "MIDWEST_PREMIUM", "JAPAN_PREMIUM",
             "ALU_DUP_RDM"],
    "freight": ["VLSFO_SG", "VLSFO_RDM", "HSFO_SG", "TC20_FUT", "EUA", "EURUSD", "BRENT"],
}


def mark_public_substitutes(routes, price_rows):
    """In the public build, flag every number whose private version comes from licensed data."""
    for r in routes:
        key = FREIGHT_KEY.get(r.get("leg"), r.get("leg"))
        if key in LICENSED or r.get("fuel") in LICENSED:
            r["private_note"] = PRIVATE_NOTE
    for p in price_rows:
        if p["key"] in LICENSED:
            p["private_note"] = PRIVATE_NOTE


def mark_licence_gaps(routes):
    """In the private build, flag the Supramax legs LSEG did not price. Returns True if there is one."""
    gap = False
    for r in routes:
        if (FREIGHT_KEY.get(r.get("leg"), r.get("leg")) in SUPRAMAX_LEGS
                and not r.get("freight_source", "").startswith("LSEG")):
            r["private_note"] = LICENCE_NOTE
            gap = True
    return gap


def main(argv=None):
    ap = argparse.ArgumentParser(description="Commodity arbitrage dashboard")
    ap.add_argument("--prices-file", help="use saved prices instead of fetching them")
    ap.add_argument("--private", help="folder with freight.csv / trade_ideas.json (kept out of git)")
    ap.add_argument("--out", help="output folder (default site/, or build_private/ with --private)")
    ap.add_argument("--no-history", action="store_true", help="do not write the history file")
    ap.add_argument("--offline", action="store_true", help="do not call PortWatch, use data/chokepoints.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    manual = inputs.read_manual_prices(DATA / "market_inputs.csv")
    if args.prices_file:
        asof, fetched, curves = prices.load_prices_file(args.prices_file)
        today = date.fromisoformat(asof[:10])
        pricing = curves
    else:
        fetched = {**prices.fetch_yahoo(), **prices.fetch_tradingview()}
        curves, expired = prices.fetch_curves()
        asof = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        today = datetime.now(timezone.utc).date()
        # contracts that stopped trading still price the cargoes of their month
        pricing = prices.with_settled(curves, expired, DATA / "settled.csv", today, save=not args.no_history)
    px = prices.assemble(prices.name_fronts(fetched, curves), manual)

    private = Path(args.private) if args.private else None
    override_files = [DATA / "freight_assumptions.csv"]
    if private:
        override_files.append(private / "freight.csv")
    overrides = inputs.read_overrides(*override_files)
    for key, o in overrides.items():           # e.g. petcoke or Eurobob from the private rate file
        typed_in = key in px and px[key].get("symbol") == "manual"
        if o["type"] == "price" and key in PRICE_BOUNDS and (key not in px or typed_in):
            px[key] = dict(value=o["value"], unit=o["unit"], label=o["label"], symbol="override",
                           source=f"{o['source']} ({o['date']})", date=o["date"])

    result = calc.evaluate(px, overrides, pricing, today)
    routes = result["routes"]
    dry = bulk.evaluate(px, overrides, pricing, today)
    all_routes = routes + dry["routes"]
    for r in all_routes:
        r["ladder"] = terms.ladder(r)
        r["buyer_side"] = terms.beyond(r)

    choke_file = DATA / "chokepoints.json"
    chokepoints = (shipping.load_chokepoints(choke_file) if args.offline or args.prices_file
                   else shipping.update_chokepoints(choke_file, today))
    freight = dict(chokepoints=shipping.chokepoint_summary(chokepoints), chokepoint_weeks=chokepoints,
                   vessels=shipping.vessel_costs(px, overrides, all_routes), curves=shipping.curve_table(curves),
                   storage=shipping.storage_check(curves, px), headroom=shipping.headroom(all_routes))

    price_rows = prices.price_table(px, today)
    supramax_gap = mark_licence_gaps(all_routes) if private else True
    if not private:
        mark_public_substitutes(all_routes, price_rows)

    # benchmark prices for the day a cargo bought today loads, for the figures quoted on the pages
    ctx = calc.Context(px, overrides, pricing, today)
    forward = {key: ctx.at(key, LOAD_LEAD_DAYS) for key in px}
    load_date = (today + timedelta(days=LOAD_LEAD_DAYS)).isoformat()

    snapshot = dict(
        prices_asof=asof, load_date=load_date, forward=forward,
        routes=routes, spreads=result["spreads"], assumptions=result["assumptions"],
        bulk=dry, freight=freight, prices=price_rows, price_keys=PRICE_GROUPS, supramax_gap=supramax_gap,
        summary=dict(routes=len(routes), open=sum(r["status"] == "Open" for r in routes),
                     freight_overrides=sum(1 for r in routes if r["freight_source"] != "Model")),
    )

    hist_path = (private / "history.csv") if private else DATA / "history.csv"
    rows = (history.load(hist_path) if args.no_history
            else history.update(hist_path, all_routes, today.isoformat()))

    out = Path(args.out) if args.out else ROOT / ("build_private" if private else "site")
    freight_ideas = ideas.load_freight_ideas(private / "trade_ideas.json") if private else []
    desk_ideas = ideas.load_commodity_ideas(DATA / "commodity_ideas.json", all_routes, curves, today)
    page = report.build(snapshot, rows, out, freight_ideas=freight_ideas, desk_ideas=desk_ideas,
                        private=bool(private))
    (out / "snapshot.json").write_text(json.dumps(snapshot, indent=1, ensure_ascii=False, default=str),
                                       encoding="utf-8")

    for r in all_routes:
        margin = "" if r.get("margin") is None else f"{r['margin']:+.2f} {r['unit']}"
        logging.info("%-36s %-14s %16s  %s", r["route"], r["status"], margin, r.get("freight_source", ""))
    logging.info("wrote %s", page)


if __name__ == "__main__":
    main()
