"""Static HTML pages (Jinja2 templates + Plotly charts): energy, dry bulk & metals, freight and trade ideas."""

import json
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version

from .config import BBL_PER_TONNE, CURVES, GAL_PER_BBL, LICENCE_NOTE, PRIVATE_NOTE
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
PATHS_FILE = ROOT / "data" / "route_paths.json"

UNITS = {"Crude": "$/bbl", "Products": "$/t", "LNG": "$/MMBtu", "Coal": "$/t", "Iron ore": "$/dmt", "Metals": "$/t",
         "India fuel": "$/t", "Coking coal": "$/t", "Scrap": "$/t", "Steel": "$/t"}
ENERGY_FAMILIES = ["Crude", "Products", "LNG"]
BULK_FAMILIES = ["Coal", "India fuel", "Coking coal", "Iron ore", "Scrap", "Steel", "Metals"]
DESKS = {"Coal desk": ["Coal", "India fuel"], "Ferrous desk": ["Steel"], "Metals desk": ["Metals"]}
COLORS = ["#2f6fdf", "#e0782f", "#8a5bd6", "#1f9e8f", "#d6457a", "#7a8a3c", "#9aa0a6"]
PALETTE = ["#2f6fdf", "#e0782f", "#1f9e8f", "#d6457a", "#8a5bd6", "#7a8a3c", "#c9a227", "#5b6472"]
STATUS_COLORS = {"Open": "#2e9e5b", "Marginal": "#d39a2a", "Closed": "#d0533f", "Netback": "#2f6fdf",
                 "Delivered": "#2f6fdf", "Stale price": "#8a8f98"}
MUTED = "#8a8f98"
LEGEND_ORDER = {"Open": 1, "Marginal": 2, "Closed": 3, "Netback": 4, "Delivered": 5, "Stale price": 6,
                "Missing price": 7}
CHART_FONT = dict(family="Inter, system-ui, sans-serif", size=12, color=MUTED)
GRID = "rgba(138,143,152,0.22)"
REF_LINE = dict(line_dash="dot", line_color=MUTED, line_width=1.5, opacity=1)   # simple_white hides shape lines by default
PLOTLY_JS = f"https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"


# ---- number formatting (used as Jinja filters)

def _bad(v):
    return v is None or v == "" or isinstance(v, Undefined)


def num(v, d=2):
    return "—" if _bad(v) else f"{float(v):,.{d}f}"


def signed(v, d=2):
    if _bad(v):
        return "—"
    v = float(v)
    return f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):,.{d}f}"


def with_unit(v, unit, sign=False):
    if _bad(v):
        return "—"
    digits = 1 if unit == "$/t" else 2
    return f"{(signed if sign else num)(v, digits)} {unit}"


def musd(v):
    if _bad(v):
        return "—"
    return f"{signed(v, 0 if abs(float(v)) >= 10 else 1)} M$"


def tce(v):
    if _bad(v):
        return "—"
    v = float(v)
    if v < 0:
        return "< 0"
    if v > 2_000_000:
        return "> 2,000 k$/d"
    return f"{v / 1000:,.1f} k$/d"


def status_class(s):
    return {"Open": "ok", "Marginal": "warn", "Closed": "bad", "Netback": "info", "Delivered": "info"}.get(s, "na")


def pct(v, d=0):
    return "—" if _bad(v) else f"{signed(v, d)} %"


def kusd(v):
    return "—" if _bad(v) else f"{float(v) / 1000:,.1f} k$"


def day(v):
    """'2026-12-12' -> '12 Dec'."""
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return "—"
    return f"{d.day} {d:%b}"


# ---- charts

def _layout(fig, height, **extra):
    fig.update_layout(template="simple_white", height=height, margin=dict(l=10, r=10, t=30, b=70), font=CHART_FONT,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", yref="container", y=0, yanchor="bottom", traceorder="normal"), hoverlabel=dict(font_size=12),
                      **extra)
    fig.update_xaxes(gridcolor=GRID, zeroline=False, showgrid=True)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def _html(fig):
    if fig is None:
        return ""
    return fig.to_html(full_html=False, include_plotlyjs=False, default_width="100%",
                       config={"displayModeBar": False, "responsive": True})


