# -*- coding: utf-8 -*-
"""Local futures dashboard — full CN + North America universe."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from analysis import (
    build_full_analysis,
    compute_futures_movers,
    extremes_summary,
    get_instrument_analysis,
)
from config import QUOTE_REFRESH_SEC
from data_sources import fill_missing_quote_history, get_quotes, history_payload, list_instruments, warmup
from supply_fetch import load_latest, maybe_weekly_fetch, overlay_parity_vs_quotes
from universe import get_instrument, get_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Always reload templates so UI edits take effect without restart
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


@app.after_request
def add_header(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/")
def index():
    # cache-bust static assets so browser picks up JS/CSS changes
    asset_v = "20261106dyn7"
    return render_template("index.html", refresh_sec=QUOTE_REFRESH_SEC, asset_v=asset_v)


@app.route("/api/health")
def health():
    items, _ = get_universe()
    cn = sum(1 for x in items if x["market"] == "cn")
    us = sum(1 for x in items if x["market"] == "us")
    return jsonify(
        {
            "ok": True,
            "time": datetime.now().isoformat(timespec="seconds"),
            "universe": {"total": len(items), "cn": cn, "us": us},
        }
    )


@app.route("/api/instruments")
def instruments():
    market = request.args.get("market")
    items = list_instruments(market)
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/universe/reload")
def universe_reload():
    items, _ = get_universe(force=True)
    cn = sum(1 for x in items if x["market"] == "cn")
    us = sum(1 for x in items if x["market"] == "us")
    return jsonify({"ok": True, "total": len(items), "cn": cn, "us": us})



def _movers_seed_for(name: str, period: str) -> dict | None:
    """Pick W/M/Y snapshot from docs/api/{name}.json (supports periods map)."""
    seed = _read_api_seed(name)
    if not seed:
        return None
    period = (period or "W").upper()
    periods = seed.get("periods") if isinstance(seed.get("periods"), dict) else None
    if periods:
        chosen = periods.get(period) or periods.get("W") or periods.get(seed.get("default_period") or "W")
        if isinstance(chosen, dict) and (chosen.get("gainers") or chosen.get("losers")):
            out = dict(chosen)
            out["source"] = "seed"
            return out
    # Legacy flat seed
    out = dict(seed)
    out.pop("periods", None)
    out["source"] = "seed"
    return out

def _read_api_seed(name: str) -> dict | None:
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parent / "docs" / "api" / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("seed read failed: %s", name, exc_info=True)
        return None


@app.route("/api/quotes")
def quotes():
    market = request.args.get("market")
    force = request.args.get("force") == "1"
    try:
        data = get_quotes(market=market, force=force)
        note = "国内=新浪全部主力连续；北美=Yahoo 可获取的 CME/CBOT/NYMEX/COMEX/CFE/ICE 连续合约。免费源有延迟。"
        # Only backfill history when explicitly refreshed — avoids Yahoo stampede.
        if force:
            threading.Thread(target=fill_missing_quote_history, kwargs={"limit": 30}, daemon=True).start()
        return jsonify(
            {
                "items": data,
                "count": len(data),
                "with_price": sum(1 for x in data if x.get("price") is not None),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "note": note,
            }
        )
    except Exception as exc:
        logger.exception("quotes failed; trying seed")
        seed = _read_api_seed("quotes.json")
        if seed and seed.get("items"):
            items = seed["items"]
            if market in ("cn", "us"):
                items = [q for q in items if q.get("market") == market]
            seed = dict(seed)
            seed["items"] = items
            seed["count"] = len(items)
            seed["note"] = (seed.get("note") or "") + " · 实时源暂不可用，已回退快照"
            seed["source"] = "seed"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history")
def history():
    instrument_id = request.args.get("id", "")
    interval = request.args.get("interval", "D").upper()
    limit = request.args.get("limit", type=int)

    if not get_instrument(instrument_id):
        return jsonify({"error": f"unknown id: {instrument_id}"}), 404
    if interval not in {"D", "W", "M", "Y"}:
        return jsonify({"error": "interval must be D, W, M or Y"}), 400

    try:
        payload = history_payload(instrument_id, interval=interval, limit=limit)
        return jsonify(payload)
    except Exception as exc:
        logger.exception("history failed for %s", instrument_id)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/extremes")
def extremes():
    force = request.args.get("force") == "1"
    try:
        data = extremes_summary(force=force)
        return jsonify(data)
    except Exception as exc:
        logger.exception("extremes failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/analysis")
def analysis_one():
    instrument_id = request.args.get("id", "")
    force = request.args.get("force") == "1"
    if not get_instrument(instrument_id):
        return jsonify({"error": f"unknown id: {instrument_id}"}), 404
    try:
        row = get_instrument_analysis(instrument_id, force=force)
        if not row:
            return jsonify({"error": "insufficient data"}), 404
        return jsonify(row)
    except Exception as exc:
        logger.exception("analysis failed for %s", instrument_id)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/analysis/all")
def analysis_all():
    force = request.args.get("force") == "1"
    try:
        data = build_full_analysis(force=force)

        def slim(r: dict) -> dict:
            c = r.get("cost") or {}
            def pick(*vals):
                for v in vals:
                    if v is not None:
                        return v
                return None
            return {
                "id": r["id"],
                "name": r["name"],
                "symbol": r["symbol"],
                "market": r["market"],
                "sector": r["sector"],
                "exchange": r.get("exchange", ""),
                "price": r["price"],
                "tag": r["tag"],
                "deflation_tag": r.get("deflation_tag"),
                "deflation_note": r.get("deflation_note"),
                "pct_all": r["pct_all"],
                "pct_5y": r["pct_5y"],
                "pct_10y": r["pct_10y"],
                "low_score": r["low_score"],
                "high_score": r["high_score"],
                "from_max_pct": r["from_max_pct"],
                "from_min_pct": r["from_min_pct"],
                "ret_1y": r["ret_1y"],
                "primary_cost": pick(c.get("primary_cost"), r.get("primary_cost")),
                "cash_cost": pick(c.get("cash_cost"), r.get("cash_cost")),
                "comfort_high": pick(c.get("comfort_high"), r.get("comfort_high")),
                "cost_unit": pick(c.get("unit"), r.get("cost_unit")),
                "vs_primary_pct": pick(c.get("vs_primary_pct"), r.get("vs_primary_pct")),
                "vs_cash_pct": pick(c.get("vs_cash_pct"), r.get("vs_cash_pct")),
                "cost_band": pick(c.get("position_vs_band"), r.get("cost_band")),
                "cost": c if c else None,
                "analysis": r.get("analysis"),
            }

        return jsonify(
            {
                "updated_at": data["updated_at"],
                "lows": [slim(r) for r in data["lows"]],
                "highs": [slim(r) for r in data["highs"]],
                "all": [slim(r) for r in data.get("all", [])],
                "criteria": data["criteria"],
                "note": data["note"],
                "count": len(data.get("all", [])),
            }
        )
    except Exception as exc:
        logger.exception("analysis all failed; trying seed")
        seed = _read_api_seed("analysis_all.json")
        if seed and (seed.get("lows") or seed.get("highs") or seed.get("all")):
            seed = dict(seed)
            seed["source"] = "seed"
            seed["note"] = (seed.get("note") or "") + " · 实时重算暂不可用，已回退快照"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/supply")
def supply():
    force = request.args.get("force") == "1"
    try:
        data = maybe_weekly_fetch(force=force) if force else (load_latest() or maybe_weekly_fetch())
        if not data:
            return jsonify({"items": [], "note": "尚无供给数据，请点击刷新或等待周更"}), 200
        # overlay latest domestic price onto import parity (shared with /refresh)
        overlay_parity_vs_quotes(data.get("items") or [])
        return jsonify(data)
    except Exception as exc:
        logger.exception("supply failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/supply/refresh")
def supply_refresh():
    try:
        data = maybe_weekly_fetch(force=True)
        if data:
            overlay_parity_vs_quotes(data.get("items") or [])
        return jsonify(data)
    except Exception as exc:
        logger.exception("supply refresh failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/futures-movers")
def futures_movers_api():
    period = request.args.get("period", "W")
    market = request.args.get("market", "all")
    limit = request.args.get("limit", default=20, type=int)
    force = request.args.get("force") == "1"

    try:
        import os
        from analysis import _futures_movers_cache

        period_u = (period or "W").upper()
        market_l = (market or "all").lower()
        lim = max(5, min(int(limit or 20), 50))
        cache_key = f"{period_u}|{market_l}|{lim}"
        hit = _futures_movers_cache.get(cache_key)
        if hit and not force:
            return jsonify(hit[1])

        seed = _movers_seed_for("futures_movers.json", period_u)
        # Seed is market=all; if caller asks cn/us only, filter lists.
        if seed is not None and market_l in {"cn", "us"}:
            seed = dict(seed)
            seed["market"] = market_l
            seed["gainers"] = [r for r in (seed.get("gainers") or []) if r.get("market") == market_l][:lim]
            seed["losers"] = [r for r in (seed.get("losers") or []) if r.get("market") == market_l][:lim]

        if seed is not None and os.environ.get("RENDER") and not force:
            def _bg():
                try:
                    compute_futures_movers(period=period_u, market=market_l, limit=lim, force=True)
                except Exception:
                    logger.exception("bg futures movers failed")

            threading.Thread(target=_bg, daemon=True).start()
            return jsonify(seed)

        if seed is not None and force and os.environ.get("RENDER"):
            def _bg():
                try:
                    compute_futures_movers(period=period_u, market=market_l, limit=lim, force=True)
                except Exception:
                    logger.exception("bg futures movers force failed")

            threading.Thread(target=_bg, daemon=True).start()
            return jsonify(seed)

        data = compute_futures_movers(period=period_u, market=market_l, limit=lim, force=force)
        return jsonify(data)
    except Exception as exc:
        logger.exception("futures movers failed; trying seed")
        seed = _movers_seed_for("futures_movers.json", period)
        if seed:
            seed["note"] = (seed.get("note") or "") + " · 实时计算暂不可用，已回退快照"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/us-movers")
def us_movers_api():
    period = request.args.get("period", "W")
    kind = request.args.get("kind", "all")
    limit = request.args.get("limit", default=20, type=int)
    force = request.args.get("force") == "1"

    try:
        import os
        from us_movers import compute_movers

        period_u = (period or "W").upper()
        seed = _movers_seed_for("us_movers.json", period_u)
        if seed is not None and os.environ.get("RENDER") and not force:
            def _bg():
                try:
                    compute_movers(period=period_u, kind=kind, limit=limit or 20, force=True)
                except Exception:
                    logger.exception("bg us movers failed")

            threading.Thread(target=_bg, daemon=True).start()
            return jsonify(seed)

        if seed is not None and force and os.environ.get("RENDER"):
            def _bg():
                try:
                    compute_movers(period=period_u, kind=kind, limit=limit or 20, force=True)
                except Exception:
                    logger.exception("bg us movers force failed")

            threading.Thread(target=_bg, daemon=True).start()
            return jsonify(seed)

        data = compute_movers(period=period_u, kind=kind, limit=limit or 20, force=force)
        return jsonify(data)
    except Exception as exc:
        logger.exception("us movers failed; trying seed")
        seed = _movers_seed_for("us_movers.json", period)
        if seed:
            seed["note"] = (seed.get("note") or "") + " · 实时计算暂不可用，已回退快照"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ng/dashboard")
def ng_dashboard_api():
    force = request.args.get("force") == "1"
    try:
        import os
        import ng_dashboard

        seed = _read_api_seed("ng_dashboard.json")
        if seed is not None and os.environ.get("RENDER") and not force:
            def _bg():
                try:
                    ng_dashboard.build_dashboard(force=True)
                except Exception:
                    logger.exception("bg ng dashboard failed")

            threading.Thread(target=_bg, daemon=True).start()
            seed = dict(seed)
            seed["source"] = "seed"
            return jsonify(seed)

        data = ng_dashboard.build_dashboard(force=force)
        return jsonify(data)
    except Exception as exc:
        logger.exception("ng dashboard failed")
        seed = _read_api_seed("ng_dashboard.json")
        if seed:
            seed = dict(seed)
            seed["source"] = "seed"
            seed["note"] = (seed.get("note") or "") + " · 已回退快照"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ng/refresh")
def ng_refresh_api():
    try:
        import ng_dashboard

        data = ng_dashboard.build_dashboard(force=True)
        return jsonify(data)
    except Exception as exc:
        logger.exception("ng refresh failed")
        return jsonify({"error": str(exc)}), 500


def _weekly_supply_loop():
    import time

    time.sleep(12)
    try:
        logger.info("Initial supply fetch if stale...")
        maybe_weekly_fetch(force_if_stale_hours=24)
        logger.info("Supply snapshot ready.")
    except Exception:
        logger.exception("initial supply fetch failed")
    while True:
        time.sleep(3600)
        try:
            maybe_weekly_fetch()
        except Exception:
            logger.exception("hourly supply check failed")


def _bg_warmup():
    try:
        logger.info("Loading full universe...")
        items, _ = get_universe(force=True, live_cn=False)
        cn = sum(1 for x in items if x["market"] == "cn")
        us = sum(1 for x in items if x["market"] == "us")
        logger.info("Universe ready: total=%d CN=%d NA=%d", len(items), cn, us)

        logger.info("Loading quotes...")
        get_quotes(force=True)
        logger.info("Initial quotes loaded.")

        # history warmup can take several minutes for full universe
        logger.info("Warming up history for all instruments (background)...")
        warmup()
        logger.info("History warmup done.")

        logger.info("Building extremes analysis for full universe...")
        build_full_analysis(force=True)
        logger.info("Extremes analysis ready.")
    except Exception:
        logger.exception("warmup error")


def _us_movers_warmup():
    import time

    time.sleep(18)
    try:
        from us_movers import get_price_panel

        logger.info("Warming US stock/ETF movers panel...")
        get_price_panel()
        logger.info("US movers panel ready.")
    except Exception:
        logger.exception("US movers warmup failed")


def _ng_warmup():
    import time

    time.sleep(6)
    try:
        import ng_dashboard

        logger.info("Pre-warming NG dedicated dashboard in background...")
        ng_dashboard.build_dashboard(force=False)
        logger.info("NG dedicated dashboard ready.")
    except Exception:
        logger.exception("NG warmup failed")




@app.route("/api/short-put/high-vol")
def short_put_high_vol_api():
    force = request.args.get("force") == "1"
    try:
        import os
        from short_put_screen import build_short_put_screen

        seed = _read_api_seed("short_put.json")
        if seed is not None and os.environ.get("RENDER") and not force:
            def _bg():
                try:
                    build_short_put_screen(force=True)
                except Exception:
                    logger.exception("bg short put failed")

            threading.Thread(target=_bg, daemon=True).start()
            seed = dict(seed)
            seed["source"] = "seed"
            return jsonify(seed)

        return jsonify(build_short_put_screen(force=force))
    except Exception as exc:
        logger.exception("short put screen failed")
        seed = _read_api_seed("short_put.json")
        if seed:
            seed = dict(seed)
            seed["source"] = "seed"
            return jsonify(seed)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/short-put/refresh")
def short_put_refresh_api():
    try:
        from short_put_screen import build_short_put_screen

        return jsonify(build_short_put_screen(force=True))
    except Exception as exc:
        logger.exception("short put refresh failed")
        return jsonify({"error": str(exc)}), 500

def _start_background_jobs() -> None:
    """Warm caches in the background (local + gunicorn/Render)."""
    import os

    # Render free dynos choke if we Yahoo-scan hundreds of symbols at boot.
    # Serve exported snapshots first; refresh lazily on demand.
    if os.environ.get("RENDER") or os.environ.get("SKIP_BG_WARMUP") == "1":
        logger.info("Skipping heavy boot warmups on Render; using seed snapshots.")
        return

    threading.Thread(target=_bg_warmup, daemon=True).start()
    threading.Thread(target=_weekly_supply_loop, daemon=True).start()
    threading.Thread(target=_us_movers_warmup, daemon=True).start()
    threading.Thread(target=_ng_warmup, daemon=True).start()


# Under gunicorn/Render the module is imported; start warmups once.
if __name__ != "__main__":
    _start_background_jobs()


if __name__ == "__main__":
    import os

    _start_background_jobs()
    port = int(os.environ.get("PORT", "5050"))
    host = os.environ.get("HOST", "127.0.0.1")
    print("=" * 60)
    print(f"  期货看板  http://{host}:{port}")
    print("  大宗商品 · 高低位/成本 · 产业周报 · 涨跌榜 · 美气NG · 卖Put")
    print("=" * 60)
    app.run(host=host, port=port, debug=False, threaded=True)
