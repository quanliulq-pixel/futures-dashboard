# -*- coding: utf-8 -*-
"""Data layer: full CN (AKShare) + North America (yfinance)."""
from __future__ import annotations

from pathlib import Path

import json
import logging
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

warnings.filterwarnings("ignore")

from config import HISTORY_CACHE_TTL, MAX_WORKERS_HISTORY, MAX_WORKERS_QUOTES, QUOTE_CACHE_TTL
from universe import get_cn_instruments, get_instrument, get_universe, get_us_instruments

logger = logging.getLogger(__name__)

_history_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_quote_bundle_cache: tuple[float, list[dict]] | None = None
_quotes_refresh_lock = __import__("threading").Lock()
_quotes_refresh_running = False



def _now() -> float:
    return time.time()


def list_instruments(market: str | None = None) -> list[dict]:
    items, _ = get_universe()
    if market in ("cn", "us"):
        items = [x for x in items if x["market"] == market]
    return [
        {
            "id": x["id"],
            "symbol": x["symbol"],
            "name": x["name"],
            "sector": x["sector"],
            "market": x["market"],
            "exchange": x.get("exchange", ""),
            "deflation_tag": x.get("deflation_tag"),
            "deflation_note": x.get("deflation_note"),
        }
        for x in items
    ]


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "oi"])

    out = df.copy()
    colmap = {}
    for c in out.columns:
        cl = str(c).lower()
        if c in ("日期", "date", "Date", "Datetime") or cl in ("date", "datetime"):
            colmap[c] = "date"
        elif c in ("开盘价", "开盘", "Open", "open"):
            colmap[c] = "open"
        elif c in ("最高价", "最高", "High", "high"):
            colmap[c] = "high"
        elif c in ("最低价", "最低", "Low", "low"):
            colmap[c] = "low"
        elif c in ("收盘价", "收盘", "Close", "close", "Adj Close"):
            colmap[c] = "close"
        elif c in ("成交量", "Volume", "volume"):
            colmap[c] = "volume"
        elif c in ("持仓量", "持仓", "Open Interest", "openinterest"):
            colmap[c] = "oi"
    out = out.rename(columns=colmap)

    if "date" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index()
            out = out.rename(columns={out.columns[0]: "date"})
        else:
            raise ValueError("history missing date column")

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = None
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "oi" not in out.columns:
        out["oi"] = None
    else:
        out["oi"] = pd.to_numeric(out["oi"], errors="coerce")

    out = out.dropna(subset=["date", "close"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last")
    return out[["date", "open", "high", "low", "close", "volume", "oi"]].reset_index(drop=True)


def _fetch_cn_daily(symbol: str) -> pd.DataFrame:
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=365 * 15)).strftime("%Y%m%d")
    raw = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    return _normalize_ohlcv(raw)


def _fetch_us_daily(symbol: str) -> pd.DataFrame:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    raw = ticker.history(period="max", auto_adjust=True)
    if raw is None or raw.empty:
        return _normalize_ohlcv(pd.DataFrame())
    raw = raw.reset_index()
    if "Date" in raw.columns:
        raw["Date"] = pd.to_datetime(raw["Date"]).dt.tz_localize(None)
    elif "Datetime" in raw.columns:
        raw["Datetime"] = pd.to_datetime(raw["Datetime"]).dt.tz_localize(None)
    return _normalize_ohlcv(raw)


def get_daily_history(instrument_id: str, force: bool = False) -> pd.DataFrame:
    meta = get_instrument(instrument_id)
    if not meta:
        raise KeyError(f"unknown instrument: {instrument_id}")

    cached = _history_cache.get(instrument_id)
    if not force and cached and _now() - cached[0] < HISTORY_CACHE_TTL:
        return cached[1].copy()

    if meta["market"] == "cn":
        df = _fetch_cn_daily(meta["symbol"])
    else:
        df = _fetch_us_daily(meta["symbol"])

    _history_cache[instrument_id] = (_now(), df)
    return df.copy()


def resample_bars(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    interval = interval.upper()
    if interval == "D":
        return df.copy()
    rule = {"W": "W-FRI", "M": "ME", "Y": "YE"}.get(interval)
    if not rule:
        raise ValueError("interval must be one of D,W,M,Y")
    tmp = df.set_index("date").sort_index()
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "oi": "last",
    }
    return tmp.resample(rule).agg(agg).dropna(subset=["close"]).reset_index()


