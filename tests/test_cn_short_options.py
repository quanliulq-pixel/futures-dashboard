import json
from datetime import datetime
from pathlib import Path

import cn_short_options as m

TODAY = datetime(2026, 11, 6, 15, 0, tzinfo=m.CST)
CFG = dict(m.DEFAULTS)


def test_expired_months_excluded():
    html = "cu2601 cu2611 cu2612 cu2701"
    assert m.discover_months(html, "cu", TODAY) == ["cu2612", "cu2701"]
    assert not m.is_active_month(2611, TODAY)
    assert m.next_month_yymm(TODAY) == 2612
    assert m.next_month_yymm(datetime(2026, 12, 1, tzinfo=m.CST)) == 2701


def test_bid_ask_invalid():
    assert not m.executable_bid(0, 1, 0.5)
    assert not m.executable_bid(None, 1, 0.5)
    assert not m.executable_bid(2, 1, 0.5)          # crossed
    assert not m.executable_bid(1, 3, 0.5)          # spread too wide
    assert m.executable_bid(1.0, 1.2, 0.5)


def test_side_filter():
    assert m.otm_ok("put", 95, 100, 0.03, 0.15) == 0.05
    assert m.otm_ok("put", 105, 100, 0.03, 0.15) is None
    assert m.otm_ok("call", 105, 100, 0.03, 0.15) == 0.05
    assert m.otm_ok("call", 95, 100, 0.03, 0.15) is None


def test_freshness_semantics():
    assert m.freshness(None, TODAY) == "unknown"
    assert m.freshness("garbage", TODAY) == "unknown"
    assert m.freshness("2026-11-06 14:50:00", TODAY) == "delayed"
    assert m.freshness("2026-11-05 22:59:00", TODAY) == "last_session"
    assert m.freshness("2026-11-02 15:00:00", TODAY) == "stale"   # 周末后仍为 stale 而非 live
    assert m.freshness("2026-11-06 15:20:00", TODAY) == "invalid"  # 未来 >10 分钟
    assert m.freshness("2026-11-06 15:05:00", TODAY) == "delayed"


def test_future_quote_ts_night_session():
    fetched = datetime(2026, 11, 6, 23, 30, tzinfo=m.CST)
    # 夜盘 nf_ 交易日记为下一交易日
    assert m.future_quote_ts("2026-11-09", "230000", fetched) == "2026-11-06 23:00:00"
    assert m.future_quote_ts("2026-11-06", "150000", fetched) == "2026-11-06 15:00:00"
    assert m.future_quote_ts(None, "150000", fetched) is None


def test_candidate_uses_bid_and_null_multiplier():
    q = {"bid": 2.0, "ask": 2.2, "last": 9.9, "oi": 500, "strike": 95, "quote_time": "2026-11-06 15:00:00"}
    c = m.make_candidate("put", {"product": "x", "contract_month": "x"}, q, 100, 10, TODAY, CFG)
    assert c["premium_ref_per_lot_bid"] == 20.0 and c["iv"] is None and c["margin"] is None
    c2 = m.make_candidate("put", {}, q, 100, None, TODAY, CFG)
    assert c2["premium_ref_per_lot_bid"] is None
    future = dict(q, quote_time="2026-11-07 15:00:00")
    assert m.make_candidate("put", {}, future, 100, 10, TODAY, CFG) is None
    stale = dict(q, quote_time="2026-01-10 15:00:00")
    assert m.make_candidate("put", {}, stale, 100, 10, TODAY, CFG)["freshness"] == "stale"
    assert m.make_candidate("put", {}, dict(q, oi=0), 100, 10, TODAY, CFG) is None


def test_parse_quotes():
    txt = 'var hq_str_P_OP_m2701C3250="1,14.500,15.000,16.000,17,26397.000,,3250,16.000,16.000,201.500,0.500,0.000,0,0.000,0,0.000,0,0.000,0,16.000,17,14.500,1,0.000,0,0.000,0,0.000,0,0.000,0,2026-11-06 22:59:52,0";'
    q = m.parse_option_quote(m.parse_hq(txt)["P_OP_m2701C3250"])
    assert q["bid"] == 14.5 and q["ask"] == 16.0 and q["strike"] == 3250 and q["quote_time"] == "2026-11-06 22:59:52"
    assert m.parse_option_quote([]) is None
    assert m.parse_multiplier("<td>交易单位</td><td>10吨/手</td>") == 10


def _row(**kw):
    r = {"market": "cn_index", "expiry": "2026-11-20", "product": "IO", "contract_month": "io2611", "otm_pct": 5}
    r.update(kw)
    return r


def test_merge_keeps_last_good_only_on_error_and_drops_expired():
    prev = {"rows": [_row(), _row(expiry="2026-10-16"),
                     _row(market="cn_commodity", expiry=None, contract_month="cu2611")], "last_good_at": "t0"}
    failed = {"rows": {"put": [], "call": []}, "products": [], "errors": ["boom"]}
    out = m.merge_market(prev, failed, "put", TODAY)
    assert out["stale"] is True and out["errors"] == ["boom"] and len(out["rows"]) == 1
    assert out["rows"][0]["expiry"] == "2026-11-20"


def test_success_empty_does_not_resurrect():
    prev = {"rows": [_row()], "last_good_at": "t0"}
    ok_empty = {"rows": {"put": [], "call": []}, "products": [{"x": 1}], "errors": []}
    out = m.merge_market(prev, ok_empty, "put", TODAY)
    assert out["rows"] == [] and out["stale"] is False and "成功" in out["empty_reason"]
    all_expired_prev = {"rows": [_row(expiry="2026-10-16")]}
    out2 = m.merge_market(all_expired_prev, {"rows": {"put": [], "call": []}, "products": [], "errors": ["e"]}, "put", TODAY)
    assert out2["rows"] == [] and "刷新失败" in out2["empty_reason"]


def test_fresh_expired_rows_dropped():
    fresh = {"rows": {"put": [_row(expiry="2026-11-05"), _row()], "call": []}, "products": [], "errors": []}
    assert len(m.merge_market(None, fresh, "put", TODAY)["rows"]) == 1


def test_index_spec_verified_sources():
    for k in ("io", "ho", "mo"):
        assert m.INDEX_SPEC[k]["multiplier"] == 100.0 and m.INDEX_SPEC[k]["source"].startswith("http://www.cffex.com.cn/")


def test_routes_and_static_map():
    root = Path(m.__file__).resolve().parent
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "'/api/cn-short-put': './api/cn_short_put.json'" in js
    assert "'/api/cn-short-call': './api/cn_short_call.json'" in js
    exp = (root / "export_static_pages.py").read_text(encoding="utf-8")
    assert "cn_options.js?v=static" in exp and "cn_short_options" in exp
    html = (root / "templates" / "index.html").read_text(encoding="utf-8")
    assert '/static/cn_options.js?v={{ asset_v }}' in html
    for side in ("put", "call"):
        assert f'id="{side}-cn-panel"' in html and f'id="{side}-us-layout"' in html
    wf = (root / ".github" / "workflows" / "refresh_pages.yml").read_text(encoding="utf-8")
    assert "cn_short_options.py" in wf and wf.count("python cn_short_options.py") == 0  # 由 exporter 调用，避免重复构建
    import app as flask_app
    rules = {r.rule for r in flask_app.app.url_map.iter_rules()}
    assert {"/api/cn-short-put", "/api/cn-short-call"} <= rules
