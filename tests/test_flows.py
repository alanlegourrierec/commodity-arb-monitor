import json
from datetime import date, timedelta

import pytest
import requests

from arb import flows

GROUPS = {
    "ORE": dict(label="Ore ports", field="export_dry_bulk", ports={1: "North", 2: "South"}),
    "LNG": dict(label="Gas port", field=flows.CALLS, dark=True, ports={3: "Gas port"}),
}


def _days(first, n, **fields):
    return [dict(date=(first + timedelta(days=i)).isoformat(), **fields) for i in range(n)]


def _weeks(first_sunday, n, **fields):
    return {(first_sunday + timedelta(weeks=i)).isoformat(): dict(days=7, **fields) for i in range(n)}


def test_weekly_keeps_only_complete_weeks():
    rows = _days(date(2026, 11, 2), 8, export_dry_bulk=1000)       # Mon 2 Nov to Mon 9 Nov
    weeks = flows.weekly(rows, ["export_dry_bulk"])
    assert list(weeks) == ["2026-11-08"]
    assert weeks["2026-11-08"]["export_dry_bulk"] == 7000


def test_weekly_reads_dates_given_in_milliseconds():
    rows = [dict(date=1793577600000 + i * 86_400_000, export_dry_bulk=1) for i in range(7)]   # from Mon 2 Nov 2026
    assert list(flows.weekly(rows, ["export_dry_bulk"])) == ["2026-11-08"]


def test_groups_sum_their_ports_in_thousand_tonnes_and_calls_as_counts():
    first = date(2025, 10, 5)
    by_port = {1: _weeks(first, 57, export_dry_bulk=2_000_000, portcalls_tanker=0),
               2: _weeks(first, 57, export_dry_bulk=1_000_000, portcalls_tanker=0),
               3: _weeks(first, 57, export_dry_bulk=0, portcalls_tanker=6)}
    for week in list(by_port[1])[-4:]:
        by_port[1][week]["export_dry_bulk"] = 1_000_000            # the north port halves in the last four weeks
    built = flows.build(by_port, GROUPS)
    assert len(built["weeks"]) == 57
    assert built["groups"]["ORE"][0] == 3000 and built["groups"]["ORE"][-1] == 2000
    assert built["groups"]["LNG"][-1] == 6
    north = built["ports"]["ORE"][0]
    assert (north["name"], north["now"], north["year_ago"]) == ("North", 1000, 2000)

    s = flows.summary(dict(built), GROUPS)
    assert s["ORE"]["unit"] == "Mt a week"
    assert s["ORE"]["now"] == pytest.approx(2.0) and s["ORE"]["year_ago"] == pytest.approx(3.0)
    assert s["ORE"]["change"] == pytest.approx(-33.33, abs=0.01)
    assert s["LNG"]["unit"] == "calls a week" and s["LNG"]["now"] == 6 and s["LNG"]["dark"]


def test_summary_without_a_full_year_gives_no_comparison():
    by_port = {1: _weeks(date(2026, 9, 6), 8, export_dry_bulk=1_000_000, portcalls_tanker=0)}
    built = flows.build(by_port, {"ORE": dict(label="Ore", field="export_dry_bulk", ports={1: "North"})})
    s = flows.summary(built, {"ORE": dict(label="Ore", field="export_dry_bulk", ports={1: "North"})})
    assert s["ORE"]["year_ago"] is None and s["ORE"]["change"] is None


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Service:
    """Stands in for PortWatch: every port loads the same tonnage every day."""

    def __init__(self, fail=0):
        self.asked, self.fail = [], fail

    def get(self, url, params=None, timeout=0):
        if self.fail:
            self.fail -= 1
            raise requests.ConnectionError("dropped")
        self.asked.append(params["where"])
        start = date.fromisoformat(params["where"].split("DATE '")[1][:10])
        rows = _days(start, 60, export_dry_bulk=1000, portcalls_tanker=1)
        return Reply({"features": [{"attributes": r} for r in rows]})


def test_update_asks_for_each_port_by_its_id_and_writes_the_file(tmp_path):
    path = tmp_path / "port_flows.json"
    service = Service()
    data = flows.update_flows(path, date(2026, 11, 14), service, GROUPS)
    assert sorted(w.split("'")[1] for w in service.asked) == ["port1", "port2", "port3"]
    assert json.loads(path.read_text(encoding="utf-8"))["groups"].keys() == data["groups"].keys()
    assert data["groups"]["ORE"][-1] == 14                         # two ports, 7 kt a week each


def test_a_failed_update_keeps_the_last_file(tmp_path, monkeypatch):
    monkeypatch.setattr(flows.time, "sleep", lambda s: None)
    path = tmp_path / "port_flows.json"
    path.write_text(json.dumps(dict(weeks=["2026-11-01"], groups={"ORE": [1]}, ports={})), encoding="utf-8")
    data = flows.update_flows(path, date(2026, 11, 14), Service(fail=99), GROUPS)
    assert data["groups"] == {"ORE": [1]}


def test_a_dropped_request_is_tried_again(tmp_path, monkeypatch):
    monkeypatch.setattr(flows.time, "sleep", lambda s: None)
    service = Service(fail=2)
    rows = flows.fetch_port(1, date(2026, 9, 1), ["export_dry_bulk"], service)
    assert len(rows) == 60 and len(service.asked) == 1


def test_every_port_belongs_to_one_field_per_group():
    for key, g in flows.GROUPS.items():
        assert g["field"] in flows.MEASURES, key
        assert g["ports"], key
