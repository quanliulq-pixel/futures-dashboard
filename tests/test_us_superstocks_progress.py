# -*- coding: utf-8 -*-
"""Progress reporting, refresh latch, remote seed, and weekly workflow. No live network."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import bg_tasks
import us_superstocks as ss

from tests.test_us_superstocks import tight_base_then_breakout

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def progress_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss, "SEED_PATH", tmp_path / "us_superstocks.json")
    monkeypatch.setattr(ss, "DOCS_SEED_PATH", tmp_path / "docs_us_superstocks.json")
    monkeypatch.setattr(ss, "PROGRESS_PATH", tmp_path / "progress.json")
    monkeypatch.setattr(ss, "WEEKLY_PKL", tmp_path / "weekly.pkl")
    monkeypatch.setattr(ss, "UNI_PATH", tmp_path / "universe.json")
    monkeypatch.setattr(ss, "CACHE_DIR", tmp_path / "cache")
    ss._cache = None
    ss._reset_progress_for_tests()
    yield tmp_path
    ss._reset_progress_for_tests()
    ss._cache = None


def test_idle_progress_has_no_fake_bar(progress_env):
    p = ss.load_progress()
    assert p["state"] == "idle"
    assert p["percent"] == 0
    assert p["phase"] is None
    assert not p["error"]
    assert p["processed"] == 0


def test_phase_percent_is_determinate_and_monotonic():
    assert ss.phase_percent("queued", 0, 0) == 0
    assert ss.phase_percent("universe", 1, 1) == 5
    mid = ss.phase_percent("weekly", 50, 100)
    assert 5 < mid < 75
    assert ss.phase_percent("weekly", 100, 100) == 75
    assert ss.phase_percent("fundamentals", 0, 4) == 75
    assert ss.phase_percent("scoring", 1, 1) == 100
    assert ss.phase_percent("weekly", 10, 100) < ss.phase_percent("weekly", 80, 100)


def test_progress_persists_across_memory_clear(progress_env):
    ss.begin_job()
    mid = ss.set_progress(phase="weekly", processed=120, total=400, message="周线 120/400", state="running")
    assert mid["state"] == "running"
    assert mid["percent"] == ss.phase_percent("weekly", 120, 400)
    assert 5 < mid["percent"] < 75
    ss._progress_state = None  # page / process cache miss; job still alive
    loaded = ss.load_progress()
    assert loaded["state"] == "running"
    assert loaded["processed"] == 120
    assert loaded["total"] == 400
    assert loaded["phase"] == "weekly"
    assert "120/400" in loaded["message"]


def test_stale_running_progress_becomes_error(progress_env):
    ss.begin_job()
    ss.set_progress(phase="weekly", processed=1, total=10, message="周线 1/10", state="running")
    stale_at = (datetime.now(ss.SHANGHAI) - timedelta(minutes=20)).isoformat()
    ss._progress_state["updated_at"] = stale_at
    ss.PROGRESS_PATH.write_text(__import__("json").dumps(ss._progress_state, ensure_ascii=False), encoding="utf-8")
    ss._job_running = False
    ss._progress_state = None
    p = ss.load_progress()
    assert p["state"] == "error"
    assert "中断" in (p["error"] or "") or "退出" in (p["error"] or "")
    assert "未编造" in (p["message"] or "")


def _frame(symbol="PASS"):
    return pd.DataFrame(tight_base_then_breakout(vol_mult=6.5, base_weeks=14, after=6))


def test_refresh_reports_phases_and_finishes(progress_env, monkeypatch):
    uni = [
        {"symbol": "PASS", "name": "Pass Co", "exchange": "NASDAQ"},
        {"symbol": "MISS", "name": "Miss Co", "exchange": "NASDAQ"},
    ]
    monkeypatch.setattr(ss, "load_universe", lambda force=False: uni)
    frames = {"PASS": _frame(), "MISS": pd.DataFrame(tight_base_then_breakout(history=8, base_weeks=4, after=1))}
    seen: list[dict] = []

    def fake_download(symbols, **kw):
        on_progress = kw.get("on_progress")
        existing = dict(kw.get("existing") or {})
        pending = [s for s in symbols if s not in existing]
        out = dict(existing)
        for i, s in enumerate(pending, 1):
            if s in frames:
                out[s] = frames[s]
            if on_progress:
                on_progress(phase="weekly", processed=len(existing) + i, total=len(symbols), message=f"周线 {len(existing)+i}/{len(symbols)}")
        return out

    monkeypatch.setattr(ss, "download_weekly_panel", fake_download)
    monkeypatch.setattr(ss, "_yahoo_fundamentals", lambda sym: {"market_cap": 80_000_000, "quarterly_eps": 0.3, "ttm_eps": 1.0})
    monkeypatch.setattr(ss, "fetch_form4_transactions", lambda sym: ("无数据", []))
    monkeypatch.setattr(ss.bg_tasks if hasattr(ss, "bg_tasks") else __import__("bg_tasks"), "in_cooldown", lambda p: False)
    monkeypatch.setattr("bg_tasks.in_cooldown", lambda p: False)

    real_set = ss.set_progress

    def wrap(**kw):
        rec = real_set(**kw)
        seen.append(dict(rec))
        return rec

    monkeypatch.setattr(ss, "set_progress", wrap)

    payload = ss.refresh(force=True)
    prog = ss.load_progress()
    assert prog["state"] == "done"
    assert prog["percent"] == 100
    phases = [r.get("phase") for r in seen if r.get("phase")]
    assert "universe" in phases
    assert "weekly" in phases
    assert "scoring" in phases
    assert any(r["state"] == "running" and r["percent"] > 0 for r in seen)
    # Honest empty-or-real names only from the mocked universe
    for row in (payload.get("candidates") or []) + (payload.get("near") or []):
        assert row["symbol"] in {"PASS", "MISS"}
    assert payload["progress"]["state"] == "done"


def test_refresh_error_does_not_invent_names(progress_env, monkeypatch):
    monkeypatch.setattr(ss, "load_universe", lambda force=False: [{"symbol": "AAA", "name": "A", "exchange": "NASDAQ"}])
    monkeypatch.setattr("bg_tasks.in_cooldown", lambda p: False)

    def boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(ss, "download_weekly_panel", boom)
    with pytest.raises(RuntimeError, match="yahoo down"):
        ss.refresh(force=True)
    prog = ss.load_progress()
    assert prog["state"] == "error"
    assert "yahoo down" in (prog["error"] or "")
    assert "未编造" in (prog["message"] or "")
    assert not ss.SEED_PATH.exists() or ss.snapshot_view(ss.load_local_seed() or {}).get("candidates") == []


def test_second_refresh_latches_existing_job(progress_env, monkeypatch):
    ss.begin_job()
    ss.set_progress(phase="weekly", processed=10, total=100, message="周线 10/100", state="running")
    called = {"n": 0}

    def fake_start(*a, **k):
        called["n"] += 1
        return "started"

    import os
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setattr(app_mod.bg_tasks, "try_start", fake_start)
    client = app_mod.app.test_client()
    r = client.get("/api/us-superstocks/refresh")
    assert r.status_code == 200
    d = r.get_json()
    assert called["n"] == 0
    assert d["refresh"]["status"] == "running"
    assert d["progress"]["state"] == "running"
    assert d["progress"]["processed"] == 10


def test_progress_api_and_snapshot_include_progress(progress_env, monkeypatch):
    import os
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setattr(ss, "load_local_seed", lambda: ss.empty_seed_payload(note="fixture empty"))
    monkeypatch.setattr(ss, "maybe_refresh_remote_seed", lambda *a, **k: None)
    ss._cache = None
    client = app_mod.app.test_client()
    r = client.get("/api/us-superstocks")
    assert r.status_code == 200
    d = r.get_json()
    assert d["candidates"] == []
    assert d["progress"]["state"] == "idle"
    assert d["weekly_scan"]["cron_utc"] == "0 0 * * 6"
    assert "周六 08:00" in d["weekly_scan"]["note"]
    p = client.get("/api/us-superstocks/progress")
    assert p.status_code == 200
    assert p.get_json()["state"] == "idle"


def test_download_resumes_existing_symbols(progress_env, monkeypatch):
    fetched = []

    def fake_yf(batch, **kw):
        fetched.extend(list(batch))
        idx = pd.date_range("2025-01-03", periods=3, freq="W-FRI")
        cols = pd.MultiIndex.from_product([list(batch), ["Open", "High", "Low", "Close", "Volume"]])
        data = {c: 5.0 if c[1] != "Volume" else 1000.0 for c in cols}
        return pd.DataFrame(data, index=idx)

    monkeypatch.setattr("net_limits.yf_download", fake_yf)
    monkeypatch.setattr("bg_tasks.in_cooldown", lambda p: False)
    monkeypatch.setenv("SS_WEEKLY_SLEEP", "0")
    existing = {"AAA": pd.DataFrame({"date": ["2025-01-03"], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]})}
    out = ss.download_weekly_panel(["AAA", "BBB"], existing=existing, pause=0, checkpoint=True, retry_on_cooldown=False)
    assert "AAA" in out and fetched == ["BBB"]
    assert ss.WEEKLY_PKL.exists()


def test_remote_seed_newer_is_adopted(progress_env, monkeypatch):
    local = ss.empty_seed_payload(note="old local")
    local["updated_at"] = "2026-11-01T08:00:00+08:00"
    ss.write_seed(local, ss.SEED_PATH)
    remote = ss.empty_seed_payload(note="weekly remote")
    remote["updated_at"] = "2026-11-12T08:00:00+08:00"
    remote["candidates"] = []
    monkeypatch.setenv("SS_SEED_REMOTE", "1")
    monkeypatch.setattr(ss, "fetch_remote_seed", lambda timeout=5.0: remote)
    ss.maybe_refresh_remote_seed(local, blocking=True)
    adopted = ss.load_local_seed()
    assert adopted["note"] == "weekly remote"
    assert adopted["updated_at"].startswith("2026-11-12")
    assert adopted["candidates"] == []


def test_remote_seed_disabled_during_pytest(progress_env, monkeypatch):
    monkeypatch.delenv("SS_SEED_REMOTE", raising=False)
    assert ss._remote_seed_allowed() is False
    assert ss.fetch_remote_seed() is None


def test_cli_weekly_scan_calls_refresh(monkeypatch):
    called = {}

    def fake_refresh(force=True, resume=None):
        called["force"] = force
        called["resume"] = resume
        return {"counts": {"universe": 2, "scanned": 0, "candidates": 0, "near": 0}, "updated_at": "x", "note": "dry", "candidates": []}

    monkeypatch.setattr(ss, "refresh", fake_refresh)
    rc = ss.main(["--weekly-scan", "--resume"])
    assert rc == 0
    assert called["force"] is True
    assert called["resume"] is True


def test_weekly_workflow_schedule_and_permissions():
    text = (ROOT / ".github/workflows/refresh_superstocks.yml").read_text(encoding="utf-8")
    assert "cron: \"0 0 * * 6\"" in text or "cron: '0 0 * * 6'" in text
    assert "Asia/Shanghai" in text
    assert "08:00" in text
    assert "workflow_dispatch:" in text
    assert "contents: write" in text
    assert "cancel-in-progress: false" in text
    assert "us_superstocks.py --weekly-scan" in text
    assert "data/us_superstocks.json" in text
    assert "docs/api/us_superstocks.json" in text
    # Must not be folded into the frequent pages export (that cancels in-progress jobs).
    pages = (ROOT / ".github/workflows/refresh_pages.yml").read_text(encoding="utf-8")
    assert "us_superstocks.py --weekly-scan" not in pages


def test_begin_job_is_idempotent_while_running(progress_env):
    first = ss.begin_job()
    ss.set_progress(phase="weekly", processed=5, total=10, message="周线 5/10", state="running")
    again = ss.begin_job()
    assert ss.is_job_running() is True
    assert again["state"] == "running"
    assert again["processed"] == 5
    assert again["phase"] == "weekly"
    assert first["started_at"] == again["started_at"]