def route_map(routes, paths):
    """Shipping lanes coloured by arb status. Routes sharing a lane get a small offset."""
    fig = go.Figure()
    seen, shown = {}, set()
    for r in routes:
        leg = paths.get(r["leg"])
        if not leg:
            continue
        shift = seen.get(r["leg"], 0) * 0.8
        seen[r["leg"]] = seen.get(r["leg"], 0) + 1
        lon = [p[0] for p in leg["path"]]
        lat = [p[1] + shift for p in leg["path"]]
        state = r["status"] if r["status"] in STATUS_COLORS else "Missing price"
        color = STATUS_COLORS.get(state, MUTED)
        legend = "Netback or cost only" if state in ("Netback", "Delivered") else state
        value = with_unit(r.get("margin"), r["unit"], sign=True)
        label = {"Netback": "netback", "Delivered": "delivered"}.get(state, "margin")
        fig.add_trace(go.Scattergeo(
            lon=lon, lat=lat, mode="lines", line=dict(width=2.5, color=color), name=legend,
            legendgroup=legend, showlegend=legend not in shown, legendrank=LEGEND_ORDER.get(state, 9), hoverinfo="text",
            text=f"{r['route']}<br>{label} {value} · {r['status']}"))
        shown.add(legend)
        fig.add_trace(go.Scattergeo(lon=[lon[0], lon[-1]], lat=[lat[0], lat[-1]], mode="markers",
                                    marker=dict(size=6, color=color), showlegend=False, hoverinfo="skip"))
    fig.update_geos(projection_type="natural earth", showland=True, landcolor="rgba(138,143,152,0.18)",
                    showcountries=False, showcoastlines=True, coastlinecolor="rgba(138,143,152,0.35)",
                    showocean=False, showframe=False, bgcolor="rgba(0,0,0,0)", lataxis_range=[-48, 68])
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      font=CHART_FONT, legend=dict(orientation="h", y=-0.02, x=0.5, xanchor="center"))
    return fig


def stacked_bars(rows, label_of, parts, unit, refs=(), height=300, title=None):
    """Horizontal stacked bars, one per row, with optional dotted reference lines."""
    if not rows:
        return None
    names = [label_of(r) for r in rows]
    fig = go.Figure()
    for i, (key, label, color) in enumerate(parts):
        fig.add_bar(y=names, x=[r.get(key) or 0 for r in rows], name=label, orientation="h",
                    marker_color=color or COLORS[i % len(COLORS)],
                    hovertemplate=f"%{{y}}<br>{label} %{{x:,.2f}} {unit}<extra></extra>")
    for x, text, anchor in refs:
        if x is None:
            continue
        fig.add_vline(x=x, **REF_LINE)
        fig.add_annotation(x=x, y=1.02, yref="paper", text=text, showarrow=False, xanchor=anchor, yanchor="bottom",
                           font_size=11, xshift=-4 if anchor == "right" else 4)
    _layout(fig, height, barmode="stack", xaxis_title=unit, title=dict(text=title, x=0, font_size=13) if title else None)
    fig.update_layout(margin=dict(l=10, r=10, t=44 if title else 40, b=70))
    fig.update_yaxes(autorange="reversed")
    return fig


def lng_chart(routes):
    rows = [r for r in routes if r["family"] == "LNG" and r.get("netback") is not None]
    if not rows:
        return None
    parts = [("netback", "Netback FOB", None), ("hire", "Charter hire", None), ("boil_off", "Boil-off", None),
             ("canal", "Canal", None), ("port", "Port", None), ("ballast_fuel", "Ballast fuel", None),
             ("ets", "EU ETS", "#c9a227"), ("time_spread", "Curve to arrival", "#5b6472"),
             ("insurance", "Insurance", "#9aa0a6")]
    feedgas, fob = rows[0]["feedgas"], rows[0]["origin_price"]
    return stacked_bars(rows, lambda r: r["route"].replace("US LNG → ", ""), parts, "$/MMBtu loaded",
                        refs=[(feedgas, f"1.15 x HH {feedgas:.2f}", "right"), (fob, f"full FOB cost {fob:.2f}", "left")],
                        height=320)


