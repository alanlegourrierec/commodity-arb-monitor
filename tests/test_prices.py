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
