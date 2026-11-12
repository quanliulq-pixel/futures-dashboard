# -*- coding: utf-8 -*-
"""A-share index location / volatility analysis (Yahoo has no CN option chains)."""
from __future__ import annotations

import net_limits

import bg_tasks

import json
import logging
import math
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from short_put_screen import _hv, _hv_percentile, _r, _rsi

logger = logging.getLogger(__name__)

CACHE_TTL = 30 * 60
_cache: tuple[float, dict] | None = None

CN_INDICES = {
    "000300.SS": {
        "id": "csi300",
        "name": "沪深300",
        "short": "CSI300",
        "futures_id": "IF0",
        "futures_name": "沪深300股指期货",
    },
    "000016.SS": {
        "id": "sse50",
        "name": "上证50",
        "short": "SSE50",
        "futures_id": "IH0",
        "futures_name": "上证50股指期货",
    },
    "000852.SS": {
        "id": "csi1000",
        "name": "中证1000",
        "short": "CSI1000",
        "futures_id": "IM0",
        "futures_name": "中证1000股指期货",
    },
}

OPTIONAL_FUTURES = {
    "IC0": {"name": "中证500股指期货", "related": "中证500"},
}

SATELLITES = {
    "FXI": {"name": "中国大型股 ETF", "note": "美股上市，跟踪中国大型股，非A股指数本身"},
    "KWEB": {"name": "中概互联 ETF", "note": "美股上市中概互联网，可作情绪对照"},
}


def _bias_index(row: dict) -> str:
    score = 0
    vs20 = row.get("vs_ma20_pct")
    vs60 = row.get("vs_ma60_pct")
    rsi = row.get("rsi14")
    chg21 = row.get("chg_21d")
    if vs20 is not None:
        if vs20 >= 3:
            score += 2
        elif vs20 <= -3:
            score -= 2
    if vs60 is not None:
        if vs60 >= 4:
            score += 1
        elif vs60 <= -4:
            score -= 1
    if rsi is not None:
        if rsi >= 60:
            score += 1
        elif rsi <= 40:
            score -= 1
    if chg21 is not None:
        if chg21 >= 5:
            score += 1
        elif chg21 <= -5:
            score -= 1
    if score >= 3:
        return "偏强"
    if score <= -3:
        return "偏弱"
    return "中性"


def _row_from_series(yahoo: str, series: pd.Series, meta: dict) -> dict | None:
    series = series.dropna()
    if len(series) < 30:
        return None
    px = float(series.iloc[-1])
    ma20 = float(series.tail(20).mean()) if len(series) >= 20 else None
    ma60 = float(series.tail(60).mean()) if len(series) >= 60 else None
    win = series.tail(min(252, len(series)))
    hi = float(win.max()) if len(win) else None
    lo = float(win.min()) if len(win) else None
    row = {
        "yahoo": yahoo,
        "id": meta["id"],
        "name": meta["name"],
        "short": meta["short"],
        "price": _r(px, 2),
        "chg_1d": _r(float(series.pct_change().iloc[-1] * 100), 2),
        "chg_5d": _r(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 2) if len(series) >= 6 else None,
        "chg_21d": _r(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 2) if len(series) >= 22 else None,
        "hv20": _r(_hv(series, 20), 1),
        "hv60": _r(_hv(series, 60), 1),
        "hv_pctile": _r(_hv_percentile(series), 1),
        "rsi14": _r(_rsi(series), 1),
        "vs_ma20_pct": _r((px / ma20 - 1) * 100, 2) if ma20 else None,
        "vs_ma60_pct": _r((px / ma60 - 1) * 100, 2) if ma60 else None,
        "high_52w": _r(hi, 2),
        "low_52w": _r(lo, 2),
        "from_52w_high_pct": _r((px / hi - 1) * 100, 2) if hi else None,
        "from_52w_low_pct": _r((px / lo - 1) * 100, 2) if lo else None,
        "futures_id": meta.get("futures_id"),
        "futures_name": meta.get("futures_name"),
        "futures": None,
    }
    row["bias_cn"] = _bias_index(row)
    return row


