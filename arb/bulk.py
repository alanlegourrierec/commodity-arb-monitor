"""Dry bulk and base metals, grouped the way the desks split them.

Coal desk
  Thermal swing: South Africa can send a Richards Bay cargo to Europe (paid at
  API2) or to Japan, where it competes with Newcastle coal (NEWC + Newcastle →
  Japan freight). Both are compared with API4.
  India: a cement or power buyer picks the cheapest delivered energy between
  South African coal, Indonesian coal and US petcoke ($ per million kcal,
  import duty included).
  Coking coal: delivered cost of Australian PLV to Chinese and Indian mills.
  Carbon: German clean dark and clean spark spreads, and the EUA price at
  which gas becomes cheaper than coal.

Ferrous desk
  Iron ore: the index is CFR China, so the question is the FOB netback per
  origin and how much of the delivered price the Brazilian freight eats.
  Scrap: netback of a US or European scrap cargo sold CFR Turkey or India.
  Steel: landed cost of imported HRC (EU coil into the US, Chinese coil into
  the EU) against local prices, tariffs and anti-dumping duties included.

Metals: a tonne sitting in an LME warehouse in Rotterdam can be shipped to
the US (COMEX copper, Midwest aluminium premium) or Japan (MJP premium).
The US aluminium tariff is paid on the metal value, which is what usually
decides that trade. Aluminium in Rotterdam also fetches its own premium
(duty-unpaid), which the holder gives up by shipping it out, so it is
counted as a cost.
"""

from .calc import Context, breakeven_tce, delay_cost, leg_freight, status, time_spread
from .config import BULK_PARAM_SOURCES, BULK_PARAMS, BULK_ROUTES, LB_PER_TONNE, METAL_LEGS, ST_PER_TONNE
from .terms import covers


def _param(ctx, key):
    return ctx.param(key, BULK_PARAMS, BULK_PARAM_SOURCES, scope="Bulk")


def cbam_per_tonne(ctx):
    """CBAM certificates for a tonne of Chinese hot-rolled coil, valued at the EUA price, in $/t.

    certificates/t = default emissions x (1 + mark-up) - CBAM factor x benchmark
    The certificate price is the quarterly average of EUA auctions, so the EUA future is a close proxy.
    """
    certs = (_param(ctx, "CBAM_DEFAULT_HRC_CN") * (1 + _param(ctx, "CBAM_MARKUP"))
             - _param(ctx, "CBAM_FACTOR") * _param(ctx, "CBAM_BENCHMARK_HRC"))
    return max(certs, 0.0) * ctx.eua_usd(), certs


def _row(route, leg_name, distance, vessel):
    return dict(route_id=route["id"], family=route["family"], route=route["name"], leg=route["leg"],
                leg_name=leg_name, vessel=vessel, unit=route["unit"], threshold=route.get("threshold"),
                distance_nm=distance, buy_basis=route["buy"], sell_term=route["sell"][0],
                sell_place=route["sell"][1])


def _voyage_row(ctx, route):
    freight = leg_freight(ctx, route["leg"])
    v = freight["voyage"]
    row = _row(route, v["leg"]["name"], v["leg"]["distance_nm"], v["leg"]["vessel"])
    row.update(freight=freight["per_tonne"], freight_source=freight["source"], ets=freight["ets_per_tonne"],
               tce_used=freight["tce"], voyage_days=v["total_days"], laden_days=v["laden_days"],
               cargo_label=f"{freight['cargo_t'] / 1000:.0f} kt")
    return row, freight


