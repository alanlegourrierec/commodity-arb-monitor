"""Arbitrage economics for each route.

    margin = delivered price - origin price - freight - other costs

Other costs are cargo insurance, war-risk premium, transit losses, financing
and misc fees. Each route is sold on a delivery term (CFR, CIF, DAP or duty
paid) and only pays the costs that term leaves with the seller. For LNG the
freight also includes boil-off and canal tolls, and the margin is shown after
the (sunk) liquefaction fee.

Freight comes from an override file when one is given (WS, $/t, lump sum or
TCE $/day), otherwise from a simple round-voyage model:

    voyage cost = TCE x round-trip days + bunkers + port costs + canal

EU ETS allowances are added on legs that touch an EU port.

Timing. A cargo bought today loads in about a month and lands a voyage later.
Each price is read off its own forward curve for one of those two days, so the
purchase and the sale never mix contract months and nothing jumps when a front
contract expires. The time spread is what the destination curve takes away
(backwardation) or adds (contango) while the cargo is at sea.
"""

from datetime import date, datetime, timedelta

from .config import (ADJUSTMENTS, BBL_PER_TONNE, BBL_PER_TONNE_GENERIC, CO2_PER_T_FUEL, CO2_PER_T_LNG, CURVES,
                     EAST_LEGS, FEEDS, FREIGHT_KEY, GAL_PER_BBL, HULL_SOURCES, LEGS, LNG_FILL, LNG_T_PER_M3,
                     LOAD_LEAD_DAYS, MMBTU_PER_M3_LNG, MWH_PER_MMBTU, PARAM_SOURCES, PARAMS, PRICE_CURVE,
                     RFS_STANDARDS, ROUTES, STALE_DAYS, VESSELS)
from .prices import contract_month
from .terms import covers

LEFT_END_DAYS = 7      # an expired future still anchors the curve this long


