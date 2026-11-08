import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("SKIP_BG_WARMUP", "1")

from us_movers import snapshot_view

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "docs" / "api" / "us_movers.json").read_text(encoding="utf-8"))
JS = ROOT / "static" / "us_movers_view.js"
NODE = shutil.which("node")


def js_view(snap, kind, limit):
    code = (f"const m=require({json.dumps(str(JS))});"
            f"process.stdout.write(JSON.stringify(m.usMoversView({json.dumps(snap, ensure_ascii=False)},{json.dumps(kind)},{json.dumps(limit)})));")
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("period", ["W", "M", "Y"])
@pytest.mark.parametrize("kind", ["all", "stock", "etf", "lev"])
def test_real_seed_kinds_never_mix(period, kind):
    snap = SEED["periods"][period]
    before = copy.deepcopy(snap)
    v = snapshot_view(snap, kind, 20)
    assert snap == before  # seed not mutated
    assert v["kind"] == kind
    rows = v["gainers"] + v["losers"]
    if kind == "all":
        assert all(r["kind"] in ("stock", "etf") for r in rows)
        assert v["universe"] == snap["universe"] and v["lev_gainers"] == snap["lev_gainers"]
        assert v["partial"] is False
    else:
        assert all(r["kind"] == kind for r in rows)
    if kind in ("stock", "etf"):
        assert v["partial"] is True and v["universe"] is None and "非完整" in v["note"]
    if kind == "lev":
        assert v["universe"] == snap["lev_universe"]
        assert [r["symbol"] for r in v["gainers"]] == [r["symbol"] for r in snap["lev_gainers"]][:20]
        assert v["partial"] is True and "不是完整排名" in v["note"]  # only 12 saved


@pytest.mark.parametrize("limit", [5, 20, 50, 0, -3, "abc", None, 999])
def test_limit_clamped_and_sorted(limit):
    v = snapshot_view(SEED["periods"]["Y"], "all", limit)
    assert 5 <= v["limit"] <= 50 and len(v["gainers"]) <= v["limit"]
    pcts = [r["change_pct"] for r in v["gainers"]]
    assert pcts == sorted(pcts, reverse=True)


@pytest.mark.parametrize("kind", ["ETF", "lev ", "x", None, 3])
def test_invalid_kind_falls_back_to_all(kind):
    assert snapshot_view(SEED["periods"]["Y"], kind, 20)["kind"] in ("all", "etf")


def test_no_cross_kind_pollution():
    snap = SEED["periods"]["Y"]
    a = snapshot_view(snap, "lev", 20)
    a["gainers"][0]["symbol"] = "HACK"
    assert snapshot_view(snap, "all", 20)["gainers"][0]["symbol"] == snap["gainers"][0]["symbol"]


def test_per_kind_export_used_when_present():
    snap = copy.deepcopy(SEED["periods"]["Y"])
    etf_rows = [{"symbol": f"E{i}", "kind": "etf", "change_pct": 100 - i} for i in range(25)]
    snap["kinds"] = {"etf": {"kind": "etf", "universe": 150, "gainers": etf_rows, "losers": etf_rows[::-1]}}
    v = snapshot_view(snap, "etf", 20)
    assert v["partial"] is False and v["universe"] == 150 and len(v["gainers"]) == 20
    assert "kinds" not in v


def test_already_filtered_payload_passthrough_keeps_partial():
    v = snapshot_view(SEED["periods"]["Y"], "etf", 20)
    again = snapshot_view(v, "etf", 20)
    assert again["partial"] is True and again["gainers"] == v["gainers"]


@pytest.mark.skipif(NODE is None, reason="node missing")
@pytest.mark.parametrize("period", ["W", "Y"])
@pytest.mark.parametrize("kind", ["all", "stock", "etf", "lev", "bogus"])
def test_js_matches_python(period, kind):
    snap = SEED["periods"][period]
    assert js_view(snap, kind, 20) == json.loads(json.dumps(snapshot_view(snap, kind, 20), ensure_ascii=False))


@pytest.mark.skipif(NODE is None, reason="node missing")
def test_js_static_full_file_after_pick():
    # static mode: frontend gets the whole file, picks period, then views
    snap = SEED["periods"]["Y"]
    v = js_view(snap, "lev", 20)
    assert all(r["kind"] == "lev" for r in v["gainers"])


def test_api_render_seed_respects_kind(monkeypatch):
    import app as app_mod

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_mod.bg_tasks, "submit", lambda *a, **k: False)
    c = app_mod.app.test_client()
    out = {}
    for k in ("all", "stock", "etf", "lev"):
        d = c.get(f"/api/us-movers?period=Y&kind={k}&limit=20").get_json()
        assert d["kind"] == k and d["source"] == "seed"
        out[k] = d
    assert {r["kind"] for r in out["lev"]["gainers"]} == {"lev"}
    assert {r["kind"] for r in out["etf"]["gainers"] + out["etf"]["losers"]} <= {"etf"}
    assert out["all"]["gainers"][0]["symbol"] != out["lev"]["gainers"][0]["symbol"]


