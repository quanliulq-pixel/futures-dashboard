import json
import os
import threading
import time

os.environ.setdefault("SKIP_BG_WARMUP", "1")

import pandas as pd
import pytest

import ng_dashboard as ng

SPEC = ng.EIA_MONTHLY_SERIES["dry_production"]


def _frame(rows, series=None, title=None):
    head = [
        ["Back to Contents", "Data 1: x"],
        ["Sourcekey", series or SPEC["series"]],
        ["Date", title or SPEC["title"]],
    ]
    return pd.DataFrame(head + [[pd.Timestamp(d), v] for d, v in rows])


@pytest.fixture
def fake_xls(monkeypatch):
    holder = {}
    monkeypatch.setattr(ng.pd, "read_excel", lambda *a, **k: holder["df"])
    return holder


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ng, "MONTHLY_LAST", tmp_path / "last.json")
    monkeypatch.setattr(ng, "MONTHLY_SEED", tmp_path / "seed.json")
    monkeypatch.setattr(ng, "DATA_DIR", tmp_path)
    ng._monthly_state.clear()
    yield
    ng._monthly_state.clear()


@pytest.mark.parametrize("d,days", [("2023-02-15", 28), ("2024-02-15", 29), ("2026-04-15", 30), ("2026-08-15", 31)])
def test_month_day_conversion(fake_xls, d, days):
    fake_xls["df"] = _frame([(d, 3_100_000)])
    p = ng.parse_eia_monthly_xls(b"", SPEC)[0]
    assert p["days"] == days
    assert p["bcfd"] == round(3_100_000 / days / 1000, 3)
    assert p["month"] == d[:7]


def test_na_w_blank_skipped_not_zero(fake_xls):
    fake_xls["df"] = _frame([("2026-01-15", 100), ("2026-02-15", "NA"), ("2026-03-15", "W"),
                             ("2026-04-15", None), ("2026-05-15", 0), ("2026-06-15", 300)])
    months = {p["month"]: p["mmcf"] for p in ng.parse_eia_monthly_xls(b"", SPEC)}
    assert months == {"2026-01": 100, "2026-05": 0, "2026-06": 300}


@pytest.mark.parametrize("kw", [{"series": "N9070US1"}, {"title": "U.S. Dry Natural Gas Production (Bcf)"},
                                {"title": "U.S. Natural Gas Marketed Production (MMcf)"}])
def test_wrong_series_or_unit_rejected(fake_xls, kw):
    fake_xls["df"] = _frame([("2026-01-15", 1)], **kw)
    with pytest.raises(ValueError):
        ng.parse_eia_monthly_xls(b"", SPEC)


def test_window_keeps_latest(fake_xls):
    rows = [(f"{2020 + i // 12}-{i % 12 + 1:02d}-15", i) for i in range(60)]
    fake_xls["df"] = _frame(rows)
    pts = ng.parse_eia_monthly_xls(b"", SPEC, window=36)
    assert len(pts) == 36 and pts[-1]["month"] == "2024-12"


class _Resp:
    content = b"x"


def _ok_dl(*a, **k):
    return b"x"


def test_no_cache_failure_is_unavailable_not_zero(monkeypatch):
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("read timeout")))
    b = ng.fetch_monthly_series("dry_production")
    assert b["status"] == "unavailable" and b["points"] == [] and b["latest"] is None
    assert "timeout" in b["error"]


def test_failure_keeps_last_success(monkeypatch, fake_xls):
    fake_xls["df"] = _frame([("2026-08-15", 3_590_584)])
    monkeypatch.setattr(ng, "_eia_download", _ok_dl)
    ok = ng.fetch_monthly_series("dry_production")
    assert ok["status"] == "ok" and ok["asof"] == "2026-08"
    fetched = ok["fetched_at"]
    assert fetched.endswith("+00:00")
    assert json.loads(ng.MONTHLY_LAST.read_text())["dry_production"]["fetched_at"] == fetched
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    st = ng.fetch_monthly_series("dry_production", force=True)
    assert st["status"] == "stale" and st["asof"] == "2026-08" and st["fetched_at"] == fetched
    assert st["points"][-1]["mmcf"] == 3_590_584


def test_24h_cache_no_refetch(monkeypatch, fake_xls):
    fake_xls["df"] = _frame([("2026-08-15", 1)])
    calls = []
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: calls.append(a) or b"x")
    ng.fetch_monthly_series("dry_production")
    ng.fetch_monthly_series("dry_production")
    assert len(calls) == 1


