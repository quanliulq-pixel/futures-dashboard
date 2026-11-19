# -*- coding: utf-8 -*-
"""A-share index location / volatility analysis.

指数日线优先新浪 CN_MarketData.getKLineData，腾讯日线兜底；不用东财行情推送或历史推送接口。
卫星 ETF（FXI / KWEB）仍用 Yahoo。后台增量刷新，请求路径只读快照，失败标「上次快照（日期）」。
"""
from __future__ import annotations

import net_limits

import bg_tasks

import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from short_put_screen import _hv, _hv_percentile, _r, _rsi

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
CLOSE_CUTOFF = dtime(15, 5)
CACHE_TTL = 30 * 60
HTTP_TIMEOUT = 15
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_DATALEN = 400
PRICE_KEEP = 600

ROOT = Path(__file__).resolve().parent
PRICE_SEED_PATH = ROOT / "data" / "cn_index_daily.json"
RUNTIME_PATH = ROOT / "data" / "cn_index_analysis_latest.json"
API_SEED_PATH = ROOT / "docs" / "api" / "cn_indices.json"

METHOD_SOURCE = (
    "指数日线优先新浪 CN_MarketData.getKLineData（腾讯日线兜底），增量合并本地缓存；"
    "卫星 ETF 用 Yahoo。后台刷新，请求路径不联网。未完成交易日（15:05 CST 前）不计入收盘。"
)
DEFAULT_METHOD = {
    "goal": "沪深300 / 上证50 / 中证1000 的指数波动与位置分析",
    "source": METHOD_SOURCE,
    "options": "交易建议用中金所 IO/HO/MO 近月 ATM IV（新浪转播，估算值）计算波动溢价；页面不展示期权链",
    "stats": "HV20/HV60、HV分位、RSI14、相对均线、近1/5/21日涨跌、距52周高低点",
    "futures": "若看板行情池含 IF0/IH0/IM0/IC0，则附加股指期货对照报价；当前大宗商品池通常不含股指",
    "satellite": "FXI / KWEB 为美股上市中国相关 ETF，仅作对照，不是指数本身",
    "fundamental": "政策/估值/流动性/宏观四组基本面（见建议评分说明），数据日期逐项标注",
    "flows": "资金进出：新浪板块资金流向（两市及有板块的指数主力净流入，特大单 r0）、上交所ETF份额申赎（估算）、沪深两融融资余额、基金半年报/年报机构ETF持仓；只展示，不计入评分",
}
DISCLAIMER = "研究用途。交易建议中的 IV 为延时转播估算值，请用国内行情核验。"

_cache: tuple[float, dict] | None = None
_cache_lock = threading.Lock()

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

SINA_SYMBOL = {
    "000300.SS": "sh000300",
    "000016.SS": "sh000016",
    "000852.SS": "sh000852",
}

AK_INDEX_MAP = {
    "000300.SS": "sh000300",
    "000016.SS": "sh000016",
    "000852.SS": "sh000852",
}


def _now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(CST)
    if now.tzinfo is None:
        return now.replace(tzinfo=CST)
    return now.astimezone(CST)


def last_completed_session(now: datetime | None = None) -> date:
    """最近一个已收盘的工作日（15:05 CST 之后才计入当日）。不维护节假日表。"""
    now = _now(now)
    d = now.date()
    if now.timetz().replace(tzinfo=None) < CLOSE_CUTOFF:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def drop_incomplete(pairs: list[tuple[date, float]], now: datetime | None = None) -> list[tuple[date, float]]:
    cutoff = last_completed_session(now)
    return [(d, c) for d, c in (pairs or []) if d <= cutoff]


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


def _asof_from_index(idx: Any) -> str | None:
    if idx is None:
        return None
    ts = pd.Timestamp(idx)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Shanghai")
    return ts.date().isoformat()


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
        "asof": _asof_from_index(series.index[-1]),
        "from_snapshot": False,
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
        "asof": _asof_from_index(series.index[-1]),
        "from_snapshot": False,
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
    _attach_futures.extras = extras  # type: ignore[attr-defined]