def iron_ore(ctx, route):
    freight = leg_freight(ctx, route["leg"])
    voyage = freight["voyage"]
    row = _row(route, voyage["leg"]["name"], voyage["leg"]["distance_nm"], voyage["leg"]["vessel"])
    cfr = ctx.price("IRON_ORE_62")
    moisture = _param(ctx, "IRON_ORE_MOISTURE")
    per_dmt = freight["per_tonne"] / (1 - moisture)      # freight is paid per wet tonne
    row.update(freight=per_dmt, freight_wmt=freight["per_tonne"], freight_source=freight["source"],
               tce_used=freight["tce"], voyage_days=voyage["total_days"], laden_days=voyage["laden_days"],
               cargo_label="170 kt")
    if cfr is None:
        row.update(status="Missing price")
        return row
    structure, _ = time_spread(ctx, "IRON_ORE_62", voyage["laden_days"])
    netback = cfr - per_dmt - structure                  # sold CFR, so the buyer insures the cargo
    dry = freight["cargo_t"] * (1 - moisture)
    row.update(dest_price=cfr, insurance=0.0, netback=netback, margin=netback, status="Netback",
               time_spread=structure, freight_share=per_dmt / cfr * 100,
               delay_day=delay_cost(ctx, freight["tce"], dry, cfr), pnl_musd=netback * dry / 1e6)
    return row


def coal(ctx, route):
    freight = leg_freight(ctx, route["leg"])
    voyage = freight["voyage"]
    cargo = freight["cargo_t"]
    term = route["sell"][0]
    row = _row(route, voyage["leg"]["name"], voyage["leg"]["distance_nm"], voyage["leg"]["vessel"])
    row.update(freight=freight["per_tonne"], freight_source=freight["source"], tce_used=freight["tce"],
               ets=freight["ets_per_tonne"], voyage_days=voyage["total_days"], laden_days=voyage["laden_days"],
               cargo_label=f"{cargo / 1000:.0f} kt")
    api4 = ctx.price("API4")
    if route["id"] == "COAL_RB_NWE":
        dest = ctx.price("API2")
        curve = "API2"
        row["dest_note"] = "API2 CIF ARA"
    else:
        newc = ctx.price("NEWC")
        curve = "NEWC"
        ref = leg_freight(ctx, "NEWC_JAPAN")
        dest = None if newc is None else newc + ref["per_tonne"]
        row["dest_note"] = f"NEWC + Newcastle → Japan freight ({ref['per_tonne']:.2f} $/t)"
        row["competitor_freight"] = ref["per_tonne"]
    if api4 is None or dest is None:
        row.update(status="Missing price", origin_price=api4, dest_price=dest)
        return row
    cargo_insurance = ctx.param("CARGO_INSURANCE") * api4
    losses = _param(ctx, "LOSSES_BULK") * api4
    financing = api4 * ctx.param("FINANCING_RATE") * (voyage["laden_days"] + 3) / 360
    misc = _param(ctx, "COAL_MISC")
    other = financing + misc
    if covers(term, "CIF"):
        other += cargo_insurance
    if covers(term, "DAP"):
        other += losses
    structure, _ = time_spread(ctx, curve, voyage["laden_days"])
    margin = dest - api4 - freight["per_tonne"] - other - structure
    breakeven = dest - api4 - other - structure
    row.update(origin_price=api4, buy_price=api4, dest_price=dest,
               netback=dest - freight["per_tonne"] - other - structure, time_spread=structure, curve=curve,
               insurance=cargo_insurance if covers(term, "CIF") else 0.0, cargo_insurance=cargo_insurance,
               losses=losses, financing=financing, misc=misc, seller_costs=financing + misc, other_costs=other,
               margin=margin, status=status(margin, route["threshold"]), breakeven_freight=breakeven,
               breakeven_tce=breakeven_tce(breakeven, freight),
               delay_day=delay_cost(ctx, freight["tce"], cargo, api4), pnl_musd=margin * cargo / 1e6)
    return row


