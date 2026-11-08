# -*- coding: utf-8 -*-
"""High-volatility US stocks/ETFs screener for cash-secured / short put ideas."""
from __future__ import annotations

import net_limits

import bg_tasks

from pathlib import Path

import logging
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CACHE_TTL = 30 * 60
_cache: tuple[float, dict] | None = None

# Liquid, optionable names commonly used for short puts. Exclude 3x leveraged.
CORE_UNIVERSE: dict[str, dict[str, str]] = {
    # megacap / high-beta
    "TSLA": {"name": "特斯拉", "kind": "stock", "sector": "汽车/科技"},
    "NVDA": {"name": "英伟达", "kind": "stock", "sector": "半导体"},
    "AMD": {"name": "超威", "kind": "stock", "sector": "半导体"},
    "AVGO": {"name": "博通", "kind": "stock", "sector": "半导体"},
    "META": {"name": "Meta", "kind": "stock", "sector": "互联网"},
    "AMZN": {"name": "亚马逊", "kind": "stock", "sector": "互联网"},
    "GOOGL": {"name": "谷歌", "kind": "stock", "sector": "互联网"},
    "NFLX": {"name": "奈飞", "kind": "stock", "sector": "媒体"},
    "AAPL": {"name": "苹果", "kind": "stock", "sector": "科技"},
    "MSFT": {"name": "微软", "kind": "stock", "sector": "科技"},
    "AMAT": {"name": "应用材料", "kind": "stock", "sector": "半导体"},
    "MU": {"name": "美光", "kind": "stock", "sector": "半导体"},
    "INTC": {"name": "英特尔", "kind": "stock", "sector": "半导体"},
    "ARM": {"name": "Arm", "kind": "stock", "sector": "半导体"},
    "SMCI": {"name": "超微电脑", "kind": "stock", "sector": "服务器"},
    "APP": {"name": "AppLovin", "kind": "stock", "sector": "广告科技"},
    "PLTR": {"name": "Palantir", "kind": "stock", "sector": "软件"},
    "SNOW": {"name": "Snowflake", "kind": "stock", "sector": "软件"},
    "CRWD": {"name": "CrowdStrike", "kind": "stock", "sector": "网络安全"},
    "NET": {"name": "Cloudflare", "kind": "stock", "sector": "云"},
    "DDOG": {"name": "Datadog", "kind": "stock", "sector": "软件"},
    "SHOP": {"name": "Shopify", "kind": "stock", "sector": "电商"},
    "UBER": {"name": "Uber", "kind": "stock", "sector": "出行"},
    "COIN": {"name": "Coinbase", "kind": "stock", "sector": "加密"},
    "HOOD": {"name": "Robinhood", "kind": "stock", "sector": "券商"},
    "MSTR": {"name": "MicroStrategy", "kind": "stock", "sector": "加密/软件"},
    "MARA": {"name": "Marathon", "kind": "stock", "sector": "比特币挖矿"},
    "RIOT": {"name": "Riot", "kind": "stock", "sector": "比特币挖矿"},
    "SOFI": {"name": "SoFi", "kind": "stock", "sector": "金融科技"},
    "BA": {"name": "波音", "kind": "stock", "sector": "航空"},
    "BAC": {"name": "美银", "kind": "stock", "sector": "银行"},
    "C": {"name": "花旗", "kind": "stock", "sector": "银行"},
    "JPM": {"name": "摩根大通", "kind": "stock", "sector": "银行"},
    "XOM": {"name": "埃克森", "kind": "stock", "sector": "能源"},
    "CVX": {"name": "雪佛龙", "kind": "stock", "sector": "能源"},
    "BABA": {"name": "阿里巴巴", "kind": "stock", "sector": "中概"},
    "PDD": {"name": "拼多多", "kind": "stock", "sector": "中概"},
    "JD": {"name": "京东", "kind": "stock", "sector": "中概"},
    "BIDU": {"name": "百度", "kind": "stock", "sector": "中概"},
    "NIO": {"name": "蔚来", "kind": "stock", "sector": "中概汽车"},
    "RIVN": {"name": "Rivian", "kind": "stock", "sector": "电动车"},
    "ROKU": {"name": "Roku", "kind": "stock", "sector": "媒体"},
    # liquid ETFs
    "SPY": {"name": "标普500 ETF", "kind": "etf", "sector": "宽基"},
    "QQQ": {"name": "纳指100 ETF", "kind": "etf", "sector": "宽基"},
    "IWM": {"name": "罗素2000 ETF", "kind": "etf", "sector": "宽基"},
    "DIA": {"name": "道指 ETF", "kind": "etf", "sector": "宽基"},
    "SMH": {"name": "半导体 ETF", "kind": "etf", "sector": "半导体"},
    "SOXX": {"name": "半导体 iShares", "kind": "etf", "sector": "半导体"},
    "XLE": {"name": "能源板块 ETF", "kind": "etf", "sector": "能源"},
    "XLF": {"name": "金融板块 ETF", "kind": "etf", "sector": "金融"},
    "XBI": {"name": "生物科技 ETF", "kind": "etf", "sector": "生科"},
    "ARKK": {"name": "方舟创新 ETF", "kind": "etf", "sector": "成长"},
    "IBIT": {"name": "比特币现货 ETF", "kind": "etf", "sector": "加密"},
    "FBTC": {"name": "Fidelity 比特币 ETF", "kind": "etf", "sector": "加密"},
    "BITO": {"name": "比特币期货 ETF", "kind": "etf", "sector": "加密"},
    "UNG": {"name": "天然气 ETF", "kind": "etf", "sector": "能源商品"},
    "USO": {"name": "原油 ETF", "kind": "etf", "sector": "能源商品"},
    "GLD": {"name": "黄金 ETF", "kind": "etf", "sector": "贵金属"},
    "SLV": {"name": "白银 ETF", "kind": "etf", "sector": "贵金属"},
    "TLT": {"name": "20年美债 ETF", "kind": "etf", "sector": "债券"},
    "HYG": {"name": "高收益债 ETF", "kind": "etf", "sector": "债券"},
    "EEM": {"name": "新兴市场 ETF", "kind": "etf", "sector": "海外"},
    "KWEB": {"name": "中概互联 ETF", "kind": "etf", "sector": "中概"},
    "FXI": {"name": "中国大型股 ETF", "kind": "etf", "sector": "中概"},
    "EWZ": {"name": "巴西 ETF", "kind": "etf", "sector": "海外"},
    "GDX": {"name": "金矿 ETF", "kind": "etf", "sector": "贵金属"},
}

