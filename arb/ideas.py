"""Trade ideas: freight ideas (private, built on licensed rates) and one physical idea per desk.

The text of an idea is written on a date and stays as written. The economics are recomputed
every run from the route it points to, so a stale idea shows it: the margin moves while the
text does not. A cargo idea is laid out term by term, from the purchase to the sale, so the
cost of going from FOB to CFR, CIF, DAP or duty paid is visible. The good and bad cases are
moves from today's margin.
"""

import json
from datetime import date
from pathlib import Path

from . import terms


def load_freight_ideas(path):
    if not path or not Path(path).exists():
        return []
    ideas = json.loads(Path(path).read_text(encoding="utf-8"))
    for i in ideas:
        i["gain"] = i["target"] - i["entry"]
        i["loss"] = i["stop"] - i["entry"]
    return ideas


def _line(label, value, note=""):
    return dict(label=label, note=note, value=value)


def _carry(econ, curves):
    """Sell prompt from tank, buy back on a later contract: the backwardation is locked."""
    curve = curves.get(econ["curve"])
    if not curve or len(curve) <= econ["far"]:
        return None
    near, far = curve[econ["near"]], curve[econ["far"]]
    k = econ.get("factor", 1.0)                       # e.g. $/gal -> $/t
    days = (date.fromisoformat(far["expiry"]) - date.fromisoformat(near["expiry"])).days
    lines = [_line("Sell in tank", near["close"] * k, near["contract"]),
             _line("Buy back in tank", -far["close"] * k, far["contract"])]
    return dict(margin=(near["close"] - far["close"]) * k, days=days, days_basis="until the buy-back",
                units=econ["volume"], unit=econ["unit"], lines=lines, signed=True,
                terms_note="Sold and bought back in tank, so there is no freight, insurance or duty to carry.")


def _route_economics(route, buy_price=None, buy_basis=None):
    """A route as a deal sheet: the purchase, each term up to the one it is sold on, then the sale.

    `buy_price` replaces the route's purchase price, for a cargo bought on another basis.
    """
    if route.get("margin") is None:
        return None
    ladder = terms.ladder(route, buy_price)
    if not ladder or ladder.get("backward"):
        return None
    val = lambda key: route.get(key) or 0.0
    sold = route["sell_term"]
    scale = buy_price / route["buy_price"] if buy_price is not None and route.get("buy_price") else 1.0
    seller = val("seller_costs") * scale              # interest follows the price paid
    margin = route["dest_price"] - ladder[sold] - seller - val("time_spread") - val("import_discount")

    digits = 1 if route["unit"] == "$/t" else 2
    lines = [_line("Buy", ladder["buy"], buy_basis or route["buy_basis"])]
    for step in ladder["steps"]:
        if step["seller"] and (abs(step["add"]) > 1e-9 or step["term"] == sold):
            lines.append(_line(step["name"], step["total"], f"+ {step['add']:,.{digits}f} {step['label']}"))
    lines.append(_line(f"Sell {terms.TERM_NAMES[sold]} {route['sell_place']}", route["dest_price"],
                       route.get("dest_note", "")))
    after = [("Financing and inspection", -seller), ("Curve over the voyage", -val("time_spread")),
             ("Discount to sell an import", -val("import_discount"))]
    units = route["pnl_musd"] * 1e6 / route["margin"] if route.get("pnl_musd") and route["margin"] else None
    buyer = [(label, add) for label, add in terms.beyond(route)]
    note = ""
    if buyer:
        note = (f"Past {terms.TERM_NAMES[sold]}, "
                + " and ".join(f"{label} ({add:,.{digits}f})" for label, add in buyer) + " are the buyer's.")
    return dict(margin=margin, days=route.get("laden_days") or route.get("voyage_days"), days_basis="at sea",
                units=units, unit=route["unit"], lines=lines, signed=False, over_lme=route.get("over_lme"),
                after=[(label, v) for label, v in after if abs(v) > 1e-9], terms_note=note,
                delay_day=route.get("delay_day"), has_ship=bool(route.get("tce_used")))


def _diversion(route, alternative):
    """A spot FOB cargo costs what its next best outlet pays, so the trade earns the gap in netbacks."""
    if route.get("netback") is None or alternative.get("netback") is None:
        return None
    place = alternative["route"].split(" → ")[-1]
    return _route_economics(route, alternative["netback"], f"{route['buy_basis']}, at its {place} netback")


def load_commodity_ideas(path, routes, curves):
    if not path or not Path(path).exists():
        return []
    by_id = {r["route_id"]: r for r in routes}
    ideas = json.loads(Path(path).read_text(encoding="utf-8"))
    for i in ideas:
        route = by_id.get(i.get("route_id"), {})
        compare = by_id.get(i.get("compare_route_id"))
        if i.get("buy_at") == "alternative" and compare:
            eco = _diversion(route, compare)
            compare = None                              # already inside the margin
        elif i.get("route_id"):
            eco = _route_economics(route)
        else:
            eco = _carry(i["economics"], curves)
        if eco is None:
            i["economics"] = None
            continue
        units = i.get("volume") or eco["units"]
        musd = (lambda m: None if units is None else m * units / 1e6)
        good = eco["margin"] + i["good"]["delta"]
        bad = eco["margin"] + i["bad"]["delta"]
        # Margin per day the cargo is tied up, to compare trades of different lengths.
        days = i.get("days") or eco.get("days")
        per_day = eco["margin"] / days if days else None
        delay = eco.get("delay_day")
        i["economics"] = dict(eco, units=units, good=good, bad=bad, pnl=musd(eco["margin"]), pnl_good=musd(good),
                              pnl_bad=musd(bad), compare=compare, days=days,
                              days_basis=i.get("days_basis") or eco.get("days_basis"), per_day=per_day,
                              pnl_day=None if per_day is None or units is None else per_day * units,
                              cover_days=eco["margin"] / delay if delay and eco.get("has_ship") and eco["margin"] > 0
                              else None)
    return ideas
