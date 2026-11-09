"""/api/cn-indices with A股指数基本面: request thread never fetches; Render queues one bg job."""
import os
import threading
import time

os.environ["SKIP_BG_WARMUP"] = "1"

import bg_tasks


def test_cn_indices_render_serves_fast_and_queues_single_fund_job(monkeypatch):
    import app as app_mod
    import cn_index_fundamentals as F

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setenv("RENDER", "true")
    hang = threading.Event()
    calls = {"n": 0}

    def slow_refresh(force=False):
        calls["n"] += 1
        hang.wait(5)
        return None

    monkeypatch.setattr(F.DEFAULT_PROVIDER, "refresh", slow_refresh)
    monkeypatch.setattr(F.DEFAULT_PROVIDER, "_cache", None)
    c = app_mod.app.test_client()
    try:
        t0 = time.time()
        for _ in range(3):
            r = c.get("/api/cn-indices")
            assert r.status_code == 200
        assert time.time() - t0 < 3.0
        data = r.get_json()
        for row in data.get("indices") or []:
            adv = row.get("advice") or {}
            assert "tech_score" in adv and "combined_score" in adv
            if not data.get("index_fundamentals"):
                assert adv["fund_status"] == "not_computed" and adv["fund_score"] is None
        time.sleep(0.1)
        assert calls["n"] == 1  # single-flight + min interval
        r2 = c.get("/api/cn-index-advice")
        assert r2.status_code == 200
    finally:
        hang.set()
        time.sleep(0.05)
        bg_tasks._reset_for_tests(max_tasks=2)