def test_concurrent_requests_single_download(monkeypatch, fake_xls):
    fake_xls["df"] = _frame([("2026-08-15", 1)])
    gate = threading.Event()
    calls = []

    def slow(*a, **k):
        calls.append(1)
        gate.wait(3)
        return b"x"

    monkeypatch.setattr(ng, "_eia_download", slow)
    t = threading.Thread(target=ng.fetch_monthly_series, args=("dry_production",))
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    b = ng.fetch_monthly_series("dry_production")  # in flight -> returns immediately
    assert time.monotonic() - t0 < 0.5
    assert b["status"] == "unavailable"
    gate.set()
    t.join(3)
    assert len(calls) == 1


def test_seed_used_without_network(monkeypatch):
    ng.MONTHLY_SEED.write_text(json.dumps({"lng_exports": {"series": "N9133US2", "fetched_at": "2026-11-08T00:32:56+00:00",
                                                          "points": [{"month": "2026-08", "days": 31, "mmcf": 514617, "bcfd": 16.601}]}}))
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: pytest.fail("network used"))
    snap = ng.monthly_supply_snapshot()["series"]
    assert snap["lng_exports"]["status"] == "seed" and snap["lng_exports"]["asof"] == "2026-08"
    assert snap["lng_exports"]["release_date"] is None
    assert snap["dry_production"]["status"] == "unavailable"


def test_committed_seed_is_valid_official_shape():
    from pathlib import Path

    seed = json.loads((Path(ng.__file__).parent / "data" / "ng" / "monthly_supply_seed.json").read_text())
    for key, spec in ng.EIA_MONTHLY_SERIES.items():
        s = seed[key]
        assert s["series"] == spec["series"]
        assert 24 <= len(s["points"]) <= 36
        for p in s["points"]:
            assert p["bcfd"] == round(p["mmcf"] / p["days"] / 1000, 3)


def test_ng_dashboard_api_seed_includes_monthly_without_network(monkeypatch):
    import app as app_mod

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: pytest.fail("network used"))
    r = app_mod.app.test_client().get("/api/ng/dashboard")
    assert r.status_code == 200
    d = r.get_json()
    assert set(d["monthly_supply"]["series"]) == {"dry_production", "lng_exports"}
    assert "factors" in d  # existing schema intact


