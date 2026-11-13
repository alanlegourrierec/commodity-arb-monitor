import importlib.util
from datetime import date
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("lseg_pull", Path(__file__).parent.parent / "tools" / "lseg_pull.py")
lseg_pull = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lseg_pull)

TODAY = date(2026, 11, 12)


def test_monthly_codes_roll_with_the_date():
    assert lseg_pull.resolve("BATMM{M}{Y}", TODAY) == "BATMMX6"
    assert lseg_pull.resolve("BATMM{M}{Y}", date(2027, 1, 4)) == "BATMMF7"
    assert lseg_pull.resolve(".BAWB", TODAY) == ".BAWB"


def test_map_has_no_empty_codes_after_reading():
    assert all(m["ric"].strip() for m in lseg_pull.read_map())


def test_merge_updates_mapped_rows_and_keeps_the_rest():
    rows = [dict(type="param", key="WAR_TD3C", label="War risk", unit="% of hull", value="1.5", ws_flat_rate="",
                 date="2026-11-09", source="Lloyd's List", comment=""),
            dict(type="freight", key="C5", label="C5", unit="$/t", value="10.0", ws_flat_rate="", date="2026-11-11",
                 source="LSEG .BAWB", comment="")]
    mapping = [dict(type="freight", key="C5", label="C5", unit="$/t", ric=".BAWB", comment=""),
               dict(type="freight", key="C4", label="C4", unit="$/day", ric="BATMM{M}{Y}", comment="")]
    fetched = {".BAWB": (13.25, "2026-11-12")}
    rows, report = lseg_pull.merge(rows, mapping, fetched, TODAY)
    by_key = {r["key"]: r for r in rows}
    assert by_key["WAR_TD3C"]["value"] == "1.5"
    assert by_key["C5"]["value"] == "13.25" and by_key["C5"]["date"] == "2026-11-12"
    assert by_key["C4"]["value"] == "" and "no data" in report[1][3]


def test_main_writes_the_rate_file(tmp_path):
    out = tmp_path / "freight.csv"
    code = lseg_pull.main(["--out", str(out)], fetch=lambda rics: {".BAWB": (13.25, None)}, today=TODAY)
    assert code == 0
    rows = {r["key"]: r for r in lseg_pull.read_rows(out)}
    assert rows["C5"]["value"] == "13.25" and rows["C5"]["date"] == "2026-11-12"
    assert rows["C5"]["source"] == "LSEG .BAWB"


def test_check_mode_writes_nothing(tmp_path):
    out = tmp_path / "freight.csv"
    lseg_pull.main(["--check", "--out", str(out)], fetch=lambda rics: {}, today=TODAY)
    assert not out.exists()


def test_the_pull_waits_for_workspace_to_open(capsys):
    calls, naps = [], []

    def fetch(rics):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("[WinError 10061] connection refused")
        return {".BAWB": (13.25, None)}

    fetched = lseg_pull.fetch_when_ready(fetch, [".BAWB"], wait_minutes=60, sleep=naps.append, clock=lambda: 0)
    assert fetched == {".BAWB": (13.25, None)}
    assert naps == [lseg_pull.RETRY_SECONDS] * 2
    assert capsys.readouterr().out.count("next try in 5 min") == 2


def test_without_a_wait_a_closed_workspace_fails_at_once_and_keeps_the_rate_file(tmp_path):
    out = tmp_path / "freight.csv"
    out.write_text("type,key,label,unit,value,ws_flat_rate,date,source,comment\n"
                   "freight,C5,C5,$/t,10.0,,2026-11-11,LSEG .BAWB,\n", encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    def closed(rics):
        raise ConnectionError("[WinError 10061] connection refused")

    assert lseg_pull.main(["--out", str(out)], fetch=closed, today=TODAY) == 1
    assert out.read_text(encoding="utf-8") == before