def iron_ore_chart(routes):
    rows = [r for r in routes if r["family"] == "Iron ore" and r.get("netback") is not None]
    parts = [("netback", "FOB netback", "#2f6fdf"), ("freight", "Freight (per dry tonne)", "#e0782f"),
             ("time_spread", "Curve to arrival", "#5b6472"), ("insurance", "Insurance", "#9aa0a6")]
    return stacked_bars(rows, lambda r: r["route"].replace("Iron ore ", ""), parts, "$/dmt", height=240)


def coal_chart(routes):
    rows = [r for r in routes if r["family"] == "Coal" and r.get("netback") is not None]
    if not rows:
        return None
    parts = [("netback", "Netback at Richards Bay", "#2f6fdf"), ("freight", "Freight", "#e0782f"),
             ("other_costs", "Insurance, financing, misc", "#9aa0a6")]
    api4 = rows[0]["origin_price"]
    return stacked_bars(rows, lambda r: r["route"].replace("Coal Richards Bay → ", "To "), parts, "$/t",
                        refs=[(api4, f"API4 FOB {api4:.2f}", "right")], height=240)


def landed_chart(rows, names, costs, target_key, target_label, height=280):
    """Stacked costs per route against a diamond for what the destination pays."""
    if not rows:
        return None
    fig = go.Figure()
    for key, label, color in costs:
        fig.add_bar(y=names, x=[r.get(key) or 0 for r in rows], name=label, orientation="h", marker_color=color,
                    hovertemplate=f"%{{y}}<br>{label} %{{x:,.0f}} $/t<extra></extra>")
    fig.add_scatter(y=names, x=[r[target_key] for r in rows], mode="markers", name=target_label,
                    marker=dict(symbol="diamond", size=13, color="#1c1d1f", line=dict(color="#ffffff", width=1)),
                    hovertemplate=f"%{{y}}<br>{target_label} %{{x:,.0f}} $/t<extra></extra>")
    _layout(fig, height, barmode="stack", xaxis_title="$/t")
    fig.update_yaxes(autorange="reversed")
    return fig


def metals_chart(routes):
    rows = [r for r in routes if r["family"] == "Metals" and r.get("gross") is not None]
    names = [r["route"].replace(" LME Rotterdam", "").replace(" LME", "") for r in rows]   # short enough for a phone
    costs = [("origin_premium", "Rotterdam premium given up", "#c9a227"), ("freight", "Freight", "#e0782f"),
             ("tariff", "US tariff", "#d0533f"), ("loadout", "LME load-out", "#8a5bd6"),
             ("financing", "Financing", "#1f9e8f"), ("insurance", "Insurance", "#9aa0a6")]
    return landed_chart(rows, names, costs, "gross", "Premium / spread available")


def steel_chart(routes):
    rows = [r for r in routes if r["family"] == "Steel" and r.get("delivered") is not None]
    names = [r["route"].replace("HRC ", "") for r in rows]
    costs = [("origin_price", "Coil price at origin", "#2f6fdf"), ("freight", "Freight", "#e0782f"),
             ("duty", "Tariff and duties", "#d0533f"), ("cbam", "CBAM", "#c9a227"), ("insurance", "Insurance", "#9aa0a6"),
             ("import_discount", "Discount to sell an import", "#8a5bd6")]
    return landed_chart(rows, names, costs, "dest_price", "Local HRC price", height=260)


def india_chart(routes):
    rows = [r for r in routes if r["family"] == "India fuel" and r.get("per_mkcal") is not None]
    if not rows:
        return None
    for r in rows:
        k = r["mkcal_per_t"]
        r.update(fob_k=r["origin_price"] / k, freight_k=r["freight"] / k, duty_k=r["duty"] / k,
                 ins_k=r["insurance"] / k)
    parts = [("fob_k", "FOB price", "#2f6fdf"), ("freight_k", "Freight", "#e0782f"), ("duty_k", "Import duty", "#d0533f"),
             ("ins_k", "Insurance", "#9aa0a6")]
    label = lambda r: r["route"].replace(" → India", "") + (" (stale price)" if r.get("stale") else "")
    return stacked_bars(rows, label, parts, "$ per million kcal delivered", height=250)