def _satellite_from_series(symbol: str, series: pd.Series, meta: dict) -> dict | None:
    series = series.dropna()
    if len(series) < 30:
        return None
    px = float(series.iloc[-1])
    ma20 = float(series.tail(20).mean()) if len(series) >= 20 else None
    return {
        "symbol": symbol,
        "name": meta["name"],
        "note": meta["note"],
        "kind": "us_etf",
        "price": _r(px, 2),
        "chg_1d": _r(float(series.pct_change().iloc[-1] * 100), 2),
        "chg_5d": _r(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 2) if len(series) >= 6 else None,
        "chg_21d": _r(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 2) if len(series) >= 22 else None,
        "hv20": _r(_hv(series, 20), 1),
        "hv60": _r(_hv(series, 60), 1),
        "hv_pctile": _r(_hv_percentile(series), 1),
        "rsi14": _r(_rsi(series), 1),
        "vs_ma20_pct": _r((px / ma20 - 1) * 100, 2) if ma20 else None,
    }


def _attach_futures(rows: list[dict]) -> None:
    quotes = []
    try:
        from data_sources import get_quotes

        quotes = get_quotes(force=False) or []
    except Exception:
        logger.debug("cn index futures xref via quotes failed", exc_info=True)
        quotes = []
    by_sym = {}
    for q in quotes:
        for key in (q.get("symbol"), q.get("id"), q.get("code")):
            if key:
                by_sym[str(key)] = q
    want = {m["futures_id"]: m for m in CN_INDICES.values()}
    want.update({k: v for k, v in OPTIONAL_FUTURES.items()})
    extras = []
    for fid, meta in want.items():
        q = by_sym.get(fid) or by_sym.get(fid.lower())
        if not q:
            continue
        info = {
            "id": fid,
            "name": meta.get("futures_name") or meta.get("name") or fid,
            "price": q.get("price"),
            "change_pct": q.get("change_pct") or q.get("chg_pct"),
            "available": True,
        }
        for row in rows:
            if row.get("futures_id") == fid:
                row["futures"] = info
        if fid in OPTIONAL_FUTURES:
            extras.append(info)
    if extras:
        # stash on first row container later
        pass
    # store extras via function attribute
    _attach_futures.extras = extras  # type: ignore[attr-defined]


AK_INDEX_MAP = {
    "000300.SS": "sh000300",
    "000016.SS": "sh000016",
    "000852.SS": "sh000852",
}


def _series_from_ak(symbol: str) -> pd.Series | None:
    """Yahoo often returns only the latest print for CN indices; use akshare history."""
    import os
    # East-Asia quote hosts often hang from US Render free dynos — never block the request path.
    if os.environ.get("RENDER") or os.environ.get("SKIP_AKSHARE") == "1":
        return None
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or df.empty:
        return None
    colmap = {c.lower(): c for c in df.columns}
    date_col = colmap.get("date") or ("date" if "date" in df.columns else df.columns[0])
    close_col = colmap.get("close") or ("close" if "close" in df.columns else None)
    if close_col is None:
        return None
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    s = pd.Series(out[close_col].astype(float).values, index=pd.DatetimeIndex(out[date_col])).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s if len(s) else None