def metal(ctx, route):
    leg = METAL_LEGS[route["leg"]]
    row = _row(route, leg["name"], leg["distance_nm"], "Container / breakbulk")
    rid = route["id"]
    if rid == "COPPER_LME_COMEX":
        lme, comex = ctx.price("LME_COPPER"), ctx.price("COMEX_COPPER")
        gross = None if lme is None or comex is None else comex * LB_PER_TONNE - lme
        freight = _param(ctx, "COPPER_FREIGHT_US")
        tariff_rate = _param(ctx, "COPPER_US_TARIFF")
        row["dest_note"] = "COMEX - LME spread"
    elif rid == "ALU_LME_MIDWEST":
        lme, mwp = ctx.price("LME_ALU"), ctx.price("MIDWEST_PREMIUM")
        gross = None if mwp is None else mwp * LB_PER_TONNE      # Midwest premium is quoted in $/lb
        freight = _param(ctx, "ALU_FREIGHT_US")
        tariff_rate = _param(ctx, "ALU_US_TARIFF")
        row["dest_note"] = "US Midwest duty-paid premium"
    else:
        lme, gross = ctx.price("LME_ALU"), ctx.price("JAPAN_PREMIUM")
        freight = _param(ctx, "ALU_FREIGHT_JAPAN")
        tariff_rate = 0.0
        row["dest_note"] = "Japan premium (MJP)"
    origin = _param(ctx, "ALU_ORIGIN_PREMIUM") if rid.startswith("ALU") else 0.0
    row.update(freight=freight, freight_source="Assumption", voyage_days=leg["transit_days"],
               cargo_label="per tonne")
    if lme is None or gross is None:
        row.update(status="Missing price", dest_price=gross)
        return row
    loadout = _param(ctx, "LME_LOADOUT")
    tariff = tariff_rate * lme
    financing = lme * ctx.param("FINANCING_RATE") * leg["transit_days"] / 360
    insurance = ctx.param("CARGO_INSURANCE") * lme
    costs = dict(origin_premium=origin, freight=freight, tariff=tariff, loadout=loadout, financing=financing,
                 insurance=insurance)
    margin = gross - sum(costs.values())
    # The ladder runs in premium over LME: what the metal fetches where it sits, then each cost on top.
    row.update(origin_price=lme, dest_price=gross, gross=gross, **costs, margin=margin, over_lme=True,
               buy_price=origin, fobbing=loadout, fobbing_note="warehouse load-out", cargo_insurance=insurance,
               seller_costs=financing, status=status(margin, route["threshold"]),
               breakeven_freight=margin + freight, delay_day=lme * ctx.param("FINANCING_RATE") / 360,
               pnl_musd=margin * 1000 / 1e6)   # per 1,000 t parcel
    return row


def coking_coal(ctx, route):
    """Delivered cost of PLV coking coal: there is no public CFR index to trade against."""
    row, freight = _voyage_row(ctx, route)
    fob = ctx.price("COKING_COAL")
    row.update(status="Delivered", dest_note="PLV FOB Australia + freight")
    if fob is None:
        row.update(status="Missing price")
        return row
    insurance = ctx.param("CARGO_INSURANCE") * fob
    cif = fob + freight["per_tonne"] + insurance
    row.update(origin_price=fob, buy_price=fob, insurance=insurance, cargo_insurance=insurance,
               losses=_param(ctx, "LOSSES_BULK") * cif, delivered=cif, margin=cif,
               freight_share=freight["per_tonne"] / cif * 100,
               delay_day=delay_cost(ctx, freight["tce"], freight["cargo_t"], fob))
    return row