def coking_chart(routes):
    rows = [r for r in routes if r["family"] == "Coking coal" and r.get("delivered") is not None]
    parts = [("origin_price", "PLV FOB Australia", "#2f6fdf"), ("freight", "Freight", "#e0782f"),
             ("insurance", "Insurance", "#9aa0a6")]
    return stacked_bars(rows, lambda r: r["route"].replace("PLV coking coal Hay Point → ", "To "), parts, "$/t",
                        height=220)


def scrap_chart(routes):
    rows = [r for r in routes if r["family"] == "Scrap" and r.get("netback") is not None]
    parts = [("netback", "Netback FOB", "#2f6fdf"), ("freight", "Freight", "#e0782f"), ("insurance", "Insurance", "#9aa0a6")]
    label = lambda r: r["route"].replace("Scrap ", "") + (" ★" if r.get("best_destination") else "")
    return stacked_bars(rows, label, parts, "$/t", height=300)


def power_chart(power):
    if not power or power.get("power") is None:
        return None
    rows = [dict(name="Coal plant", fuel=power["coal_fuel"], carbon=power["coal_carbon"]),
            dict(name="Gas plant (CCGT)", fuel=power["gas_fuel"], carbon=power["gas_carbon"])]
    parts = [("fuel", "Fuel", "#2f6fdf"), ("carbon", "Carbon (EUA)", "#5b6472")]
    return stacked_bars(rows, lambda r: r["name"], parts, "EUR per MWh of power",
                        refs=[(power["power"], f"German power {power['power']:.0f}", "right")], height=220)


CURVE_GROUPS = [("Oil", ["BRENT", "WTI", "HO", "RBOB"]), ("Gas", ["TTF", "JKM", "HENRY_HUB"]),
                ("Coal and ore", ["API2", "API4", "NEWC", "IRON_ORE_62", "COKING_COAL"]),
                ("Bunkers and carbon", ["VLSFO_SG", "EUA"])]


def curve_chart(curves, keys=None, title=None):
    """Forward curves as a % of their front month, so backwardation and contango read the same way."""
    curves = [c for c in curves if keys is None or c["key"] in keys]
    if not curves:
        return None
    fig = go.Figure()
    for i, c in enumerate(curves):
        pts = c["points"]
        fig.add_scatter(x=[f"M{k + 1}" for k in range(len(pts))], y=[p["close"] / pts[0]["close"] * 100 - 100 for p in pts],
                        mode="lines+markers", name=c["label"], line=dict(color=PALETTE[i % len(PALETTE)], width=2),
                        hovertemplate=f"{c['label']}<br>%{{x}} (%{{customdata}}) %{{y:+.1f}} % vs M1<extra></extra>",
                        customdata=[p["contract"] for p in pts])
    fig.add_hline(y=0, **REF_LINE)
    _layout(fig, 300, yaxis_title="% vs front month", title=dict(text=title, x=0, font_size=13) if title else None)
    return fig


def chokepoint_chart(data, field, title):
    """Weekly transits as an index of the pre-war average (= 100), so a small strait and a big one compare."""
    if not data or not data.get("baseline"):
        return None
    base = data["baseline"]["values"]
    fig = go.Figure()
    for i, (name, rows) in enumerate(data["weeks"].items()):
        b = base.get(name, {}).get(field)
        if not b:
            continue
        idx = [r[field] / b * 100 for r in rows]
        smooth = [sum(idx[max(0, k - 3):k + 1]) / len(idx[max(0, k - 3):k + 1]) for k in range(len(idx))]
        fig.add_scatter(x=[r["week"] for r in rows], y=smooth, mode="lines", name=name,
                        line=dict(color=PALETTE[i % len(PALETTE)], width=2), customdata=[r[field] / 7 for r in rows],
                        hovertemplate=f"{name}<br>week to %{{x}}<br>index %{{y:.0f}}, 4-week average (%{{customdata:.1f}} a day that week)<extra></extra>")
    fig.add_hline(y=100, **REF_LINE)
    _layout(fig, 320, title=dict(text=f"{title}, 4-week average", x=0, font_size=13), yaxis_title="pre-war average = 100")
    fig.update_yaxes(rangemode="tozero")
    return fig


