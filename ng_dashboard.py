# -*- coding: utf-8 -*-
"""
Henry Hub 天然气 (NYMEX NG) 专属看板。

自动从公开源抓取影响价格的信息，转成 [-1, +1] 的多空因子分（正=利多），
按季节给权重，合成综合分，并生成规则化的交易建议。

数据源（全部免费、无需 API Key）：
- 价格 / 远期曲线 / TTF / 汇率：Yahoo Finance（yfinance）
- 库存：EIA 每周库存报告 wngsr.txt + EIA 历史库存 xls（2010 年起）
- 天气：Open-Meteo 16 日预报 + 档案库（近 5 年同期作“常态”）
- 新闻：Google News RSS（多关键词，近 4 天）
- 飓风：NOAA NHC 大西洋 RSS
- 持仓：AKShare CFTC 商业持仓

本模块为研究用的规则模型，不构成投资建议。
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data" / "ng"
LAST_PAYLOAD = DATA_DIR / "last_dashboard.json"
SCORE_HISTORY = DATA_DIR / "score_history.jsonl"
WEATHER_SNAPS = DATA_DIR / "weather_snapshots.json"
STORAGE_XLS = DATA_DIR / "storage_hist.xls"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

DASHBOARD_TTL = 10 * 60
SOURCE_TTL = {
    "price": 10 * 60,
    "curve": 15 * 60,
    "storage": 3 * 3600,
    "weather": 2 * 3600,
    "normals": 24 * 3600,
    "news": 20 * 60,
    "lng": 30 * 60,
    "cot": 12 * 3600,
    "hurricane": 60 * 60,
}

# NYMEX NG 合约规格
CONTRACTS = {
    "NG": {"name": "NYMEX 标准 NG", "mmbtu": 10000},
    "QG": {"name": "E-mini QG", "mmbtu": 2500},
    "MNG": {"name": "Micro MNG", "mmbtu": 1000},
}

MONTH_CODES = "FGHJKMNQUVXZ"

# 采暖/制冷需求加权城市（粗略按住宅+商业用气与人口分布赋权，合计 1.0）
CITIES = [
    ("芝加哥", 41.88, -87.63, 0.16),
    ("纽约", 40.71, -74.01, 0.16),
    ("底特律", 42.33, -83.05, 0.08),
    ("波士顿", 42.36, -71.06, 0.07),
    ("费城", 39.95, -75.17, 0.07),
    ("达拉斯", 32.78, -96.80, 0.07),
    ("明尼阿波利斯", 44.98, -93.27, 0.06),
    ("哥伦布", 39.96, -83.00, 0.06),
    ("亚特兰大", 33.75, -84.39, 0.06),
    ("休斯敦", 29.76, -95.37, 0.06),
    ("洛杉矶", 34.05, -118.24, 0.06),
    ("匹兹堡", 40.44, -79.99, 0.05),
    ("丹佛", 39.74, -104.99, 0.04),
]

# 季节基础权重（%）。缺数据的因子会被剔除后重新归一。
SEASON_WEIGHTS = {
    "winter": {"weather": 30, "storage": 20, "news": 12, "lng": 10, "technical": 10, "curve": 5, "cot": 5, "cost": 5, "hurricane": 3},
    "summer": {"weather": 22, "storage": 22, "news": 12, "lng": 10, "technical": 10, "curve": 5, "cot": 5, "cost": 6, "hurricane": 8},
    "shoulder": {"weather": 12, "storage": 28, "news": 14, "lng": 12, "technical": 12, "curve": 6, "cot": 6, "cost": 7, "hurricane": 3},
}

FACTOR_META = {
    "weather": {
        "name": "天气（HDD/CDD 预报）",
        "why": "天气决定采暖与发电用气，是 NG 短期波动的第一驱动。市场交易的是“预报相对常态的偏差”以及“最新一轮预报的修正”，而不是天气本身。",
    },
    "storage": {
        "name": "EIA 库存",
        "why": "库存相对 5 年均值的盈余/赤字反映供需累积结果；每周四的实际变化与季节常态的差（意外）常引发当日大波动。",
    },
    "news": {
        "name": "新闻事件（自动检索）",
        "why": "LNG 装置停机、产量冻停、管道事故、政策与地缘事件会突发改变供需，新闻流是对结构化数据的补充与预警。",
    },
    "lng": {
        "name": "LNG 出口套利（TTF−HH）",
        "why": "美国 LNG 出口约占需求的 15%+。欧亚气价相对 Henry Hub 的价差决定出口经济性：价差收窄到运费+液化费以下会引发取消货、需求下降。",
    },
    "technical": {
        "name": "趋势与动量",
        "why": "NG 趋势性强、波动大，CTA/量化资金放大趋势；RSI 极端区提示短线均值回归风险。",
    },
    "curve": {
        "name": "远期曲线结构",
        "why": "近月相对次月的升贴水反映即期松紧：近月深度贴水（contango 过陡）意味着现货过剩、市场愿意付费储存；近月升水则代表即期偏紧。",
    },
    "cot": {
        "name": "CFTC 持仓",
        "why": "商业（套保）净持仓处于历史高/低分位时，往往对应投机资金的极端仓位，反向信号意义较强，但时效较慢（周二数据周五发布）。",
    },
    "cost": {
        "name": "生产成本锚",
        "why": "价格跌破干气现金成本会抑制钻井、中期压低产量；远高于激励上沿会刺激增产。属于中长期慢变量。",
    },
    "hurricane": {
        "name": "飓风/热带风暴",
        "why": "如今墨西哥湾飓风对 LNG 出口终端（需求端）的冲击往往大于对海上产量（供给端）的冲击，净效果通常偏空；仅在飓风季且有活跃风暴时计入。",
    },
}

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_compute_lock = threading.Lock()
_dashboard: tuple[float, dict] | None = None


# ---------------------------------------------------------------- helpers

def _clip(x: float | None, lo: float = -1.0, hi: float = 1.0) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return max(lo, min(hi, float(x)))


def _r(x: Any, n: int = 3) -> Any:
    if x is None:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return x


def _http_get(url: str, params: dict | None = None, timeout: int = 20):
    import requests

    last_exc = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 503 and attempt < 2:
                time.sleep(1.0 + attempt * 1.5)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.8 + attempt * 1.2)
            else:
                raise last_exc
    if last_exc:
        raise last_exc


def _cached(key: str, ttl: int, fn, force: bool = False):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if not force and hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        logger.exception("NG source %s failed", key)
        if hit:
            return hit[1]  # stale but better than nothing
        raise
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def _season(d: date) -> str:
    if d.month in (11, 12, 1, 2, 3):
        return "winter"
    if d.month in (6, 7, 8, 9):
        return "summer"
    return "shoulder"


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))


def _to_bj(dt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return dt.astimezone(timezone(timedelta(hours=8)))


def _et_dt(d: date, hh: int, mm: int) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo("America/New_York"))
    except Exception:
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone(timedelta(hours=-5)))


def _cbd():
    from pandas.tseries.holiday import USFederalHolidayCalendar
    from pandas.tseries.offsets import CustomBusinessDay

    return CustomBusinessDay(calendar=USFederalHolidayCalendar())


def _ng_expiry(year: int, month: int) -> date:
    """NG 期货最后交易日：交割月首日之前第 3 个营业日（近似：美国联邦假日日历）。"""
    first = pd.Timestamp(year=year, month=month, day=1)
    return (first - 3 * _cbd()).date()


def _add_month(y: int, m: int, k: int = 1) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


def _front_delivery(today: date) -> tuple[int, int]:
    y, m = _add_month(today.year, today.month, 1)
    if today > _ng_expiry(y, m):
        y, m = _add_month(y, m, 1)
    return y, m


def _ticker(y: int, m: int) -> str:
    return f"NG{MONTH_CODES[m - 1]}{y % 100:02d}.NYM"


# ---------------------------------------------------------------- sources

def fetch_price() -> dict:
    from data_sources import get_daily_history

    df = get_daily_history("US:NG=F")
    if df is None or df.empty:
        raise RuntimeError("NG=F history empty")
    df = df.dropna(subset=["close"]).copy()
    c = df["close"].astype(float)
    h = df["high"].astype(float).fillna(c)
    lo = df["low"].astype(float).fillna(c)
    prev_c = c.shift(1)
    tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] else 100.0
    logret = (c / prev_c).apply(lambda v: math.log(v) if v and v > 0 else float("nan"))
    vol20 = float(logret.tail(20).std() * math.sqrt(252) * 100)
    last = float(c.iloc[-1])

    def ret(n: int) -> float | None:
        return (last / float(c.iloc[-n - 1]) - 1) * 100 if len(c) > n else None

    tail252 = c.tail(252)
    hist = df.tail(260)
    return {
        "price": last,
        "asof": str(df["date"].iloc[-1].date()),
        "chg_1d": ret(1),
        "chg_5d": ret(5),
        "chg_21d": ret(21),
        "chg_63d": ret(63),
        "ma20": float(c.tail(20).mean()),
        "ma60": float(c.tail(60).mean()),
        "ma200": float(c.tail(200).mean()) if len(c) >= 200 else None,
        "rsi14": rsi,
        "atr14": atr14,
        "vol20": vol20,
        "hi52": float(tail252.max()),
        "lo52": float(tail252.min()),
        "pct52": float((tail252 < last).mean() * 100),
        "bars": [
            {
                "date": str(r.date.date()),
                "open": _r(r.open, 4),
                "high": _r(r.high, 4),
                "low": _r(r.low, 4),
                "close": _r(r.close, 4),
            }
            for r in hist.itertuples(index=False)
        ],
    }


def fetch_curve() -> dict:
    import yfinance as yf

    today = _et_now().date()
    y, m = _front_delivery(today)
    months = [_add_month(y, m, k) for k in range(12)]
    tickers = [_ticker(yy, mm) for yy, mm in months]
    raw = yf.download(tickers, period="5d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
    pts = []
    for (yy, mm), t in zip(months, tickers):
        px = None
        try:
            if t in raw.columns.get_level_values(0):
                s = raw[t]["Close"].dropna()
                if len(s):
                    px = float(s.iloc[-1])
        except Exception:
            px = None
        pts.append({
            "ticker": t,
            "label": f"{yy % 100:02d}{mm:02d}",
            "year": yy,
            "month": mm,
            "price": px,
            "expiry": str(_ng_expiry(yy, mm)),
        })
    valid = [p for p in pts if p["price"]]
    m1 = valid[0]["price"] if valid else None
    m2 = valid[1]["price"] if len(valid) > 1 else None
    winter = [p["price"] for p in valid if p["month"] in (11, 12, 1, 2, 3)]
    summer = [p["price"] for p in valid if p["month"] in (4, 5, 6, 7, 8, 9, 10)]
    return {
        "points": pts,
        "front": pts[0],
        "m1": m1,
        "m2": m2,
        "m1_m2_pct": ((m2 / m1 - 1) * 100) if m1 and m2 else None,
        "strip12": sum(p["price"] for p in valid) / len(valid) if valid else None,
        "winter_avg": sum(winter) / len(winter) if winter else None,
        "summer_avg": sum(summer) / len(summer) if summer else None,
    }


def _load_storage_series() -> pd.Series:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    content = None
    try:
        r = _http_get("https://www.eia.gov/dnav/ng/hist_xls/NW2_EPG0_SWO_R48_BCFw.xls", timeout=25)
        content = r.content
        STORAGE_XLS.write_bytes(content)
    except Exception as exc:
        logger.warning("EIA xls download failed (%s), trying disk copy", exc)
        if STORAGE_XLS.exists():
            content = STORAGE_XLS.read_bytes()
    if not content:
        raise RuntimeError("no EIA storage history")
    df = pd.read_excel(io.BytesIO(content), sheet_name=1, skiprows=2)
    df.columns = ["date", "bcf"] + list(df.columns[2:])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["bcf"] = pd.to_numeric(df["bcf"], errors="coerce")
    s = df.dropna(subset=["date", "bcf"]).set_index("date")["bcf"].sort_index()

    # 周报 txt 可能比 xls 更新一周
    try:
        txt = _http_get("https://ir.eia.gov/ngs/wngsr.txt", timeout=15).content.decode("utf-8", "ignore").lstrip("\ufeff")
        mt = re.search(r"Total \((\d{2})/(\d{2})/(\d{2})\):\s*([\d,]+)\s*Bcf", txt)
        if mt:
            d = pd.Timestamp(2000 + int(mt.group(3)), int(mt.group(1)), int(mt.group(2)))
            v = float(mt.group(4).replace(",", ""))
            if d not in s.index:
                s.loc[d] = v
                s = s.sort_index()
    except Exception:
        logger.debug("wngsr.txt overlay skipped", exc_info=True)
    return s


def _nearest(s: pd.Series, d: pd.Timestamp, tol_days: int = 5) -> float | None:
    if s.empty:
        return None
    i = s.index.get_indexer([d], method="nearest")[0]
    if i < 0 or abs((s.index[i] - d).days) > tol_days:
        return None
    v = s.iloc[i]
    return None if pd.isna(v) else float(v)


def _shift_years(d: pd.Timestamp, k: int) -> pd.Timestamp:
    try:
        return d.replace(year=d.year - k)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - k, day=28)


def fetch_storage() -> dict:
    s = _load_storage_series()
    chg = s.diff()
    d = s.index[-1]
    last = float(s.iloc[-1])
    wk = float(chg.iloc[-1])

    five = [_nearest(s, _shift_years(d, k)) for k in range(1, 6)]
    five = [v for v in five if v is not None]
    avg5 = sum(five) / len(five) if five else None
    five_chg = [_nearest(chg, _shift_years(d, k)) for k in range(1, 6)]
    five_chg = [v for v in five_chg if v is not None]
    avg5_chg = sum(five_chg) / len(five_chg) if five_chg else None
    yago = _nearest(s, _shift_years(d, 1))

    # 季末库存推演：剩余周按 5 年均值变化推进
    if d.month in (11, 12, 1, 2, 3):
        end_year = d.year + 1 if d.month >= 11 else d.year
        target = pd.Timestamp(end_year, 3, 31)
        target_label = f"{end_year}年3月底（采暖季末）"
    else:
        target = pd.Timestamp(d.year, 10, 31)
        target_label = f"{d.year}年10月底（注入季末）"
    proj = last
    cur = d + pd.Timedelta(days=7)
    while cur <= target:
        vals = [_nearest(chg, _shift_years(cur, k)) for k in range(1, 6)]
        vals = [v for v in vals if v is not None]
        if vals:
            proj += sum(vals) / len(vals)
        cur += pd.Timedelta(days=7)
    t5 = [_nearest(s, _shift_years(target, k), tol_days=7) for k in range(1, 6)]
    t5 = [v for v in t5 if v is not None]
    target_avg5 = sum(t5) / len(t5) if t5 else None

    # 季节图：周序号 1..53
    df = s.to_frame("bcf")
    df["year"] = df.index.year
    df["week"] = [min(52, max(1, int((d - 1) // 7 + 1))) for d in df.index.dayofyear]
    cy = d.year
    band = df[(df["year"] >= cy - 5) & (df["year"] <= cy - 1)].groupby("week")["bcf"].agg(["min", "max", "mean"])
    seasonal = {
        "weeks": [int(w) for w in band.index],
        "min5": [_r(v, 0) for v in band["min"]],
        "max5": [_r(v, 0) for v in band["max"]],
        "avg5": [_r(v, 0) for v in band["mean"]],
        "this_year": {int(r.week): _r(r.bcf, 0) for r in df[df["year"] == cy].itertuples()},
        "last_year": {int(r.week): _r(r.bcf, 0) for r in df[df["year"] == cy - 1].itertuples()},
        "year": cy,
    }
    return {
        "asof": str(d.date()),
        "stocks": last,
        "net_change": wk,
        "avg5": avg5,
        "vs_avg5": last - avg5 if avg5 else None,
        "vs_avg5_pct": (last / avg5 - 1) * 100 if avg5 else None,
        "avg5_change": avg5_chg,
        "surprise_vs_5y": wk - avg5_chg if avg5_chg is not None else None,
        "year_ago": yago,
        "vs_yago_pct": (last / yago - 1) * 100 if yago else None,
        "proj_end": proj,
        "proj_target": target_label,
        "proj_end_avg5": target_avg5,
        "proj_end_vs_avg5_pct": (proj / target_avg5 - 1) * 100 if target_avg5 else None,
        "seasonal": seasonal,
        "recent": [{"date": str(i.date()), "bcf": float(v), "chg": _r(chg.loc[i], 0)} for i, v in s.tail(10).items()],
    }


def _om_multi(url: str, params: dict) -> list[dict]:
    r = _http_get(url, params=params, timeout=25).json()
    return r if isinstance(r, list) else [r]


def _weighted_dd(responses: list[dict]) -> dict[str, dict]:
    """date -> {'hdd','cdd','tdd'} 需求加权度日（基准 65°F）。"""
    acc: dict[str, dict] = {}
    for (name, _la, _lo, w), resp in zip(CITIES, responses):
        daily = (resp or {}).get("daily") or {}
        for t, temp in zip(daily.get("time") or [], daily.get("temperature_2m_mean") or []):
            if temp is None:
                continue
            a = acc.setdefault(t, {"hdd": 0.0, "cdd": 0.0, "w": 0.0})
            a["hdd"] += w * max(0.0, 65.0 - temp)
            a["cdd"] += w * max(0.0, temp - 65.0)
            a["w"] += w
    out = {}
    for t, a in acc.items():
        if a["w"] <= 0:
            continue
        hdd, cdd = a["hdd"] / a["w"], a["cdd"] / a["w"]
        out[t] = {"hdd": hdd, "cdd": cdd, "tdd": hdd + cdd}
    return out


def _weather_normals(dates: list[str]) -> dict[str, dict]:
    """近 5 年同日期的需求加权度日均值（按 MM-DD 对齐，支持磁盘持久化缓存）。"""
    normals_file = DATA_DIR / "weather_normals.json"
    if normals_file.exists():
        try:
            cached_map = json.loads(normals_file.read_text(encoding="utf-8"))
            if all(d in cached_map for d in dates):
                return {d: cached_map[d] for d in dates}
        except Exception:
            pass

    start, end = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    lat = ",".join(str(c[1]) for c in CITIES)
    lon = ",".join(str(c[2]) for c in CITIES)
    per_md: dict[str, list[dict]] = {}

    def one(k: int):
        s, e = _shift_years(start, k), _shift_years(end, k)
        try:
            resp = _om_multi(
                "https://archive-api.open-meteo.com/v1/archive",
                {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": s.strftime("%Y-%m-%d"),
                    "end_date": e.strftime("%Y-%m-%d"),
                    "daily": "temperature_2m_mean",
                    "temperature_unit": "fahrenheit",
                    "timezone": "America/New_York",
                },
            )
            return _weighted_dd(resp)
        except Exception as exc:
            logger.warning("archive year -%d failed: %s", k, exc)
            return {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        for dd in pool.map(one, range(1, 6)):
            for t, v in dd.items():
                per_md.setdefault(t[5:], []).append(v)
    out = {}
    for t in dates:
        vals = per_md.get(t[5:]) or []
        if vals:
            out[t] = {k: sum(v[k] for v in vals) / len(vals) for k in ("hdd", "cdd", "tdd")}
    if out:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            normals_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return out


def _load_snaps() -> list[dict]:
    try:
        return json.loads(WEATHER_SNAPS.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_snap(dd: dict[str, dict]) -> None:
    snaps = _load_snaps()
    now = time.time()
    if snaps and now - snaps[-1].get("ts", 0) < 3 * 3600:
        return  # 每 3 小时最多存一份，足够比较“24 小时修正”
    snaps.append({"ts": now, "tdd": {t: round(v["tdd"], 3) for t, v in dd.items()}})
    snaps = snaps[-20:]
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        WEATHER_SNAPS.write_text(json.dumps(snaps), encoding="utf-8")
    except Exception:
        logger.debug("weather snapshot write failed", exc_info=True)


def fetch_weather() -> dict:
    lat = ",".join(str(c[1]) for c in CITIES)
    lon = ",".join(str(c[2]) for c in CITIES)
    resp = _om_multi(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_mean",
            "temperature_unit": "fahrenheit",
            "forecast_days": 16,
            "timezone": "America/New_York",
        },
    )
    fc = _weighted_dd(resp)
    dates = sorted(fc.keys())
    if not dates:
        raise RuntimeError("weather forecast empty")
    normals = _cached(f"normals:{dates[0]}", SOURCE_TTL["normals"], lambda: _weather_normals(dates))

    def total(keys: list[str], src: dict, k: str = "tdd") -> float:
        return sum(src[t][k] for t in keys if t in src)

    d1, d2 = dates[:7], dates[7:]
    f1, n1 = total(d1, fc), total(d1, normals)
    f2, n2 = total(d2, fc), total(d2, normals)

    # 与约 24 小时前的预报比较（同一批未来日期）
    revision = None
    snaps = _load_snaps()
    now = time.time()
    base = None
    for sp in snaps:
        age = now - sp.get("ts", 0)
        if 12 * 3600 <= age <= 48 * 3600:
            if base is None or abs(age - 24 * 3600) < abs(now - base["ts"] - 24 * 3600):
                base = sp
    if base:
        overlap = [t for t in dates if t in base["tdd"]]
        if overlap:
            revision = {
                "hours_ago": round((now - base["ts"]) / 3600, 1),
                "days": len(overlap),
                "now_tdd": total(overlap, fc),
                "prev_tdd": sum(base["tdd"][t] for t in overlap),
                "normal_tdd": total(overlap, normals),
            }
            revision["delta"] = revision["now_tdd"] - revision["prev_tdd"]
    _save_snap(fc)

    cities_now = []
    for (name, _la, _lo, w), r in zip(CITIES, resp):
        daily = (r or {}).get("daily") or {}
        temps = [t for t in (daily.get("temperature_2m_mean") or [])[:7] if t is not None]
        cities_now.append({"city": name, "weight": w, "avg7_f": _r(sum(temps) / len(temps), 1) if temps else None})

    return {
        "dates": dates,
        "daily": [
            {
                "date": t,
                "hdd": _r(fc[t]["hdd"], 2),
                "cdd": _r(fc[t]["cdd"], 2),
                "tdd": _r(fc[t]["tdd"], 2),
                "normal_tdd": _r(normals.get(t, {}).get("tdd"), 2),
            }
            for t in dates
        ],
        "d1_7": {"fcst": f1, "normal": n1, "dev_pct": (f1 / n1 - 1) * 100 if n1 > 1 else None},
        "d8_16": {"fcst": f2, "normal": n2, "dev_pct": (f2 / n2 - 1) * 100 if n2 > 1 else None},
        "revision": revision,
        "cities": cities_now,
        "source": "Open-Meteo 预报（多模型最佳匹配）；常态=近 5 年同日期档案均值；需求加权 13 城、基准 65°F",
    }


def fetch_lng() -> dict:
    import yfinance as yf

    raw = yf.download(["TTF=F", "EURUSD=X", "NG=F"], period="2mo", group_by="ticker", auto_adjust=False, threads=True, progress=False)

    def series(t: str) -> pd.Series:
        s = raw[t]["Close"].dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) else pd.to_datetime(s.index)
        return s.astype(float)

    ttf, eur, hh = series("TTF=F"), series("EURUSD=X"), series("NG=F")
    df = pd.concat({"ttf": ttf, "eur": eur, "hh": hh}, axis=1).ffill().dropna()
    df["ttf_usd"] = df["ttf"] * df["eur"] / 3.412  # EUR/MWh → USD/MMBtu
    df["spread"] = df["ttf_usd"] - df["hh"]
    last = df.iloc[-1]
    prev20 = df.iloc[-21] if len(df) > 21 else df.iloc[0]
    return {
        "asof": str(df.index[-1].date()),
        "ttf_eur_mwh": float(last["ttf"]),
        "eurusd": float(last["eur"]),
        "ttf_usd": float(last["ttf_usd"]),
        "hh": float(last["hh"]),
        "spread": float(last["spread"]),
        "spread_chg_20d": float(last["spread"] - prev20["spread"]),
        "ttf_chg_20d_pct": float((last["ttf"] / prev20["ttf"] - 1) * 100),
        "history": [{"date": str(i.date()), "spread": _r(v, 3)} for i, v in df["spread"].tail(40).items()],
    }


def fetch_cot() -> dict:
    import akshare as ak

    df = ak.macro_usa_cftc_merchant_goods_holding()
    col = "纽约天然气-净仓位"
    if df is None or df.empty or col not in df.columns:
        raise RuntimeError("CFTC NG column missing")
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    tail = s.tail(156)
    last = float(s.iloc[-1])
    return {
        "asof": str(df["日期"].iloc[-1])[:10] if "日期" in df.columns else None,
        "net": last,
        "wow": float(s.iloc[-1] - s.iloc[-2]) if len(s) > 1 else None,
        "pct_3y": float((tail < last).mean() * 100),
        "min_3y": float(tail.min()),
        "max_3y": float(tail.max()),
    }


def fetch_hurricane() -> dict:
    r = _http_get("https://www.nhc.noaa.gov/index-at.xml", timeout=15)
    root = ET.fromstring(r.content)
    titles = [it.findtext("title") or "" for it in root.iter("item")]
    active = [
        t for t in titles
        if t and "Tropical Weather Outlook" not in t and "no tropical cyclones" not in t.lower()
        and re.search(r"Hurricane|Tropical Storm|Depression|Potential Tropical Cyclone|Advisory", t, re.I)
    ]
    return {"titles": titles[:6], "active": active[:6], "n_active": len(active)}


# ---------------------------------------------------------------- news

NEWS_QUERIES = [
    "natural gas prices",
    "henry hub natural gas futures",
    "natural gas storage EIA",
    "LNG feedgas exports US",
    "natural gas weather forecast cold OR warm",
    "natural gas production Haynesville OR Permian OR Appalachia",
    "Freeport LNG OR Sabine Pass OR Plaquemines LNG",
]

NEWS_CATS = [
    # key, 中文名, 基础影响权重, 关键词
    ("lng", "LNG出口", 0.85, r"\blng\b|feedgas|feed gas|freeport|sabine|cameron lng|corpus christi|plaquemines|golden pass|calcasieu|export terminal|liquefaction"),
    ("hurricane", "飓风", 0.6, r"hurricane|tropical storm|tropical depression|gulf storm"),
    ("weather", "天气", 1.0, r"weather|forecast|temperature|cold|colder|warm|warmer|mild|heat wave|heating degree|\bhdd\b|arctic|polar vortex|freez|frigid|winter storm"),
    ("storage", "库存", 0.9, r"storage|inventor|injection|withdrawal|\bdraw\b|\bbuild\b|stockpile|eia report|bcf"),
    ("production", "产量/供给", 0.8, r"production|output|drill|rig count|haynesville|permian|appalachia|marcellus|shale|dry gas|supply"),
    ("europe", "欧洲/地缘", 0.6, r"europe|\bttf\b|russia|ukraine|qatar|middle east|asia|\bjkm\b|norway|geopolit"),
    ("pipeline", "管道/基础设施", 0.5, r"pipeline|waha|maintenance|outage|explosion|force majeure"),
    ("policy", "政策/监管", 0.4, r"ferc|\bdoe\b|permit|tariff|sanction|policy|regulat|white house|congress"),
]

BULL_RX = re.compile(
    r"colder|cold snap|cold blast|arctic|polar vortex|freez(e|ing)|frigid|winter storm|below[- ]normal temp"
    r"|(larger|bigger)[- ]than[- ]expected (draw|withdrawal)|smaller[- ]than[- ]expected (build|injection)"
    r"|freeze[- ]offs?|(output|production) (drop|declin|fall|cut|slip)|shut[- ]in|curtail"
    r"|record (lng )?(feedgas|feed gas|exports)|lng (demand|exports|feedgas) (rise|jump|surge|climb|hit)"
    r"|supply (concern|disruption|risk)|tighten|deficit|heating demand (rise|jump|surge)",
    re.I,
)
BEAR_RX = re.compile(
    r"milder|warmer|mild (weather|temp|winter)|above[- ]normal temp|warm (weather|forecast|spell)"
    r"|smaller[- ]than[- ]expected (draw|withdrawal)|(larger|bigger)[- ]than[- ]expected (build|injection)"
    r"|record (production|output|supply)|(output|production) (rise|climb|growth|hit record|jump)"
    r"|surplus|glut|oversuppl|ample supply|demand (weak|slump|drop|falls)|cargo cancel",
    re.I,
)
LNG_DOWN_RX = re.compile(
    r"(outage|shut|trip|offline|maintenance|halt|fire|delay).{0,40}(lng|feedgas|terminal|freeport)"
    r"|(lng|feedgas|terminal|freeport).{0,40}(outage|shut|trip|offline|maintenance|halt|fire|delay)",
    re.I,
)
UP_MOVE_RX = re.compile(r"\b(rall(y|ies|ied)|surg(e|es|ed)|soar|jump|spike|climb|gain|rises?|rose|higher|up \d|bulls?)\b", re.I)
DOWN_MOVE_RX = re.compile(r"\b(fall|falls|fell|drop|drops|slide|slump|plunge|tumble|declin|lower|sink|slip|retreat|bears?|down \d)\b", re.I)

CREDIBLE = re.compile(r"reuters|bloomberg|wall street journal|wsj|natural gas intelligence|ngi|eia|s&p global|platts|argus|financial times|cnbc|marketwatch|rigzone|barchart", re.I)

NEWS_EXPLAIN = {
    ("weather", 1): "预报转冷/采暖需求上升 → 用气增加、库存去化加快 → 利多",
    ("weather", -1): "预报转暖/偏温和 → 采暖需求下降 → 库存去化放缓 → 利空",
    ("storage", 1): "库存去化超预期或注入低于预期 → 供需偏紧 → 利多",
    ("storage", -1): "库存注入超预期或去化不及预期 → 盈余扩大 → 利空",
    ("lng", 1): "LNG 出口/原料气提升 → 美国国内需求增加 → 利多",
    ("lng", -1): "LNG 终端停机/检修/延期 → 原料气需求骤降、气回流国内 → 利空",
    ("production", 1): "产量下降（冻停、减产、关井） → 供给收缩 → 利多",
    ("production", -1): "产量创新高/增长 → 供给宽松 → 利空",
    ("hurricane", 1): "风暴威胁海上产量 → 供给扰动 → 偏多（近年影响较小）",
    ("hurricane", -1): "风暴威胁墨西哥湾 LNG 终端 → 出口需求中断 → 偏空",
    ("europe", 1): "欧洲/亚洲供给紧张、TTF/JKM 走强 → 美国 LNG 出口需求稳固 → 利多",
    ("europe", -1): "海外气价回落/供给宽松 → LNG 套利收窄 → 利空",
    ("pipeline", 1): "管道/设施事故限制产区外输 → 消费区供给偏紧 → 局部利多",
    ("pipeline", -1): "管道检修使产区憋气、需求端设施停运 → 偏空",
    ("policy", 1): "政策利好出口/限制供给 → 偏多",
    ("policy", -1): "政策抑制需求或放松供给约束 → 偏空",
    ("price", 1): "行情描述类：反映已发生的上涨，权重较低（追随而非原因）",
    ("price", -1): "行情描述类：反映已发生的下跌，权重较低（追随而非原因）",
}


def _classify_news(title: str) -> tuple[str, str, float, int, float]:
    """返回 (cat_key, cat_cn, cat_weight, direction, strength)。"""
    cat_key, cat_cn, cw = "other", "其他", 0.2
    for k, cn, w, rx in NEWS_CATS:
        if re.search(rx, title, re.I):
            cat_key, cat_cn, cw = k, cn, w
            break

    if cat_key in ("lng", "hurricane") and LNG_DOWN_RX.search(title):
        return cat_key, cat_cn, cw, -1, 1.0
    if cat_key == "hurricane":
        return cat_key, cat_cn, cw, -1, 0.6  # 近年飓风净偏空

    bull = len(BULL_RX.findall(title))
    bear = len(BEAR_RX.findall(title))
    if bull != bear:
        d = 1 if bull > bear else -1
        return cat_key, cat_cn, cw, d, min(1.0, 0.6 + 0.2 * abs(bull - bear))

    up = len(UP_MOVE_RX.findall(title))
    dn = len(DOWN_MOVE_RX.findall(title))
    if up != dn:
        d = 1 if up > dn else -1
        # 纯行情描述：降为“行情”类，低权重
        return "price", "行情描述", 0.35, d, 0.6
    return cat_key, cat_cn, cw, 0, 0.0


def _fetch_rss(q: str) -> list[dict]:
    r = _http_get(
        "https://news.google.com/rss/search",
        params={"q": f"{q} when:4d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=15,
    )
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
        except Exception:
            pub = None
        out.append({"title": title, "link": it.findtext("link"), "source": src, "pub": pub})
    return out


def fetch_news() -> dict:
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for res in pool.map(lambda q: _safe(_fetch_rss, q, default=[]), NEWS_QUERIES):
            items.extend(res)

    now = datetime.now(timezone.utc)
    seen = set()
    rows = []
    for it in items:
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        # 只保留与天然气相关
        if not re.search(r"natural gas|\bnat ?gas\b|\bng\b|henry hub|\blng\b|gas price|gas futures|gas storage|feedgas|pipeline|\bbcf\b", it["title"], re.I):
            continue
        pub = it["pub"]
        age_h = (now - pub).total_seconds() / 3600 if pub else 48
        if age_h > 96:
            continue
        cat_key, cat_cn, cw, d, strength = _classify_news(it["title"])
        recency = 0.5 ** (max(age_h, 0) / 36)  # 半衰期 36 小时
        src_mult = 1.2 if CREDIBLE.search(it["source"] or "") else 1.0
        w = cw * recency * src_mult
        rows.append({
            "title": it["title"],
            "link": it["link"],
            "source": it["source"],
            "time": pub.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M") if pub else None,
            "age_h": round(age_h, 1),
            "category": cat_cn,
            "cat_key": cat_key,
            "direction": d,
            "strength": round(strength, 2),
            "weight": round(w, 3),
            "impact": round(d * strength * w, 3),
            "explain": NEWS_EXPLAIN.get((cat_key, d), "未识别到明确的多空方向，仅作背景信息"),
        })
    rows.sort(key=lambda r: (abs(r["impact"]), r["weight"]), reverse=True)
    directional = [r for r in rows if r["direction"] != 0]
    tot_w = sum(r["weight"] * r["strength"] for r in directional)
    net = sum(r["impact"] for r in directional)
    raw_score = net / tot_w if tot_w > 0 else 0.0
    coverage = min(1.0, len(directional) / 6)
    by_cat: dict[str, dict] = {}
    for r in directional:
        c = by_cat.setdefault(r["category"], {"n": 0, "impact": 0.0})
        c["n"] += 1
        c["impact"] += r["impact"]
    return {
        "items": rows[:40],
        "n_total": len(rows),
        "n_directional": len(directional),
        "n_bull": sum(1 for r in directional if r["direction"] > 0),
        "n_bear": sum(1 for r in directional if r["direction"] < 0),
        "score": raw_score * coverage,
        "by_category": {k: {"n": v["n"], "impact": round(v["impact"], 3)} for k, v in by_cat.items()},
        "queries": NEWS_QUERIES,
    }


def _safe(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("NG fetch %s failed: %s", getattr(fn, "__name__", fn), exc)
        return default


# ---------------------------------------------------------------- factors

def _f(key: str, score: float | None, reading: str, detail: str, data: dict | None = None) -> dict:
    return {
        "key": key,
        "name": FACTOR_META[key]["name"],
        "why": FACTOR_META[key]["why"],
        "score": _r(_clip(score), 3) if score is not None else None,
        "reading": reading,
        "detail": detail,
        "data": data or {},
    }


def factor_weather(w: dict | None, season: str) -> dict:
    if not w:
        return _f("weather", None, "天气数据不可用", "Open-Meteo 抓取失败，本因子不计入。")
    dev1 = w["d1_7"]["dev_pct"]
    dev2 = w["d8_16"]["dev_pct"]
    devs = [(dev1, 0.6), (dev2, 0.4)]
    devs = [(v, k) for v, k in devs if v is not None]
    if not devs:
        return _f("weather", None, "常态度日缺失", "无法与常态比较。")
    dev = sum(v * k for v, k in devs) / sum(k for _v, k in devs)
    # 度日基数很小时（春秋），百分比偏差会被放大，按季节收敛
    scale = 25.0 if season == "winter" else (35.0 if season == "summer" else 45.0)
    level = _clip(dev / scale)
    rev = w.get("revision")
    rev_score, rev_txt = None, "暂无 12–48 小时前的预报快照（运行一天后自动出现）"
    if rev and rev.get("normal_tdd"):
        rev_pct = rev["delta"] / max(rev["normal_tdd"], 5) * 100
        rev_score = _clip(rev_pct / 10.0)
        rev_txt = f"较 {rev['hours_ago']:.0f} 小时前预报：未来 {rev['days']} 天总度日 {rev['delta']:+.1f}（{rev_pct:+.1f}% 常态），{'转冷/需求上修' if rev['delta'] > 0 else '转暖/需求下修'}"
    score = level if rev_score is None else 0.65 * level + 0.35 * rev_score
    reading = f"1–7 日度日 {w['d1_7']['fcst']:.0f} vs 常态 {w['d1_7']['normal']:.0f}（{dev1:+.1f}%）"
    if dev2 is not None:
        reading += f"；8–16 日 {dev2:+.1f}%"
    detail = (
        f"需求加权度日（HDD+CDD）相对近 5 年同期偏差加权约 {dev:+.1f}%，"
        f"{'偏冷/偏热 → 需求高于常态' if dev > 0 else '偏温和 → 需求低于常态'}。{rev_txt}。"
    )
    return _f("weather", score, reading, detail, {"dev_pct": dev, "level": level, "revision_score": rev_score})


def factor_storage(s: dict | None) -> dict:
    if not s:
        return _f("storage", None, "库存数据不可用", "EIA 抓取失败。")
    dev = s.get("vs_avg5_pct")
    sur = s.get("surprise_vs_5y")
    level = _clip(-(dev or 0) / 8.0)  # 高于 5 年均值 8% → −1
    surp = _clip(-(sur or 0) / 30.0) if sur is not None else None  # 比季节常态多注入 30 Bcf → −1
    proj = s.get("proj_end_vs_avg5_pct")
    proj_s = _clip(-(proj or 0) / 12.0) if proj is not None else None
    parts = [(level, 0.5), (surp, 0.25), (proj_s, 0.25)]
    parts = [(v, k) for v, k in parts if v is not None]
    score = sum(v * k for v, k in parts) / sum(k for _v, k in parts)
    act = "注入" if s["net_change"] >= 0 else "去化"
    reading = (
        f"{s['asof']} 库存 {s['stocks']:.0f} Bcf，较 5 年均值 {s['vs_avg5']:+.0f} Bcf（{dev:+.1f}%），"
        f"当周{act} {abs(s['net_change']):.0f} Bcf"
    )
    detail = (
        f"当周变化 {s['net_change']:+.0f} vs 5 年同周均值 {s['avg5_change']:+.0f}，"
        f"意外 {sur:+.0f} Bcf（{'偏空：比常态多注/少采' if sur > 0 else '偏多：比常态少注/多采'}）。"
        f"按 5 年均值节奏推演，{s['proj_target']}库存约 {s['proj_end']:.0f} Bcf，"
        f"较 5 年均值 {proj:+.1f}%。同比 {s['vs_yago_pct']:+.1f}%。"
        if sur is not None and proj is not None and s.get("vs_yago_pct") is not None
        else "部分对比数据缺失。"
    )
    return _f("storage", score, reading, detail, {"level": level, "surprise": surp, "projection": proj_s})


def factor_lng(l: dict | None) -> dict:
    if not l:
        return _f("lng", None, "TTF/汇率数据不可用", "")
    sp = l["spread"]
    # 价差 ≥6 美元：出口满负荷、偏多；3.5–6：中性偏多；<3.5 逐步转空，1.5 以下取消货风险高
    if sp >= 6:
        score = 0.5
    elif sp >= 3.5:
        score = (sp - 3.5) / 2.5 * 0.5
    else:
        score = -min(1.0, (3.5 - sp) / 2.0)
    trend = _clip(l["spread_chg_20d"] / 4.0) * 0.3
    score = _clip(score + trend)
    reading = f"TTF ≈ ${l['ttf_usd']:.2f}/MMBtu（€{l['ttf_eur_mwh']:.1f}/MWh），HH ${l['hh']:.2f}，价差 ${sp:.2f}"
    detail = (
        f"扣除液化（约 $2.5–3）与跨大西洋运输（约 $0.7–1.2）后，价差 {'足以覆盖，出口满负荷、外需稳固' if sp >= 4 else '接近或低于盈亏线，需警惕取消货'}；"
        f"近 20 日价差变化 {l['spread_chg_20d']:+.2f}，TTF {l['ttf_chg_20d_pct']:+.1f}%。"
        "注意：美国 LNG 出口多数时间受产能（而非价差）约束，价差的边际作用在收窄时才显著。"
    )
    return _f("lng", score, reading, detail)


def factor_technical(p: dict | None) -> dict:
    if not p:
        return _f("technical", None, "价格数据不可用", "")
    px = p["price"]
    trend = _clip((px / p["ma60"] - 1) / 0.15)
    mom = _clip((p["chg_21d"] or 0) / 20.0)
    rsi = p["rsi14"]
    rsi_adj = -0.4 if rsi > 75 else -0.2 if rsi > 70 else 0.4 if rsi < 25 else 0.2 if rsi < 30 else 0.0
    score = _clip(0.5 * trend + 0.3 * mom + rsi_adj)
    reading = f"价 {px:.3f}，MA20 {p['ma20']:.3f}，MA60 {p['ma60']:.3f}，RSI {rsi:.0f}，21日 {p['chg_21d']:+.1f}%"
    pos = "站上" if px > p["ma60"] else "跌破"
    detail = (
        f"价格{pos} 60 日均线（{(px / p['ma60'] - 1) * 100:+.1f}%），"
        f"{'RSI 超买，短线有回吐风险' if rsi > 70 else 'RSI 超卖，短线有反弹动能' if rsi < 30 else 'RSI 中性'}；"
        f"20 日年化波动率 {p['vol20']:.0f}%，ATR(14) {p['atr14']:.3f}。连续合约换月会造成跳空，均线信号需结合主力合约。"
    )
    return _f("technical", score, reading, detail)


def factor_curve(c: dict | None) -> dict:
    if not c or c.get("m1_m2_pct") is None:
        return _f("curve", None, "远期曲线不可用", "")
    s = c["m1_m2_pct"]
    # 冬季次月天然升水约 3%，超出部分视为现货过剩
    norm = 3.0 if c["front"]["month"] in (10, 11, 12) else 1.0
    score = _clip(-(s - norm) / 15.0)
    reading = f"近月 {c['front']['label']} {c['m1']:.3f} vs 次月 {c['m2']:.3f}（{s:+.1f}%）"
    w, su = c.get("winter_avg"), c.get("summer_avg")
    ws = f"；12 个月冬季合约均价 {w:.3f} / 夏季 {su:.3f}（冬夏价差 {w - su:+.3f}）" if w and su else ""
    detail = (
        f"{'近月相对次月深度贴水（contango 过陡），市场在为储存付费 → 即期偏松' if s > norm + 3 else '近月升水或曲线平坦 → 即期偏紧' if s < norm - 2 else '曲线结构接近季节常态'}{ws}。"
    )
    return _f("curve", score, reading, detail)


def factor_cot(c: dict | None) -> dict:
    if not c:
        return _f("cot", None, "CFTC 数据不可用", "AKShare 源抓取失败或字段变化，本因子不计入。")
    pct = c["pct_3y"]
    score = _clip((pct - 50) / 50 * 0.8)
    reading = f"商业净持仓 {c['net']:,.0f} 手（3 年分位 {pct:.0f}%），周变化 {c['wow']:+,.0f}" if c.get("wow") is not None else f"商业净持仓 {c['net']:,.0f}"
    detail = (
        "商业（套保方）净多处于高分位 ≈ 投机资金偏空拥挤 → 空头回补风险（反向偏多）；"
        "低分位 ≈ 投机资金偏多拥挤 → 多头踩踏风险（反向偏空）。"
        f"数据截至 {c.get('asof') or '—'}。"
    )
    return _f("cot", score, reading, detail)


def factor_cost(p: dict | None) -> dict:
    if not p:
        return _f("cost", None, "价格数据不可用", "")
    from cost_benchmarks import get_cost

    c = get_cost("US:NG=F")
    if not c:
        return _f("cost", None, "无成本锚", "")
    px, cash, prim, hi = p["price"], c["cash_cost"], c["primary_cost"], c["comfort_high"]
    if px < cash:
        score = 0.9
    elif px < prim:
        score = (prim - px) / (prim - cash) * 0.6
    elif px < hi:
        score = -(px - prim) / (hi - prim) * 0.4
    else:
        score = -min(0.8, 0.4 + (px - hi) / hi)
    reading = f"现价 {px:.2f} vs 现金成本 {cash} / 全周期 {prim} / 激励上沿 {hi}（USD/MMBtu）"
    detail = (
        "低于全周期成本 → 钻井放缓、中期供给收缩（偏多但慢）；高于激励上沿 → 刺激增产（偏空）。"
        "伴生气（Permian 石油钻井产出）不受气价约束，削弱了成本支撑。成本锚为静态行业估计。"
    )
    return _f("cost", score, reading, detail)


def factor_hurricane(h: dict | None, d: date) -> dict:
    in_season = d.month in (6, 7, 8, 9, 10, 11)
    if not h:
        return _f("hurricane", None, "NHC 数据不可用", "")
    if not in_season or h["n_active"] == 0:
        f = _f("hurricane", None, "无活跃大西洋风暴" if in_season else "非飓风季", "当前不计入综合分。", {"titles": h.get("titles")})
        return f
    score = -0.3 - 0.1 * min(3, h["n_active"] - 1)
    return _f(
        "hurricane",
        score,
        f"活跃风暴 {h['n_active']} 个：" + "；".join(h["active"][:2]),
        "需关注路径是否指向路易斯安那/得州沿岸 LNG 终端（Sabine Pass、Cameron、Calcasieu、Freeport、Corpus Christi）。",
        {"titles": h.get("active")},
    )


def factor_news(n: dict | None) -> dict:
    if not n:
        return _f("news", None, "新闻源不可用", "Google News RSS 抓取失败。")
    top = [r for r in n["items"] if r["direction"] != 0][:3]
    cats = "，".join(f"{k}{'+' if v['impact'] > 0 else ''}{v['impact']:.2f}" for k, v in sorted(n["by_category"].items(), key=lambda kv: -abs(kv[1]["impact"]))[:4])
    reading = f"近 4 天相关新闻 {n['n_total']} 条，可判向 {n['n_directional']} 条（利多 {n['n_bull']} / 利空 {n['n_bear']}）"
    detail = (
        "按类别影响权重 × 时效衰减（半衰期 36h）× 来源可信度加权。"
        + (f"分类净影响：{cats}。" if cats else "")
        + ("影响最大：" + "；".join(f"「{t['title'][:70]}」" for t in top) if top else "")
        + " 关键词规则判向，存在误判可能，请点开原文核实。"
    )
    return _f("news", n["score"], reading, detail)


# ---------------------------------------------------------------- composite & advice

def _events(today_et: datetime, curve: dict | None) -> list[dict]:
    ev = []
    now = today_et

    def add(name: str, dt: datetime, note: str):
        ev.append({
            "name": name,
            "time_et": dt.strftime("%Y-%m-%d %H:%M ET"),
            "time_bj": _to_bj(dt).strftime("%m-%d %H:%M 北京"),
            "ts": dt.timestamp(),
            "in_hours": round((dt - now).total_seconds() / 3600, 1),
            "note": note,
        })

    d = now.date()
    days = (3 - d.weekday()) % 7
    eia = _et_dt(d + timedelta(days=days), 10, 30)
    if eia <= now:
        eia = _et_dt(d + timedelta(days=days + 7), 10, 30)
    add("EIA 周度库存报告", eia, "每周四 10:30 ET；节假日周可能提前至周三。发布前后 15 分钟波动最大。")
    days = (4 - d.weekday()) % 7
    cot = _et_dt(d + timedelta(days=days), 15, 30)
    if cot <= now:
        cot = _et_dt(d + timedelta(days=days + 7), 15, 30)
    add("CFTC 持仓报告", cot, "每周五 15:30 ET，反映周二持仓。")
    if curve and curve.get("front"):
        f = curve["front"]
        exp = date.fromisoformat(f["expiry"])
        add(f"近月 {f['ticker']} 期货到期", _et_dt(exp, 14, 30), "到期前 3–5 个交易日流动性转向次月，持仓需提前换月；实物交割需避免。")
        opt = (pd.Timestamp(exp) - _cbd()).date()
        add(f"近月 {f['ticker']} 期权到期", _et_dt(opt, 14, 30), "期权到期日（期货到期前 1 个营业日），大量行权价附近易“钉价”。")
    ev.sort(key=lambda e: e["ts"])
    return ev


def _advice(composite: float, conf: float, price: dict | None, factors: list[dict], events: list[dict]) -> dict:
    if not price:
        return {"bias": "unknown", "bias_cn": "数据不足", "summary": "价格数据缺失，无法给出建议。"}
    px, atr, vol = price["price"], price["atr14"], price["vol20"]
    s = composite
    if s >= 0.35:
        bias, cn, act = "long", "偏多", "顺势做多：等待回调到入场区分批买入，不追高。"
    elif s >= 0.15:
        bias, cn, act = "lean_long", "轻度偏多", "轻仓试多或仅做回调低吸；信号不强，仓位减半。"
    elif s > -0.15:
        bias, cn, act = "neutral", "观望/区间", "多空因子相互抵消，宜观望或在区间上下沿做短线高抛低吸，等待周四库存或天气预报修正打破平衡。"
    elif s > -0.35:
        bias, cn, act = "lean_short", "轻度偏空", "轻仓试空或仅做反弹高空；信号不强，仓位减半。"
    else:
        bias, cn, act = "short", "偏空", "顺势做空：等待反弹到入场区分批卖出，不追空。"

    stop_mult = 2.0 if vol < 70 else 2.5
    if bias in ("long", "lean_long"):
        entry = [px - 0.6 * atr, px - 0.1 * atr]
        stop = entry[0] - stop_mult * atr
        tgt = [px + 1.5 * atr, px + 3.0 * atr]
    elif bias in ("short", "lean_short"):
        entry = [px + 0.1 * atr, px + 0.6 * atr]
        stop = entry[1] + stop_mult * atr
        tgt = [px - 1.5 * atr, px - 3.0 * atr]
    else:
        entry, stop, tgt = None, None, None
    mid = (entry[0] + entry[1]) / 2 if entry else None
    rr = (abs(tgt[1] - mid) / abs(mid - stop)) if entry and stop and mid != stop else None
    size_mult = 1.0 if bias in ("long", "short") else 0.5 if bias in ("lean_long", "lean_short") else 0.0

    risks = []
    for e in events:
        if 0 <= e["in_hours"] <= 24 and "EIA" in e["name"]:
            risks.append(f"{e['name']}将在 {e['in_hours']:.0f} 小时内发布（{e['time_bj']}）：建议发布前不新开仓或减半仓位。")
            size_mult *= 0.5
        if "期货到期" in e["name"] and 0 <= e["in_hours"] <= 24 * 7:
            risks.append(f"{e['name']}（{e['time_bj']}），请交易次月合约或提前换月。")
    if vol >= 80:
        risks.append(f"20 日年化波动率 {vol:.0f}% 处于极高水平，止损已放宽至 {stop_mult}×ATR，仓位应进一步降低。")
        size_mult *= 0.7
    if conf < 45:
        risks.append(f"模型置信度仅 {conf:.0f}/100（因子分歧大或数据缺失），建议降低仓位或观望。")
        size_mult *= 0.7
    risks.append("NG 期货杠杆高、单日 ±5–10% 常见；周末天气模型更新会造成周一跳空。")
    risks.append("UNG/BOIL/KOLD 等 ETF 存在展期损耗与杠杆衰减，不适合长期持有。")

    # 失效条件：取贡献最大的两个因子
    inval_tpl = {
        "weather": ("下一轮预报（6–15 日）度日下修超过 10%", "下一轮预报（6–15 日）度日上修超过 10%"),
        "storage": ("周四 EIA 库存变化比 5 年同周均值偏空超过 15 Bcf", "周四 EIA 库存变化比 5 年同周均值偏多超过 15 Bcf"),
        "lng": ("TTF−HH 价差跌破 $3.5 或出现主要 LNG 终端停机", "TTF−HH 价差回升至 $6 以上且 LNG 原料气创新高"),
        "technical": ("收盘跌破 60 日均线", "收盘站上 60 日均线"),
        "news": ("出现新的 LNG 停机/暖冬预报类重大利空", "出现产量冻停/寒潮类重大利多"),
        "curve": ("近月相对次月贴水进一步扩大", "近月转为升水"),
        "cot": ("投机净多快速堆积至历史高位", "投机净空快速堆积至历史高位"),
        "cost": ("价格大幅偏离成本锚（慢变量，参考意义有限）", "价格大幅偏离成本锚（慢变量，参考意义有限）"),
        "hurricane": ("风暴路径转向远离 LNG 终端", "风暴直击 LNG 终端"),
    }
    long_side = bias in ("long", "lean_long")
    contrib = sorted([f for f in factors if f.get("contribution") is not None], key=lambda f: -abs(f["contribution"]))
    invalid = []
    for f in contrib[:3]:
        t = inval_tpl.get(f["key"])
        if t:
            invalid.append(t[0] if long_side else t[1])

    drivers = [f"{f['name']}（{'+' if f['contribution'] > 0 else ''}{f['contribution'] * 100:.1f}）" for f in contrib[:3]]
    summary = f"综合分 {s * 100:+.0f}（{cn}），置信度 {conf:.0f}/100。主要驱动：{'、'.join(drivers)}。{act}"

    return {
        "bias": bias,
        "bias_cn": cn,
        "action": act,
        "summary": summary,
        "price": px,
        "atr": atr,
        "entry": [_r(x, 3) for x in entry] if entry else None,
        "stop": _r(stop, 3),
        "targets": [_r(x, 3) for x in tgt] if tgt else None,
        "rr": _r(rr, 2),
        "stop_mult": stop_mult,
        "size_mult": _r(size_mult, 2),
        "risks": risks,
        "invalidation": invalid,
        "horizon": "1–3 周波段（随每周四库存与每日天气预报更新而变化）",
        "contracts": CONTRACTS,
        "disclaimer": "以上为基于公开数据的规则化量化模型输出，仅供研究，不构成投资建议。期货交易风险极高，请独立判断并严格风控。",
    }


def _append_history(composite: float, price: float | None) -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hist: list[dict] = []
    if SCORE_HISTORY.exists():
        try:
            for line in SCORE_HISTORY.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    hist.append(json.loads(line))
        except Exception:
            hist = []
    now = time.time()
    if not hist or now - hist[-1].get("ts", 0) >= 55 * 60:
        rec = {"ts": now, "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "score": round(composite * 100, 1), "price": _r(price, 4)}
        hist.append(rec)
        with SCORE_HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return hist[-500:]


def build_dashboard(force: bool = False) -> dict:
    global _dashboard
    if not force and _dashboard and time.time() - _dashboard[0] < DASHBOARD_TTL:
        return _dashboard[1]
    with _compute_lock:
        if not force and _dashboard and time.time() - _dashboard[0] < DASHBOARD_TTL:
            return _dashboard[1]
        t0 = time.time()
        jobs = {
            "price": (fetch_price, SOURCE_TTL["price"]),
            "curve": (fetch_curve, SOURCE_TTL["curve"]),
            "storage": (fetch_storage, SOURCE_TTL["storage"]),
            "weather": (fetch_weather, SOURCE_TTL["weather"]),
            "news": (fetch_news, SOURCE_TTL["news"]),
            "lng": (fetch_lng, SOURCE_TTL["lng"]),
            "cot": (fetch_cot, SOURCE_TTL["cot"]),
            "hurricane": (fetch_hurricane, SOURCE_TTL["hurricane"]),
        }
        src: dict[str, Any] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {k: pool.submit(_cached, k, ttl, fn, force) for k, (fn, ttl) in jobs.items()}
            for k, fut in futs.items():
                try:
                    src[k] = fut.result(timeout=60)
                except Exception as exc:
                    src[k] = None
                    errors[k] = f"{type(exc).__name__}: {exc}"[:200]

        et_now = _et_now()
        season = _season(et_now.date())
        base_w = SEASON_WEIGHTS[season]
        factors = [
            factor_weather(src["weather"], season),
            factor_storage(src["storage"]),
            factor_news(src["news"]),
            factor_lng(src["lng"]),
            factor_technical(src["price"]),
            factor_curve(src["curve"]),
            factor_cot(src["cot"]),
            factor_cost(src["price"]),
            factor_hurricane(src["hurricane"], et_now.date()),
        ]
        avail_w = sum(base_w[f["key"]] for f in factors if f["score"] is not None)
        total_w = sum(base_w.values())
        composite = 0.0
        for f in factors:
            f["base_weight"] = base_w[f["key"]]
            if f["score"] is None or avail_w <= 0:
                f["weight"] = 0.0
                f["contribution"] = None
                continue
            f["weight"] = base_w[f["key"]] / avail_w
            f["contribution"] = f["weight"] * f["score"]
            composite += f["contribution"]
        abs_sum = sum(abs(f["contribution"]) for f in factors if f["contribution"] is not None)
        agreement = abs(composite) / abs_sum if abs_sum > 0 else 0.0
        coverage = avail_w / total_w if total_w else 0.0
        confidence = 100 * coverage * (0.45 + 0.55 * agreement)

        events = _events(et_now, src["curve"])
        advice = _advice(composite, confidence, src["price"], factors, events)
        history = _append_history(composite, (src["price"] or {}).get("price"))

        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_sec": round(time.time() - t0, 1),
            "season": season,
            "season_cn": {"winter": "采暖季（11–3 月）", "summer": "制冷季（6–9 月）", "shoulder": "过渡季（4–5、10 月）"}[season],
            "composite": round(composite, 4),
            "composite_100": round(composite * 100, 1),
            "confidence": round(confidence, 1),
            "agreement": round(agreement, 3),
            "coverage": round(coverage, 3),
            "factors": factors,
            "advice": advice,
            "events": events,
            "price": src["price"],
            "curve": src["curve"],
            "storage": src["storage"],
            "weather": src["weather"],
            "lng": src["lng"],
            "cot": src["cot"],
            "hurricane": src["hurricane"],
            "news": src["news"],
            "history": history,
            "errors": errors,
            "method": {
                "scale": "每个因子输出 −1（强利空）到 +1（强利多），按季节权重加权求和得到综合分（×100 显示）。",
                "confidence": "置信度 = 数据覆盖率 ×（0.45 + 0.55 × 因子一致性）。一致性 = |Σ贡献| / Σ|贡献|。",
                "weights": base_w,
            },
            "disclaimer": advice.get("disclaimer"),
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = LAST_PAYLOAD.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(LAST_PAYLOAD)
        except Exception:
            logger.debug("NG payload persist failed", exc_info=True)
        _dashboard = (time.time(), payload)
        logger.info("NG dashboard built in %.1fs, composite=%+.1f, errors=%s", payload["elapsed_sec"], composite * 100, list(errors))
        return payload


def load_last_payload() -> dict | None:
    try:
        return json.loads(LAST_PAYLOAD.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = build_dashboard(force=True)
    print("composite", out["composite_100"], "conf", out["confidence"], "errors", out["errors"])
    for f in out["factors"]:
        print(f"{f['name']:<20} score={f['score']} w={f['weight']:.2f} | {f['reading']}")
    print(out["advice"]["summary"])
    for n in out["news"]["items"][:8] if out["news"] else []:
        print(n["direction"], n["category"], n["title"][:90])

