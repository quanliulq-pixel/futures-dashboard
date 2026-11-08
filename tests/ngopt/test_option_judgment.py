import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "static" / "ng_option_judgment.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def judge(data):
    code = f"const m=require({json.dumps(str(JS))});process.stdout.write(JSON.stringify(m.ngOptionJudgment({json.dumps(data, ensure_ascii=False)})));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def base(bias, cn, **kw):
    """Explicitly valid structure: live source, no errors, non-empty core, finite confidence."""
    d = {"source": "live", "advice": {"bias": bias, "bias_cn": cn}, "confidence": 63, "events": [], "errors": {},
         "price": {"price": 3.1}, "storage": {"total": 3900}, "weather": {"tdd": 20}}
    d.update(kw)
    return d


def test_bullish_valid_put_direction_not_trade():
    j = judge(base("lean_long", "轻度偏多"))
    assert j["state"] == "sell_put" and j["put"]["ok"] and not j["call"]["ok"]
    assert "尚未核验期权条件" in j["put"]["text"] and "非开仓建议" in j["put"]["text"]
    assert "适合" not in j["put"]["text"]
    assert "不是可交易信号" in " ".join(j["notes"]) and "现金担保" in " ".join(j["notes"])


def test_bearish_valid_call_direction_defined_risk():
    j = judge(base("short", "偏空"))
    assert j["state"] == "sell_call" and j["call"]["ok"] and not j["put"]["ok"]
    assert "备兑或定义风险价差" in " ".join(j["notes"])


def test_neutral_no_sell_no_strangle():
    j = judge(base("neutral", "观望/区间"))
    assert j["state"] == "no_sell" and j["direction_hint"] == "none"
    assert "不自动双卖或裸卖Call" in j["call"]["text"]


@pytest.mark.parametrize("bias", ["unknown", "longish", "not_short", "", None, 5, "LONG", "__proto__", "constructor", "toString"])
def test_illegal_bias_never_matches(bias):
    j = judge(base(bias, "x"))
    assert j["state"] == "no_sell" and j["direction_hint"] == "unknown"
    assert "方向缺失或无效" in j["put"]["text"]


@pytest.mark.parametrize("src", ["seed", "stale", "cache", "snapshot", "", None, "LIVE"])
def test_non_live_source_reference_only(src):
    j = judge(base("long", "偏多", source=src))
    assert j["state"] == "no_sell" and j["reference_only"] and j["direction_hint"] == "put"
    assert "次级研究提示" in j["put"]["text"] and "暂不卖" in j["put"]["text"]


def test_missing_source_key_is_unknown():
    d = base("long", "偏多")
    d.pop("source")
    assert judge(d)["state"] == "no_sell"


def test_stale_flag_blocks():
    assert judge(base("long", "偏多", stale=True))["state"] == "no_sell"


@pytest.mark.parametrize("patch", [
    {"errors": {"storage": "Timeout"}}, {"weather": None}, {"price": None}, {"price": {}},
    {"storage": {"stale": True, "total": 1}}, {"storage": {"seed": True}}, {"weather": {"status": "stale"}},
    {"price": {"source": "cache", "price": 3}}, {"errors": None},
])
def test_core_missing_empty_stale_or_error(patch):
    j = judge(base("long", "偏多", **patch))
    assert j["state"] == "no_sell" and j["reference_only"]


@pytest.mark.parametrize("conf", [None, "63", float("nan"), float("inf"), 40])
def test_confidence_invalid_or_low_blocks(conf):
    d = base("long", "偏多")
    if conf is None:
        d.pop("confidence")
    else:
        d["confidence"] = conf
    j = judge(d)
    assert j["state"] == "no_sell"
    assert "暂不卖" in j["put"]["text"] and "风险条件未解除" in j["put"]["text"]


@pytest.mark.parametrize("events", [[{"name": "EIA 周度库存报告", "in_hours": 10}],
                                    [{"name": "EIA 周度库存报告", "in_hours": None}],
                                    [{"name": "EIA 周度库存报告", "in_hours": "5"}], None])
def test_eia_window_or_invalid_events_block(events):
    j = judge(base("long", "偏多", events=events))
    assert j["state"] == "no_sell"


def test_eia_outside_window_ok():
    assert judge(base("long", "偏多", events=[{"name": "EIA 周度库存报告", "in_hours": 60}]))["state"] == "sell_put"


def test_missing_advice_and_null_payload():
    assert judge({})["state"] == "no_sell"
    assert judge(None)["state"] == "no_sell"


def test_monthly_supply_does_not_change_judgment():
    a = judge(base("neutral", "观望/区间"))
    b = judge(base("neutral", "观望/区间", monthly_supply={"series": {"dry_production": {"status": "stale"}}}))
    assert a == b


def test_no_fabricated_option_numbers():
    text = json.dumps(judge(base("long", "偏多")), ensure_ascii=False)
    assert "strike" not in text and "$" not in text and "%" not in text
    assert "不给行权价、收益与仓位" in text


def test_current_snapshot_no_sell_both_as_exported_and_as_render_seed():
    snap = json.loads((ROOT / "docs" / "api" / "ng_dashboard.json").read_text(encoding="utf-8"))
    assert judge(snap)["state"] == "no_sell"  # export carries no source -> unknown
    snap["source"] = "seed"
    j = judge(snap)
    assert j["state"] == "no_sell" and j["reference_only"]


def test_render_uses_text_nodes_and_script_included():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = js.index("function renderNgOptionJudgment")
    body = js[start:js.index("function renderNgEvents")]
    assert "innerHTML" not in body and "textContent" in body
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert html.index("ng_option_judgment.js") < html.index("/static/app.js")


def test_api_marks_only_in_process_build_as_live(monkeypatch):
    import os
    os.environ.setdefault("SKIP_BG_WARMUP", "1")
    import app as app_mod
    import ng_dashboard

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(ng_dashboard, "build_dashboard", lambda force=False: {"advice": {}, "updated_at": "x"})
    assert app_mod.app.test_client().get("/api/ng/dashboard").get_json()["source"] == "live"
    monkeypatch.setenv("RENDER", "true")
    assert app_mod.app.test_client().get("/api/ng/dashboard").get_json()["source"] == "seed"