def headroom_chart(rows, cap=150_000):
    rows = [r for r in rows if r["breakeven"] < 1_000_000]
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r["room"])
    names = [r["route"] for r in rows]
    fig = go.Figure()
    clip = lambda v: max(min(v, cap), -cap / 3)
    for r, name in zip(rows, names):
        color = STATUS_COLORS["Open"] if r["room"] > 0 else STATUS_COLORS["Closed"]
        fig.add_scatter(x=[clip(r["tce"]), clip(r["breakeven"])], y=[name, name], mode="lines",
                        line=dict(color=color, width=4), showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=[clip(r["tce"]) for r in rows], y=names, mode="markers", name="TCE paid today",
                    marker=dict(size=9, color="#1c1d1f"), customdata=[r["tce"] for r in rows],
                    hovertemplate="%{y}<br>paid %{customdata:,.0f} $/day<extra></extra>")
    fig.add_scatter(x=[clip(r["breakeven"]) for r in rows], y=names, mode="markers", name="Break-even TCE",
                    marker=dict(size=9, color="#ffffff", line=dict(color="#1c1d1f", width=2)),
                    customdata=[r["breakeven"] for r in rows],
                    hovertemplate="%{y}<br>break-even %{customdata:,.0f} $/day<extra></extra>")
    _layout(fig, 90 + 28 * len(rows), xaxis_title="$/day")
    fig.update_xaxes(tickformat="~s")
    return fig


def history_chart(history, metric, title, families):
    """One trace per route. Only the first family is visible; the page's selector switches families."""
    series = OrderedDict()
    for row in history:
        if row["family"] not in families or _bad(row.get(metric)):
            continue
        series.setdefault((row["family"], row["route"]), []).append((row["date"], float(row[metric])))
    if not series:
        return None
    fig = go.Figure()
    for i, (family, route) in enumerate(series):
        pts = sorted(series[(family, route)])
        fig.add_scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], name=route, mode="lines+markers",
                        visible=family == families[0], meta=family, line=dict(color=COLORS[i % len(COLORS)], width=2),
                        hovertemplate=f"{route}<br>%{{x}}<br>%{{y:,.3f}} {UNITS[family]}<extra></extra>")
    _layout(fig, 300, title=dict(text=title, x=0, font_size=13), yaxis_title=UNITS[families[0]])
    if metric == "margin":
        fig.add_hline(y=0, **REF_LINE)
    else:
        fig.update_yaxes(rangemode="tozero")
    dates = sorted({d for pts in series.values() for d, _ in pts})
    fig.update_xaxes(type="date", tickformat="%d %b", dtick=86_400_000 if len(dates) < 15 else None)
    if len(dates) == 1:   # a single run: give the point some room
        day = datetime.strptime(dates[0], "%Y-%m-%d")
        fig.update_xaxes(range=[(day - timedelta(days=1)).strftime("%Y-%m-%d"),
                                (day + timedelta(days=1)).strftime("%Y-%m-%d")])
    return fig


# ---- page helpers

def bar_geometry(freight, breakeven):
    """Positions (in %) for the freight vs break-even mini bar."""
    if _bad(freight) or _bad(breakeven):
        return None
    freight, breakeven = float(freight), float(breakeven)
    top = max(freight, breakeven, 1e-9) * 1.12
    pct = lambda v: max(0.0, min(100.0, v / top * 100))
    return dict(green=pct(max(breakeven, 0)), red_from=pct(max(breakeven, 0)),
                red_to=pct(freight) if freight > breakeven else None, marker=pct(freight))


def idea_geometry(idea):
    marks = [idea["stop"], idea["entry"], idea["target"]]
    if idea.get("spot_ref") is not None:
        marks.append(idea["spot_ref"])
    lo, hi = min(marks), max(marks)
    pad = (hi - lo) * 0.08 or 1
    pos = lambda v: (v - lo + pad) / (hi - lo + 2 * pad) * 100
    return dict(stop=pos(idea["stop"]), entry=pos(idea["entry"]), target=pos(idea["target"]),
                spot=pos(idea["spot_ref"]) if idea.get("spot_ref") is not None else None)


