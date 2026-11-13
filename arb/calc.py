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

EU ETS allowances are added on legs that touch an EU port. The time spread is
what the forward curve takes away (backwardation) or adds (contango) between
loading and delivery: the cargo is bought on today's price and sold on the
price for its arrival date.
"""

from datetime import date, datetime, timedelta

from .config import (ADJUSTMENTS, BBL_PER_TONNE, BBL_PER_TONNE_GENERIC, CO2_PER_T_FUEL, CO2_PER_T_LNG, CURVES,
                     EAST_LEGS, FEEDS, FREIGHT_KEY, GAL_PER_BBL, HULL_SOURCES, LEGS, LNG_FILL, LNG_T_PER_M3,
                     MMBTU_PER_M3_LNG, MWH_PER_MMBTU, PARAM_SOURCES, PARAMS, RFS_STANDARDS, ROUTES, STALE_DAYS,
                     VESSELS)
from .terms import covers

MONTH_CODES = "FGHJKMNQUVXZ"


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

    def param(self, key, table=PARAMS, sources=PARAM_SOURCES, scope="Global"):
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
            value = p["value"] * feed[1]
            source = f"{p.get('source', 'Market')} {p.get('symbol', '')} ({p.get('date', '')})".strip()
        shown = value * 100 if unit.startswith("%") else value
        self.note(scope, key, label, shown, unit, source)
        return value

    def age(self, raw_date):
        try:
            return (self.today - datetime.strptime(raw_date, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            return None

    def adjustment(self, key):
        return self.param(key, ADJUSTMENTS)

    def note(self, scope, key, label, value, unit, source):
        if not any(a["scope"] == scope and a["key"] == key for a in self.assumptions):
            self.assumptions.append(dict(scope=scope, key=key, label=label, value=round(value, 6),
                                         unit=unit, source=source))

    def bunker_price(self, hub="RDM"):
        if "VLSFO" in self.overrides:
            o = self.overrides["VLSFO"]
            self.note("Global", "VLSFO", "VLSFO bunker price", o["value"], "$/t", f"{o['source']} ({o['date']})")
            return o["value"]
        key = f"VLSFO_{hub}"
        if self.price(key) is not None:
            return self.price(key)
        brent = self.price("BRENT")
        value = brent * BBL_PER_TONNE_GENERIC * self.param("VLSFO_BRENT_RATIO")
        self.note("Global", "VLSFO", "VLSFO bunker price (Brent proxy)", value, "$/t", "Brent proxy")
        return value

    def stale(self, key, max_days=None):
        age = self.age(self.prices.get(key, {}).get("date", ""))
        return age is not None and age > (max_days or STALE_DAYS)

    def eua_usd(self):
        eua, fx = self.price("EUA"), self.price("EURUSD")
        if eua is None or fx is None:
            return 0.0
        return eua * fx

    def forward(self, key, days):
        """(front, price for a cargo arriving in `days`) on curve `key`, or None without a curve."""
        curve = self.curves.get(key)
        if not curve or len(curve) < 2:
            return None
        front = curve[0]["close"]
        if CURVES[key]["basis"] == "delivery":
            arrival = self.today + timedelta(days=days)
            target = (arrival.year, arrival.month)
            months = [((int(c["contract"][-4:]), MONTH_CODES.index(c["contract"][-5]) + 1), c["close"]) for c in curve]
            if target <= months[0][0]:
                return front, front
            return front, next((close for ym, close in months if ym == target), months[-1][1])
        e0, e1 = (datetime.strptime(c["expiry"], "%Y-%m-%d").date() for c in curve[:2])
        slope = (curve[1]["close"] - front) / max((e1 - e0).days, 1)
        return front, front + slope * days


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
    bunker_price = ctx.bunker_price(hub)
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


def time_spread(ctx, curve_key, days, factor=1.0):
    """What the curve costs between loading and arrival, in route units (positive = backwardation)."""
    fwd = ctx.forward(curve_key, days) if curve_key else None
    if fwd is None:
        return 0.0, None
    front, later = fwd
    return (front - later) * factor, later


def rvo_per_gallon(ctx):
    """RINs an obligated party retires for a gallon of gasoline or diesel, in $/gal. None without RIN prices."""
    year = min(max(ctx.today.year, min(RFS_STANDARDS)), max(RFS_STANDARDS))
    std = RFS_STANDARDS[year]
    rins = [ctx.price(k) for k in ("RIN_D6", "RIN_D3", "RIN_D4", "RIN_D5")]
    if None in rins:
        return None
    d6, d3, d4, d5 = rins
    conventional = std["total"] - std["advanced"]
    other_advanced = std["advanced"] - std["bbd"] - std["cellulosic"]
    value = conventional * d6 + std["cellulosic"] * d3 + std["bbd"] * d4 + other_advanced * d5
    quoted = ctx.prices.get("RIN_D6", {}).get("date") or "no date"
    ctx.note("Global", "RVO", "RINs owed on a gallon of gasoline or diesel", value * 100, "c/gal",
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


def _oil_prices(ctx, route, gal_per_t):
    rid = route["id"]
    p = ctx.price
    if rid == "CRUDE_USGC_NWE":
        # WTI Midland cargoes in Rotterdam price off Dated Brent. Front-month Brent only if Dated is missing.
        dated = p("DATED_BRENT")
        base, name = (dated, "Dated Brent") if dated is not None else (p("BRENT"), "Brent futures")
        dest = None if base is None else base + ctx.adjustment("WTI_MIDLAND_CIF_DIFF")
        return p("WTI_HOUSTON"), dest, f"{name} + WTI Midland CIF diff"
    if rid == "CRUDE_USGC_ASIA":
        # A Chinese refiner compares WTI with Murban delivered: Murban FOB + TD3C freight + war risk.
        td3c = leg_freight(ctx, "TD3C")
        bbl = td3c["cargo_t"] * BBL_PER_TONNE["murban"]
        freight_bbl = td3c["per_tonne"] / BBL_PER_TONNE["murban"]
        war_bbl = war_risk(ctx, "TD3C") / bbl
        murban = p("MURBAN")
        dest = None if murban is None else murban + freight_bbl + war_bbl + ctx.adjustment("MURBAN_QUALITY_DIFF")
        return p("WTI_HOUSTON"), dest, f"Murban + TD3C freight ({freight_bbl:.2f}) + war risk ({war_bbl:.2f}) $/bbl"
    if rid == "DIESEL_USGC_NWE":
        ho = p("ULSD_NYH")
        origin = None if ho is None else (ho + ctx.adjustment("ULSD_USGC_DIFF") / 100) * gal_per_t
        gasoil = p("GASOIL_ICE")
        dest = None if gasoil is None else gasoil + ctx.adjustment("GASOIL_CARGO_DIFF")
        return origin, dest, "ICE gasoil + cargo diff"
    if rid == "DIESEL_NWE_USAC":
        gasoil = p("GASOIL_ICE")
        origin = None if gasoil is None else gasoil + ctx.adjustment("GASOIL_CARGO_DIFF")
        ho = p("ULSD_NYH")
        return origin, None if ho is None else ho * gal_per_t, "NYMEX ULSD (NY Harbor)"
    if rid == "GASOLINE_NWE_USAC":
        rbob = p("RBOB")
        dest = None if rbob is None else rbob * gal_per_t - ctx.adjustment("RBOB_QUALITY_ADJ")
        return p("EUROBOB"), dest, "NYMEX RBOB - blending cost"
    raise KeyError(rid)


def oil_route(ctx, route):
    bbl_per_t = BBL_PER_TONNE[route["density"]]
    gal_per_t = bbl_per_t * GAL_PER_BBL
    freight = leg_freight(ctx, route["leg"])
    voyage = freight["voyage"]
    origin, dest, dest_note = _oil_prices(ctx, route, gal_per_t)

    crude = route["family"] == "Crude"
    to_unit = bbl_per_t if crude else 1.0        # $/t -> route unit
    term = route["sell"][0]
    row = _base_row(route, voyage)
    row.update(dest_note=dest_note, freight=freight["per_tonne"] / to_unit, freight_source=freight["source"],
               ets=freight["ets_per_tonne"] / to_unit, tce_used=freight["tce"],
               cargo_label=f"{freight['cargo_t'] / 1000:.0f} kt")
    if origin is None or dest is None:
        row.update(status="Missing price", origin_price=origin, dest_price=dest)
        return row

    # RINs: a Gulf Coast refiner owes none on barrels it exports, so an export cargo is worth the
    # pipeline price less the obligation. An importer into New York owes them in full.
    rvo = 0.0
    if route.get("rvo"):
        per_gal = rvo_per_gallon(ctx)
        row["rvo_missing"] = per_gal is None
        rvo_t = (per_gal or 0.0) * gal_per_t
        if route["rvo"] == "export":
            origin -= rvo_t
            row["rvo_credit"] = rvo_t
        else:
            rvo = rvo_t
    duty = ctx.param(route["duty"]) * bbl_per_t if route.get("duty") else 0.0

    curve_factor = {"HO": gal_per_t, "RBOB": gal_per_t}.get(route.get("curve"), 1.0)
    structure, _ = time_spread(ctx, route.get("curve"), voyage["laden_days"], curve_factor)

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
    hh = ctx.price("HENRY_HUB")
    feedgas = None if hh is None else ctx.param("HH_MULTIPLIER") * hh
    fee = ctx.param("LIQUEFACTION_FEE")
    if route["id"] == "LNG_US_NWE":
        ttf = ctx.price("TTF_USD")
        dest = None if ttf is None else ttf - ctx.param("REGAS_EUROPE")
        dest_note = "TTF - regas"
    else:
        dest = ctx.price("JKM")
        dest_note = "JKM (DES Japan/Korea)"

    row = _base_row(route, voyage)
    source = "Model"
    if o:
        age = ctx.age(o["date"])
        source = f"{o['source']} TCE ({o['date']})" + (f", {age} days old" if age is not None and age > STALE_DAYS else "")
    row.update(dest_note=dest_note, freight_source=source,
               tce_used=voyage["tce"], cargo_label=f"{loaded / 1e6:.2f} TBtu")
    if dest is None or feedgas is None:
        row.update(status="Missing price", origin_price=feedgas, dest_price=dest)
        return row

    bor = ctx.param("LNG_BOIL_OFF")
    boil_off = bor * voyage["laden_days"]              # share of the cargo lost on the laden leg
    delivered = loaded * (1 - boil_off)
    ballast_fuel = bor * loaded * voyage["laden_days"] * feedgas   # heel burnt on the way back
    hire = voyage["tce"] * voyage["total_days"]
    ship_costs = hire + ship["port_costs"] + voyage["leg"]["canal"] + ballast_fuel + voyage["ets"]
    factor = ctx.price("EURUSD") * MWH_PER_MMBTU if route.get("curve") == "TTF" and ctx.price("EURUSD") else 1.0
    structure, _ = time_spread(ctx, route.get("curve"), voyage["laden_days"], factor)
    cargo_insurance = ctx.param("CARGO_INSURANCE") * dest
    war_usd = war_risk(ctx, route["leg"])
    insurance = cargo_insurance + war_usd / loaded
    financing = (feedgas + fee) * ctx.param("FINANCING_RATE") * voyage["laden_days"] / 360
    shipping = ship_costs / loaded + dest * boil_off
    netback = dest - shipping - insurance - financing - structure    # $/MMBtu loaded, FOB basis
    margin_variable = netback - feedgas
    margin = margin_variable - fee
    fixed = ship["port_costs"] + voyage["leg"]["canal"] + ballast_fuel + war_usd + voyage["ets"]
    per_loaded = feedgas + fee + cargo_insurance + financing + structure
    tce_be = (delivered * dest - fixed - loaded * per_loaded) / voyage["total_days"]
    row.update(origin_price=feedgas + fee, dest_price=dest, spread=dest - feedgas - fee, time_spread=structure,
               buy_price=feedgas + fee, freight=shipping, hire=hire / loaded, boil_off=dest * boil_off,
               ets=voyage["ets"] / loaded, canal=voyage["leg"]["canal"] / loaded, port=ship["port_costs"] / loaded,
               ballast_fuel=ballast_fuel / loaded, insurance=insurance, cargo_insurance=cargo_insurance,
               war_risk=war_usd / loaded, financing=financing, seller_costs=financing,
               other_costs=insurance + financing, margin=margin,
               margin_variable=margin_variable, netback=netback, feedgas=feedgas, liquefaction_fee=fee,
               boil_off_pct=boil_off * 100, status=status(margin, route["threshold"]),
               breakeven_freight=dest - feedgas - fee - insurance - financing - structure, breakeven_tce=tce_be,
               delay_day=delay_cost(ctx, voyage["tce"], loaded, feedgas + fee) + dest * bor,
               pnl_musd=margin * loaded / 1e6)
    return row


def spreads(ctx):
    p = ctx.price
    gal_d = BBL_PER_TONNE["diesel"] * GAL_PER_BBL
    gal_g = BBL_PER_TONNE["gasoline"] * GAL_PER_BBL
    rows = []

    def add(key, label, a, b, unit, note=""):
        value = None if a is None or b is None else round(a - b, 3)
        rows.append(dict(key=key, label=label, value=value, unit=unit,
                         note=note if value is not None else "Missing price"))

    ho_t = None if p("ULSD_NYH") is None else p("ULSD_NYH") * gal_d
    rb_t = None if p("RBOB") is None else p("RBOB") * gal_g
    hh = p("HENRY_HUB")
    add("BRENT_WTIH", "Brent - WTI Houston", p("BRENT"), p("WTI_HOUSTON"), "$/bbl", "Compare with TD25 in $/bbl")
    add("WTIH_WTIC", "WTI Houston - WTI Cushing", p("WTI_HOUSTON"), p("WTI_CUSHING"), "$/bbl",
        "Implied pipeline cost Cushing → Gulf Coast")
    add("MURBAN_WTIH", "Murban - WTI Houston", p("MURBAN"), p("WTI_HOUSTON"), "$/bbl", "East-West light sweet spread")
    add("HO_GASOIL", "ULSD NYH - ICE gasoil", ho_t, p("GASOIL_ICE"), "$/t", "When positive, Europe can ship diesel to the US")
    add("RBOB_EBOB", "RBOB - Eurobob", rb_t, p("EUROBOB"), "$/t", "Compare with TC2")
    add("JKM_TTF", "JKM - TTF", p("JKM"), p("TTF_USD"), "$/MMBtu", "Compare with the Asia vs Europe shipping gap")
    add("TTF_HH", "TTF - 1.15 x HH", p("TTF_USD"), None if hh is None else 1.15 * hh, "$/MMBtu",
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