# Futures / theme → related short-put ETFs & stocks
THEME_LINKS: dict[str, dict] = {
    "ng": {"label": "天然气", "symbols": ["UNG"], "keywords": ["天然气", "NG", "Henry", "HH"]},
    "oil": {"label": "原油", "symbols": ["USO", "XLE", "XOM", "CVX"], "keywords": ["原油", "石油", "CL", "WTI", "Brent"]},
    "gold": {"label": "黄金", "symbols": ["GLD", "GDX"], "keywords": ["黄金", "Gold", "AU"]},
    "silver": {"label": "白银", "symbols": ["SLV"], "keywords": ["白银", "Silver", "AG"]},
    "copper": {"label": "铜", "symbols": ["FCX"], "keywords": ["铜", "Copper", "CU"]},
    "rates": {"label": "美债利率", "symbols": ["TLT", "HYG"], "keywords": ["国债", "美债", "利率", "TLT"]},
    "china": {"label": "中概/中国", "symbols": ["KWEB", "FXI", "BABA", "PDD", "JD"], "keywords": ["中概", "中国", "A50"]},
    "semi": {"label": "半导体", "symbols": ["SMH", "SOXX", "NVDA", "AMD", "AVGO", "MU"], "keywords": ["半导体", "芯片", "Silicon"]},
    "crypto": {"label": "加密", "symbols": ["IBIT", "FBTC", "BITO", "COIN", "MSTR"], "keywords": ["比特币", "加密", "BTC"]},
    "broad": {"label": "宽基", "symbols": ["SPY", "QQQ", "IWM"], "keywords": ["标普", "纳指", "罗素"]},
}

# Default CSP book assumptions (research UI only)
ACCOUNT_DEFAULTS = {
    "budget_usd": 50000,
    "max_name_pct": 15,
    "max_contracts_cap": 8,
}


def _sane_iv(iv: float | None, hv20: float | None = None) -> float | None:
    """Drop clearly broken Yahoo IV quotes (often ~0 or absurdly below HV)."""
    if iv is None:
        return None
    try:
        v = float(iv)
    except Exception:
        return None
    if v <= 1.0 or v > 400:
        return None
    if hv20 and hv20 >= 20 and v < hv20 * 0.25:
        return None
    return v


