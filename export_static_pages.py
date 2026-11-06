#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild docs/ static snapshot for GitHub Pages."""
from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
API = DOCS / "api"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def slim_row(r: dict) -> dict:
    c = r.get("cost") or {}
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "symbol": r.get("symbol"),
        "market": r.get("market"),
        "sector": r.get("sector"),
        "exchange": r.get("exchange", ""),
        "price": r.get("price"),
        "tag": r.get("tag"),
        "deflation_tag": r.get("deflation_tag"),
        "deflation_note": r.get("deflation_note"),
        "pct_all": r.get("pct_all"),
        "pct_5y": r.get("pct_5y"),
        "pct_10y": r.get("pct_10y"),
        "low_score": r.get("low_score"),
        "high_score": r.get("high_score"),
        "from_max_pct": r.get("from_max_pct"),
        "from_min_pct": r.get("from_min_pct"),
        "ret_1y": r.get("ret_1y"),
        "primary_cost": c.get("primary_cost"),
        "cash_cost": c.get("cash_cost"),
        "comfort_high": c.get("comfort_high"),
        "cost_unit": c.get("unit"),
        "vs_primary_pct": c.get("vs_primary_pct"),
        "change_pct": r.get("change_pct"),
        "volume": r.get("volume"),
    }


def export_html() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    if "<base href=" not in html:
        html = html.replace(
            "<head>\n",
            '<head>\n  <base href="/futures-dashboard/" />\n  <script>window.STATIC_MODE = true;</script>\n',
            1,
        )
    html = html.replace('href="/static/style.css?v={{ asset_v }}"', 'href="./static/style.css?v=static"')
    html = html.replace('src="/static/app.js?v={{ asset_v }}"', 'src="./static/app.js?v=static"')
    html = html.replace("refreshSec: {{ refresh_sec }}", "refreshSec: 0")
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    dst = DOCS / "static"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "static", dst)
    print("copied templates + static -> docs/")