def india_fuel(ctx, routes):
    """Cheapest delivered energy into West India: each fuel is compared with the next best one.

    margin = (next best $/Mkcal - this fuel $/Mkcal) x Mkcal in a tonne of this fuel
    so a positive margin is how much this fuel could rise before losing the buyer.
    """
    cv_key = {"API4": "CV_API4", "INDO_4200": "CV_INDO_4200", "PETCOKE_USG": "CV_PETCOKE"}
    duty_key = {"API4": "INDIA_COAL_DUTY", "INDO_4200": "INDIA_COAL_DUTY", "PETCOKE_USG": "INDIA_PETCOKE_DUTY"}
    rows = []
    for route in routes:
        row, freight = _voyage_row(ctx, route)
        fob = ctx.price(route["fuel"])
        mkcal = _param(ctx, cv_key[route["fuel"]]) / 1000         # million kcal per tonne
        row.update(fuel=route["fuel"], mkcal_per_t=mkcal, dest_note="Delivered West India, duty paid")
        if fob is None:
            row.update(status="Missing price")
        else:
            insurance = ctx.param("CARGO_INSURANCE") * fob
            cif = fob + freight["per_tonne"] + insurance
            losses = _param(ctx, "LOSSES_BULK") * cif              # the buyer pays for what reaches his yard
            duty = _param(ctx, duty_key[route["fuel"]]) * cif      # Indian customs value is CIF
            landed = cif + losses + duty
            row.update(origin_price=fob, buy_price=fob, insurance=insurance, cargo_insurance=insurance,
                       losses=losses, duty=duty, delivered=landed, cfr=cif,
                       per_mkcal=landed / mkcal, stale=ctx.stale(route["fuel"]),
                       delay_day=delay_cost(ctx, freight["tce"], freight["cargo_t"], fob))
        rows.append(row)
    priced = [r for r in rows if r.get("per_mkcal") is not None]
    fresh = [r for r in priced if not r["stale"]]
    for r in priced:
        others = [o["per_mkcal"] for o in fresh if o is not r]
        if not others:
            continue
        margin = (min(others) - r["per_mkcal"]) * r["mkcal_per_t"]
        r.update(margin=margin, status="Stale price" if r["stale"] else status(margin, r["threshold"]),
                 breakeven_freight=margin + r["freight"],
                 pnl_musd=margin * float(r["cargo_label"].split()[0]) * 1000 / 1e6)
    return rows


def scrap(ctx, route):
    row, freight = _voyage_row(ctx, route)
    cfr = ctx.price(route["dest"])
    row.update(dest_note={"SCRAP_TURKEY": "HMS 80:20 CFR Turkey", "SCRAP_INDIA": "Scrap CFR India"}[route["dest"]])
    if cfr is None:
        row.update(status="Missing price")
        return row
    netback = cfr - freight["per_tonne"]                 # sold CFR, so the buyer insures the cargo
    row.update(dest_price=cfr, insurance=0.0, netback=netback, margin=netback, status="Netback",
               freight_share=freight["per_tonne"] / cfr * 100,
               delay_day=delay_cost(ctx, freight["tce"], freight["cargo_t"], cfr))
    return row


def steel(ctx, route):
    """Landed cost of imported HRC against the local price. Flat breakbulk freight, no voyage model."""
    row = _row(route, "", None, "Breakbulk")
    rid = route["id"]
    if rid == "HRC_EU_US":
        mill = ctx.price("HRC_NWE")                      # ex-works, so the coil still has to reach the ship
        local = ctx.price("HRC_US")
        local = None if local is None else local * ST_PER_TONNE        # CME HRC is quoted per short ton
        fobbing = _param(ctx, "HRC_FOBBING_EU")
        freight = _param(ctx, "HRC_FREIGHT_EU_US")
        duty_rate, cbam = _param(ctx, "US_STEEL_TARIFF"), 0.0
        discount = _param(ctx, "US_IMPORT_DISCOUNT_HRC")
        row["dest_note"] = "US Midwest HRC (CRU), converted to $/t"
    else:
        mill = ctx.price("HRC_CHINA")
        local = ctx.price("HRC_NWE")
        fobbing = 0.0
        freight = _param(ctx, "HRC_FREIGHT_CN_EU")
        duty_rate = _param(ctx, "EU_AD_CHINA_HRC")
        if rid == "HRC_CN_EU_OUT":
            duty_rate += _param(ctx, "EU_STEEL_DUTY")
        cbam, certs = cbam_per_tonne(ctx)
        discount = 0.0
        row.update(dest_note="NW Europe HRC (Argus)", cbam_certs=certs)
    row.update(freight=freight, freight_source="Assumption", cargo_label="per tonne",
               laden_days=route.get("transit_days"))
    if mill is None or local is None:
        row.update(status="Missing price", dest_price=local)
        return row
    fob = mill + fobbing
    insurance = ctx.param("CARGO_INSURANCE") * fob
    financing = fob * ctx.param("FINANCING_RATE") * route["transit_days"] / 360
    # The EU charges duty on the CIF value, the US on the FOB value.
    duty = duty_rate * (fob + freight + insurance if rid != "HRC_EU_US" else fob)
    landed = fob + freight + insurance + duty + cbam
    margin = local - discount - landed - financing
    row.update(origin_price=mill, buy_price=mill, dest_price=local, fobbing=fobbing,
               fobbing_note="mill to port" if fobbing else None, insurance=insurance, cargo_insurance=insurance,
               duty=duty, duty_note="Section 232 tariff" if rid == "HRC_EU_US" else None, cbam=cbam,
               delivered=landed, import_discount=discount, financing=financing,
               seller_costs=financing, margin=margin, status=status(margin, route["threshold"]),
               breakeven_freight=margin + freight, delay_day=fob * ctx.param("FINANCING_RATE") / 360,
               pnl_musd=margin * 1000 / 1e6)
    return row