def _r(x: Any, n: int = 2) -> Any:
    if x is None:
        return None
    try:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
        return round(float(x), n)
    except Exception:
        return None


def _rsi(series: pd.Series, n: int = 14) -> float | None:
    if series is None or len(series) < n + 2:
        return None
    delta = series.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    v = float(val.iloc[-1])
    return v if np.isfinite(v) else None


def _hv(series: pd.Series, window: int = 20) -> float | None:
    if series is None or len(series) < window + 2:
        return None
    rets = series.pct_change().dropna().tail(window)
    if len(rets) < max(5, window // 2):
        return None
    v = float(rets.std() * math.sqrt(252) * 100)
    return v if np.isfinite(v) else None


def _hv_percentile(series: pd.Series, window: int = 20, lookback: int = 252) -> float | None:
    if series is None or len(series) < window + 40:
        return None
    rets = series.pct_change()
    roll = rets.rolling(window).std() * math.sqrt(252) * 100
    cur = roll.iloc[-1]
    hist = roll.dropna().tail(lookback)
    if not np.isfinite(cur) or len(hist) < 40:
        return None
    return float((hist <= cur).mean() * 100)


def _load_price_panel() -> tuple[pd.DataFrame, dict[str, dict]]:
    """Prefer us_movers cache; fill gaps via yfinance."""
    from us_movers import get_price_panel, load_universe

    close, uni = get_price_panel(force=False)
    meta = {**load_universe(), **{k: v for k, v in CORE_UNIVERSE.items()}}
    missing = [s for s in CORE_UNIVERSE if s not in close.columns]
    if missing:
        try:
            import yfinance as yf

            extra = net_limits.yf_download(
                missing,
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            frames = {}
            if isinstance(extra.columns, pd.MultiIndex):
                for s in missing:
                    try:
                        if s in extra.columns.get_level_values(0):
                            part = extra[s]["Close"].dropna()
                            if not part.empty:
                                frames[s] = part
                    except Exception:
                        continue
            elif "Close" in extra.columns and len(missing) == 1:
                frames[missing[0]] = extra["Close"].dropna()
            if frames:
                add = pd.DataFrame(frames)
                close = close.join(add, how="outer") if not close.empty else add
        except Exception:
            logger.debug("yfinance fill failed", exc_info=True)
    return close.sort_index(), meta


def _pick_expiry(options: tuple[str, ...] | list[str], min_dte: int = 14, max_dte: int = 45) -> str | None:
    if not options:
        return None
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    best = None
    best_diff = 10**9
    target = 30
    for exp in options:
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
        except Exception:
            continue
        dte = (d - today).days
        if dte < min_dte or dte > max_dte:
            continue
        diff = abs(dte - target)
        if diff < best_diff:
            best_diff = diff
            best = exp
    if best:
        return best
    # fallback: first expiry with dte>=7
    for exp in options:
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
            if (d - today).days >= 7:
                return exp
        except Exception:
            continue
    return options[0] if options else None



def _estimate_iv_pct(spot: float, strike: float, mid: float, dte: int) -> float | None:
    """Rough IV% from option mid when Yahoo impliedVolatility is missing/junk.

    Brenner–Subrahmanyam style ATM approximation, with a light OTM haircut.
    Good enough for IV贵贱 tagging; not a pricing engine.
    """
    try:
        if spot <= 0 or mid <= 0 or dte is None or dte <= 0:
            return None
        T = max(dte, 1) / 365.0
        # ATM-ish premium ratio
        prem = mid / spot
        iv = (2.0 * math.pi / T) ** 0.5 * prem * 100.0
        # OTM puts are cheaper → lift a bit so we don't systematically understate
        moneyness = strike / spot
        if moneyness < 0.97:
            iv *= 1.0 + min(0.35, (0.97 - moneyness) * 2.5)
        if iv < 3 or iv > 400:
            return None
        return float(iv)
    except Exception:
        return None


def _normalize_iv_pct(iv_raw: float, spot: float, strike: float, mid: float, dte: int) -> tuple[float | None, str | None]:
    """Convert Yahoo IV to percent; replace junk with estimate from mid.

    Returns (iv_pct, source) where source is yahoo|est|None.
    """
    try:
        iv_raw = float(iv_raw or 0)
    except Exception:
        iv_raw = 0.0
    iv = iv_raw * 100 if 0 < iv_raw <= 5 else iv_raw
    if iv <= 1 or iv > 400:
        iv = None
    est = _estimate_iv_pct(spot, strike, mid, dte)
    # Yahoo often parks near-zero IV on ETF puts that still have a last/mid.
    if iv is None and est is not None:
        return est, "est"
    if iv is not None and est is not None and iv < max(8.0, est * 0.45):
        return est, "est"
    if iv is not None:
        return iv, "yahoo"
    return None, None


def _option_put_idea(symbol: str, spot: float) -> dict | None:
    """Pick an OTM short-put candidate (~5–12% OTM) on ~30 DTE + vol structure + chain slice."""
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        options = list(t.options or ())
        if spot <= 0:
            return None
        # try several candidate expiries until we get a chain with strikes near spot
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
        puts = None
        chain = None
        fallback = None  # (score, exp, puts)
        for cand in candidates:
            try:
                chain = t.option_chain(cand)
                ptmp = chain.puts.copy()
                if ptmp.empty:
                    continue
                strikes = ptmp["strike"].fillna(0).astype(float)
                near_mask = (strikes >= spot * 0.70) & (strikes <= spot * 1.05)
                near_n = int(near_mask.sum())
                # Skip stale pre-split chains (e.g. SOXX 300+ strikes while spot ~132).
                if near_n < 2:
                    continue
                ivs = ptmp["impliedVolatility"].fillna(0).astype(float)
                bids = ptmp["bid"].fillna(0).astype(float)
                asks = ptmp["ask"].fillna(0).astype(float)
                lasts = ptmp["lastPrice"].fillna(0).astype(float)
                liquid = int(((bids + asks > 0) | (lasts > 0)).sum())
                usable_iv = int((ivs > 0.03).sum())
                score = near_n * 3 + liquid + usable_iv
                if liquid >= 3 or usable_iv >= 3 or near_n >= 5:
                    exp, puts = cand, ptmp
                    break
                if fallback is None or score > fallback[0]:
                    fallback = (score, cand, ptmp)
            except Exception:
                continue
        if (not exp or puts is None or puts.empty) and fallback is not None:
            _, exp, puts = fallback
        if not exp or puts is None or puts.empty:
            return None
        # target 8% OTM (cash-secured put zone)
        target = spot * 0.92
        puts["dist"] = (puts["strike"] - target).abs()
        otm = puts[puts["strike"] < spot * 0.995].copy()
        if otm.empty:
            otm = puts
        # prefer quotes with real bid/ask and non-zero IV
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
        yield_ann = (mid / strike * 365 / max(dte, 1) * 100) if strike > 0 and mid > 0 else None
        premium_pct = mid / spot * 100 if spot else None
        if mid <= 0 or strike <= 0:
            return None
        if strike >= spot or strike < spot * 0.70:
            return None
        if mid >= strike * 0.45:
            return None
        if yield_ann is not None and yield_ann > 250:
            yield_ann = 250.0
        iv, iv_source = _normalize_iv_pct(float(row.get("impliedVolatility") or 0), spot, strike, mid, dte)

        # ATM IV + skew from same expiry
        atm_iv = None
        try:
            puts2 = puts.copy()
            puts2["adist"] = (puts2["strike"] - spot).abs()
            puts2["_iv"] = puts2["impliedVolatility"].fillna(0).astype(float)
            puts2["_bid"] = puts2["bid"].fillna(0).astype(float)
            puts2["_ask"] = puts2["ask"].fillna(0).astype(float)
            puts2["_ok"] = ((puts2["_iv"] > 0.03) & (puts2["_bid"] + puts2["_ask"] > 0)).astype(int)
            atm_row = puts2.sort_values(["_ok", "adist"], ascending=[False, True]).iloc[0]
            atm_mid = float(atm_row.get("bid") or 0) + float(atm_row.get("ask") or 0)
            atm_mid = atm_mid / 2 if atm_mid else float(atm_row.get("lastPrice") or 0)
            atm_iv, _atm_src = _normalize_iv_pct(
                float(atm_row.get("impliedVolatility") or 0),
                spot,
                float(atm_row.get("strike") or strike),
                atm_mid or mid,
                dte,
            )
            atm_iv = atm_iv or iv
        except Exception:
            atm_iv = iv

        # Prefer ATM IV when Yahoo marks the OTM strike with junk near-zero IV.
        if atm_iv is not None and (iv is None or iv < atm_iv * 0.85):
            iv = atm_iv
            iv_source = (iv_source or "yahoo") if iv_source == "est" else "yahoo_atm"
        if iv is None and atm_iv is not None:
            iv = atm_iv
            iv_source = "yahoo_atm"

        # farther expiry for term structure (~60 DTE)
        far_iv = None
        far_exp = None
        try:
            today = datetime.now(timezone.utc).replace(tzinfo=None).date()
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
                fputs = fchain.puts.copy()
                if not fputs.empty:
                    fputs["adist"] = (fputs["strike"] - spot).abs()
                    fputs["_iv"] = fputs["impliedVolatility"].fillna(0).astype(float)
                    fputs["_ok"] = (fputs["_iv"] > 0.03).astype(int)
                    iv_raw = float(fputs.sort_values(["_ok", "adist"], ascending=[False, True]).iloc[0].get("impliedVolatility") or 0)
                    far_iv = iv_raw * 100 if iv_raw <= 5 else iv_raw
                    if far_iv > 400:
                        far_iv = 400.0
                    if far_iv <= 1:
                        far_iv = None
        except Exception:
            far_iv = None

        # chain slice for drill-down: strikes from ~85% to 100% spot
        chain_slice = []
        try:
            band = puts[(puts["strike"] >= spot * 0.82) & (puts["strike"] <= spot * 1.01)].copy()
            band = band.sort_values("strike", ascending=False).head(14)
            for _, rr in band.iterrows():
                b = float(rr.get("bid") or 0)
                a = float(rr.get("ask") or 0)
                m = (b + a) / 2 if b or a else float(rr.get("lastPrice") or 0)
                k = float(rr["strike"])
                civ = float(rr.get("impliedVolatility") or 0) * 100
                chain_slice.append(
                    {
                        "strike": _r(k, 2),
                        "bid": _r(b, 2),
                        "ask": _r(a, 2),
                        "mid": _r(m, 2),
                        "iv": _r(min(civ, 400), 1),
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
            "capital": _r(strike * 100, 0),
            "atm_iv": _r(atm_iv, 1),
            "near_iv": _r(atm_iv if atm_iv is not None else iv, 1),
            "far_iv": _r(far_iv, 1),
            "far_expiry": far_exp,
            "otm_put_iv": _r(iv, 1),
            "chain": chain_slice,
        }
    except Exception as exc:
        logger.debug("option idea %s failed: %s", symbol, exc)
        return None




_earnings_cache: dict[str, dict | None] = {}


def _next_earnings(symbol: str) -> dict | None:
    """Return next earnings date info if available."""
    if symbol in _earnings_cache:
        return _earnings_cache[symbol]
    out = None
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        dates = []
        try:
            cal = t.calendar
        except Exception:
            cal = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)):
                dates.extend(ed)
            elif ed is not None:
                dates.append(ed)
        try:
            edf = t.get_earnings_dates(limit=8)
            if edf is not None and not edf.empty:
                for idx in edf.index:
                    try:
                        dates.append(idx.to_pydatetime().date() if hasattr(idx, "to_pydatetime") else idx.date())
                    except Exception:
                        continue
        except Exception:
            pass
        future = []
        for d in dates:
            try:
                if hasattr(d, "date") and not isinstance(d, type(today)):
                    d = d.date()
                d = d if isinstance(d, type(today)) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if d >= today:
                future.append(d)
        if future:
            nxt = min(future)
            out = {"date": nxt.isoformat(), "days": (nxt - today).days}
    except Exception as exc:
        logger.debug("earnings %s: %s", symbol, exc)
        out = None
    _earnings_cache[symbol] = out
    return out


def _iv_tag(spread: float | None) -> str:
    if spread is None:
        return "IV未知"
    if spread >= 8:
        return "IV偏贵"
    if spread <= -5:
        return "IV偏便宜"
    return "IV中性"


def _build_risk(row: dict, budget: float | None = None) -> dict:
    put = row.get("put") or {}
    budget = float(budget or ACCOUNT_DEFAULTS["budget_usd"])
    max_pct = float(ACCOUNT_DEFAULTS["max_name_pct"])
    cap = int(ACCOUNT_DEFAULTS["max_contracts_cap"])
    capital = put.get("capital")
    if capital is None and put.get("strike"):
        capital = float(put["strike"]) * 100
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
        flags.append("超卖刀口")
    if put.get("open_interest") is not None and put["open_interest"] < 100:
        flags.append("OI偏低")
    spread = row.get("iv_hv_spread")
    iv_tag = _iv_tag(spread)
    put = row.get("put") or {}
    if iv_tag != "IV未知" and put.get("iv_source") == "est":
        iv_tag = iv_tag.replace("IV", "IV估")
    elif iv_tag == "IV未知" and put.get("iv") is not None and row.get("hv20"):
        # recompute if put has iv but spread missing
        spread = _r(float(put["iv"]) - float(row["hv20"]), 1)
        row["iv_hv_spread"] = spread
        iv_tag = _iv_tag(spread)
        if put.get("iv_source") == "est":
            iv_tag = iv_tag.replace("IV", "IV估")
    if "偏便宜" in iv_tag:
        flags.append("IV偏便宜")
    level = "中"
    if any(x in flags for x in ("财报≤7天", "超卖刀口", "极高波动")):
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
        "margin_note": "CSP按现金担保估算（行权价×100）；券商保证金规则可能更松或更严",
        "iv_tag": iv_tag,
        "iv_hv_spread": spread,
        "earnings": earn,
        "flags": flags,
        "level": level,
    }


