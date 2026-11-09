# -*- coding: utf-8 -*-
"""Covered / short-call screener parallel to short_put_screen."""
from __future__ import annotations

import bg_tasks

import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from short_put_screen import (
    ACCOUNT_DEFAULTS,
    CORE_UNIVERSE,
    THEME_LINKS,
    _hv,
    _hv_percentile,
    _iv_tag,
    _load_price_panel,
    _next_earnings,
    _normalize_iv_pct,
    _pick_expiry,
    _r,
    _rsi,
    _sane_iv,
    _estimate_iv_pct,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 30 * 60
_cache: tuple[float, dict] | None = None


def _estimate_call_iv_pct(spot: float, strike: float, mid: float, dte: int) -> float | None:
    est = _estimate_iv_pct(spot, strike, mid, dte)
    if est is None or spot <= 0:
        return None
    moneyness = strike / spot
    if moneyness > 1.03:
        est *= 1.0 + min(0.35, (moneyness - 1.03) * 2.5)
    if est < 3 or est > 400:
        return None
    return float(est)


def _normalize_call_iv(iv_raw: float, spot: float, strike: float, mid: float, dte: int) -> tuple[float | None, str | None]:
    iv, src = _normalize_iv_pct(iv_raw, spot, strike, mid, dte)
    est = _estimate_call_iv_pct(spot, strike, mid, dte)
    if iv is None and est is not None:
        return est, "est"
    if iv is not None and est is not None and iv < max(8.0, est * 0.45):
        return est, "est"
    if src == "est" and est is not None:
        return est, "est"
    return iv, src


def _option_call_idea(symbol: str, spot: float) -> dict | None:
    """Pick an OTM short-call candidate (~8% OTM) on ~30 DTE."""
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        options = list(t.options or ())
        if spot <= 0:
            return None
        candidates = []
        primary = _pick_expiry(options)
        if primary:
            candidates.append(primary)
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        ranked_exps = []
        for e in options:
            try:
                dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
            except Exception:
                continue
            if 10 <= dte <= 120:
                ranked_exps.append((abs(dte - 28), dte, e))
        for _, _, e in sorted(ranked_exps):
            if e not in candidates:
                candidates.append(e)
            if len(candidates) >= 10:
                break
        exp = None
        calls = None
        fallback = None
        for cand in candidates:
            try:
                chain = t.option_chain(cand)
                ctmp = chain.calls.copy()
                if ctmp.empty:
                    continue
                strikes = ctmp["strike"].fillna(0).astype(float)
                near_mask = (strikes >= spot * 0.95) & (strikes <= spot * 1.30)
                near_n = int(near_mask.sum())
                # Skip stale pre-split chains (e.g. SOXX 300+ while spot ~132).
                if near_n < 2:
                    continue
                ivs = ctmp["impliedVolatility"].fillna(0).astype(float)
                bids = ctmp["bid"].fillna(0).astype(float)
                asks = ctmp["ask"].fillna(0).astype(float)
                lasts = ctmp["lastPrice"].fillna(0).astype(float)
                liquid = int(((bids + asks > 0) | (lasts > 0)).sum())
                usable_iv = int((ivs > 0.03).sum())
                score = near_n * 3 + liquid + usable_iv
                if liquid >= 3 or usable_iv >= 3 or near_n >= 5:
                    exp, calls = cand, ctmp
                    break
                if fallback is None or score > fallback[0]:
                    fallback = (score, cand, ctmp)
            except Exception:
                continue
        if (not exp or calls is None or calls.empty) and fallback is not None:
            _, exp, calls = fallback
        if not exp or calls is None or calls.empty:
            return None

        target = spot * 1.08
        calls["dist"] = (calls["strike"] - target).abs()
        otm = calls[(calls["strike"] > spot) & (calls["strike"] < spot * 1.30)].copy()
        if otm.empty:
            return None
        otm = otm.copy()
        otm["_iv"] = otm["impliedVolatility"].fillna(0).astype(float)
        otm["_bid"] = otm["bid"].fillna(0).astype(float)
        otm["_ask"] = otm["ask"].fillna(0).astype(float)
        otm["_last"] = otm["lastPrice"].fillna(0).astype(float)
        otm["_liq"] = (
            ((otm["_bid"] + otm["_ask"] > 0) | (otm["_last"] > 0)).astype(int) * 2
            + (otm["_iv"] > 0.03).astype(int)
        )
        otm = otm.sort_values(["_liq", "dist", "openInterest"], ascending=[False, True, False])
        row = otm.iloc[0]
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        mid = (bid + ask) / 2 if bid or ask else float(row.get("lastPrice") or 0)
        strike = float(row["strike"])
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        otm_pct = (strike / spot - 1) * 100
        yield_ann = (mid / spot * 365 / max(dte, 1) * 100) if spot > 0 and mid > 0 else None
        premium_pct = mid / spot * 100 if spot else None
        if mid <= 0 or strike <= 0:
            return None
        if not (spot < strike < spot * 1.30):
            return None
        if mid >= strike * 0.45:
            return None
        if yield_ann is not None and yield_ann > 250:
            yield_ann = 250.0
        iv, iv_source = _normalize_call_iv(float(row.get("impliedVolatility") or 0), spot, strike, mid, dte)

        atm_iv = None
        try:
            calls2 = calls.copy()
            calls2["adist"] = (calls2["strike"] - spot).abs()
            calls2["_iv"] = calls2["impliedVolatility"].fillna(0).astype(float)
            calls2["_bid"] = calls2["bid"].fillna(0).astype(float)
            calls2["_ask"] = calls2["ask"].fillna(0).astype(float)
            calls2["_ok"] = ((calls2["_iv"] > 0.03) & (calls2["_bid"] + calls2["_ask"] > 0)).astype(int)
            atm_row = calls2.sort_values(["_ok", "adist"], ascending=[False, True]).iloc[0]
            atm_mid = float(atm_row.get("bid") or 0) + float(atm_row.get("ask") or 0)
            atm_mid = atm_mid / 2 if atm_mid else float(atm_row.get("lastPrice") or 0)
            atm_iv, _atm_src = _normalize_call_iv(
                float(atm_row.get("impliedVolatility") or 0),
                spot,
                float(atm_row.get("strike") or strike),
                atm_mid or mid,
                dte,
            )
            atm_iv = atm_iv or iv
        except Exception:
            atm_iv = iv

        if atm_iv is not None and (iv is None or iv < atm_iv * 0.85):
            iv = atm_iv
            iv_source = (iv_source or "yahoo") if iv_source == "est" else "yahoo_atm"
        if iv is None and atm_iv is not None:
            iv = atm_iv
            iv_source = "yahoo_atm"

        far_iv = None
        far_exp = None
        try:
            far_candidates = []
            for e in options:
                try:
                    d = datetime.strptime(e, "%Y-%m-%d").date()
                    dd = (d - today).days
                    if 45 <= dd <= 90:
                        far_candidates.append((abs(dd - 60), e))
                except Exception:
                    continue
            if far_candidates:
                far_exp = sorted(far_candidates)[0][1]
                fchain = t.option_chain(far_exp)
                fcalls = fchain.calls.copy()
                if not fcalls.empty:
                    fcalls["adist"] = (fcalls["strike"] - spot).abs()
                    fcalls["_iv"] = fcalls["impliedVolatility"].fillna(0).astype(float)
                    fcalls["_ok"] = (fcalls["_iv"] > 0.03).astype(int)
                    iv_raw = float(fcalls.sort_values(["_ok", "adist"], ascending=[False, True]).iloc[0].get("impliedVolatility") or 0)
                    far_iv = iv_raw * 100 if iv_raw <= 5 else iv_raw
                    if far_iv > 400:
                        far_iv = 400.0
                    if far_iv <= 1:
                        far_iv = None
        except Exception:
            far_iv = None

        chain_slice = []
        try:
            band = calls[(calls["strike"] >= spot * 0.99) & (calls["strike"] <= spot * 1.22)].copy()
            band = band.sort_values("strike", ascending=True).head(14)
            for _, rr in band.iterrows():
                b = float(rr.get("bid") or 0)
                a = float(rr.get("ask") or 0)
                m = (b + a) / 2 if b or a else float(rr.get("lastPrice") or 0)
                k = float(rr["strike"])
                civ_raw = float(rr.get("impliedVolatility") or 0)
                civ, _ = _normalize_call_iv(civ_raw, spot, k, m or mid, dte)
                chain_slice.append(
                    {
                        "strike": _r(k, 2),
                        "bid": _r(b, 2),
                        "ask": _r(a, 2),
                        "mid": _r(m, 2),
                        "iv": _r(min(civ, 400), 1) if civ is not None else None,
                        "volume": int(rr.get("volume") or 0),
                        "open_interest": int(rr.get("openInterest") or 0),
                        "otm_pct": _r((k / spot - 1) * 100, 1),
                        "selected": abs(k - strike) < 1e-6,
                    }
                )
        except Exception:
            chain_slice = []

        return {
            "expiry": exp,
            "dte": dte,
            "strike": _r(strike, 2),
            "bid": _r(bid, 2),
            "ask": _r(ask, 2),
            "mid": _r(mid, 2),
            "iv": _r(iv, 1),
            "iv_source": iv_source or ("est" if iv is not None else None),
            "volume": int(row.get("volume") or 0),
            "open_interest": int(row.get("openInterest") or 0),
            "otm_pct": _r(otm_pct, 1),
            "premium_pct": _r(premium_pct, 2),
            "yield_ann_pct": _r(yield_ann, 1),
            "capital": _r(spot * 100, 0),
            "atm_iv": _r(atm_iv, 1),
            "near_iv": _r(atm_iv if atm_iv is not None else iv, 1),
            "far_iv": _r(far_iv, 1),
            "far_expiry": far_exp,
            "otm_call_iv": _r(iv, 1),
            "otm_put_iv": _r(iv, 1),
            "chain": chain_slice,
        }
    except Exception as exc:
        logger.debug("option call idea %s failed: %s", symbol, exc)
        return None


def _build_risk(row: dict, budget: float | None = None) -> dict:
    call = row.get("call") or {}
    budget = float(budget or ACCOUNT_DEFAULTS["budget_usd"])
    max_pct = float(ACCOUNT_DEFAULTS["max_name_pct"])
    cap = int(ACCOUNT_DEFAULTS["max_contracts_cap"])
    capital = call.get("capital")
    if capital is None and row.get("price"):
        capital = float(row["price"]) * 100
    max_by_budget = 0
    if capital and capital > 0:
        max_by_budget = int((budget * max_pct / 100) // capital)
    max_contracts = max(0, min(cap, max_by_budget))
    earn = row.get("earnings")
    flags = []
    if capital and capital > budget * max_pct / 100:
        flags.append("单笔超预算")
    if earn and earn.get("days") is not None:
        d = earn["days"]
        if d <= 7:
            flags.append("财报≤7天")
        elif d <= 14:
            flags.append("财报≤14天")
    hv = row.get("hv20") or 0
    if hv >= 90:
        flags.append("极高波动")
    rsi = row.get("rsi14")
    if rsi is not None and rsi < 28:
        flags.append("超卖急跌")
    vs20 = row.get("vs_ma20_pct")
    if vs20 is not None and vs20 <= -12:
        flags.append("远低于均线")
    if call.get("open_interest") is not None and call["open_interest"] < 100:
        flags.append("OI偏低")
    spread = row.get("iv_hv_spread")
    iv_tag = _iv_tag(spread)
    if iv_tag != "IV未知" and call.get("iv_source") == "est":
        iv_tag = iv_tag.replace("IV", "IV估")
    elif iv_tag == "IV未知" and call.get("iv") is not None and row.get("hv20"):
        spread = _r(float(call["iv"]) - float(row["hv20"]), 1)
        row["iv_hv_spread"] = spread
        iv_tag = _iv_tag(spread)
        if call.get("iv_source") == "est":
            iv_tag = iv_tag.replace("IV", "IV估")
    if "偏便宜" in iv_tag:
        flags.append("IV偏便宜")
    level = "中"
    if any(x in flags for x in ("财报≤7天", "超卖急跌", "极高波动", "远低于均线")):
        level = "高"
    elif not flags and "偏贵" in iv_tag:
        level = "低"
    elif "财报≤14天" in flags or "OI偏低" in flags:
        level = "中高"
    return {
        "capital_per_contract": _r(capital, 0),
        "max_contracts_hint": max_contracts,
        "budget_usd": budget,
        "max_name_pct": max_pct,
        "margin_note": "备兑按持股市值估算（现价×100）；裸卖Call保证金更高、风险不对称",
        "iv_tag": iv_tag,
        "iv_hv_spread": spread,
        "earnings": earn,
        "flags": flags,
        "level": level,
    }


def _score_row(row: dict) -> float:
    hv = row.get("hv20") or 0
    hv_pct = row.get("hv_pctile") or 50
    rsi = row.get("rsi14")
    chg5 = row.get("chg_5d") or 0
    chg21 = row.get("chg_21d") or 0
    call = row.get("call") or {}
    iv = call.get("iv")
    yld = call.get("yield_ann_pct") or 0
    otm = abs(call.get("otm_pct") or 8)
    oi = call.get("open_interest") or 0
    vs20 = row.get("vs_ma20_pct")

    score = 0.0
    if hv >= 25:
        score += min(25, (hv - 20) * 0.35)
    if hv_pct >= 60:
        score += (hv_pct - 50) * 0.25
    if rsi is not None:
        if 48 <= rsi <= 68:
            score += 18
        elif 68 < rsi <= 78:
            score += 12
        elif 40 <= rsi < 48:
            score += 8
        elif rsi < 32:
            score -= 12
        elif rsi > 80:
            score -= 4
    if vs20 is not None and vs20 <= -12:
        score -= 12
    elif vs20 is not None and vs20 <= -6:
        score -= 6
    if chg5 <= -18:
        score -= 15
    elif -4 <= chg5 <= 10:
        score += 6
    if chg21 <= -30:
        score -= 10
    if yld:
        score += min(20, yld * 0.35)
    if iv and hv:
        score += min(12, max(-5, (iv - hv) * 0.25))
    if 5 <= otm <= 12:
        score += 6
    if oi >= 500:
        score += 5
    elif oi >= 100:
        score += 2
    if row.get("kind") == "etf":
        score += 3
    earn = row.get("earnings") or {}
    days = earn.get("days")
    if days is not None:
        if days <= 5:
            score -= 18
        elif days <= 10:
            score -= 10
        elif days <= 14:
            score -= 5
    spread = None
    if call.get("iv") is not None and row.get("hv20"):
        spread = call["iv"] - row["hv20"]
    if spread is not None and spread >= 8:
        score += 6
    if spread is not None and spread <= -8:
        score -= 6
    return float(score)


def _bias_cn(row: dict) -> str:
    s = row.get("score") or 0
    rsi = row.get("rsi14")
    earn = row.get("earnings") or {}
    if earn.get("days") is not None and earn["days"] <= 7:
        return "财报回避"
    if s >= 55:
        return "优先关注"
    if s >= 40:
        return "可考虑"
    if s >= 28:
        return "观望等待"
    if rsi is not None and rsi < 28:
        return "急跌慎卖"
    return "暂不推荐"


def _action(row: dict) -> str:
    call = row.get("call") or {}
    if not call:
        return "期权链暂不可用；可等波动回落后再看备兑卖Call。"
    strike = call.get("strike")
    exp = call.get("expiry")
    mid = call.get("mid")
    yld = call.get("yield_ann_pct")
    parts = [f"建议备兑卖出 {exp} ${strike} Call"]
    if mid is not None:
        parts.append(f"权利金约 ${mid}")
    if yld is not None:
        parts.append(f"静态年化约 {yld}%")
    earn = row.get("earnings") or {}
    if earn.get("days") is not None:
        parts.append(f"距财报约 {earn['days']} 天({earn.get('date')})")
    parts.append("仅作研究参考，备兑需持有标的，裸卖风险不对称。")
    return "；".join(parts)


def _fund_rank_key(r: dict) -> float:
    from fundamentals import rank_key

    return rank_key(r)


def _fund_method_text(market: str, side: str) -> str:
    try:
        from fundamentals import method_text

        return method_text(market, side)
    except Exception:  # noqa: BLE001
        return "基本面模块不可用"


def _apply_fundamentals(rows: list[dict], side: str) -> dict:
    """Never fatal: on any failure every row keeps combined_score = tech score."""
    try:
        from fundamentals import apply_us_fundamentals, default_snapshot_index

        # info 取不到时：报表回退 → ≤48h 的 Pages 快照（bundled + GitHub main），标「来自快照」
        meta = apply_us_fundamentals(rows, side, option_key=side, snapshot=default_snapshot_index)
        meta["status"] = "ok"
        return meta
    except Exception as exc:  # noqa: BLE001
        logger.warning("fundamentals failed (%s): %s", side, exc)
        try:
            from fundamentals import apply_fund_to_row

            for r in rows:
                apply_fund_to_row(r, None, not_computed_reason=f"基本面计算失败 {type(exc).__name__}")
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def _load_seed_snapshot() -> dict | None:
    for rel in ("docs/api/short_call.json", "data/short_call_seed.json"):
        path = Path(__file__).resolve().parent / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("picks") is not None:
                data = dict(data)
                data["source"] = "seed"
                return data
        except Exception:
            logger.debug("seed load failed for %s", path, exc_info=True)
    return None


def build_short_call_screen(force: bool = False, top_n: int = 20, option_n: int = 28) -> dict:
    global _cache
    if not force and _cache and time.time() - _cache[0] < CACHE_TTL:
        return _cache[1]

    if not force and _cache is None:
        seed = _load_seed_snapshot()
        if seed is not None:
            from cn_option_candidates import ensure_cn_candidate_fields
            seed = ensure_cn_candidate_fields(seed, side="call")
            _cache = (time.time(), seed)
            bg_tasks.submit("short-call", lambda: build_short_call_screen(force=True, top_n=top_n, option_n=option_n))
            return seed

    close, meta = _load_price_panel()
    symbols = [s for s in CORE_UNIVERSE if s in close.columns]
    theme_force = set()
    for _meta in THEME_LINKS.values():
        theme_force.update(_meta.get("symbols") or [])
    for s in theme_force:
        if s in close.columns and s not in symbols:
            symbols.append(s)
    try:
        rets = close.pct_change()
        hv_all = (rets.tail(21).std() * math.sqrt(252) * 100).dropna()
        for s, hv in hv_all.nlargest(80).items():
            if s in symbols:
                continue
            m = meta.get(s) or {}
            if m.get("kind") == "lev":
                continue
            if hv < 35:
                continue
            symbols.append(s)
            CORE_UNIVERSE.setdefault(
                s,
                {
                    "name": m.get("name") or s,
                    "kind": m.get("kind") or "stock",
                    "sector": m.get("sector") or "高波动",
                },
            )
    except Exception:
        logger.debug("extra hv scan failed", exc_info=True)

    rows: list[dict] = []
    for s in symbols:
        series = close[s].dropna()
        if len(series) < 40:
            continue
        px = float(series.iloc[-1])
        hv20 = _hv(series, 20)
        hv60 = _hv(series, 60)
        if hv20 is None:
            continue
        if hv20 < 18 and s not in theme_force:
            continue
        info = CORE_UNIVERSE.get(s) or meta.get(s) or {"name": s, "kind": "stock", "sector": ""}
        ma20 = float(series.tail(20).mean()) if len(series) >= 20 else None
        ma60 = float(series.tail(60).mean()) if len(series) >= 60 else None
        row = {
            "symbol": s,
            "name": info.get("name") or s,
            "kind": info.get("kind") or "stock",
            "sector": info.get("sector") or "",
            "price": _r(px, 2),
            "hv20": _r(hv20, 1),
            "hv60": _r(hv60, 1),
            "hv_pctile": _r(_hv_percentile(series), 1),
            "rsi14": _r(_rsi(series), 1),
            "chg_1d": _r(float(series.pct_change().iloc[-1] * 100), 2),
            "chg_5d": _r(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 2) if len(series) >= 6 else None,
            "chg_21d": _r(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 2) if len(series) >= 22 else None,
            "vs_ma20_pct": _r((px / ma20 - 1) * 100, 2) if ma20 else None,
            "vs_ma60_pct": _r((px / ma60 - 1) * 100, 2) if ma60 else None,
            "call": None,
        }
        rows.append(row)

    rows.sort(key=lambda r: (r.get("hv_pctile") or 0) * 0.5 + (r.get("hv20") or 0) * 0.5, reverse=True)
    theme_syms = []
    for meta_th in THEME_LINKS.values():
        theme_syms.extend(meta_th.get("symbols") or [])
    by_sym = {r["symbol"]: r for r in rows}
    theme_set = set(theme_syms)
    tops = [r for r in rows if r["symbol"] not in theme_set][:option_n]
    targets = []
    seen = set()
    for r in [by_sym[s] for s in theme_syms if s in by_sym] + tops:
        if r["symbol"] in seen:
            continue
        targets.append(r)
        seen.add(r["symbol"])

    seed_calls: dict[str, dict] = {}
    try:
        seed = _load_seed_snapshot() or {}
        for bucket in ("picks", "watchlist", "all"):
            for rr in seed.get(bucket) or []:
                if rr.get("symbol") and rr.get("call"):
                    seed_calls[rr["symbol"]] = rr["call"]
        for th in seed.get("themes") or []:
            for rr in th.get("items") or []:
                if rr.get("symbol") and rr.get("call"):
                    seed_calls[rr["symbol"]] = rr["call"]
    except Exception:
        seed_calls = {}

    def fetch(r: dict) -> dict:
        idea = _option_call_idea(r["symbol"], r["price"] or 0)
        if idea is None and r["symbol"] in seed_calls:
            idea = dict(seed_calls[r["symbol"]])
            idea["from_seed"] = True
        r["call"] = idea
        return r

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(fetch, r) for r in targets]
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception:
                continue

    def earn_one(r: dict) -> None:
        if r.get("kind") == "etf":
            r["earnings"] = None
            return
        r["earnings"] = _next_earnings(r["symbol"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(earn_one, targets))

    for r in rows:
        if r.get("call") and r["call"].get("iv") is not None and r.get("hv20"):
            r["iv_hv_spread"] = _r(r["call"]["iv"] - r["hv20"], 1)
        r["score"] = _r(_score_row(r), 1)
        r["bias_cn"] = _bias_cn(r)
        r["action"] = _action(r)
        r["risk"] = _build_risk(r)
        call = r.get("call") or {}
        try:
            from put_edge import compute_edge, build_call_checklist

            hv20 = r.get("hv20")
            iv = _sane_iv(call.get("iv"), hv20)
            atm = _sane_iv(call.get("atm_iv"), hv20) or iv
            near = _sane_iv(call.get("near_iv"), hv20) or atm or iv
            far = _sane_iv(call.get("far_iv"), hv20)
            otm_iv = _sane_iv(call.get("otm_call_iv"), hv20) or iv
            r["edge"] = compute_edge(
                iv=iv or atm,
                hv20=hv20,
                hv60=r.get("hv60"),
                hv_pctile=r.get("hv_pctile"),
                near_iv=near,
                far_iv=far,
                atm_iv=atm,
                otm_put_iv=otm_iv,
            )
            if r["edge"].get("metrics") is not None:
                r["edge"]["metrics"]["near_iv"] = call.get("near_iv")
                r["edge"]["metrics"]["far_iv"] = call.get("far_iv")
                r["edge"]["metrics"]["atm_iv"] = call.get("atm_iv")
                r["edge"]["metrics"]["otm_put_iv"] = call.get("otm_call_iv")
            r["checklist"] = build_call_checklist(r)
            eg = r["edge"].get("edge") or 0
            if r["checklist"].get("blocked"):
                r["score"] = _r(max(0, (r.get("score") or 0) - 15), 1)
            else:
                r["score"] = _r((r.get("score") or 0) + max(-5, min(12, (eg - 50) * 0.15)), 1)
            r["bias_cn"] = _bias_cn(r)
            if r["checklist"].get("blocked"):
                r["bias_cn"] = "检查否决"
            r["action"] = _action(r)
        except Exception:
            logger.debug("edge/checklist failed for %s", r.get("symbol"), exc_info=True)
            r["edge"] = None
            r["checklist"] = None

    # 基本面：仅对技术分前N且有期权合约的标的取数（24h缓存）；失败不致命，综合分退回技术分。
    fund_meta = _apply_fundamentals(rows, "call")
    # 入选门槛仍按技术分 score≥22（不变）；排序改为综合分 combined_score。
    ranked = sorted(rows, key=_fund_rank_key, reverse=True)
    eligible = [r for r in ranked if (r.get("score") or 0) >= 22 and r.get("call")]
    openable = [r for r in eligible if not ((r.get("checklist") or {}).get("blocked"))]
    picks = (openable + [r for r in eligible if r not in openable])[:top_n]
    watch = [r for r in ranked if r not in picks][:15]

    all_by_sym = {r["symbol"]: r for r in ranked}
    themes = []
    for key, meta_th in THEME_LINKS.items():
        items = []
        for s in meta_th["symbols"]:
            if s in all_by_sym:
                items.append(all_by_sym[s])
            elif s in CORE_UNIVERSE:
                stub = {
                    "symbol": s,
                    "name": CORE_UNIVERSE[s]["name"],
                    "kind": CORE_UNIVERSE[s]["kind"],
                    "sector": CORE_UNIVERSE[s].get("sector") or "",
                    "missing": True,
                    "call": None,
                }
                try:
                    if s in close.columns:
                        series = close[s].dropna()
                        if len(series) >= 20:
                            px = float(series.iloc[-1])
                            stub.update({
                                "price": _r(px, 2),
                                "hv20": _r(_hv(series, 20), 1),
                                "hv60": _r(_hv(series, 60), 1),
                                "hv_pctile": _r(_hv_percentile(series), 1),
                                "rsi14": _r(_rsi(series), 1),
                                "chg_1d": _r(float(series.pct_change().iloc[-1] * 100), 2),
                                "chg_5d": _r(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 2) if len(series) >= 6 else None,
                                "missing": False,
                            })
                            stub["call"] = _option_call_idea(s, px)
                            if stub.get("call"):
                                stub["score"] = _r(_score_row(stub), 1)
                                stub["bias_cn"] = _bias_cn(stub)
                                stub["action"] = _action(stub)
                                stub["risk"] = _build_risk(stub)
                except Exception:
                    logger.debug("theme stub enrich failed for %s", s, exc_info=True)
                items.append(stub)
        themes.append({
            "id": key,
            "label": meta_th["label"],
            "symbols": meta_th["symbols"],
            "keywords": meta_th.get("keywords") or [],
            "items": items,
        })

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "method": {
            "goal": "高波动美股/ETF 的备兑卖出看涨（Covered / Short Call）候选",
            "vol": "HV20/HV60 为近20/60日收益年化波动；hv_pctile 为近一年分位",
            "option": "优先选取约 14–45 DTE、约 8% OTM 的看涨期权（行权价在现价与现价×1.30之间）；权利金取买卖中间价，无报价时用最新成交",
            "score": "技术分（score 字段）：高波动溢价 + RSI非急跌/轻度超买 + 非远低于均线 + 权利金年化 + IV相对HV + 流动性 − 临近财报",
            "risk": "风控层标注 IV贵贱、财报窗口、备兑占用、建议合约上限；杠杆ETF已排除",
            "filters": "前端可按 HV分位/RSI/OI/年化/OTM/类型/仅核心池 过滤",
            "edge": "Edge分=VRP+IV分位+期限结构+RV稳定性+Skew；检查清单含硬否决(财报/倒挂/RV加速/自由落体等)",
            "fundamental": _fund_method_text("us", "call"),
            "chain": "卡片可下钻同到期看涨链，权重滑条可重算Edge",
        },
        "account_defaults": ACCOUNT_DEFAULTS,
        "edge_weights_default": {
            "vrp": 30,
            "iv_pctile": 25,
            "term": 20,
            "rv_stable": 15,
            "skew": 10,
        },
        "universe_size": len(rows),
        "fund_meta": fund_meta,
        "picks": picks,
        "watchlist": watch,
        "all": ranked[:60],
        "themes": themes,
        "core_symbols": sorted(CORE_UNIVERSE.keys()),
        "disclaimer": "研究用途，非投资建议。备兑需持有标的；裸卖Call损失理论上不封顶。",
    }
    from cn_option_candidates import attach_cn_candidates
    payload = attach_cn_candidates(payload, side="call", populate=True)
    _cache = (time.time(), payload)
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = build_short_call_screen(force=True, top_n=12, option_n=20)
    print("updated", out["updated_at"], "picks", len(out["picks"]), "universe", out["universe_size"])
    for r in out["picks"][:8]:
        c = r.get("call") or {}
        print(
            f"{r['symbol']:6} score={r['score']} HV={r['hv20']} RSI={r['rsi14']} "
            f"call={c.get('strike')}@{c.get('expiry')} mid={c.get('mid')} yld={c.get('yield_ann_pct')} {r['bias_cn']}"
        )
