import json
from datetime import date
from pathlib import Path

import pytest
import requests

from arb import eia

FIXTURES = Path(__file__).parent / "fixtures"


def _tables():
    return {name: (FIXTURES / f"eia_{name}.csv").read_text(encoding="utf-8") for name in ("table1", "table2", "table4")}


class Reply:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class Site:
    """Serves the saved report tables, or fails on the ones named in `down`.

    The real storage file starts with a byte order mark, so this one does too.
    """

    def __init__(self, down=()):
        self.down = down

    def get(self, url, timeout=0):
        name = url.rsplit("/", 1)[-1]
        if any(part in name for part in self.down):
            raise requests.ConnectionError(name)
        mark = b"\xef\xbb\xbf" if name.endswith(".json") else b""
        return Reply(mark + (FIXTURES / f"eia_{name}").read_bytes())


def test_stocks_are_read_with_the_week_before_and_the_year_before():
    series = eia.parse_petroleum(_tables())
    crude = series["crude_stocks"]
    assert (crude["week"], crude["value"], crude["previous"], crude["year_ago"]) == ("2026-11-06", 414.417, 409.76, 427.581)
    assert series["cushing"]["value"] == 27.356
    assert series["distillate_stocks"]["value"] == 103.33
    assert "four_week" not in crude                               # stock tables carry no four-week average


def test_flows_are_read_with_their_four_week_averages():
    series = eia.parse_petroleum(_tables())
    production = series["crude_production"]
    assert (production["value"], production["previous"]) == (14013, 14003)
    assert (production["four_week"], production["four_week_year_ago"]) == (13819, 13697)
    assert series["crude_imports"]["value"] == 6698 and series["crude_exports"]["value"] == 3455
    assert series["product_exports"]["value"] == 7543              # same label as crude exports, another block
    assert series["utilisation"]["value"] == 95.2 and series["utilisation"]["year_ago"] == 89.4


def test_gas_storage_comes_with_its_five_year_average():
    gas = eia.parse_storage(json.loads((FIXTURES / "eia_wngsr.json").read_text(encoding="utf-8")))
    assert (gas["week"], gas["value"], gas["previous"], gas["year_ago"], gas["five_year"]) == \
        ("2026-11-06", 3862, 3801, 3954, 3787)


def test_the_storage_file_is_read_past_its_byte_order_mark(tmp_path):
    data = eia.update_weekly(tmp_path / "us_weekly.json", date(2026, 11, 14), Site())
    assert data["gas"]["value"] == 3862 and data["series"]["crude_stocks"]["value"] == 414.417


def test_rows_compare_stocks_on_the_week_and_flows_on_four_weeks(tmp_path):
    data = eia.update_weekly(tmp_path / "us_weekly.json", date(2026, 11, 14), Site())
    rows = {r["key"]: r for r in eia.rows(data, ["crude_stocks", "crude_production", "utilisation", "gas_storage"])}
    assert rows["crude_stocks"]["on_week"] == pytest.approx(4.657)
    assert rows["crude_stocks"]["on_year"] == pytest.approx(-3.08, abs=0.01)
    assert rows["crude_production"]["on_year_unit"] == "% (4-week)"
    assert rows["crude_production"]["on_year"] == pytest.approx(0.89, abs=0.01)
    assert rows["utilisation"]["on_year_unit"] == "pts" and rows["utilisation"]["on_year"] == pytest.approx(5.8)
    assert rows["gas_storage"]["vs_five_year"] == pytest.approx(1.98, abs=0.01)


def test_a_report_that_fails_keeps_its_last_reading(tmp_path):
    path = tmp_path / "us_weekly.json"
    first = eia.update_weekly(path, date(2026, 11, 14), Site())
    assert first["week"] == "2026-11-06" and first["history"]["2026-11-06"]["crude_stocks"] == 414.417

    later = eia.update_weekly(path, date(2026, 11, 21), Site(down=("table", "wngsr")))
    assert later["series"]["crude_stocks"]["value"] == 414.417
    assert later["gas"]["value"] == 3862
    assert later["week"] == "2026-11-06"

    gas_only = eia.update_weekly(path, date(2026, 11, 21), Site(down=("table",)))
    assert gas_only["gas"]["value"] == 3862 and gas_only["series"]["cushing"]["value"] == 27.356


def test_nothing_fetched_and_nothing_saved_gives_no_data(tmp_path):
    assert eia.update_weekly(tmp_path / "us_weekly.json", date(2026, 11, 14), Site(down=("table", "wngsr"))) is None
    assert eia.rows(None, ["crude_stocks"]) == []
