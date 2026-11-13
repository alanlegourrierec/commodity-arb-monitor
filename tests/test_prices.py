from datetime import date

import pytest

from arb import prices


def test_parse_tradingview_payload():
    payload = {"totalCount": 2, "data": [{"s": "ICEEUR:ULS1!", "d": [1341.25]}, {"s": "NYMEX:JKM1!", "d": [None]}]}
    out = prices.parse_tradingview(payload)
    assert out["GASOIL_ICE"]["value"] == 1341.25
    assert "JKM" not in out


def test_assemble_derives_missing_prices():
    fetched = {
        "WTI_CUSHING": dict(value=87.27, unit="$/bbl"),
        "TTF_EUR": dict(value=87.185, unit="EUR/MWh", date="2026-11-12"),
        "EURUSD": dict(value=1.15, unit="USD per EUR"),
    }
    manual = {"HOU_DIFF": dict(value=0.58, unit="$/bbl", date="2026-11-12", source="x", label="x"),
              "JKM": dict(value=31.3, unit="$/MMBtu", date="2026-11-12", source="x", label="JKM")}
    px = prices.assemble(fetched, manual)
    assert px["WTI_HOUSTON"]["value"] == 87.27 + 0.58
    assert px["TTF_USD"]["value"] == 87.185 * 1.15 * 0.29307
    assert px["JKM"]["source"].startswith("Manual")


def test_price_table_flags_out_of_range_values():
    rows = prices.price_table({"BRENT": dict(value=9.0, unit="$/bbl")})
    assert rows[0]["check"] == "check"


def test_out_of_bounds_price_is_dropped_and_manual_takes_over():
    # ICEEUR:G1! once returned a 94.94 GBP contract instead of gasoil: the bound catches it.
    fetched = {"GASOIL_ICE": dict(value=94.94, unit="$/t", symbol="ICEEUR:G1!")}
    manual = {"GASOIL_ICE": dict(value=1341.25, unit="$/t", date="2026-11-12", source="x", label="Gasoil")}
    px = prices.assemble(fetched, manual)
    assert px["GASOIL_ICE"]["value"] == 1341.25


def test_houston_diff_from_the_market_beats_the_manual_one():
    fetched = {"WTI_CUSHING": dict(value=87.0, unit="$/bbl"), "HOU_DIFF": dict(value=0.6, unit="$/bbl", date="d")}
    manual = {"HOU_DIFF": dict(value=0.58, unit="$/bbl", date="old", source="x", label="x")}
    assert prices.assemble(fetched, manual)["WTI_HOUSTON"]["value"] == 87.6


def test_parse_curve_splits_live_and_expired_contracts():
    payload = {"data": [{"s": "ICEEUR:ULSX2026", "d": ["ULSX2026", 1341.25, 20261112]},
                        {"s": "ICEEUR:ULSZ2026", "d": ["ULSZ2026", 1249.75, 20261210]}]}
    live, gone = prices.parse_curve(payload, today="2026-11-13")
    assert [c["contract"] for c in live] == ["ULSZ2026"]
    assert [c["contract"] for c in gone] == ["ULSX2026"]
    assert len(prices.parse_curve(payload, today="2026-11-12")[0]) == 2


def test_the_last_close_of_an_expired_contract_is_kept(tmp_path):
    path = tmp_path / "settled.csv"
    dec = dict(contract="TFMZ2026", close=92.26, expiry="2026-11-27")
    jan = dict(contract="TFMF2027", close=92.08, expiry="2026-12-30")
    prices.with_settled({"TTF": [dec, jan]}, {}, path, date(2026, 11, 27))
    # the next day the feed no longer lists December, and January has moved
    jan = dict(jan, close=93.0)
    out = prices.with_settled({"TTF": [jan]}, {}, path, date(2026, 11, 28))
    assert [(c["contract"], c["close"]) for c in out["TTF"]] == [("TFMZ2026", 92.26), ("TFMF2027", 93.0)]
    # long after its month it is dropped
    out = prices.with_settled({"TTF": [jan]}, {}, path, date(2027, 3, 1), save=False)
    assert [c["contract"] for c in out["TTF"]] == ["TFMF2027"]


DAY = 86400
NOV12 = 1794438000          # CME bar of the 12 November session, which opens at 23:00 UTC on the 11th


def test_a_contract_trading_today_steps_back_to_its_last_settlement():
    # the bar of the 13th is open, so the settlement is the previous close and its change the one before
    assert prices.last_settlement("2026-11-13", 6.221, 6.196, NOV12 + DAY, 6.29, NOV12) == (
        6.196, "2026-11-12", pytest.approx(-0.094))
    # no trade yet today, the close is already the settlement of the 12th
    assert prices.last_settlement("2026-11-13", 6.234, 6.3285, NOV12, 6.30, NOV12 - DAY) == (
        6.234, "2026-11-12", pytest.approx(-0.0945))


def curve_row(name, close, expiry, previous, bar_time, before=None):
    return {"d": [name, close, expiry, previous, bar_time, before, bar_time - DAY]}


def test_a_contract_that_missed_the_session_moves_with_its_neighbour():
    payload = {"data": [curve_row("AUBZ2026", 94.37, 20270105, 92.33, NOV12 - DAY),      # last traded on the 11th
                        curve_row("AUBF2027", 94.22, 20270202, 91.85, NOV12)]}           # +2.37 on the 12th
    live, _ = prices.parse_curve(payload, today="2026-11-13")
    assert [c["close"] for c in live] == [pytest.approx(94.37 + 2.37), 94.22]


def test_a_thin_curve_a_session_behind_follows_brent():
    brent = prices.read_curve({"data": [curve_row("BRNF2027", 95.82, 20261130, 92.76, NOV12)]}, "2026-11-13")
    eurobob = prices.read_curve({"data": [curve_row("B7HZ2026", 843.817, 20270105, 842.685, NOV12 - DAY)]}, "2026-11-13")
    raw = {"BRENT": brent, "EUROBOB": eurobob}
    prices.follow(raw)
    assert raw["EUROBOB"][0]["close"] == pytest.approx(843.817 * 95.82 / 92.76)
    assert raw["BRENT"][0]["close"] == 95.82