def history_payload(instrument_id: str, interval: str = "D", limit: int | None = None) -> dict:
    meta = get_instrument(instrument_id)
    if not meta:
        raise KeyError(instrument_id)
    daily = get_daily_history(instrument_id)
    bars = resample_bars(daily, interval)
    if limit and limit > 0:
        bars = bars.tail(limit)
    records = []
    for row in bars.itertuples(index=False):
        records.append(
            {
                "date": row.date.strftime("%Y-%m-%d"),
                "open": _num(row.open),
                "high": _num(row.high),
                "low": _num(row.low),
                "close": _num(row.close),
                "volume": _num(row.volume),
                "oi": _num(row.oi),
            }
        )
    return {
        "id": instrument_id,
        "symbol": meta["symbol"],
        "name": meta["name"],
        "market": meta["market"],
        "exchange": meta.get("exchange", ""),
        "interval": interval.upper(),
        "bars": records,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quote_from_history(instrument_id: str) -> dict:
    meta = get_instrument(instrument_id)
    base = {
        "id": instrument_id,
        "symbol": meta["symbol"],
        "name": meta["name"],
        "market": meta["market"],
        "sector": meta["sector"],
        "exchange": meta.get("exchange", ""),
        "deflation_tag": meta.get("deflation_tag"),
        "deflation_note": meta.get("deflation_note"),
    }
    try:
        df = get_daily_history(instrument_id)
    except Exception:
        return {
            **base,
            "price": None,
            "prev_close": None,
            "change": None,
            "change_pct": None,
            "open": None,
            "high": None,
            "low": None,
            "volume": None,
            "oi": None,
            "source": "empty",
            "asof": None,
        }
    if df.empty:
        return {
            **base,
            "price": None,
            "prev_close": None,
            "change": None,
            "change_pct": None,
            "open": None,
            "high": None,
            "low": None,
            "volume": None,
            "oi": None,
            "source": "empty",
            "asof": None,
        }
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    price = _num(last.close)
    prev_close = _num(prev.close) if prev is not None else None
    change = (price - prev_close) if price is not None and prev_close is not None else None
    change_pct = (change / prev_close * 100) if change is not None and prev_close else None
    return {
        **base,
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "open": _num(last.open),
        "high": _num(last.high),
        "low": _num(last.low),
        "volume": _num(last.volume),
        "oi": _num(last.oi),
        "source": "daily",
        "asof": last.date.strftime("%Y-%m-%d"),
    }


def _fetch_cn_realtime_map() -> dict[str, dict]:
    import akshare as ak

    result: dict[str, dict] = {}
    names = []
    seen = set()
    for item in get_cn_instruments():
        n = item.get("name_cn") or item.get("name")
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    def one(name: str) -> list[tuple[str, dict]]:
        try:
            df = ak.futures_zh_realtime(symbol=name)
        except Exception:
            return []
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            sym = str(r.get("symbol", "")).upper()
            if not sym.endswith("0"):
                continue
            trade = _num(r.get("trade")) or _num(r.get("close"))
            bid = _num(r.get("bidprice1"))
            ask = _num(r.get("askprice1"))
            if not trade:
                if bid and ask:
                    trade = (bid + ask) / 2
                else:
                    trade = bid or ask
            pre = _num(r.get("presettlement")) or _num(r.get("prevsettlement")) or _num(r.get("preclose"))
            if not trade and pre:
                trade = pre
            change = (trade - pre) if trade is not None and pre is not None else None
            change_pct = (change / pre * 100) if change is not None and pre else None
            rows.append(
                (
                    sym,
                    {
                        "price": trade,
                        "prev_close": pre,
                        "change": change,
                        "change_pct": change_pct,
                        "open": _num(r.get("open")),
                        "high": _num(r.get("high")),
                        "low": _num(r.get("low")),
                        "volume": _num(r.get("volume")),
                        "oi": _num(r.get("position")),
                        "source": "sina_rt",
                        "asof": f"{r.get('tradedate', '')} {r.get('ticktime', '')}".strip(),
                    },
                )
            )
        return rows

    # moderate parallelism to reduce ban risk
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(one, n): n for n in names}
        for fut in as_completed(futs):
            try:
                for sym, q in fut.result():
                    result[sym] = q
            except Exception:
                pass
    return result