def _score_row(row: dict) -> float:
    """Higher = better short-put candidate among high-vol names."""
    hv = row.get("hv20") or 0
    hv_pct = row.get("hv_pctile") or 50
    rsi = row.get("rsi14")
    chg5 = row.get("chg_5d") or 0
    chg21 = row.get("chg_21d") or 0
    put = row.get("put") or {}
    iv = put.get("iv")
    yld = put.get("yield_ann_pct") or 0
    otm = abs(put.get("otm_pct") or 8)
    oi = put.get("open_interest") or 0

    score = 0.0
    # want elevated vol (premium) but not absurd crash names only
    if hv >= 25:
        score += min(25, (hv - 20) * 0.35)
    if hv_pct >= 60:
        score += (hv_pct - 50) * 0.25
    # RSI sweet spot for selling puts: mild weakness 32–48
    if rsi is not None:
        if 32 <= rsi <= 48:
            score += 18
        elif 25 <= rsi < 32:
            score += 10
        elif 48 < rsi <= 58:
            score += 8
        elif rsi < 25:
            score -= 8  # knife
        elif rsi > 70:
            score -= 6  # chase
    # prefer not free-falling
    if chg5 <= -18:
        score -= 15
    elif -12 <= chg5 <= -3:
        score += 8
    elif -3 < chg5 <= 3:
        score += 4
    if chg21 <= -30:
        score -= 10
    # option premium
    if yld:
        score += min(20, yld * 0.35)
    if iv and hv:
        # IV rich vs HV
        score += min(12, max(-5, (iv - hv) * 0.25))
    if 5 <= otm <= 12:
        score += 6
    if oi >= 500:
        score += 5
    elif oi >= 100:
        score += 2
    # ETF slight liquidity bonus
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
    if row.get("put") and row["put"].get("iv") is not None and row.get("hv20"):
        spread = row["put"]["iv"] - row["hv20"]
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
        return "谨慎抄底"
    return "暂不推荐"


