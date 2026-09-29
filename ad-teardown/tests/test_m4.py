"""M4: export and dashboard numbers reconcile with the database (no network)."""

import csv
import json

from teardown import analyze, report
from teardown.provider import MockProvider

from .test_m3 import IDS, _result, conn, responder_factory  # noqa: F401 — fixture reuse


def test_export_counts_equal_rows_and_dimensions_sum(conn, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(report, "TEMPLATE", tmp_path / "t.html")
    (tmp_path / "t.html").write_text("<script>const DATA = /*__DATA__*/null;</script>")
    analyze.run(conn, MockProvider(responder_factory()), log=lambda *_: None)
    out = report.build(conn, tmp_path / "out", log=lambda *_: None)
    payload = json.loads((tmp_path / "out" / "report.json").read_text())
    n_ads = conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
    assert out["rows"] == len(payload["rows"]) == n_ads
    with (tmp_path / "out" / "ads.csv").open() as f:
        assert sum(1 for _ in csv.reader(f)) - 1 == n_ads
    agg = payload["aggregates"]
    for d, cells in agg["dims"].items():  # unknown included: every dimension sums to all analyzed ads
        assert sum(sum(v.values()) for v in cells.values()) == agg["n_analyzed"], d
    assert agg["compare"]["nowa"].get("mismatch") == 1  # only the genuinely conflicting ad
    blocked = next(r for r in payload["rows"] if r["id"] == IDS["blocked"])
    assert blocked["compare"]["status"] == "unverifiable"
    html = (tmp_path / "out" / "dashboard.html").read_text()
    assert "/*__DATA__*/null" not in html and "</script>" in html