def family_cards(routes, families):
    cards = []
    for fam in families:
        rows = [r for r in routes if r["family"] == fam]
        priced = [r for r in rows if r.get("margin") is not None]
        best = max(priced, key=lambda r: r["margin"]) if priced else None
        cards.append(dict(family=fam, best=best, open=sum(r["status"] == "Open" for r in rows), total=len(rows),
                          note=best.get("whose", "") if best else ""))
    return cards


def desk_cards(routes, desks):
    cards = []
    for desk, fams in desks.items():
        rows = [r for r in routes if r["family"] in fams]
        priced = [r for r in rows if r.get("margin") is not None and r["status"] in ("Open", "Marginal", "Closed")]
        best = max(priced, key=lambda r: r["margin"]) if priced else None
        cards.append(dict(family=desk, best=best, open=sum(r["status"] == "Open" for r in rows),
                          total=sum(r["status"] in ("Open", "Marginal", "Closed") for r in rows)))
    return cards


def rin_summary(snapshot, px):
    """The RIN obligation in the units a products trader uses, or None when RIN prices are missing.

    Around the new year an export cargo and an import cargo can fall under two standards. The page
    shows the later one, which is the one an import landing in New York pays.
    """
    notes = sorted((a for a in snapshot["assumptions"] if a["key"].startswith("RVO_")), key=lambda a: a["key"])
    if not notes:
        return None
    note = notes[-1]
    per_gal = note["value"] / 100
    return dict(cents=note["value"], year=int(note["key"][-4:]), date=px.get("RIN_D6", {}).get("date", ""),
                gasoline=per_gal * BBL_PER_TONNE["gasoline"] * GAL_PER_BBL,
                diesel=per_gal * BBL_PER_TONNE["diesel"] * GAL_PER_BBL)


def _env():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters.update(num=num, signed=signed, with_unit=with_unit, musd=musd, tce=tce, status_class=status_class,
                       pct=pct, kusd=kusd, day=day)
    return env