def _action(row: dict) -> str:
    put = row.get("put") or {}
    if not put:
        return "期权链暂不可用；可等波动回落后再看 CSP。"
    strike = put.get("strike")
    exp = put.get("expiry")
    mid = put.get("mid")
    yld = put.get("yield_ann_pct")
    parts = [f"建议卖出 {exp} ${strike} Put"]
    if mid is not None:
        parts.append(f"权利金约 ${mid}")
    if yld is not None:
        parts.append(f"静态年化约 {yld}%")
    earn = row.get("earnings") or {}
    if earn.get("days") is not None:
        parts.append(f"距财报约 {earn['days']} 天({earn.get('date')})")
    parts.append("仅作研究参考，注意保证金与事件风险。")
    return "；".join(parts)


def _load_seed_snapshot() -> dict | None:
    """Cold-start fallback from exported Pages JSON (bundled in repo)."""
    for rel in ("docs/api/short_put.json", "data/short_put_seed.json"):
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


def build_short_put_screen(force: bool = False, top_n: int = 20, option_n: int = 28) -> dict:
    global _cache
    if not force and _cache and time.time() - _cache[0] < CACHE_TTL:
        return _cache[1]

    # First hit on a fresh dyno: serve last export while a live scan would take minutes.
    if not force and _cache is None:
        seed = _load_seed_snapshot()
        if seed is not None:
            _cache = (time.time(), seed)
            bg_tasks.submit("short-put", lambda: build_short_put_screen(force=True, top_n=top_n, option_n=option_n))
            return seed

    close, meta = _load_price_panel()
    symbols = [s for s in CORE_UNIVERSE if s in close.columns]
    # Theme ETFs (TLT/HYG/GLD/…) must stay even when HV is quiet.
    theme_force = set()
    for _meta in THEME_LINKS.values():
        theme_force.update(_meta.get("symbols") or [])
    for s in theme_force:
        if s in close.columns and s not in symbols:
            symbols.append(s)
    # also add other high-HV liquid names from panel that are stock/etf (not lev)
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
        # Quiet bond/gold names stay for theme panels; HV screen still focuses elevated vol.
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
            "put": None,
        }
        rows.append(row)

    # rank by HV first, fetch options for top option_n
    rows.sort(key=lambda r: (r.get("hv_pctile") or 0) * 0.5 + (r.get("hv20") or 0) * 0.5, reverse=True)
    # Always include theme-linked names (GLD/UNG/…) even if HV rank is lower.
    theme_syms = []
    for meta in THEME_LINKS.values():
        theme_syms.extend(meta.get("symbols") or [])
    by_sym = {r["symbol"]: r for r in rows}
    targets = []
    seen = set()
    for s in theme_syms:
        if s in by_sym and s not in seen:
            targets.append(by_sym[s])
            seen.add(s)
    for r in rows:
        if r["symbol"] in seen:
            continue
        targets.append(r)
        seen.add(r["symbol"])
        if len(targets) >= max(option_n, len(seen & set(theme_syms)) + option_n):
            # keep theme + top option_n others
            break
    # Cap: themes + option_n
    theme_set = set(theme_syms)
    tops = [r for r in rows if r["symbol"] not in theme_set][:option_n]
    targets = []
    seen = set()
    for r in [by_sym[s] for s in theme_syms if s in by_sym] + tops:
        if r["symbol"] in seen:
            continue
        targets.append(r)
        seen.add(r["symbol"])

    # Seed fallback map (previous successful option ideas)
    seed_puts: dict[str, dict] = {}
    try:
        seed = _load_seed_snapshot() or {}
        for bucket in ("picks", "watchlist", "all"):
            for rr in seed.get(bucket) or []:
                if rr.get("symbol") and rr.get("put"):
                    seed_puts[rr["symbol"]] = rr["put"]
        for th in seed.get("themes") or []:
            for rr in th.get("items") or []:
                if rr.get("symbol") and rr.get("put"):
                    seed_puts[rr["symbol"]] = rr["put"]
    except Exception:
        seed_puts = {}

    def fetch(r: dict) -> dict:
        idea = _option_put_idea(r["symbol"], r["price"] or 0)
        if idea is None and r["symbol"] in seed_puts:
            idea = dict(seed_puts[r["symbol"]])
            idea["from_seed"] = True
        r["put"] = idea
        return r

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(fetch, r) for r in targets]
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception:
                continue

    # earnings for option targets (and top HV rows without options stay None)
    def earn_one(r: dict) -> None:
        if r.get("kind") == "etf":
            r["earnings"] = None  # ETFs generally no single earnings
            return
        r["earnings"] = _next_earnings(r["symbol"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(earn_one, targets))

    for r in rows:
        if r.get("put") and r["put"].get("iv") is not None and r.get("hv20"):
            r["iv_hv_spread"] = _r(r["put"]["iv"] - r["hv20"], 1)
        r["score"] = _r(_score_row(r), 1)
        r["bias_cn"] = _bias_cn(r)
        r["action"] = _action(r)
        r["risk"] = _build_risk(r)
        put = r.get("put") or {}
        try:
            from put_edge import compute_edge, build_checklist

            hv20 = r.get("hv20")
            iv = _sane_iv(put.get("iv"), hv20)
            atm = _sane_iv(put.get("atm_iv"), hv20) or iv
            near = _sane_iv(put.get("near_iv"), hv20) or atm or iv
            far = _sane_iv(put.get("far_iv"), hv20)
            otm_iv = _sane_iv(put.get("otm_put_iv"), hv20) or iv
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
            # stash structure metrics for client reweight
            if r["edge"].get("metrics") is not None:
                r["edge"]["metrics"]["near_iv"] = put.get("near_iv")
                r["edge"]["metrics"]["far_iv"] = put.get("far_iv")
                r["edge"]["metrics"]["atm_iv"] = put.get("atm_iv")
                r["edge"]["metrics"]["otm_put_iv"] = put.get("otm_put_iv")
            r["checklist"] = build_checklist(r)
            # soft boost / penalty on score
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

    ranked = sorted(rows, key=lambda r: r.get("score") or -1, reverse=True)
    eligible = [r for r in ranked if (r.get("score") or 0) >= 22 and r.get("put")]
    openable = [r for r in eligible if not ((r.get("checklist") or {}).get("blocked"))]
    picks = (openable + [r for r in eligible if r not in openable])[:top_n]
    watch = [r for r in ranked if r not in picks][:15]

    # theme link enrichment: attach live rows when present
    all_by_sym = {r["symbol"]: r for r in ranked}
    themes = []
    for key, meta in THEME_LINKS.items():
        items = []
        for s in meta["symbols"]:
            if s in all_by_sym:
                items.append(all_by_sym[s])
            elif s in CORE_UNIVERSE:
                stub = {
                    "symbol": s,
                    "name": CORE_UNIVERSE[s]["name"],
                    "kind": CORE_UNIVERSE[s]["kind"],
                    "sector": CORE_UNIVERSE[s].get("sector") or "",
                    "missing": True,
                    "put": None,
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
                            stub["put"] = _option_put_idea(s, px)
                            if stub.get("put"):
                                stub["score"] = _r(_score_row(stub), 1)
                                stub["bias_cn"] = _bias_cn(stub)
                                stub["action"] = _action(stub)
                                stub["risk"] = _build_risk(stub)
                except Exception:
                    logger.debug("theme stub enrich failed for %s", s, exc_info=True)
                items.append(stub)
        themes.append({
            "id": key,
            "label": meta["label"],
            "symbols": meta["symbols"],
            "keywords": meta.get("keywords") or [],
            "items": items,
        })

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "method": {
            "goal": "高波动美股/ETF 的现金担保卖出看跌（CSP / Short Put）候选",
            "vol": "HV20/HV60 为近20/60日收益年化波动；hv_pctile 为近一年分位",
            "option": "优先选取约 14–45 DTE、约 8% OTM 的看跌期权；权利金取买卖中间价",
            "score": "综合：高波动溢价 + RSI 回调甜蜜区 + 非自由落体 + 权利金年化 + IV相对HV + 流动性 − 临近财报",
            "risk": "风控层标注 IV贵贱、财报窗口、CSP占用、建议合约上限；杠杆ETF已排除",
            "filters": "前端可按 HV分位/RSI/OI/年化/OTM/类型/仅核心池 过滤",
            "edge": "Edge分=VRP+IV分位+期限结构+RV稳定性+Skew；检查清单含硬否决(财报/倒挂/RV加速/自由落体等)",
            "chain": "卡片可下钻同到期看跌链，权重滑条可重算Edge",
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
        "picks": picks,
        "watchlist": watch,
        "all": ranked[:60],
        "themes": themes,
        "core_symbols": sorted(CORE_UNIVERSE.keys()),
        "disclaimer": "研究用途，非投资建议。期权有杠杆与流动性风险，请独立判断。",
    }
    _cache = (time.time(), payload)
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = build_short_put_screen(force=True, top_n=12, option_n=20)
    print("updated", out["updated_at"], "picks", len(out["picks"]), "universe", out["universe_size"])
    for r in out["picks"][:8]:
        p = r.get("put") or {}
        print(
            f"{r['symbol']:6} score={r['score']} HV={r['hv20']} RSI={r['rsi14']} "
            f"put={p.get('strike')}@{p.get('expiry')} mid={p.get('mid')} yld={p.get('yield_ann_pct')} {r['bias_cn']}"
        )