def steel_margins(ctx):
    """Raw material spreads a steel desk watches (not full mill margins)."""
    p = ctx.price
    out = {}
    hrc, ore, coal = p("HRC_CHINA"), p("IRON_ORE_62"), p("COKING_COAL")
    if None not in (hrc, ore, coal):
        ore_t, coal_t = _param(ctx, "ORE_PER_T_HM"), _param(ctx, "COAL_PER_T_HM")
        out["china_hrc"] = dict(hrc=hrc, ore=ore * ore_t, coal=coal * coal_t,
                                spread=hrc - ore * ore_t - coal * coal_t)
    rebar, scrap_tr = p("REBAR_TURKEY"), p("SCRAP_TURKEY")
    if None not in (rebar, scrap_tr):
        out["turkey_rebar"] = dict(rebar=rebar, scrap=scrap_tr * _param(ctx, "SCRAP_PER_T_REBAR"),
                                   spread=rebar - scrap_tr * _param(ctx, "SCRAP_PER_T_REBAR"))
    return out


def power(ctx):
    """German clean dark and clean spark spreads (EUR/MWh) and the coal-to-gas switching EUA price."""
    p = ctx.price
    api2, ttf, eua, fx, pw = p("API2"), p("TTF_EUR"), p("EUA"), p("EURUSD"), p("DE_POWER")
    if None in (api2, ttf, eua, fx):
        return None
    coal_eff, gas_eff = _param(ctx, "COAL_PLANT_EFF"), _param(ctx, "CCGT_EFF")
    coal_fuel = api2 / fx / (_param(ctx, "MWH_PER_T_COAL") * coal_eff)        # EUR per MWh of power
    gas_fuel = ttf / gas_eff
    coal_co2 = _param(ctx, "COAL_CO2") / coal_eff                              # t CO2 per MWh of power
    gas_co2 = _param(ctx, "GAS_CO2") / gas_eff
    out = dict(eua=eua, power=pw, coal_fuel=coal_fuel, gas_fuel=gas_fuel, coal_carbon=coal_co2 * eua,
               gas_carbon=gas_co2 * eua, coal_cost=coal_fuel + coal_co2 * eua, gas_cost=gas_fuel + gas_co2 * eua,
               switch_eua=(gas_fuel - coal_fuel) / (coal_co2 - gas_co2))
    if pw is not None:
        out.update(dark=pw - out["coal_cost"], spark=pw - out["gas_cost"])
    out["cheaper"] = "coal" if out["coal_cost"] < out["gas_cost"] else "gas"
    return out