def _download_close(tickers: list[str]) -> pd.DataFrame:
    frames = {}
    cn = [s for s in tickers if s in AK_INDEX_MAP]
    us = [s for s in tickers if s not in AK_INDEX_MAP]
    for yahoo in cn:
        try:
            part = _series_from_ak(AK_INDEX_MAP[yahoo])
            if part is not None and len(part) >= 30:
                frames[yahoo] = part.tail(320)
                continue
        except Exception:
            logger.debug("akshare index failed for %s", yahoo, exc_info=True)
        # fallback: Yahoo quote-only or short history
        try:
            import yfinance as yf
            hist = net_limits.yf_history(yf.Ticker(yahoo), period="1y")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                frames[yahoo] = hist["Close"].dropna()
        except Exception:
            logger.debug("yahoo fallback failed for %s", yahoo, exc_info=True)
    if us:
        try:
            import yfinance as yf
            extra = net_limits.yf_download(
                us,
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if isinstance(extra.columns, pd.MultiIndex):
                for s in us:
                    try:
                        if s in extra.columns.get_level_values(0):
                            part = extra[s]["Close"].dropna()
                            if not part.empty:
                                frames[s] = part
                    except Exception:
                        continue
            elif "Close" in extra.columns and len(us) == 1:
                frames[us[0]] = extra["Close"].dropna()
        except Exception:
            logger.debug("yahoo satellite download failed", exc_info=True)
    return pd.DataFrame(frames).sort_index()


def _load_seed_snapshot() -> dict | None:
    path = Path(__file__).resolve().parent / "docs" / "api" / "cn_indices.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("indices"):
            data = dict(data)
            data["source"] = "seed"
            return data
    except Exception:
        logger.debug("cn indices seed load failed", exc_info=True)
    return None


def _index_fundamentals() -> dict | None:
    """A股指数基本面 bundle（政策/估值/流动性/宏观）；失败不致命。只在后台/导出路径调用。"""
    import os

    if os.environ.get("SKIP_INDEX_FUND") == "1":
        return None
    try:
        from cn_index_fundamentals import DEFAULT_PROVIDER

        return DEFAULT_PROVIDER.refresh()
    except Exception:  # noqa: BLE001
        logger.warning("cn index fundamentals failed", exc_info=True)
        return None


def _index_flows() -> dict | None:
    """A股指数资金进出 bundle（主力净流入/ETF申赎/两融/北向/机构ETF持仓）；只做展示，失败不致命。只在后台/导出路径调用。"""
    import os

    if os.environ.get("SKIP_INDEX_FLOW") == "1":
        return None
    try:
        from cn_index_flow import DEFAULT_FLOW_PROVIDER

        return DEFAULT_FLOW_PROVIDER.refresh()
    except Exception:  # noqa: BLE001
        logger.warning("cn index flows failed", exc_info=True)
        return None


def build_cn_index_analysis(force: bool = False) -> dict:
    global _cache
    if not force and _cache and time.time() - _cache[0] < CACHE_TTL:
        return _cache[1]
    if not force and _cache is None:
        seed = _load_seed_snapshot()
        if seed is not None:
            _cache = (time.time(), seed)
            bg_tasks.submit("cn-index-analysis", lambda: build_cn_index_analysis(force=True))
            return seed

    tickers = list(CN_INDICES.keys()) + list(SATELLITES.keys())
    close = _download_close(tickers)
    indices = []
    for yahoo, meta in CN_INDICES.items():
        if yahoo not in close.columns:
            logger.warning("missing CN index series: %s", yahoo)
            continue
        row = _row_from_series(yahoo, close[yahoo], meta)
        if row:
            indices.append(row)
    _attach_futures.extras = []  # type: ignore[attr-defined]
    _attach_futures(indices)
    satellites = []
    for sym, meta in SATELLITES.items():
        if sym not in close.columns:
            continue
        row = _satellite_from_series(sym, close[sym], meta)
        if row:
            satellites.append(row)

    if len(indices) < 3:
        seed = _load_seed_snapshot()
        if seed and seed.get("indices"):
            # Keep seed index cards; refresh satellites if we got them.
            seed = dict(seed)
            if satellites:
                seed["satellites"] = satellites
                seed["note"] = (seed.get("note") or "") + " · 指数历史源超时，卡片沿用快照"
            fund = _index_fundamentals()
            if fund is not None:
                seed["index_fundamentals"] = fund
            flows = _index_flows()
            if flows is not None:
                seed["index_flows"] = flows
            _cache = (time.time(), seed)
            return seed

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "method": {
            "goal": "沪深300 / 上证50 / 中证1000 的指数波动与位置分析",
            "source": "指数日线优先 akshare，Render 上改用快照/Yahoo；卫星 ETF 用 Yahoo",
            "options": "交易建议用中金所 IO/HO/MO 近月 ATM IV（新浪转播，估算值）计算波动溢价；页面不展示期权链",
            "stats": "HV20/HV60、HV分位、RSI14、相对均线、近1/5/21日涨跌、距52周高低点",
            "futures": "若看板行情池含 IF0/IH0/IM0/IC0，则附加股指期货对照报价；当前大宗商品池通常不含股指",
            "satellite": "FXI / KWEB 为美股上市中国相关 ETF，仅作对照，不是指数本身",
        },
        "indices": indices,
        "satellites": satellites,
        "futures_extra": getattr(_attach_futures, "extras", []),
        "disclaimer": "研究用途。交易建议中的 IV 为延时转播估算值，请用国内行情核验。",
    }
    fund = _index_fundamentals()
    if fund is not None:
        payload["index_fundamentals"] = fund
        payload["method"]["fundamental"] = "政策/估值/流动性/宏观四组基本面（见建议评分说明），数据日期逐项标注"
    flows = _index_flows()
    if flows is not None:
        payload["index_flows"] = flows
        payload["method"]["flows"] = "资金进出：东方财富主力净流入、上交所ETF份额申赎（估算）、沪深两融融资余额、基金半年报/年报机构ETF持仓；只展示，不计入评分"
    _cache = (time.time(), payload)
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = build_cn_index_analysis(force=True)
    print("updated", out["updated_at"], "indices", len(out["indices"]))
    for r in out["indices"]:
        print(r["name"], r.get("price"), "HV20", r.get("hv20"), r.get("bias_cn"))