@pytest.mark.skipif(NODE is None, reason="node missing")
def test_frontend_request_sequence_guard():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    body = js[js.index("async function loadUsMovers"):js.index("async function loadSupply")]
    # behavioural check of the guard pattern with a tiny harness
    harness = r"""
    const state={usReqSeq:0,usKind:'all',usPeriod:'Y'};const rendered=[];
    async function load(kind,delay){const seq=(state.usReqSeq=(state.usReqSeq||0)+1);
      await new Promise(r=>setTimeout(r,delay)); if(seq!==state.usReqSeq) return; rendered.push(kind);}
    Promise.all([load('all',50),load('lev',5)]).then(()=>process.stdout.write(JSON.stringify(rendered)));
    """
    assert "if (seq !== state.usReqSeq) return;" in body and body.count("seq !== state.usReqSeq") >= 2
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["lev"]
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert html.index("us_movers_view.js") < html.index("/static/app.js")


def _rows(kind, n, start=100):
    return [{"symbol": f"{kind[:1].upper()}{i}", "kind": kind, "change_pct": start - i} for i in range(n)]


def _cases():
    base = copy.deepcopy(SEED["periods"]["Y"])
    asym = dict(base, lev_gainers=_rows("lev", 12), lev_losers=_rows("lev", 3), lev_universe=109)
    sub = copy.deepcopy(base)
    sub["kinds"] = {"etf": {"kind": "etf", "universe": 150, "gainers": _rows("etf", 20), "losers": _rows("etf", 20),
                            "asof": "2026-11-05", "note": "子快照说明", "partial": True}}
    small = copy.deepcopy(base)
    small["kinds"] = {"etf": {"kind": "etf", "universe": 3, "gainers": _rows("etf", 3), "losers": _rows("etf", 3)}}
    nouni = copy.deepcopy(base)
    nouni["kinds"] = {"etf": {"kind": "etf", "gainers": _rows("etf", 20), "losers": _rows("etf", 20)}}
    empty = copy.deepcopy(base)
    empty["kinds"] = {"stock": {"kind": "stock", "universe": None, "gainers": [], "losers": []}}
    return {"base": base, "asym": asym, "sub": sub, "small": small, "nouni": nouni, "empty": empty}


CASES = _cases()


def test_limit_above_stored_is_partial_for_all():
    v = snapshot_view(CASES["base"], "all", 50)
    assert v["partial"] is True and len(v["gainers"]) == 20  # never padded
    assert snapshot_view(CASES["base"], "all", 20)["partial"] is False


def test_lev_asymmetric_losers_short_is_partial():
    v = snapshot_view(CASES["asym"], "lev", 10)
    assert len(v["gainers"]) == 10 and len(v["losers"]) == 3 and v["partial"] is True


def test_sub_view_partial_note_asof_preserved():
    v = snapshot_view(CASES["sub"], "etf", 20)
    assert v["partial"] is True and "子快照说明" in v["note"] and v["asof"] == "2026-11-05"


def test_small_known_universe_complete():
    v = snapshot_view(CASES["small"], "etf", 20)
    assert v["partial"] is False and v["universe"] == 3


@pytest.mark.parametrize("case,kind", [("nouni", "etf"), ("empty", "stock")])
def test_unknown_universe_or_empty_never_complete(case, kind):
    assert snapshot_view(CASES[case], kind, 20)["partial"] is True


@pytest.mark.skipif(NODE is None, reason="node missing")
@pytest.mark.parametrize("case,kind,limit", [("base", "all", 50), ("asym", "lev", 10), ("sub", "etf", 20),
                                             ("small", "etf", 20), ("nouni", "etf", 20), ("empty", "stock", 20)])
def test_js_parity_edge_cases(case, kind, limit):
    snap = CASES[case]
    assert js_view(snap, kind, limit) == json.loads(json.dumps(snapshot_view(snap, kind, limit), ensure_ascii=False))


def test_build_movers_export_uses_cache_and_all_kinds(tmp_path, monkeypatch):
    import us_movers

    calls = []

    def fake_compute(period, kind, limit, force):
        calls.append((period, kind, limit, force))
        rows = _rows("lev" if kind == "lev" else ("etf" if kind == "etf" else "stock"), 3)
        return {"period": period, "kind": kind, "universe": 3, "gainers": rows, "losers": rows,
                "lev_gainers": [], "lev_losers": [], "asof": "2026-11-06", "note": "n"}

    monkeypatch.setattr(us_movers, "get_price_panel", lambda **k: pytest.fail("real panel used"))
    flat = us_movers.build_movers_export(compute=fake_compute)
    assert {f for *_, f in calls} == {False}
    assert sorted(calls) == sorted((p, k, 20, False) for p in "WMY" for k in ("all", "stock", "etf", "lev"))
    for p in "WMY":
        assert set(flat["periods"][p]["kinds"]) == {"stock", "etf", "lev"}
        assert flat["periods"][p]["kinds"]["lev"]["kind"] == "lev"
    assert flat["default_period"] == "W"
    v = snapshot_view(flat["periods"]["Y"], "etf", 20)
    assert v["partial"] is False and {r["kind"] for r in v["gainers"]} == {"etf"}


def test_export_script_delegates_to_helper():
    src = (ROOT / "export_static_pages.py").read_text(encoding="utf-8")
    assert "build_movers_export()" in src