def spreads(ctx, rows):
    by_id = {r["route_id"]: r for r in rows}
    p = ctx.price
    out = []

    def add(key, label, value, unit, note=""):
        out.append(dict(key=key, label=label, value=None if value is None else round(value, 3), unit=unit,
                        note=note if value is not None else "Missing price"))

    au, br = by_id.get("ORE_AUS_CHINA", {}), by_id.get("ORE_BRA_CHINA", {})
    add("ORE_FREIGHT_GAP", "C3 - C5 freight (Brazil penalty)",
        None if not (au.get("freight") and br.get("freight")) else br["freight"] - au["freight"], "$/dmt",
        "How much more a Brazilian tonne pays to reach China")
    add("API2_API4", "API2 - API4", None if p("API2") is None or p("API4") is None else p("API2") - p("API4"),
        "$/t", "Compare with Richards Bay → Rotterdam freight")
    add("NEWC_API4", "NEWC - API4", None if p("NEWC") is None or p("API4") is None else p("NEWC") - p("API4"),
        "$/t", "Pacific vs Atlantic FOB")
    add("API2_NEWC", "API2 - NEWC", None if p("API2") is None or p("NEWC") is None else p("API2") - p("NEWC"),
        "$/t", "Europe delivered vs Australia FOB")
    lme, comex = p("LME_COPPER"), p("COMEX_COPPER")
    add("COMEX_LME", "COMEX - LME copper", None if lme is None or comex is None else comex * LB_PER_TONNE - lme,
        "$/t", "Premium for copper in the US")
    mwp = p("MIDWEST_PREMIUM")
    add("MWP", "US Midwest aluminium premium", None if mwp is None else mwp * LB_PER_TONNE, "$/t",
        f"{mwp * 100:.1f} c/lb" if mwp is not None else "")
    alu = p("LME_ALU")
    rate = _param(ctx, "ALU_US_TARIFF")
    add("ALU_TARIFF", "US tariff on a tonne of aluminium", None if alu is None else rate * alu, "$/t",
        f"{rate * 100:.0f}% of LME")
    coke, newc = p("COKING_COAL"), p("NEWC")
    add("PLV_NEWC", "PLV coking coal - Newcastle thermal", None if coke is None or newc is None else coke - newc,
        "$/t", "Met coal premium over thermal")
    sc_tr, sc_in = p("SCRAP_TURKEY"), p("SCRAP_INDIA")
    add("SCRAP_TR_IN", "Scrap CFR Turkey - CFR India", None if sc_tr is None or sc_in is None else sc_tr - sc_in,
        "$/t", "Compare with the extra freight to India")
    hrc_us, hrc_eu, hrc_cn = p("HRC_US"), p("HRC_NWE"), p("HRC_CHINA")
    add("HRC_US_CN", "US HRC - China HRC FOB",
        None if hrc_us is None or hrc_cn is None else hrc_us * ST_PER_TONNE - hrc_cn, "$/t", "Kept open by tariffs")
    add("HRC_EU_CN", "NW Europe HRC - China HRC FOB",
        None if hrc_eu is None or hrc_cn is None else hrc_eu - hrc_cn, "$/t", "Kept open by quotas and duties")
    return out


def evaluate(prices, overrides, curves=None, today=None):
    ctx = Context(prices, overrides, curves, today)
    rows = []
    single = {"Iron ore": iron_ore, "Coal": coal, "Metals": metal, "Coking coal": coking_coal, "Scrap": scrap,
              "Steel": steel}
    for r in BULK_ROUTES:
        if r["family"] in single:
            rows.append(single[r["family"]](ctx, r))
    rows += india_fuel(ctx, [r for r in BULK_ROUTES if r["family"] == "India fuel"])
    order = {r["id"]: i for i, r in enumerate(BULK_ROUTES)}
    rows.sort(key=lambda r: order[r["route_id"]])
    for fam in ("Scrap",):
        best = {}
        for r in rows:
            if r["family"] == fam and r.get("netback") is not None:
                origin = r["route"].split(" → ")[0]
                if origin not in best or r["netback"] > best[origin]["netback"]:
                    best[origin] = r
        for r in best.values():
            r["best_destination"] = True
    clean = lambda d: {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}
    rows = [clean(r) for r in rows]
    extra = dict(power=power(ctx), steel=steel_margins(ctx))
    extra = {k: clean(v) if isinstance(v, dict) and all(not isinstance(x, dict) for x in v.values()) else
             ({kk: clean(vv) for kk, vv in v.items()} if isinstance(v, dict) else v) for k, v in extra.items()}
    return dict(routes=rows, spreads=spreads(ctx, rows), assumptions=ctx.assumptions, **extra)

