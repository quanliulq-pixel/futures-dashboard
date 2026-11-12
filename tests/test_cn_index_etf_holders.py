"""机构ETF持仓：F10 持有人结构解析、按报告期加总、缺失=无数据、不把内部持有加进机构。不联网。"""
import json
from datetime import date
from pathlib import Path

import pytest

import cn_index_etf_holders as H
import cn_index_flow as F

FIX = Path(__file__).resolve().parent / "fixtures"


def _txt(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_cyrjg_complete_and_dash_rows():
    rows = H.parse_cyrjg(_txt("cn_etf_holders_cyrjg_510300.txt"))
    assert [r["date"] for r in rows] == [date(2012, 5, 21), date(2025, 12, 31), date(2026, 6, 30)]
    last = rows[-1]
    assert last["inst_pct"] == pytest.approx(61.65)
    assert last["indiv_pct"] == pytest.approx(32.95)
    assert last["internal_pct"] == pytest.approx(5.40)
    assert last["total_shares"] == pytest.approx(189.15)
    dash = rows[0]
    assert dash["inst_pct"] == pytest.approx(82.64)
    assert dash["total_shares"] is None  # --- 不当成 0
    complete = H.complete_rows(rows)
    assert [r["date"] for r in complete] == [date(2025, 12, 31), date(2026, 6, 30)]
    assert complete[-1]["inst_shares"] == pytest.approx(61.65 / 100 * 189.15)


def test_parse_cyrjg_empty_and_bad_html():
    assert H.parse_cyrjg(None) == []
    assert H.parse_cyrjg("") == []
    assert H.parse_cyrjg(_txt("cn_etf_holders_cyrjg_empty.txt")) == []
    assert H.parse_cyrjg(_txt("cn_etf_holders_cyrjg_bad.txt")) == []


def test_never_add_internal_to_institution():
    rows = H.parse_cyrjg(_txt("cn_etf_holders_cyrjg_overlap.txt"))
    assert len(rows) == 1
    # 机构+个人=100，内部 11.94% 与机构重叠，不能加总
    assert rows[0]["inst_pct"] + rows[0]["indiv_pct"] == pytest.approx(100.0)
    assert rows[0]["internal_pct"] == pytest.approx(11.94)
    etfs = [("515310", "汇添富沪深300ETF")]
    out = H.aggregate_index(etfs, {"515310": rows})
    assert out["status"] == "ok"
    assert out["inst_shares"] == pytest.approx(round(45.56 / 100 * 2.31, 2))
    assert out["inst_shares"] != pytest.approx(round((45.56 + 11.94) / 100 * 2.31, 2))


def test_aggregate_skips_funds_missing_the_report_date():
    funds = {
        "A": [
            {"date": "2026-06-30", "inst_pct": 50.0, "total_shares": 10.0},
            {"date": "2025-12-31", "inst_pct": 80.0, "total_shares": 20.0},
        ],
        "B": [
            {"date": "2025-12-31", "inst_pct": 90.0, "total_shares": 5.0},
        ],
        "C": [
            {"date": "2026-06-30", "inst_pct": None, "total_shares": 3.0},  # 缺比例，不算
        ],
    }
    etfs = [("A", "a"), ("B", "b"), ("C", "c")]
    out = H.aggregate_index(etfs, funds)
    assert out["status"] == "ok"
    assert out["report_date"] == "2026-06-30"
    assert out["codes"] == ["A"]
    assert out["coverage"] == "1/3 只有数据"
    assert out["inst_shares"] == pytest.approx(5.0)  # 50% * 10
    assert out["prev_date"] == "2025-12-31"
    # 较上期只比较同时有两期的基金（仅 A），不含 B 的 2025-12-31
    assert out["chg_shares"] == pytest.approx(5.0 - 16.0)
    assert out["prev_inst_shares"] == pytest.approx(16.0)
    assert out["items"][1]["status"] == "missing"
    assert out["items"][2]["status"] == "missing"


def test_aggregate_missing_is_not_zero():
    out = H.aggregate_index([("X", "x")], {})
    assert out["status"] == "missing"
    assert out.get("inst_shares") in (None, ) or "inst_shares" not in out
    assert "无完整" in out["reason"]
    empty_rows = H.aggregate_index(
        [("X", "x")],
        {"X": [{"date": "2026-09-30", "inst_pct": None, "total_shares": 1.0}]},
    )
    assert empty_rows["status"] == "missing"
    assert empty_rows.get("inst_shares") not in (0, 0.0)


def test_seed_bundle_matches_2026_h1_totals():
    payload = H.load_seed_payload()
    assert payload and payload["funds"]
    b = H.build_holder_bundle(payload, from_seed=True)
    assert b["in_score"] is False
    csi = b["indices"]["csi300"]
    assert csi["status"] == "ok" and csi["report_date"] == "2026-06-30"
    assert csi["prev_date"] == "2025-12-31"
    assert csi["inst_shares"] == pytest.approx(348.75, abs=0.01)
    assert csi["inst_pct"] == pytest.approx(72.76, abs=0.01)
    assert csi["chg_shares"] < 0 and csi["chg_pct_pt"] < 0
    assert csi["codes"][0] == "510300"
    assert csi["source_url"].startswith("https://fundf10.eastmoney.com/cyrjg_510300.html")
    cx = b["indices"]["chinext"]
    assert cx["status"] == "ok" and cx["inst_shares"] == pytest.approx(104.86, abs=0.01)


def test_attach_holders_does_not_blank_existing_flows():
    bundle = {
        "indices": {
            "csi300": {"id": "csi300", "name": "沪深300", "main": {"status": "ok", "net_1d": 1},
                       "etf": {"status": "ok", "net_1d": -2}},
        },
        "notes": ["keep-me"],
        "in_score": False,
    }
    holders = H.build_holder_bundle(H.load_seed_payload(), from_seed=True)
    out = F.attach_etf_holders(bundle, holders)
    assert out["indices"]["csi300"]["main"]["net_1d"] == 1
    assert out["indices"]["csi300"]["etf"]["net_1d"] == -2
    assert out["indices"]["csi300"]["holders"]["status"] == "ok"
    assert out["indices"]["csi300"]["holders"]["report_date"] == "2026-06-30"
    assert "keep-me" in out["notes"]
    assert any("半年报" in n for n in out["notes"])
    # 持仓源挂掉时，资金流仍在，持仓为无数据
    blank = F.attach_etf_holders(bundle, {"reason": "F10 失败", "indices": {}})
    assert blank["indices"]["csi300"]["main"]["status"] == "ok"
    assert blank["indices"]["csi300"]["holders"]["status"] == "missing"
    assert "F10 失败" in blank["indices"]["csi300"]["holders"]["reason"]


def test_ensure_holders_loads_seed_without_network(monkeypatch):
    bundle = {"indices": {"csi300": {"id": "csi300", "main": {"status": "ok"}}}, "notes": []}

    def deny(*a, **k):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(H, "fetch_cyrjg", deny)
    monkeypatch.setattr(H, "refresh_holder_bundle", deny)
    out = F.ensure_etf_holders(bundle)
    assert out["indices"]["csi300"]["holders"]["status"] == "ok"
    # already attached → same object
    assert F.ensure_etf_holders(out) is out