def _naive_day_index(series: pd.Series) -> pd.Series:
    """Drop timezone info and normalize to calendar dates.

    akshare CN index history is tz-naive; Yahoo (satellites / CN fallback) is often
    tz-aware. Building a DataFrame from a mix raises
    TypeError: Cannot join tz-naive with tz-aware DatetimeIndex.
    """
    if series is None or getattr(series, "empty", True):
        return series
    s = pd.Series(pd.to_numeric(series, errors="coerce"), copy=True).dropna()
    if s.empty:
        return s
    idx = pd.to_datetime(s.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    s.index = pd.DatetimeIndex(idx).normalize()
    s = s[~s.index.isna()]
    return s[~s.index.duplicated(keep="last")].sort_index()


def _series_from_ak(symbol: str) -> pd.Series | None:
    # akshare is often hung / blocked from some hosts; Sina is the primary path now.
    if os.environ.get("RENDER") or os.environ.get("SKIP_AKSHARE") == "1":
        return None
    try:
        import akshare as ak
    except Exception:
        return None
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
    except Exception:
        logger.debug("akshare index daily failed: %s", symbol, exc_info=True)
        return None
    if df is None or df.empty:
        return None
    close_col = "close" if "close" in df.columns else df.columns[-1]
    date_col = "date" if "date" in df.columns else df.columns[0]
    s = pd.Series(pd.to_numeric(df[close_col], errors="coerce").values, index=pd.to_datetime(df[date_col]))
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s if len(s) >= 30 else None


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v).strip()[:10]
    try:
        return date.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def parse_sina_kline(js: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for row in js or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("day") or row.get("date"))
        c = _num(row.get("close"))
        if d is not None and c is not None:
            out.append((d, c))
    out.sort()
    return out


def parse_tencent_kline(js: Any, symbol: str) -> list[tuple[date, float]]:
    data = js.get("data") if isinstance(js, dict) else None
    if not isinstance(data, dict):
        return []
    block = data.get(symbol) or data.get(symbol.lower()) or data.get(symbol.upper())
    if not isinstance(block, dict):
        # sometimes the only key is the symbol
        if len(data) == 1:
            only = next(iter(data.values()))
            block = only if isinstance(only, dict) else None
        if not isinstance(block, dict):
            return []
    rows = block.get("day") or block.get("qfqday") or block.get("qfqDay") or []
    out: list[tuple[date, float]] = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        d = _to_date(row[0])
        c = _num(row[2])  # [date, open, close, high, low, volume]
        if d is not None and c is not None:
            out.append((d, c))
    out.sort()
    return out


def merge_pairs(*seqs: list[tuple[date, float]]) -> list[tuple[date, float]]:
    by: dict[date, float] = {}
    for seq in seqs:
        for d, v in seq or []:
            by[d] = v
    return sorted(by.items())


def pairs_to_series(pairs: list[tuple[date, float]]) -> pd.Series | None:
    if not pairs:
        return None
    idx = pd.to_datetime([d for d, _ in pairs])
    s = pd.Series([c for _, c in pairs], index=idx, dtype=float).sort_index()
    s = _naive_day_index(s)
    return s if s is not None and len(s) >= 30 else None


def series_to_pairs(series: pd.Series | None) -> list[tuple[date, float]]:
    if series is None or series.empty:
        return []
    out: list[tuple[date, float]] = []
    for idx, val in series.dropna().items():
        d = _to_date(idx)
        c = _num(val)
        if d is not None and c is not None:
            out.append((d, c))
    out.sort()
    return out[-PRICE_KEEP:]