class Context:
    """Prices + overrides, and a log of every assumption actually used."""

    def __init__(self, prices, overrides, curves=None, today=None):
        self.prices = prices
        self.overrides = overrides
        self.curves = curves or {}
        self.today = today or date.today()
        self.assumptions = []

    def price(self, key):
        if key in self.prices:
            return self.prices[key]["value"]
        if key in self.overrides:
            return self.overrides[key]["value"]
        return None

    def param(self, key, table=PARAMS, sources=PARAM_SOURCES, scope="Global", days=0):
        """Assumption value: an override wins, then a live market feed, then the default."""
        value, unit, label = table[key]
        source = sources.get(key, "Model default")
        feed = FEEDS.get(key)
        if key in self.overrides:
            o = self.overrides[key]
            value = o["value"] / 100 if unit.startswith("%") else o["value"]
            source = f"{o['source'] or 'Override'} ({o['date'] or 'no date'})"
        elif feed and feed[0] in self.prices:
            p = self.prices[feed[0]]
            value = self.at(feed[0], days) * feed[1]
            source = f"{p.get('source', 'Market')} {p.get('symbol', '')} ({p.get('date', '')})".strip()
        shown = value * 100 if unit.startswith("%") else value
        self.note(scope, key, label, shown, unit, source)
        return value

    def age(self, raw_date):
        try:
            return (self.today - datetime.strptime(raw_date, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            return None

    def adjustment(self, key, days=0):
        return self.param(key, ADJUSTMENTS, days=days)

    def note(self, scope, key, label, value, unit, source):
        if not any(a["scope"] == scope and a["key"] == key for a in self.assumptions):
            self.assumptions.append(dict(scope=scope, key=key, label=label, value=round(value, 6),
                                         unit=unit, source=source))

    def bunker_price(self, hub="RDM", days=0):
        if "VLSFO" in self.overrides:
            o = self.overrides["VLSFO"]
            self.note("Global", "VLSFO", "VLSFO bunker price", o["value"], "$/t", f"{o['source']} ({o['date']})")
            return o["value"]
        key = f"VLSFO_{hub}"
        if self.price(key) is not None:
            return self.at(key, days)
        brent = self.price("BRENT")
        value = brent * BBL_PER_TONNE_GENERIC * self.param("VLSFO_BRENT_RATIO")
        self.note("Global", "VLSFO", "VLSFO bunker price (Brent proxy)", value, "$/t", "Brent proxy")
        return value

    def stale(self, key, max_days=None):
        age = self.age(self.prices.get(key, {}).get("date", ""))
        return age is not None and age > (max_days or STALE_DAYS)

    def eua_usd(self):
        eua, fx = self.at("EUA", LOAD_LEAD_DAYS), self.price("EURUSD")
        if eua is None or fx is None:
            return 0.0
        return eua * fx

    def at(self, key, days):
        """Price of `key` for a cargo `days` from today, read off its forward curve.

        Each contract is pinned to a date and the days in between are interpolated, so the price for a
        given day does not step when a front contract expires. A benchmark without a curve keeps its
        front price.
        """
        name = PRICE_CURVE.get(key, key)
        points = self.points(name) if name in CURVES else []
        if not points:
            return self.price(key)
        target = self.today + timedelta(days=days)
        if target <= points[0][0]:
            return points[0][1]
        for (d0, p0), (d1, p1) in zip(points, points[1:]):
            if target <= d1:
                return p0 + (p1 - p0) * (target - d0).days / max((d1 - d0).days, 1)
        return points[-1][1]

    def points(self, name):
        """(date, price) along a curve, nearest first.

        A future meets the spot price when it expires, so it is pinned to its expiry. A monthly index
        or swap settles on the average of its month, so it is pinned to the 15th, and it still prices
        cargoes of that month after it stops trading. In both cases the last contract behind today
        stays as the left end, so nothing moves on the day it drops out.
        """
        rows = sorted(self.curves.get(name) or [], key=lambda c: c["expiry"])
        if CURVES[name]["basis"] == "delivery":
            months = sorted({contract_month(c["contract"]): c["close"] for c in rows}.items())
            ahead = [i for i, (month, _) in enumerate(months) if month >= (self.today.year, self.today.month)]
            first = max(ahead[0] - 1, 0) if ahead else len(months)      # one past month as the left end
            return [(date(y, m, 15), price) for (y, m), price in months[first:]]
        today = self.today.isoformat()
        recent = (self.today - timedelta(days=LEFT_END_DAYS)).isoformat()
        past = [c for c in rows if recent <= c["expiry"] < today][-1:]
        return [(date.fromisoformat(c["expiry"]), c["close"]) for c in past + [c for c in rows if c["expiry"] >= today]]


def timing(ctx, laden_days):
    """Days from today to loading and to landing, plus both dates for the page."""
    load = LOAD_LEAD_DAYS
    land = load + laden_days
    day = lambda n: (ctx.today + timedelta(days=round(n))).isoformat()
    return load, land, dict(load_date=day(load), land_date=day(land))


def ets_cost(ctx, leg, ship, sea_days):
    """EU allowances for one voyage, in $. LNG carriers burn boil-off gas instead of fuel oil."""
    share = leg.get("eu_ets", 0)
    if not share:
        return 0.0
    if ship.get("cargo_m3"):
        co2_per_day = PARAMS["LNG_BOIL_OFF"][0] * ship["cargo_m3"] * LNG_T_PER_M3 * CO2_PER_T_LNG
    else:
        co2_per_day = ship["fuel_t_day"] * CO2_PER_T_FUEL
    return co2_per_day * sea_days * share * ctx.eua_usd()


def round_voyage(ctx, leg_id, tce=None):
    """Round voyage, or a one-way trip for legs fixed on a trip basis (no ballast leg paid)."""
    leg = LEGS[leg_id]
    ship = VESSELS[leg["vessel"]]
    laden_days = leg["distance_nm"] / (ship["speed_kn"] * 24)
    sea_days = laden_days if leg.get("trip") else 2 * laden_days
    total_days = sea_days + ship["port_days"]
    tce = ship["default_tce"] if tce is None else tce
    hub = "SG" if leg_id in EAST_LEGS else "RDM"
    bunker_price = ctx.bunker_price(hub, LOAD_LEAD_DAYS)      # fuel is bought when the ship loads
    fuel_t = ship["fuel_t_day"] * sea_days
    bunkers = fuel_t * bunker_price
    ets = ets_cost(ctx, leg, ship, sea_days)
    fixed = bunkers + ship["port_costs"] + leg["canal"]
    return dict(leg=leg, ship=ship, laden_days=laden_days, sea_days=sea_days, total_days=total_days,
                tce=tce, bunkers=bunkers, fuel_t=fuel_t, bunker_price=bunker_price, bunker_hub=hub, ets=ets,
                fixed=fixed, cost=tce * total_days + fixed)


def war_risk(ctx, leg_id):
    """Additional war-risk premium for one voyage, in $ (premium x hull value)."""
    leg = LEGS[leg_id]
    ship = VESSELS[leg["vessel"]]
    key = f"WAR_{leg_id}"
    o = ctx.overrides.get(key)
    if o:
        pct = o["value"] / 100
        source = f"{o['source'] or 'Override'} ({o['date'] or 'no date'})"
    else:
        pct = 0.0
        source = "No public quote, fill in if known" if leg.get("war_zone") else "Outside listed areas"
    if o or leg.get("war_zone"):
        label = f"War risk {leg['name']}" + (f" ({leg['war_zone']})" if leg.get("war_zone") else "")
        ctx.note(leg_id, key, label, pct * 100, "% of hull", source)
    return pct * ship["hull_value"]


def leg_freight(ctx, leg_id):
    """Freight in $/t for a tanker or bulk leg, plus the implied TCE."""
    voyage = round_voyage(ctx, leg_id)
    cargo = voyage["leg"].get("cargo_t", voyage["ship"]["cargo_t"])
    o = ctx.overrides.get(FREIGHT_KEY.get(leg_id, leg_id))
    source = "Model"
    per_tonne = voyage["cost"] / cargo
    if o:
        unit = o["unit"].lower().replace(" ", "")
        if unit == "ws" and o.get("flat_rate"):
            per_tonne = o["value"] / 100 * o["flat_rate"]
            source = f"{o['source']} WS {o['value']:g} ({o['date']})"
        elif unit in ("$/t", "usd/t", "$/mt"):
            per_tonne = o["value"]
            source = f"{o['source']} $/t ({o['date']})"
        elif unit in ("$/voyage", "usd/voyage", "lumpsum"):
            per_tonne = o["value"] / cargo
            source = f"{o['source']} lump sum ({o['date']})"
        elif unit in ("$/day", "usd/day", "tce"):
            voyage = round_voyage(ctx, leg_id, tce=o["value"])
            per_tonne = voyage["cost"] / cargo
            source = f"{o['source']} TCE ({o['date']})"
        else:
            source = f"Model (unknown unit '{o['unit']}')"
        age = ctx.age(o["date"])
        if age is not None and age > STALE_DAYS:
            source += f", {age} days old"
    implied_tce = (per_tonne * cargo - voyage["fixed"]) / voyage["total_days"]
    ets = voyage["ets"] / cargo
    return dict(per_tonne=per_tonne + ets, ets_per_tonne=ets, source=source, tce=implied_tce, voyage=voyage,
                cargo_t=cargo)


def breakeven_tce(breakeven_per_tonne, freight):
    """TCE that makes the freight (allowances included) equal to the break-even freight."""
    v = freight["voyage"]
    return (breakeven_per_tonne * freight["cargo_t"] - v["fixed"] - v["ets"]) / v["total_days"]


def rvo_per_gallon(ctx, days=0):
    """RINs an obligated party retires for a gallon of gasoline or diesel, in $/gal. None without RIN prices.

    The standard is the one of the year the fuel is produced or imported, `days` from today.
    """
    year = (ctx.today + timedelta(days=round(days))).year
    year = min(max(year, min(RFS_STANDARDS)), max(RFS_STANDARDS))
    std = RFS_STANDARDS[year]
    rins = [ctx.price(k) for k in ("RIN_D6", "RIN_D3", "RIN_D4", "RIN_D5")]
    if None in rins:
        return None
    d6, d3, d4, d5 = rins
    conventional = std["total"] - std["advanced"]
    other_advanced = std["advanced"] - std["bbd"] - std["cellulosic"]
    value = conventional * d6 + std["cellulosic"] * d3 + std["bbd"] * d4 + other_advanced * d5
    quoted = ctx.prices.get("RIN_D6", {}).get("date") or "no date"
    ctx.note("Global", f"RVO_{year}", f"RINs owed on a gallon of gasoline or diesel in {year}", value * 100, "c/gal",
             f"{year} standards (EPA, 1 April 2026) x RIN prices of {quoted}")
    return value


def delay_cost(ctx, hire, cargo, value):
    """What one more day costs per unit of cargo: the ship's hire and the money tied up."""
    return max(hire, 0.0) / cargo + value * ctx.param("FINANCING_RATE") / 360


def status(margin, threshold):
    if margin is None:
        return "Missing price"
    if margin >= threshold:
        return "Open"
    if margin >= 0:
        return "Marginal"
    return "Closed"


def _base_row(route, voyage):
    return dict(route_id=route["id"], family=route["family"], route=route["name"], leg=route["leg"],
                leg_name=voyage["leg"]["name"], vessel=voyage["leg"]["vessel"], unit=route["unit"],
                threshold=route["threshold"], distance_nm=voyage["leg"]["distance_nm"],
                laden_days=voyage["laden_days"], voyage_days=voyage["total_days"],
                bunker_hub=voyage["bunker_hub"], curve=route.get("curve"),
                buy_basis=route["buy"], sell_term=route["sell"][0], sell_place=route["sell"][1],
                whose=route.get("whose", ""))


def _oil_prices(ctx, route, gal_per_t, load, land):
    """(purchase price at loading, destination price at loading, destination price at landing, note)."""
    rid = route["id"]
    at = ctx.at

    def pair(dest):
        return dest(load), dest(land)

    if rid == "CRUDE_USGC_NWE":
        # WTI Midland cargoes in Rotterdam price off Dated Brent. Brent futures only if Dated is missing.
        key, name = ("DATED_BRENT", "Dated Brent") if ctx.price("DATED_BRENT") is not None else ("BRENT", "Brent futures")
        diff = ctx.adjustment("WTI_MIDLAND_CIF_DIFF")
        dest = lambda d: None if at(key, d) is None else at(key, d) + diff
        return (at("WTI_HOUSTON", load), *pair(dest), f"{name} + WTI Midland CIF diff")
    if rid == "CRUDE_USGC_ASIA":
        # A Chinese refiner compares WTI with Murban landed the same day. Murban loads in Fujairah, outside
        # Hormuz, so its ship is costed at the daily rate a VLCC earns on the Houston voyage. The Baltic TD3C
        # rate loads inside the Gulf and carries a war premium that a Fujairah cargo does not pay.
        own = leg_freight(ctx, route["leg"])
        voyage = round_voyage(ctx, "TD3C", tce=max(own["tce"], 0.0))
        cargo = voyage["ship"]["cargo_t"]
        freight_bbl = voyage["cost"] / cargo / BBL_PER_TONNE["murban"]
        war_bbl = war_risk(ctx, "TD3C") / (cargo * BBL_PER_TONNE["murban"])
        extra = freight_bbl + war_bbl + ctx.adjustment("MURBAN_QUALITY_DIFF")
        sail = voyage["laden_days"]                      # the Murban cargo loads this much before it lands
        dest = lambda d: None if at("MURBAN", d - sail) is None else at("MURBAN", d - sail) + extra
        note = f"Murban + Fujairah → Ningbo freight ({freight_bbl:.2f} $/bbl) at the daily hire of the Houston voyage"
        if war_bbl:
            note += f" + war risk ({war_bbl:.2f})"
        return (at("WTI_HOUSTON", load), *pair(dest), note)
    if rid == "DIESEL_USGC_NWE":
        ho = at("ULSD_NYH", load)
        origin = None if ho is None else (ho + ctx.adjustment("ULSD_USGC_DIFF", load) / 100) * gal_per_t
        diff = ctx.adjustment("GASOIL_CARGO_DIFF")
        dest = lambda d: None if at("GASOIL_ICE", d) is None else at("GASOIL_ICE", d) + diff
        return (origin, *pair(dest), "ICE gasoil + cargo diff")
    if rid == "DIESEL_NWE_USAC":
        gasoil = at("GASOIL_ICE", load)
        origin = None if gasoil is None else gasoil + ctx.adjustment("GASOIL_CARGO_DIFF")
        dest = lambda d: None if at("ULSD_NYH", d) is None else at("ULSD_NYH", d) * gal_per_t
        return (origin, *pair(dest), "NYMEX ULSD (NY Harbor)")
    if rid == "GASOLINE_NWE_USAC":
        adj = ctx.adjustment("RBOB_QUALITY_ADJ")
        dest = lambda d: None if at("RBOB", d) is None else at("RBOB", d) * gal_per_t - adj
        return (at("EUROBOB", load), *pair(dest), "NYMEX RBOB - blending cost")
    raise KeyError(rid)


def oil_route(ctx, route):
    bbl_per_t = BBL_PER_TONNE[route["density"]]
    gal_per_t = bbl_per_t * GAL_PER_BBL
    freight = leg_freight(ctx, route["leg"])
    voyage = freight["voyage"]
    load, land, dates = timing(ctx, voyage["laden_days"])
    origin, dest, landed, dest_note = _oil_prices(ctx, route, gal_per_t, load, land)

    crude = route["family"] == "Crude"
    to_unit = bbl_per_t if crude else 1.0        # $/t -> route unit
    term = route["sell"][0]
    row = _base_row(route, voyage)
    row.update(dest_note=dest_note, freight=freight["per_tonne"] / to_unit, freight_source=freight["source"],
               ets=freight["ets_per_tonne"] / to_unit, tce_used=freight["tce"],
               cargo_label=f"{freight['cargo_t'] / 1000:.0f} kt", **dates)
    if origin is None or dest is None:
        row.update(status="Missing price", origin_price=origin, dest_price=dest)
        return row

    # RINs: a Gulf Coast refiner owes none on barrels it exports, so an export cargo is worth the
    # pipeline price less the obligation. An importer into New York owes them in full.
    rvo = 0.0
    if route.get("rvo"):
        per_gal = rvo_per_gallon(ctx, load if route["rvo"] == "export" else land)
        row["rvo_missing"] = per_gal is None
        rvo_t = (per_gal or 0.0) * gal_per_t
        if route["rvo"] == "export":
            origin -= rvo_t
            row["rvo_credit"] = rvo_t
        else:
            rvo = rvo_t
    duty = ctx.param(route["duty"]) * bbl_per_t if route.get("duty") else 0.0

    structure = dest - landed                    # what the destination curve does while the cargo sails

    units = freight["cargo_t"] * to_unit
    fobbing = ctx.param("CRUDE_DOCK_FEE") if crude else 0.0      # the Houston price is in the terminal
    fob = origin + fobbing
    financing_days = voyage["laden_days"] + voyage["ship"]["port_days"] / 2
    losses = ctx.param("LOSSES_CRUDE" if crude else "LOSSES_PRODUCTS") * fob
    misc = ctx.param("MISC_CRUDE") if crude else ctx.param("MISC_PRODUCTS")
    cargo_insurance = ctx.param("CARGO_INSURANCE") * fob
    war = war_risk(ctx, route["leg"]) / units
    financing = fob * ctx.param("FINANCING_RATE") * financing_days / 360
    other = fobbing + war + misc + financing
    if covers(term, "CIF"):
        other += cargo_insurance
    if covers(term, "DAP"):
        other += losses
    if covers(term, "DDP"):
        other += duty + rvo
    margin = dest - origin - row["freight"] - other - structure
    breakeven = dest - origin - other - structure
    row.update(origin_price=origin, dest_price=dest, spread=dest - origin, time_spread=structure,
               buy_price=origin, fobbing=fobbing, fobbing_note="terminal and dock fees" if crude else None,
               insurance=cargo_insurance + war, cargo_insurance=cargo_insurance, war_risk=war,
               losses=losses, duty=duty, rvo=rvo, financing=financing, misc=misc, seller_costs=financing + misc,
               other_costs=other, margin=margin, status=status(margin, route["threshold"]),
               breakeven_freight=breakeven, breakeven_tce=breakeven_tce(breakeven * to_unit, freight),
               delay_day=delay_cost(ctx, freight["tce"], units, fob), pnl_musd=margin * units / 1e6)
    return row


def lng_route(ctx, route):
    o = ctx.overrides.get("LNG_ATL")
    voyage = round_voyage(ctx, route["leg"], tce=o["value"] if o else None)
    ship = voyage["ship"]
    loaded = ship["cargo_m3"] * LNG_FILL * MMBTU_PER_M3_LNG
    load, land, dates = timing(ctx, voyage["laden_days"])
    hh = ctx.at("HENRY_HUB", load)
    feedgas = None if hh is None else ctx.param("HH_MULTIPLIER") * hh
    fee = ctx.param("LIQUEFACTION_FEE")
    if route["id"] == "LNG_US_NWE":
        regas = ctx.param("REGAS_EUROPE")

        def dest_at(day):
            usd = ttf_usd(ctx, day)
            return None if usd is None else usd - regas
        dest_note = "TTF - regas"
    else:
        dest_at = lambda day: ctx.at("JKM", day)
        dest_note = "JKM (DES Japan/Korea)"
    dest, landed = dest_at(load), dest_at(land)

    row = _base_row(route, voyage)
    source = "Model"
    if o:
        age = ctx.age(o["date"])
        source = f"{o['source']} TCE ({o['date']})" + (f", {age} days old" if age is not None and age > STALE_DAYS else "")
    row.update(dest_note=dest_note, freight_source=source,
               tce_used=voyage["tce"], cargo_label=f"{loaded / 1e6:.2f} TBtu", **dates)
    if dest is None or feedgas is None:
        row.update(status="Missing price", origin_price=feedgas, dest_price=dest)
        return row

    bor = ctx.param("LNG_BOIL_OFF")
    boil_off = bor * voyage["laden_days"]              # share of the cargo lost on the laden leg
    delivered = loaded * (1 - boil_off)
    ballast_fuel = bor * loaded * voyage["laden_days"] * feedgas   # heel burnt on the way back
    hire = voyage["tce"] * voyage["total_days"]
    ship_costs = hire + ship["port_costs"] + voyage["leg"]["canal"] + ballast_fuel + voyage["ets"]
    structure = dest - landed                          # the cargo is sold at the price of the day it lands
    cargo_insurance = ctx.param("CARGO_INSURANCE") * landed
    war_usd = war_risk(ctx, route["leg"])
    insurance = cargo_insurance + war_usd / loaded
    financing = (feedgas + fee) * ctx.param("FINANCING_RATE") * voyage["laden_days"] / 360
    shipping = ship_costs / loaded + landed * boil_off
    netback = dest - shipping - insurance - financing - structure    # $/MMBtu loaded, FOB basis
    margin_variable = netback - feedgas
    margin = margin_variable - fee
    fixed = ship["port_costs"] + voyage["leg"]["canal"] + ballast_fuel + war_usd + voyage["ets"]
    per_loaded = feedgas + fee + cargo_insurance + financing
    tce_be = (delivered * landed - fixed - loaded * per_loaded) / voyage["total_days"]
    row.update(origin_price=feedgas + fee, dest_price=dest, spread=dest - feedgas - fee, time_spread=structure,
               buy_price=feedgas + fee, freight=shipping, hire=hire / loaded, boil_off=landed * boil_off,
               ets=voyage["ets"] / loaded, canal=voyage["leg"]["canal"] / loaded, port=ship["port_costs"] / loaded,
               ballast_fuel=ballast_fuel / loaded, insurance=insurance, cargo_insurance=cargo_insurance,
               war_risk=war_usd / loaded, financing=financing, seller_costs=financing,
               other_costs=insurance + financing, margin=margin,
               margin_variable=margin_variable, netback=netback, feedgas=feedgas, liquefaction_fee=fee,
               boil_off_pct=boil_off * 100, status=status(margin, route["threshold"]),
               breakeven_freight=dest - feedgas - fee - insurance - financing - structure, breakeven_tce=tce_be,
               delay_day=delay_cost(ctx, voyage["tce"], loaded, feedgas + fee) + landed * bor,
               pnl_musd=margin * loaded / 1e6)
    return row


def ttf_usd(ctx, days):
    """TTF in $/MMBtu for a day `days` from today."""
    ttf, fx = ctx.at("TTF_EUR", days), ctx.price("EURUSD")
    if ttf is None or fx is None:
        return ctx.price("TTF_USD")
    return ttf * fx * MWH_PER_MMBTU


def spreads(ctx):
    """Key spreads, both sides read for the day a cargo bought today would load.

    Front months do not roll on the same day, so a spread between two front prices can compare
    November with December. Reading both curves for one date removes that.
    """
    day = LOAD_LEAD_DAYS
    p = lambda key: ctx.at(key, day)
    gal_d = BBL_PER_TONNE["diesel"] * GAL_PER_BBL
    gal_g = BBL_PER_TONNE["gasoline"] * GAL_PER_BBL
    rows = []

    def add(key, label, a, b, unit, note=""):
        value = None if a is None or b is None else round(a - b, 3)
        rows.append(dict(key=key, label=label, value=value, unit=unit,
                         note=note if value is not None else "Missing price"))

    ho_t = None if p("ULSD_NYH") is None else p("ULSD_NYH") * gal_d
    rb_t = None if p("RBOB") is None else p("RBOB") * gal_g
    hh, ttf = p("HENRY_HUB"), ttf_usd(ctx, day)
    add("BRENT_WTIH", "Brent - WTI Houston", p("BRENT"), p("WTI_HOUSTON"), "$/bbl", "Compare with TD25 in $/bbl")
    add("WTIH_WTIC", "WTI Houston - WTI Cushing", p("WTI_HOUSTON"), p("WTI_CUSHING"), "$/bbl",
        "Implied pipeline cost Cushing → Gulf Coast")
    add("MURBAN_WTIH", "Murban - WTI Houston", p("MURBAN"), p("WTI_HOUSTON"), "$/bbl", "East-West light sweet spread")
    add("HO_GASOIL", "ULSD NYH - ICE gasoil", ho_t, p("GASOIL_ICE"), "$/t",
        "Has to pay the freight and the RINs before Europe ships diesel to the US")
    add("RBOB_EBOB", "RBOB - Eurobob", rb_t, p("EUROBOB"), "$/t", "Has to pay TC2, the blending cost and the RINs")
    add("JKM_TTF", "JKM - TTF", p("JKM"), ttf, "$/MMBtu", "Compare with the Asia vs Europe shipping gap")
    add("TTF_HH", "TTF - 1.15 x HH", ttf, None if hh is None else 1.15 * hh, "$/MMBtu",
        "Gross margin before shipping and liquefaction")
    vlcc = VESSELS["VLCC"]
    hormuz = ctx.param("WAR_GULF") * vlcc["hull_value"] / (vlcc["cargo_t"] * BBL_PER_TONNE_GENERIC)
    rows.append(dict(key="WAR_HORMUZ_VLCC", label="Hormuz war-risk premium (VLCC)", value=round(hormuz, 3),
                     unit="$/bbl", note="For reference, premium x 140 M$ hull / 2 Mbbl"))
    brent = p("BRENT")
    if brent is not None and ho_t is not None:
        add("CRACK_DIESEL", "ULSD NYH crack vs Brent", ho_t / gal_d * GAL_PER_BBL, brent, "$/bbl")
    if brent is not None and rb_t is not None:
        add("CRACK_GASOLINE", "RBOB crack vs Brent", rb_t / gal_g * GAL_PER_BBL, brent, "$/bbl")
    return rows


def voyage_assumptions():
    rows = []
    for leg_id, leg in LEGS.items():
        ship = VESSELS[leg["vessel"]]
        rows.append(dict(scope=leg_id, key="VOYAGE", label=f"{leg['name']} - {leg['vessel']}",
                         value=leg["distance_nm"], unit="nm",
                         source=(f"{ship['speed_kn']:g} kn, {ship['port_days']:g} port days, "
                                 f"default TCE {ship['default_tce']:,.0f} $/day, port {ship['port_costs']:,.0f} $, "
                                 f"canal {leg['canal']:,.0f} $, hull {ship['hull_value'] / 1e6:,.0f} M$ "
                                 f"({HULL_SOURCES.get(leg['vessel'], 'assumption')})")))
    return rows


def evaluate(prices, overrides, curves=None, today=None):
    ctx = Context(prices, overrides, curves, today)
    rows = [lng_route(ctx, r) if r["family"] == "LNG" else oil_route(ctx, r) for r in ROUTES]
    rows = [{k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()} for row in rows]
    lng = [r for r in rows if r["family"] == "LNG" and r.get("netback") is not None]
    if lng:
        best = max(lng, key=lambda r: r["netback"])["route_id"]
        for r in rows:
            if r["family"] == "LNG":
                r["best_destination"] = r["route_id"] == best
    return dict(routes=rows, spreads=spreads(ctx), assumptions=ctx.assumptions + voyage_assumptions())