def main() -> None:
    export_html()
    API.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    from universe import get_universe

    items, _ = get_universe()
    dump(
        API / "health.json",
        {
            "ok": True,
            "time": now,
            "universe": {
                "total": len(items),
                "cn": sum(1 for x in items if x.get("market") == "cn"),
                "na": sum(1 for x in items if x.get("market") != "cn"),
            },
            "static": True,
        },
    )
    dump(
        API / "instruments.json",
        {
            "count": len(items),
            "items": [
                {
                    "id": x["id"],
                    "name": x.get("name"),
                    "symbol": x.get("symbol"),
                    "market": x.get("market"),
                    "sector": x.get("sector"),
                    "exchange": x.get("exchange", ""),
                }
                for x in items
            ],
        },
    )

    print("fetching quotes...")
    try:
        from data_sources import get_quotes

        q = get_quotes(force=True)
        dump(
            API / "quotes.json",
            {
                "items": q,
                "count": len(q),
                "with_price": sum(1 for x in q if x.get("price") is not None),
                "updated_at": now,
                "note": "静态快照（导出时拉取）",
            },
        )
    except Exception as exc:
        print("quotes failed:", exc)
        traceback.print_exc()
        dump(API / "quotes.json", {"items": [], "count": 0, "with_price": 0, "updated_at": now, "error": str(exc)})

    print("building analysis...")
    try:
        from analysis import build_full_analysis

        data = build_full_analysis(force=True)
        payload = {
            "updated_at": data.get("updated_at", now),
            "lows": [slim_row(r) for r in data.get("lows", [])],
            "highs": [slim_row(r) for r in data.get("highs", [])],
            "all": [slim_row(r) for r in data.get("all", [])],
            "criteria": data.get("criteria"),
            "note": data.get("note"),
            "static": True,
        }
        dump(API / "analysis_all.json", payload)
    except Exception as exc:
        print("analysis failed:", exc)
        traceback.print_exc()
        dump(API / "analysis_all.json", {"lows": [], "highs": [], "all": [], "updated_at": now, "error": str(exc)})

    supply_path = ROOT / "data" / "supply" / "latest.json"
    if supply_path.exists():
        dump(API / "supply.json", json.loads(supply_path.read_text(encoding="utf-8")))
    else:
        dump(API / "supply.json", {"items": [], "note": "no supply snapshot", "updated_at": now})

    print("building NG dashboard...")
    try:
        from ng_dashboard import build_dashboard

        dump(API / "ng_dashboard.json", build_dashboard(force=True))
    except Exception as exc:
        print("ng failed:", exc)
        traceback.print_exc()
        last = ROOT / "data" / "ng" / "last_dashboard.json"
        if last.exists():
            dump(API / "ng_dashboard.json", json.loads(last.read_text(encoding="utf-8")))
        else:
            dump(API / "ng_dashboard.json", {"error": str(exc), "updated_at": now})

    print("futures movers...")
    try:
        from analysis import compute_futures_movers

        periods = {
            p: compute_futures_movers(period=p, market="all", limit=20, force=True)
            for p in ("W", "M", "Y")
        }
        flat = dict(periods["W"])
        flat["periods"] = periods
        flat["default_period"] = "W"
        dump(API / "futures_movers.json", flat)
    except Exception as exc:
        print("futures movers failed:", exc)
        dump(
            API / "futures_movers.json",
            {"gainers": [], "losers": [], "note": f"静态导出未取到: {exc}", "snapshot_at": now},
        )

    print("us movers...")
    try:
        from us_movers import compute_movers

        periods = {
            p: compute_movers(period=p, kind="all", limit=20, force=False)
            for p in ("W", "M", "Y")
        }
        flat = dict(periods["W"])
        flat["periods"] = periods
        flat["default_period"] = "W"
        dump(API / "us_movers.json", flat)
    except Exception as exc:
        print("us movers failed:", exc)
        dump(
            API / "us_movers.json",
            {"gainers": [], "losers": [], "note": f"静态导出未取到: {exc}", "snapshot_at": now},
        )


    print("short put high-vol screen...")
    try:
        from short_put_screen import build_short_put_screen

        dump(API / "short_put.json", build_short_put_screen(force=True, top_n=20, option_n=28))
    except Exception as exc:
        print("short put failed:", exc)
        traceback.print_exc()
        dump(
            API / "short_put.json",
            {"picks": [], "all": [], "watchlist": [], "updated_at": now, "error": str(exc)},
        )


    print("short call high-vol screen...")
    try:
        from short_call_screen import build_short_call_screen

        dump(API / "short_call.json", build_short_call_screen(force=True, top_n=20, option_n=28))
    except Exception as exc:
        print("short call failed:", exc)
        traceback.print_exc()
        dump(
            API / "short_call.json",
            {"picks": [], "all": [], "watchlist": [], "updated_at": now, "error": str(exc)},
        )

    print("cn indices analysis...")
    try:
        from cn_index_analysis import build_cn_index_analysis

        dump(API / "cn_indices.json", build_cn_index_analysis(force=True))
    except Exception as exc:
        print("cn indices failed:", exc)
        traceback.print_exc()
        dump(
            API / "cn_indices.json",
            {"indices": [], "satellites": [], "updated_at": now, "error": str(exc)},
        )

    print("cffex index options (IO/HO/MO)...")
    try:
        from cffex_options import build_cn_index_options

        dump(API / "cn_index_options.json", build_cn_index_options())
    except Exception as exc:
        print("cn index options failed:", exc)
        traceback.print_exc()
        dump(
            API / "cn_index_options.json",
            {"products": [], "by_index": {}, "updated_at": now, "error": str(exc)},
        )

    dump(API / "history_empty.json", {"bars": [], "note": "静态站不提供K线"})
    dump(API / "analysis_one_empty.json", {"analysis": None, "note": "静态站请看高低位总表"})
    print("DONE", now)


if __name__ == "__main__":
    main()