def _get_json(url: str, params: dict | None = None, headers: dict | None = None) -> Any:
    import requests

    r = requests.get(url, params=params, headers=headers or {}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    text = (r.text or "").strip()
    if not text:
        raise ValueError("empty body")
    return json.loads(text)


def fetch_sina_kline(
    symbol: str,
    datalen: int = SINA_DATALEN,
    *,
    get_json: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> list[tuple[date, float]]:
    get_json = get_json or _get_json
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": int(datalen)}
    headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": UA}
    js = get_json(SINA_KLINE_URL, params, headers)
    return drop_incomplete(parse_sina_kline(js), now)


def fetch_tencent_kline(
    symbol: str,
    datalen: int = SINA_DATALEN,
    *,
    get_json: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> list[tuple[date, float]]:
    get_json = get_json or _get_json
    params = {"param": f"{symbol},day,,,{int(datalen)},qfq"}
    headers = {"Referer": "https://gu.qq.com/", "User-Agent": UA}
    js = get_json(TENCENT_KLINE_URL, params, headers)
    return drop_incomplete(parse_tencent_kline(js, symbol), now)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("failed to read %s", path, exc_info=True)
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_price_seed(path: Path | None = None) -> dict[str, list[tuple[date, float]]]:
    data = _load_json(path or PRICE_SEED_PATH) or {}
    out: dict[str, list[tuple[date, float]]] = {}
    closes = data.get("closes") if isinstance(data.get("closes"), dict) else data
    if not isinstance(closes, dict):
        return out
    for yahoo in CN_INDICES:
        rows = closes.get(yahoo) or []
        pairs: list[tuple[date, float]] = []
        for item in rows:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                d, c = _to_date(item[0]), _num(item[1])
            elif isinstance(item, dict):
                d, c = _to_date(item.get("d") or item.get("date")), _num(item.get("c") or item.get("close"))
            else:
                continue
            if d is not None and c is not None:
                pairs.append((d, c))
        if pairs:
            out[yahoo] = merge_pairs(pairs)
    return out


def save_price_seed(closes: dict[str, list[tuple[date, float]]], path: Path | None = None) -> None:
    body = {
        "updated_at": _now().isoformat(timespec="seconds"),
        "source": "sina",
        "closes": {
            yahoo: [[d.isoformat(), c] for d, c in (pairs or [])[-PRICE_KEEP:]]
            for yahoo, pairs in closes.items()
        },
    }
    _atomic_write(path or PRICE_SEED_PATH, json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n")


def _index_fundamentals():
    try:
        from index_fundamentals import DEFAULT_PROVIDER
        return DEFAULT_PROVIDER.refresh() or {}
    except Exception:
        logger.warning("index fundamentals refresh failed", exc_info=True)
        return {}


def _index_flows():
    try:
        from cn_index_flow import refresh as refresh_flows
        return refresh_flows() or {}
    except Exception:
        logger.warning("cn index flow refresh failed", exc_info=True)
        return {}


def snapshot_asof_label(asof: str | None) -> str:
    return f"上次快照（{asof}）" if asof else "上次快照"


def _method_dict(prev: dict | None = None) -> dict:
    out = dict(DEFAULT_METHOD)
    if isinstance((prev or {}).get("method"), dict):
        out.update(prev["method"])
    out["source"] = METHOD_SOURCE
    return out


def payload_asof(payload: dict | None) -> str | None:
    if not payload:
        return None
    if payload.get("asof"):
        return str(payload["asof"])[:10]
    if payload.get("snapshot_asof"):
        return str(payload["snapshot_asof"])[:10]
    dates = []
    for row in (payload.get("indices") or []) + (payload.get("satellites") or []):
        if row.get("asof"):
            dates.append(str(row["asof"])[:10])
    return max(dates) if dates else None


def _mark_snapshot(payload: dict, asof: str | None) -> dict:
    out = dict(payload)
    out["from_snapshot"] = True
    out["snapshot_asof"] = asof
    out["source"] = out.get("source") or "snapshot"
    # 失败时必须标「上次快照（日期）」；不要把超时备注无限追加
    out["note"] = snapshot_asof_label(asof)
    for key in ("indices", "satellites"):
        rows = []
        for row in out.get(key) or []:
            item = dict(row)
            item["from_snapshot"] = True
            if not item.get("asof") and asof:
                item["asof"] = asof
            rows.append(item)
        out[key] = rows
    return out


def _remember(payload: dict) -> dict:
    global _cache
    with _cache_lock:
        _cache = (time.time(), payload)
    return payload


def snapshot() -> dict:
    """请求路径：内存 → 运行时缓存 → 提交的 API seed。不联网。"""
    values: list[dict] = []
    with _cache_lock:
        if _cache:
            values.append(_cache[1])
    for path in (RUNTIME_PATH, API_SEED_PATH):
        data = _load_json(path)
        if data:
            values.append(data)
    for data in values:
        if data.get("indices") and len(data["indices"]) >= 3:
            return data
    return {"ok": False, "indices": [], "satellites": [], "error": "无数据", "note": "无数据"}


def needs_refresh(payload: dict | None, now: datetime | None = None) -> bool:
    now = _now(now)
    if not payload or not payload.get("indices") or len(payload["indices"]) < 3:
        return True
    if payload.get("from_snapshot"):
        return True
    asof = payload_asof(payload)
    if not asof:
        return True
    try:
        ad = date.fromisoformat(asof[:10])
    except ValueError:
        return True
    last = last_completed_session(now)
    if ad >= last:
        return False
    try:
        updated = datetime.fromisoformat(str(payload.get("updated_at")))
        updated = _now(updated) if updated.tzinfo is None else updated.astimezone(CST)
    except (TypeError, ValueError):
        return True
    session_end = datetime.combine(last, CLOSE_CUTOFF, CST)
    if updated >= session_end:
        return False
    return True


def _yahoo_history(ticker: str) -> pd.Series | None:
    try:
        hist = net_limits.yf_history(ticker, period="2y", interval="1d", auto_adjust=True, timeout=12, retries=1)
    except Exception:
        logger.debug("yahoo history failed: %s", ticker, exc_info=True)
        return None
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    s = hist["Close"].dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s if len(s) >= 30 else None


def _download_close(
    tickers: list[str],
    *,
    live: bool = True,
    now: datetime | None = None,
    fetch_sina: Callable[..., list[tuple[date, float]]] | None = None,
    fetch_tencent: Callable[..., list[tuple[date, float]]] | None = None,
    fetch_ak: Callable[..., pd.Series | None] | None = None,
    fetch_yahoo: Callable[..., pd.Series | None] | None = None,
) -> dict[str, pd.Series]:
    """合并本地日线缓存与（可选）新浪/腾讯增量。CN 指数不依赖 Yahoo。"""
    now = _now(now)
    stored = load_price_seed()
    frames: dict[str, pd.Series] = {}
    sina_fn = fetch_sina or fetch_sina_kline
    tencent_fn = fetch_tencent or fetch_tencent_kline
    ak_fn = fetch_ak or _series_from_ak
    yf_fn = fetch_yahoo or _yahoo_history

    for yahoo in tickers:
        if yahoo not in CN_INDICES:
            continue
        pairs = list(stored.get(yahoo) or [])
        if live:
            sym = SINA_SYMBOL.get(yahoo)
            live_pairs: list[tuple[date, float]] = []
            if sym:
                try:
                    live_pairs = sina_fn(sym, now=now) or []
                except Exception:
                    logger.warning("sina kline failed: %s", sym, exc_info=True)
                    live_pairs = []
                if not live_pairs:
                    try:
                        live_pairs = tencent_fn(sym, now=now) or []
                    except Exception:
                        logger.warning("tencent kline failed: %s", sym, exc_info=True)
                        live_pairs = []
            if live_pairs:
                pairs = merge_pairs(pairs, live_pairs)
        series = pairs_to_series(drop_incomplete(pairs, now))
        # 仅在本地缓存+增量仍不够 30 根时，才回退 akshare / Yahoo（不覆盖已有增量）
        if series is None and live:
            fallback_pairs: list[tuple[date, float]] = []
            if not os.environ.get("RENDER"):
                try:
                    ak_s = ak_fn(AK_INDEX_MAP.get(yahoo, yahoo))
                    if ak_s is not None:
                        fallback_pairs = drop_incomplete(series_to_pairs(ak_s), now)
                except Exception:
                    logger.debug("akshare fallback failed: %s", yahoo, exc_info=True)
            if len(fallback_pairs) < 30:
                try:
                    y_s = yf_fn(yahoo)
                    if y_s is not None:
                        fallback_pairs = drop_incomplete(series_to_pairs(y_s), now)
                except Exception:
                    logger.debug("yahoo fallback failed: %s", yahoo, exc_info=True)
            if fallback_pairs:
                series = pairs_to_series(drop_incomplete(merge_pairs(pairs, fallback_pairs), now))
        if series is not None:
            frames[yahoo] = series

    us = [t for t in tickers if t in SATELLITES]
    if live and us:
        for sym in us:
            try:
                s = yf_fn(sym)
            except Exception:
                logger.debug("yahoo satellite failed: %s", sym, exc_info=True)
                s = None
            if s is not None:
                frames[sym] = _naive_day_index(s)
    return frames


def refresh(
    *,
    live: bool = True,
    now: datetime | None = None,
    attach_bundles: bool = False,
    fetch_sina: Callable[..., list[tuple[date, float]]] | None = None,
    fetch_tencent: Callable[..., list[tuple[date, float]]] | None = None,
    fetch_ak: Callable[..., pd.Series | None] | None = None,
    fetch_yahoo: Callable[..., pd.Series | None] | None = None,
    write: bool = True,
    prev: dict | None = None,
) -> dict:
    """增量刷新指数卡片。失败时保留上次快照并标注「上次快照（日期）」，不编造数据。"""
    now = _now(now)
    prev = prev if prev is not None else snapshot()
    tickers = list(CN_INDICES) + list(SATELLITES)
    frames = _download_close(
        tickers,
        live=live,
        now=now,
        fetch_sina=fetch_sina,
        fetch_tencent=fetch_tencent,
        fetch_ak=fetch_ak,
        fetch_yahoo=fetch_yahoo,
    )

    rows: list[dict] = []
    for yahoo, meta in CN_INDICES.items():
        series = frames.get(yahoo)
        if series is None:
            continue
        row = _row_from_series(yahoo, series, meta)
        if row:
            rows.append(row)
    extras: list[dict] = []
    if rows:
        _attach_futures.extras = []  # type: ignore[attr-defined]
        _attach_futures(rows)
        extras = list(getattr(_attach_futures, "extras", []) or [])

    sats: list[dict] = []
    for sym, meta in SATELLITES.items():
        series = frames.get(sym)
        if series is None:
            # keep last satellite card if live fetch failed
            old = next((dict(s) for s in (prev.get("satellites") or []) if s.get("symbol") == sym), None)
            if old:
                old["from_snapshot"] = True
                sats.append(old)
            continue
        item = _satellite_from_series(sym, series, meta)
        if item:
            sats.append(item)

    if len(rows) < 3:
        asof = payload_asof(prev)
        out = _mark_snapshot(prev if prev.get("indices") else {"ok": False, "indices": [], "satellites": sats or prev.get("satellites") or []}, asof)
        out["ok"] = bool(out.get("indices"))
        out["updated_at"] = out.get("updated_at") or now.isoformat(timespec="seconds")
        out["method"] = _method_dict(out)
        if write:
            _atomic_write(RUNTIME_PATH, json.dumps(out, ensure_ascii=False, default=str) + "\n")
        return _remember(out)

    # persist incremental daily closes for the three CN indices
    closes = {yahoo: series_to_pairs(frames[yahoo]) for yahoo in CN_INDICES if yahoo in frames}
    if write and closes:
        save_price_seed(closes)

    asof = max((r.get("asof") or "" for r in rows), default="") or None
    payload = {
        "ok": True,
        "updated_at": now.isoformat(timespec="seconds"),
        "asof": asof,
        "from_snapshot": False,
        "snapshot_asof": None,
        "source": "sina",
        "method": _method_dict(prev),
        "note": "",
        "indices": rows,
        "satellites": sats,
        "futures_extra": extras,
        "disclaimer": (prev or {}).get("disclaimer") or DISCLAIMER,
    }
    for key in (
        "index_fundamentals", "index_flows", "holder_history", "advices",
        "advice_updated_at", "advice_method", "advice_fund_asof", "advice_fund_notes",
    ):
        if prev.get(key) is not None:
            payload[key] = prev[key]
    if attach_bundles:
        fund = _index_fundamentals()
        if fund:
            payload["index_fundamentals"] = fund
        flows = _index_flows()
        if flows:
            payload["index_flows"] = flows
    if write:
        _atomic_write(RUNTIME_PATH, json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return _remember(payload)


def build_cn_index_analysis(force: bool = False) -> dict:
    """兼容旧入口。force=False 只读快照；force=True 后台式联网刷新并附带估值/资金。"""
    if not force:
        return snapshot()
    return refresh(live=True, attach_bundles=True)