def test_frontend_ids_and_compat():
    from pathlib import Path

    root = Path(ng.__file__).parent
    html = (root / "templates" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    for el in ("chart-ng-dryprod", "ng-dryprod-hint", "chart-ng-lngexp", "ng-lngexp-hint"):
        assert f'id="{el}"' in html and el in js
    assert "跨大西洋价格套利代理" in html
    # missing monthly_supply (old snapshots) must be tolerated
    assert "(data.monthly_supply && data.monthly_supply.series) || {}" in js
    assert "[f.why, f.detail].filter(Boolean)" in js
    assert "近5年常态均值" in js and "10年常态均值" not in js


def test_lng_factor_score_unchanged_by_rename():
    l = {"spread": 5.0, "spread_chg_20d": 1.0, "ttf_usd": 9.0, "ttf_eur_mwh": 30.0, "hh": 4.0, "ttf_chg_20d_pct": 2.0}
    f = ng.factor_lng(l)
    assert f["score"] == pytest.approx(ng._clip(0.3 + ng._clip(0.25) * 0.3))
    assert f["name"] == "跨大西洋价格套利代理（TTF−HH）"


def test_global_eia_limit_one_across_series(monkeypatch, fake_xls):
    fake_xls["df"] = _frame([("2026-08-15", 1)])
    gate = threading.Event()
    calls = []

    def slow(url):
        calls.append(url)
        gate.wait(3)
        return b"x"

    monkeypatch.setattr(ng, "_eia_download", slow)
    t = threading.Thread(target=ng.fetch_monthly_series, args=("dry_production",))
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    b = ng.fetch_monthly_series("lng_exports")  # other series, global slot busy
    assert time.monotonic() - t0 < 0.5 and b["status"] == "unavailable"
    gate.set()
    t.join(3)
    assert len(calls) == 1
    assert ng._eia_download_slot.acquire(blocking=False)
    ng._eia_download_slot.release()


def test_concurrent_persist_no_lost_update():
    st = {"points": [{"month": "2026-08", "days": 31, "mmcf": 1, "bcfd": 0.0}], "fetched_at": "2026-11-08T00:00:00+00:00", "series": "S"}
    ths = [threading.Thread(target=ng._persist_monthly_last, args=(k, st)) for k in ("dry_production", "lng_exports") * 10]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert set(json.loads(ng.MONTHLY_LAST.read_text())) == {"dry_production", "lng_exports"}


def _write_last(key, fetched_at):
    ng.MONTHLY_LAST.write_text(json.dumps({key: {"series": "N9070US2", "fetched_at": fetched_at,
        "points": [{"month": "2026-08", "days": 31, "mmcf": 3590584, "bcfd": 115.825}]}}))


def test_restart_last_success_within_ttl_not_refetched(monkeypatch):
    from datetime import datetime, timedelta, timezone

    _write_last("dry_production", (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"))
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: pytest.fail("refetched within TTL"))
    b = ng.fetch_monthly_series("dry_production")
    assert b["status"] == "ok" and b["data_source"] == "last_success" and b["seed"] is False
    assert 7000 <= b["cache_age_sec"] <= 7300 and b["cache_expired"] is False


def test_expired_last_success_snapshot_marks_age_not_failure():
    from datetime import datetime, timedelta, timezone

    _write_last("dry_production", (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(timespec="seconds"))
    b = ng.monthly_supply_snapshot()["series"]["dry_production"]
    assert b["status"] == "ok" and b["cache_expired"] is True and b["error"] is None


def test_zoneless_fetched_at_not_trusted_as_fresh(monkeypatch, fake_xls):
    _write_last("dry_production", "2026-11-08T00:00:00")
    fake_xls["df"] = _frame([("2026-09-15", 2)])
    monkeypatch.setattr(ng, "_eia_download", _ok_dl)
    b = ng.fetch_monthly_series("dry_production")
    assert b["data_source"] == "live" and b["asof"] == "2026-09"


def test_seed_and_stale_coexist(monkeypatch):
    ng.MONTHLY_SEED.write_text(json.dumps({"dry_production": {"series": "N9070US2", "fetched_at": "2026-11-08T00:32:56+00:00",
        "points": [{"month": "2026-08", "days": 31, "mmcf": 1, "bcfd": 0.0}]}}))
    monkeypatch.setattr(ng, "_eia_download", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x")))
    b = ng.fetch_monthly_series("dry_production")
    assert b["status"] == "stale" and b["seed"] is True and b["asof"] == "2026-08"


class _FakeStream:
    def __init__(self, chunks, headers=None, delay=0.0):
        self.chunks, self.headers, self.delay = chunks, headers or {}, delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for c in self.chunks:
            time.sleep(self.delay)
            yield c


def test_download_size_cap_and_timeouts(monkeypatch):
    import requests

    seen = {}

    def fake_get(url, **k):
        seen.update(k)
        return _FakeStream([b"a" * (1024 * 1024)] * 3)

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(ValueError):
        ng._eia_download("u")
    assert seen["stream"] is True and seen["timeout"] == (ng.EIA_CONNECT_TIMEOUT, ng.EIA_READ_TIMEOUT)
    monkeypatch.setattr(requests, "get", lambda url, **k: _FakeStream([b"x"], {"Content-Length": str(5 * 1024 * 1024)}))
    with pytest.raises(ValueError):
        ng._eia_download("u")


def test_download_overall_deadline(monkeypatch):
    import requests

    monkeypatch.setattr(ng, "EIA_TOTAL_DEADLINE", 0.05)
    monkeypatch.setattr(requests, "get", lambda url, **k: _FakeStream([b"x"] * 100, delay=0.01))
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        ng._eia_download("u")
    assert time.monotonic() - t0 < 0.5


def test_overlay_prefers_newer_fetched_at():
    ng.MONTHLY_SEED.write_text(json.dumps({"dry_production": {"series": "N9070US2", "fetched_at": "2026-11-08T00:32:56+00:00",
        "points": [{"month": "2026-08", "days": 31, "mmcf": 1, "bcfd": 0.0}]}}))
    newer_old = {"series": {"dry_production": {"fetched_at": "2026-12-01T00:00:00+00:00", "asof": "2026-09",
        "points": [{"month": "2026-09", "days": 30, "mmcf": 2, "bcfd": 0.0}]}}}
    older_old = {"series": {"dry_production": {"fetched_at": "2026-10-01T00:00:00+00:00", "asof": "2026-07",
        "points": [{"month": "2026-07", "days": 31, "mmcf": 3, "bcfd": 0.0}]}}}
    assert ng.overlay_monthly_supply(newer_old)["series"]["dry_production"]["asof"] == "2026-09"
    assert ng.overlay_monthly_supply(older_old)["series"]["dry_production"]["asof"] == "2026-08"
    assert ng.overlay_monthly_supply(None)["series"]["dry_production"]["asof"] == "2026-08"