def _batch_us_quotes() -> dict[str, dict]:
    """Batch download last prices for all NA symbols via yfinance."""
    import yfinance as yf

    us = get_us_instruments()
    symbols = [x["symbol"] for x in us]
    out: dict[str, dict] = {}
    if not symbols:
        return out
    try:
        # 10d to compute change
        raw = yf.download(
            symbols,
            period="10d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as exc:
        logger.warning("yfinance batch failed: %s", exc)
        return out

    if raw is None or raw.empty:
        return out

    # multi-ticker vs single
    if len(symbols) == 1:
        sym = symbols[0]
        try:
            close = raw["Close"].dropna()
            if len(close):
                price = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else None
                change = (price - prev) if prev is not None else None
                out[sym] = {
                    "price": price,
                    "prev_close": prev,
                    "change": change,
                    "change_pct": (change / prev * 100) if change is not None and prev else None,
                    "open": float(raw["Open"].iloc[-1]) if "Open" in raw else None,
                    "high": float(raw["High"].iloc[-1]) if "High" in raw else None,
                    "low": float(raw["Low"].iloc[-1]) if "Low" in raw else None,
                    "volume": float(raw["Volume"].iloc[-1]) if "Volume" in raw else None,
                    "oi": None,
                    "source": "yfinance",
                    "asof": str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(close.index[-1]),
                }
        except Exception:
            pass
        return out

    for sym in symbols:
        try:
            if sym not in raw.columns.get_level_values(0):
                continue
            sub = raw[sym].dropna(how="all")
            if sub.empty or "Close" not in sub.columns:
                continue
            close = sub["Close"].dropna()
            if close.empty:
                continue
            price = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else None
            change = (price - prev) if prev is not None else None
            last = sub.iloc[-1]
            asof = close.index[-1]
            out[sym] = {
                "price": price,
                "prev_close": prev,
                "change": change,
                "change_pct": (change / prev * 100) if change is not None and prev else None,
                "open": _num(last.get("Open")),
                "high": _num(last.get("High")),
                "low": _num(last.get("Low")),
                "volume": _num(last.get("Volume")),
                "oi": None,
                "source": "yfinance",
                "asof": str(asof.date()) if hasattr(asof, "date") else str(asof),
            }
        except Exception:
            continue
    return out


def _load_quotes_seed() -> list[dict] | None:
    """Cold-start fallback from exported Pages snapshot."""
    path = Path(__file__).resolve().parent / "docs" / "api" / "quotes.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            return items
    except Exception:
        logger.debug("quotes seed load failed", exc_info=True)
    return None


def get_quotes(market: str | None = None, force: bool = False) -> list[dict]:
    """Return quotes fast. Live Yahoo/Sina pulls never block the HTTP request.

    force=True only schedules a background refresh; callers always get the
    current cache or the exported docs/api/quotes.json seed within milliseconds.
    """
    global _quote_bundle_cache, _quotes_refresh_running

    def _filter(quotes: list[dict]) -> list[dict]:
        if market in ("cn", "us"):
            return [q for q in quotes if q.get("market") == market]
        return quotes

    # Fresh-enough cache: serve immediately.
    if _quote_bundle_cache and (not force) and _now() - _quote_bundle_cache[0] < QUOTE_CACHE_TTL:
        return _filter(_quote_bundle_cache[1])

    # Prefer any in-memory cache, else the Pages export seed.
    snapshot: list[dict] | None = None
    if _quote_bundle_cache is not None:
        snapshot = _quote_bundle_cache[1]
    else:
        seed = _load_quotes_seed()
        if seed is not None:
            _quote_bundle_cache = (_now(), seed)
            snapshot = seed

    def _kick_bg() -> None:
        global _quotes_refresh_running
        if not _quotes_refresh_lock.acquire(blocking=False):
            return
        if _quotes_refresh_running:
            _quotes_refresh_lock.release()
            return
        _quotes_refresh_running = True
        _quotes_refresh_lock.release()

        import threading

        def _bg():
            global _quotes_refresh_running, _quote_bundle_cache
            try:
                live = _fetch_quotes_live()
                if live:
                    _quote_bundle_cache = (_now(), live)
            except Exception:
                logger.exception("background quotes refresh failed")
            finally:
                with _quotes_refresh_lock:
                    _quotes_refresh_running = False

        threading.Thread(target=_bg, daemon=True).start()

    if snapshot is not None:
        # TTL miss or force: return snapshot now, refresh once in background.
        _kick_bg()
        return _filter(snapshot)

    # No seed/cache at all (shouldn't happen in deploy) — last resort sync with timeout.
    try:
        live = _fetch_quotes_live(timeout_sec=12)
        if live:
            _quote_bundle_cache = (_now(), live)
            return _filter(live)
    except Exception:
        logger.exception("sync quotes fetch failed")
    return []


def _fetch_quotes_live(timeout_sec: int = 45) -> list[dict]:
    """Build a live quote bundle. Soft-timeouts so one slow source cannot hang forever."""
    quotes: list[dict] = []

    cn_rt: dict[str, dict] = {}
    us_rt: dict[str, dict] = {}

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        fut_cn = pool.submit(_fetch_cn_realtime_map)
        fut_us = pool.submit(_batch_us_quotes)
        try:
            cn_rt = fut_cn.result(timeout=timeout_sec) or {}
        except Exception as exc:
            logger.warning("cn realtime batch failed/timeout: %s", exc)
        try:
            # Don't let US wait the full CN budget if CN already consumed it.
            us_rt = fut_us.result(timeout=max(5, min(20, timeout_sec))) or {}
        except Exception as exc:
            logger.warning("us batch quotes failed/timeout: %s", exc)
    finally:
        # Never block the caller on stranded Yahoo/Sina workers.
        pool.shutdown(wait=False, cancel_futures=True)

    # CN first
    for item in get_cn_instruments():
        base = {
            "id": item["id"],
            "symbol": item["symbol"],
            "name": item["name"],
            "market": item["market"],
            "sector": item["sector"],
            "exchange": item.get("exchange", ""),
            "deflation_tag": item.get("deflation_tag"),
            "deflation_note": item.get("deflation_note"),
        }
        rt = cn_rt.get(item["symbol"].upper())
        if rt and rt.get("price") is not None:
            quotes.append({**base, **rt})
        else:
            # cheap fallback: only if history already cached to avoid thundering herd
            cached = _history_cache.get(item["id"])
            if cached:
                quotes.append(_quote_from_history(item["id"]))
            else:
                quotes.append(
                    {
                        **base,
                        "price": None,
                        "prev_close": None,
                        "change": None,
                        "change_pct": None,
                        "open": None,
                        "high": None,
                        "low": None,
                        "volume": None,
                        "oi": None,
                        "source": "pending",
                        "asof": None,
                    }
                )

    for item in get_us_instruments():
        base = {
            "id": item["id"],
            "symbol": item["symbol"],
            "name": item["name"],
            "market": item["market"],
            "sector": item["sector"],
            "exchange": item.get("exchange", ""),
            "deflation_tag": item.get("deflation_tag"),
            "deflation_note": item.get("deflation_note"),
        }
        rt = us_rt.get(item["symbol"])
        if rt and rt.get("price") is not None:
            quotes.append({**base, **rt})
        else:
            cached = _history_cache.get(item["id"])
            if cached:
                quotes.append(_quote_from_history(item["id"]))
            else:
                quotes.append(
                    {
                        **base,
                        "price": None,
                        "prev_close": None,
                        "change": None,
                        "change_pct": None,
                        "open": None,
                        "high": None,
                        "low": None,
                        "volume": None,
                        "oi": None,
                        "source": "pending",
                        "asof": None,
                    }
                )

    return quotes



def warmup(ids: list[str] | None = None, max_workers: int | None = None) -> None:
    """Prefetch daily history. Prefer major list first if ids is None — warmup ALL."""
    items, by_id = get_universe()
    targets = ids or [x["id"] for x in items]
    workers = max_workers or MAX_WORKERS_HISTORY

    def one(i: str):
        try:
            get_daily_history(i)
            return True
        except Exception as exc:
            logger.debug("warmup fail %s: %s", i, exc)
            return False

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(one, targets):
            if res:
                ok += 1
    logger.info("Warmup history: %d/%d ok", ok, len(targets))


def fill_missing_quote_history(limit: int = 40) -> None:
    """Background: fill pending quotes using history for symbols without price."""
    global _quote_bundle_cache
    if not _quote_bundle_cache:
        return
    quotes = list(_quote_bundle_cache[1])
    pending = [q for q in quotes if q.get("price") is None][:limit]
    if not pending:
        return

    def one(q):
        try:
            return _quote_from_history(q["id"])
        except Exception:
            return q

    with ThreadPoolExecutor(max_workers=6) as pool:
        updated = list(pool.map(one, pending))
    by_id = {q["id"]: q for q in updated}
    new_quotes = [by_id.get(q["id"], q) for q in quotes]
    _quote_bundle_cache = (_now(), new_quotes)


def get_cached_quote_price(instrument_id: str) -> float | None:
    """Return latest price from in-memory quote cache if available (non-blocking)."""
    if not _quote_bundle_cache:
        return None
    for q in _quote_bundle_cache[1]:
        if q.get("id") == instrument_id and q.get("price") is not None:
            return _num(q.get("price"))
    return None
