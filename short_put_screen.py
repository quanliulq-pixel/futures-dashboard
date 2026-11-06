# -*- coding: utf-8 -*-
"""High-volatility US stocks/ETFs screener for cash-secured / short put ideas."""
from __future__ import annotations

import logging
import math
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

            extra = yf.download(
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


def _option_put_idea(symbol: str, spot: float) -> dict | None:
    """Pick an OTM short-put candidate (~5–12% OTM) on ~30 DTE."""
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        options = t.options or ()
        exp = _pick_expiry(options)
        if not exp or spot <= 0:
            return None
        chain = t.option_chain(exp)
        puts = chain.puts.copy()
        if puts.empty:
            return None
        # target 8% OTM (cash-secured put zone)
        target = spot * 0.92
        puts["dist"] = (puts["strike"] - target).abs()
        # prefer OTM with some OI/volume
        otm = puts[puts["strike"] < spot * 0.995].copy()
        if otm.empty:
            otm = puts
        otm = otm.sort_values(["dist", "openInterest"], ascending=[True, False])
        row = otm.iloc[0]
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        mid = (bid + ask) / 2 if bid or ask else float(row.get("lastPrice") or 0)
        strike = float(row["strike"])
        iv = float(row.get("impliedVolatility") or 0) * 100
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
        otm_pct = (strike / spot - 1) * 100
        # annualized yield approx on cash secured: mid/strike * 365/dte
        yield_ann = (mid / strike * 365 / max(dte, 1) * 100) if strike > 0 and mid > 0 else None
        premium_pct = mid / spot * 100 if spot else None
        if mid <= 0 or strike <= 0:
            return None
        if strike >= spot or strike < spot * 0.70:
            return None  # keep practical CSP zone
        if mid >= strike * 0.45:
            return None
        if yield_ann is not None and yield_ann > 250:
            yield_ann = 250.0
        if iv > 400:
            iv = 400.0
        return {
            "expiry": exp,
            "dte": dte,
            "strike": _r(strike, 2),
            "bid": _r(bid, 2),
            "ask": _r(ask, 2),
            "mid": _r(mid, 2),
            "iv": _r(iv, 1),
            "volume": int(row.get("volume") or 0),
            "open_interest": int(row.get("openInterest") or 0),
            "otm_pct": _r(otm_pct, 1),
            "premium_pct": _r(premium_pct, 2),
            "yield_ann_pct": _r(yield_ann, 1),
            "capital": _r(strike * 100, 0),  # CSP cash per contract
        }
    except Exception as exc:
        logger.debug("option idea %s failed: %s", symbol, exc)
        return None


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
    return float(score)


def _bias_cn(row: dict) -> str:
    s = row.get("score") or 0
    rsi = row.get("rsi14")
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
    parts.append("仅作研究参考，注意保证金与事件风险。")
    return "；".join(parts)


def build_short_put_screen(force: bool = False, top_n: int = 20, option_n: int = 28) -> dict:
    global _cache
    if not force and _cache and time.time() - _cache[0] < CACHE_TTL:
        return _cache[1]

    close, meta = _load_price_panel()
    symbols = [s for s in CORE_UNIVERSE if s in close.columns]
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
        if hv20 is None or hv20 < 18:
            continue  # focus high / elevated vol
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
    targets = rows[:option_n]

    def fetch(r: dict) -> dict:
        idea = _option_put_idea(r["symbol"], r["price"] or 0)
        r["put"] = idea
        return r

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(fetch, r) for r in targets]
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception:
                continue

    for r in rows:
        r["score"] = _r(_score_row(r), 1)
        r["bias_cn"] = _bias_cn(r)
        r["action"] = _action(r)
        # IV premium vs HV
        if r.get("put") and r["put"].get("iv") is not None and r.get("hv20"):
            r["iv_hv_spread"] = _r(r["put"]["iv"] - r["hv20"], 1)

    ranked = sorted(rows, key=lambda r: r.get("score") or -1, reverse=True)
    picks = [r for r in ranked if (r.get("score") or 0) >= 28 and r.get("put")][:top_n]
    watch = [r for r in ranked if r not in picks][:15]

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "method": {
            "goal": "高波动美股/ETF 的现金担保卖出看跌（CSP / Short Put）候选",
            "vol": "HV20/HV60 为近20/60日收益年化波动；hv_pctile 为近一年分位",
            "option": "优先选取约 14–45 DTE、约 8% OTM 的看跌期权；权利金取买卖中间价",
            "score": "综合：高波动溢价 + RSI 回调甜蜜区 + 非自由落体 + 权利金年化 + IV相对HV + 流动性",
            "risk": "卖 Put 有被行权/亏损扩大风险；杠杆ETF已排除；财报/事件前后请降仓或回避",
        },
        "universe_size": len(rows),
        "picks": picks,
        "watchlist": watch,
        "all": ranked[:60],
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