def build(snapshot, history, out_dir, freight_ideas=None, desk_ideas=None, private=False):
    paths = json.loads(PATHS_FILE.read_text(encoding="utf-8")) if PATHS_FILE.exists() else {}
    env = _env()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    px = {p["key"]: p for p in snapshot["prices"]}
    common = dict(snap=snapshot, private=private, built=built, plotly_js=PLOTLY_JS, px=px, lb=2204.62, st=1.10231,
                  private_note=PRIVATE_NOTE, licence_note=LICENCE_NOTE, fwd=snapshot.get("forward") or {},
                  load_date=snapshot.get("load_date"))
    price_rows = lambda group: [p for p in snapshot["prices"] if p["key"] in snapshot["price_keys"][group]]

    # energy
    routes = snapshot["routes"]
    for r in routes:
        r["bar"] = bar_geometry(r.get("freight"), r.get("breakeven_freight"))
    priced = [r for r in routes if r.get("pnl_musd") is not None and not r.get("whose")]    # what a trader can buy
    export = next((r for r in routes if r["route_id"] == "DIESEL_USGC_NWE" and (r.get("margin") or 0) > 0), None)
    lng = [r for r in routes if r["family"] == "LNG" and r.get("netback") is not None]
    europe = next((r for r in lng if r["route_id"] == "LNG_US_NWE"), None)
    asia = max((r for r in lng if r["route_id"] != "LNG_US_NWE"), key=lambda r: r["netback"], default=None)
    curve_cost = [r for r in routes if (r.get("time_spread") or 0) > 0 and r.get("margin") is not None]
    charts = dict(map=_html(route_map(routes, paths)), lng=_html(lng_chart(routes)),
                  margin=_html(history_chart(history, "margin", "Margin", ENERGY_FAMILIES)),
                  freight=_html(history_chart(history, "freight", "Freight", ENERGY_FAMILIES)),
                  insurance=_html(history_chart(history, "insurance", "Insurance (cargo + war risk)", ENERGY_FAMILIES)))
    html = env.get_template("energy.html").render(
        **common, page="energy", routes=routes, cards=family_cards(routes, ENERGY_FAMILIES), charts=charts,
        best=max(priced, key=lambda r: r["pnl_musd"]) if priced else None, rvo=rin_summary(snapshot, px),
        export=export,
        lng_gap=(asia["netback"] - europe["netback"]) if asia and europe else None, lng_asia=asia,
        killed=[r for r in curve_cost if r["margin"] < 0 < r["margin"] + r["time_spread"]],
        spreads={s["key"]: s for s in snapshot["spreads"]}, spread_rows=snapshot["spreads"], prices=price_rows("energy"),
        assumptions=snapshot["assumptions"], families=ENERGY_FAMILIES, units=UNITS)
    (out / "index.html").write_text(html, encoding="utf-8")

    # dry bulk & metals
    bulk = snapshot["bulk"]
    brows = bulk["routes"]
    for r in brows:
        r["bar"] = bar_geometry(r.get("freight"), r.get("breakeven_freight"))
    coal = [r for r in brows if r["family"] == "Coal" and r.get("netback") is not None]
    india = [r for r in brows if r["family"] == "India fuel" and r.get("per_mkcal") is not None and not r.get("stale")]
    bcharts = dict(map=_html(route_map(brows, paths)), ore=_html(iron_ore_chart(brows)), coal=_html(coal_chart(brows)),
                   india=_html(india_chart(brows)), coking=_html(coking_chart(brows)),
                   power=_html(power_chart(bulk.get("power"))), scrap=_html(scrap_chart(brows)),
                   steel=_html(steel_chart(brows)), metals=_html(metals_chart(brows)),
                   margin=_html(history_chart(history, "margin", "Margin (netback or delivered cost where marked)",
                                              BULK_FAMILIES)),
                   freight=_html(history_chart(history, "freight", "Freight", BULK_FAMILIES)))
    html = env.get_template("bulk.html").render(
        **common, page="bulk", routes=brows, cards=desk_cards(brows, DESKS), charts=bcharts,
        open_count=sum(r["status"] == "Open" for r in brows),
        trade_count=sum(r["status"] in ("Open", "Marginal", "Closed") for r in brows),
        best_coal=max(coal, key=lambda r: r["netback"]) if coal else None,
        india_best=min(india, key=lambda r: r["per_mkcal"]) if india else None,
        power=bulk.get("power"), steel=bulk.get("steel") or {},
        spreads={s["key"]: s for s in bulk["spreads"]}, spread_rows=bulk["spreads"], prices=price_rows("bulk"),
        assumptions=bulk["assumptions"], families=BULK_FAMILIES, units=UNITS)
    (out / "bulk.html").write_text(html, encoding="utf-8")

    # freight
    fr = snapshot["freight"]
    for idea in freight_ideas or []:
        idea["geo"] = idea_geometry(idea)
    hormuz = next((c for c in fr["chokepoints"] if c["name"] == "Hormuz"), None)
    td25 = next((r for r in routes if r["route_id"] == "CRUDE_USGC_NWE"), None)
    fcharts = dict(curves=[_html(curve_chart(fr["curves"], keys, title)) for title, keys in CURVE_GROUPS],
                   headroom=_html(headroom_chart(fr["headroom"])),
                   tankers=_html(chokepoint_chart(fr["chokepoint_weeks"], "n_tanker", "Tankers")),
                   bulkers=_html(chokepoint_chart(fr["chokepoint_weeks"], "n_dry_bulk", "Bulk carriers")))
    html = env.get_template("freight.html").render(
        **common, page="freight", fr=fr, charts=fcharts, ideas=freight_ideas or [], hormuz=hormuz, td25=td25,
        all_routes=routes + brows, prices=price_rows("freight"),
        curve_labels={key: c["label"] for key, c in CURVES.items()})
    (out / "freight.html").write_text(html, encoding="utf-8")

    # commodity trade ideas
    html = env.get_template("ideas.html").render(**common, page="ideas", ideas=desk_ideas or [])
    (out / "ideas.html").write_text(html, encoding="utf-8")
    return out / "index.html"
