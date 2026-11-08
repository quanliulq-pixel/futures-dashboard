"""Endpoint behaviour under hung upstream compute, without network."""
import os
import threading
import time

os.environ["SKIP_BG_WARMUP"] = "1"

import pytest

import bg_tasks


@pytest.fixture
def client(monkeypatch):
    import app as app_mod

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setenv("RENDER", "true")
    hang = threading.Event()
    calls = {"movers": 0, "put": 0}

    def slow_movers(**kw):
        calls["movers"] += 1
        hang.wait(5)  # simulates a hung upstream call

    def slow_put(**kw):
        calls["put"] += 1
        hang.wait(5)

    monkeypatch.setattr(app_mod, "compute_futures_movers", slow_movers)
    import short_put_screen
    monkeypatch.setattr(short_put_screen, "build_short_put_screen", slow_put)
    yield app_mod.app.test_client(), calls, hang
    hang.set()
    time.sleep(0.05)
    bg_tasks._reset_for_tests(max_tasks=2)


def test_movers_burst_serves_seed_fast_and_starts_one_job(client):
    c, calls, hang = client
    t0 = time.monotonic()
    for _ in range(30):
        r = c.get("/api/futures-movers?period=W&market=all&limit=20")
        assert r.status_code == 200
        d = r.get_json()
        assert d.get("source") == "seed"
        assert len(d["gainers"]) > 0
    assert time.monotonic() - t0 < 5
    time.sleep(0.05)
    assert calls["movers"] == 1
    assert len(bg_tasks.snapshot()["running"]) <= bg_tasks.MAX_BG_TASKS


def test_health_responsive_while_slots_hung(client):
    c, calls, hang = client
    c.get("/api/futures-movers?period=W&market=all&limit=20")
    c.get("/api/short-put/high-vol")
    for p in ("M", "Y"):
        c.get(f"/api/futures-movers?period={p}&market=all&limit=20")
    t0 = time.monotonic()
    r = c.get("/api/health")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert time.monotonic() - t0 < 1.0
    assert len(r.get_json()["background"]["running"]) == 2
    assert calls["put"] + calls["movers"] == 2


def test_quotes_seed_returns_quickly(client, monkeypatch):
    import data_sources

    monkeypatch.setattr(data_sources, "_fetch_quotes_live", lambda *a, **k: (time.sleep(5), [])[1])
    c, *_ = client
    t0 = time.monotonic()
    r = c.get("/api/quotes")
    assert r.status_code == 200
    assert r.get_json()["count"] > 0
    assert time.monotonic() - t0 < 3


def test_manual_refresh_burst_returns_promptly(client, monkeypatch):
    import short_put_screen

    c, calls, hang = client
    monkeypatch.setattr(short_put_screen, "_cache", None)
    t0 = time.monotonic()
    statuses = []
    for _ in range(20):
        r = c.get("/api/short-put/refresh")
        assert r.status_code == 200
        d = r.get_json()
        assert d.get("source") == "seed"
        statuses.append(d["refresh"]["status"])
    assert time.monotonic() - t0 < 3
    assert statuses[0] == "started" and set(statuses[1:]) == {"running"}
    assert calls["put"] == 1


def test_manual_refresh_respects_cooldown(client):
    c, calls, hang = client
    bg_tasks.note_rate_limited("yahoo", 60)
    d = c.get("/api/short-call/refresh").get_json()
    assert d["refresh"] == {"status": "cooldown", "queued": False}
    d = c.get("/api/futures-movers?period=W&market=all&limit=20&force=1").get_json()
    assert d["source"] == "seed" and calls["movers"] == 0


def test_supply_refresh_does_not_fetch_in_request(client, monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "maybe_weekly_fetch", lambda **k: time.sleep(5))
    c, *_ = client
    t0 = time.monotonic()
    r = c.get("/api/supply/refresh")
    assert time.monotonic() - t0 < 2
    assert r.status_code in (200, 503)
    assert r.get_json()["refresh"]["status"] == "started"
