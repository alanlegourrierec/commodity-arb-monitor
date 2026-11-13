import importlib.util
from datetime import date
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("lseg_pull", Path(__file__).parent.parent / "tools" / "lseg_pull.py")
lseg_pull = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lseg_pull)


def test_a_stale_code_keeps_the_previous_value():
    rows = [dict(type="freight", key="LNG_ATL", label="LNG", unit="$/day", value="25750", ws_flat_rate="",
                 date="2026-10-06", source="press", comment="")]
    mapping = [dict(type="freight", key="LNG_ATL", label="LNG", unit="$/day", ric=".BLNG2", comment="")]
    rows, report = lseg_pull.merge(rows, mapping, {".BLNG2": (50000.0, "2025-12-23")}, date(2026, 11, 12))
    assert rows[0]["value"] == "25750"
    assert report[0][3].startswith("stale since 2025-12-23")
