# -*- coding: utf-8 -*-
"""Regression: 周报 / 卖Put / 卖Call / AI应用 / 美股Superstocks tabs were removed on request (2026-11-13).

The remaining tabs (盯盘, 高低位, 涨跌榜, A股指数, 美气NG, 黄金) must still load.
No network: Flask test client on seed snapshots only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("SKIP_BG_WARMUP", "1")

ROOT = Path(__file__).resolve().parents[1]
ASSET_V = "20261114etf1"
REMAINING = ["watch", "extremes", "movers", "cnidx", "ng", "gold", "usetf"]
REMOVED = ["supply", "put", "call", "ai"]
REMOVED_LABELS = [">周报<", ">卖Put<", ">卖Call<", ">AI应用<"]
REMOVED_SCRIPTS = ["ai_apps_view.js", "cn_option_candidates_view.js", "fund_score_view.js"]
REMOVED_API = ["/api/supply", "/api/short-put/", "/api/short-call/", "/api/ai-apps"]


def _surfaces():
    return [
        (ROOT / "templates" / "index.html").read_text(encoding="utf-8"),
        (ROOT / "docs" / "index.html").read_text(encoding="utf-8"),
    ]


def test_tab_bar_has_only_remaining_tabs_and_default_is_valid():
    for html in _surfaces():
        nav = html.split('id="view-tabs"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'data-view="([^"]+)"', nav) == REMAINING
        for label in REMOVED_LABELS:
            assert label not in nav
        # default landing tab is 盯盘 and its view is the one marked active
        assert '<button type="button" data-view="watch" class="active">盯盘</button>' in nav
        assert 'class="view view-watch active" id="view-watch"' in html
        for v in REMAINING:
            assert f'id="view-{v}"' in html, v
        for v in REMOVED:
            assert f'id="view-{v}"' not in html, v
        for gone in ("put-chain-modal", "call-chain-modal", "supply-body", "ai-content", "ng-put-links", "btn-ext-put"):
            assert gone not in html, gone
        for script in REMOVED_SCRIPTS:
            assert script not in html


def test_removed_files_gone_and_static_copies_match():
    for script in REMOVED_SCRIPTS:
        assert not (ROOT / "static" / script).exists()
        assert not (ROOT / "docs" / "static" / script).exists()
    for name in ("data/ai_apps.json", "docs/api/ai_apps.json", "docs/api/supply.json"):
        assert not (ROOT / name).exists(), name
    for p in sorted((ROOT / "static").iterdir()):
        assert p.read_bytes() == (ROOT / "docs" / "static" / p.name).read_bytes(), p.name
    assert sorted(x.name for x in (ROOT / "static").iterdir()) == sorted(x.name for x in (ROOT / "docs" / "static").iterdir())


def test_frontend_no_longer_wires_removed_tabs():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for token in ("AIAppsView", "loadShortPut", "loadShortCall", "loadSupply", "renderSupply",
                  "openPutTheme", "openCallTheme", "renderNgPutLinks", "cnOptionCandidatesView", "fundScoreView"):
        assert token not in js, token
    for api in REMOVED_API:
        assert api not in js, api
    for view in ("gold", "movers", "ng", "cnidx", "watch"):
        assert f'name === "{view}"' in js
    for removed in REMOVED:
        assert f'name === "{removed}"' not in js


def test_export_and_workflows_do_not_rebuild_removed_tabs():
    src = (ROOT / "export_static_pages.py").read_text(encoding="utf-8")
    for token in ("ai_apps.json", "supply.json", "short_put.json", "short_call.json",
                  "build_short_put_screen", "build_short_call_screen"):
        assert token not in src, token
    assert f'"{ASSET_V}"' in src
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text(encoding="utf-8")
        assert "short-put" not in text and "ai_apps" not in text and "supply.json" not in text


def test_flask_index_and_remaining_endpoints_load(monkeypatch):
    monkeypatch.setenv("RENDER", "true")  # seed-only read paths, never fetch in the request
    import app as app_mod

    c = app_mod.app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"app.js?v={ASSET_V}" in html
    assert re.findall(r'data-view="([^"]+)"', html.split('id="view-tabs"', 1)[1].split("</nav>", 1)[0]) == REMAINING
    rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
    for api in ("/api/supply", "/api/supply/refresh", "/api/short-put/high-vol", "/api/short-put/refresh",
                "/api/short-call/high-vol", "/api/short-call/refresh", "/api/ai-apps"):
        assert api not in rules, api
    for api in ("/api/quotes", "/api/analysis/all", "/api/futures-movers", "/api/us-movers",
                "/api/gold", "/api/gold/refresh", "/api/cn-indices",
                "/api/ng/dashboard", "/api/ng-sell-put", "/api/ng-boil-kold"):
        assert api in rules, api
    for gone in ("/api/us-superstocks", "/api/us-superstocks/progress", "/api/us-superstocks/refresh"):
        assert gone not in rules, gone
        assert c.get(gone).status_code == 404
    assert c.get("/api/ng-sell-put").status_code == 200


def test_superstocks_tab_fully_removed():
    for html in _surfaces():
        assert 'data-view="superstocks"' not in html
        assert "美股Superstocks" not in html and "view-superstocks" not in html and "tab-ss" not in html
        assert "us_superstocks_view.js" not in html
    for name in ("us_superstocks.py", "static/us_superstocks_view.js", "docs/static/us_superstocks_view.js",
                 "data/us_superstocks.json", "docs/api/us_superstocks.json",
                 ".github/workflows/refresh_superstocks.yml", "docs/us_superstocks_method.md"):
        assert not (ROOT / name).exists(), name
    for src in ("static/app.js", "export_static_pages.py", "app.py", ".github/workflows/refresh_pages.yml"):
        text = (ROOT / src).read_text(encoding="utf-8")
        assert "superstocks" not in text.lower(), src
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".view-superstocks" not in css and ".tab-ss" not in css
