"""Delivery terms.

A physical price only means something with its term. This module records what each benchmark
includes and walks a route up the terms, in the route's own unit.

    FOB   on board at the load port
    CFR   FOB plus freight, with the war-risk premium the owner passes on
    CIF   CFR plus cargo insurance
    DAP   CIF plus what is lost on the way, because the cargo is sold on what arrives
    DDP   DAP plus import duties, tariffs and, in the US, the RINs an importer owes

A sale on one term leaves the seller with every cost up to it and the buyer with the rest.
Financing and inspection stay with the seller whatever the term.
"""

from .config import TERMS

# What the quoted price includes and where.
BASIS = {
    "WTI_CUSHING": "In store, Cushing",
    "WTI_HOUSTON": "In terminal, Houston",
    "HOU_DIFF": "In terminal, Houston",
    "BRENT": "FOB North Sea, cash settled",
    "DATED_BRENT": "FOB North Sea",
    "MURBAN": "FOB Fujairah",
    "RBOB": "FOB New York Harbor, barge",
    "ULSD_NYH": "FOB New York Harbor, barge",
    "ULSD_USGC_UPDOWN": "Pipeline, US Gulf Coast",
    "GASOIL_ICE": "FOB ARA, barge",
    "EUROBOB": "FOB ARA, barge",
    "HENRY_HUB": "Pipeline, Louisiana",
    "TTF_EUR": "Dutch grid",
    "TTF_USD": "Dutch grid",
    "JKM": "DES North-East Asia",
    "IRON_ORE_62": "CFR Qingdao",
    "API2": "CIF ARA",
    "API4": "FOB Richards Bay",
    "NEWC": "FOB Newcastle",
    "COKING_COAL": "FOB Queensland",
    "INDO_4200": "FOB Kalimantan",
    "PETCOKE_USG": "FOB US Gulf",
    "LME_COPPER": "In LME warehouse",
    "COMEX_COPPER": "In COMEX warehouse, US",
    "LME_ALU": "In LME warehouse",
    "MIDWEST_PREMIUM": "Delivered US Midwest, duty paid",
    "JAPAN_PREMIUM": "CIF Japan",
    "ALU_DUP_RDM": "In warehouse Rotterdam, duty unpaid",
    "HRC_CHINA": "FOB Tianjin",
    "HRC_NWE": "Ex-works, Ruhr",
    "HRC_US": "Ex-works, US Midwest",
    "SCRAP_TURKEY": "CFR Turkey",
    "SCRAP_INDIA": "CFR Nhava Sheva",
    "SCRAP_TAIWAN": "CFR Taiwan",
    "REBAR_TURKEY": "FOB Turkey",
    "VLSFO_SG": "FOB Singapore",
    "VLSFO_RDM": "FOB Rotterdam, barge",
    "HSFO_SG": "FOB Singapore",
}

TERM_NAMES = {"FOB": "FOB", "CFR": "CFR", "CIF": "CIF", "DAP": "DAP", "DDP": "Duty paid"}


def covers(term, rung):
    """True when a sale on `term` leaves the cost of `rung` with the seller."""
    return TERMS.index(term) >= TERMS.index(rung)


def _duty_label(row):
    if row.get("duty_note"):
        return row["duty_note"]
    parts = [name for key, name in (("duty", "import duty"), ("tariff", "tariff"), ("cbam", "CBAM"), ("rvo", "RINs"))
             if row.get(key)]
    return " and ".join(parts) if parts else "no duty"


def ladder(row, buy_price=None):
    """Cost of the cargo at each term, or None when the route has no build-up.

    `buy_price` replaces the route's own purchase price, for a cargo bought on another basis.
    """
    sold = row.get("sell_term")
    if row.get("status") == "Netback":                  # sold CFR, walked back to the load port
        if row.get("netback") is None:
            return None
        return dict(sold=sold, backward=True, FOB=row["netback"], CFR=row["dest_price"], steps=[])
    start = row.get("buy_price") if buy_price is None else buy_price
    if start is None:
        return None
    val = lambda key: row.get(key) or 0.0
    boil_off = val("boil_off")
    adds = [
        ("FOB", val("fobbing"), row.get("fobbing_note") or "loading"),
        ("CFR", val("freight") - boil_off + val("war_risk"), "freight and war risk" if val("war_risk") else "freight"),
        ("CIF", val("cargo_insurance"), "cargo insurance"),
        ("DAP", val("losses") + boil_off, "boil-off" if boil_off else "transit loss"),
        ("DDP", val("duty") + val("tariff") + val("cbam") + val("rvo"), _duty_label(row)),
    ]
    out = dict(sold=sold, backward=False, buy=start, steps=[])
    total = start
    for term, add, label in adds:
        total += add
        out[term] = total
        out["steps"].append(dict(term=term, name=TERM_NAMES[term], add=add, label=label, total=total,
                                 seller=covers(sold, term) if sold else True))
    return out


def beyond(row):
    """Costs past the sale term, which the buyer carries. [(label, value)]"""
    steps = (ladder(row) or {}).get("steps", [])
    return [(s["label"], s["add"]) for s in steps if not s["seller"] and abs(s["add"]) > 1e-9]
