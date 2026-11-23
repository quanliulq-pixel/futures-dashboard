# -*- coding: utf-8 -*-
"""吸筹 / 诱多 量价监控（/api/accum-trap）。

把一套可计算、阈值透明的规则套在指数和自选个股上，给出吸筹/诱多得分与
历史回看（信号触发后 20/60 日收益与胜率）。研究参考，不能下单，不构成投资建议。

请求路径只读内存 / 运行时缓存 / 已提交种子，不联网。刷新在后台进行。
失败标「上次快照（日期）」；取不到显示「无数据」。不编造、不用东财 push2/push2his。

A 股日线：新浪 CN_MarketData.getKLineData（腾讯日线兜底）。
美股日线：Yahoo Finance。A 股个股股东户数：东财数据中心 datacenter-web 季报，指数与美股不适用。
筹码分布按成交量在当日高低价区间内均匀摊入价位，标注「估算，非真实持仓」。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
ET = ZoneInfo("America/New_York")
SEED_PATHS = (ROOT / "data" / "accum_trap.json", ROOT / "docs" / "api" / "accum_trap.json")
BARS_PATH = ROOT / "data" / "accum_trap_bars.json"
RUNTIME_PATH = ROOT / "data" / "accum_trap_latest.json"
WATCHLIST_PATH = ROOT / "data" / "accum_trap_watchlist.json"
DISCLAIMER = "研究参考，不能下单，不构成投资建议"
REFRESH_HOURS = 6.0
MAX_SNAPSHOT_DAYS = 10
HTTP_TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_DATALEN = 8000
BARS_KEEP = 4000

# ---- 公开阈值（写入 payload.rules，页面原样展示）----
RULES = {
    "range_days": 250,
    "low_pctile": 0.30,          # 收盘在近250日高低区间分位 ≤30% → 低位
    "high_pctile": 0.70,         # ≥70% → 高位，其间为中位
    "long_decline_dd": -0.20,    # 距250日高点回撤 ≤ -20% 视为长期下跌后
    "vol_short": 20,
    "vol_long": 60,
    "consol_days": 20,
    "consol_amplitude": 0.08,    # 近20日（最高价-最低价）/均价 ≤8% → 横盘
    "mild_vol_lo": 1.10,         # 20日均量/60日均量
    "mild_vol_hi": 1.80,
    "sudden_vol": 2.0,           # 单日量 > 2×20日均量 → 突然放量
    "upper_shadow_body": 2.0,    # 上影/实体
    "upper_shadow_range": 0.50,  # 上影/当日振幅
    "min_range_pct": 0.01,       # 振幅过小的交易日不判长上影/冲高回落
    "pattern_lookback": 20,
    "weak_after_days": 5,
    "rs_days": 20,
    "bench_down_lookback": 60,   # 在近60个交易日里取基准下跌日
    "hold_rate": 0.60,           # 基准下跌日里标的不跌的比例
    "chip_lookback": 60,
    "chip_bins": 40,
    "chip_single_share": 0.45,   # 主峰及其左右各1档占总估算量 ≥45% 视为单峰
    "chip_low_pctile": 0.30,     # 主峰落在近250日区间分位 ≤30% 为低位密集
    "holder_decline_n": 3,       # 最近连续3个报告期股东户数下降
    "accum_label_min": 60,
    "trap_label_min": 60,
    "label_margin": 10,
    "backtest_min_n": 8,
    "signal_cooldown": 20,
    "fwd_days": (20, 60),
}

ACCUM_WEIGHTS = (
    ("low_zone", "低位（250日分位≤30%）", 25),
    ("long_decline", "长期下跌后（距250日高点回撤≤-20%）", 10),
    ("tight_or_mild", "缩量横盘或温和放量", 20),
    ("chip_low_single", "筹码低位单峰密集（估算）", 15),
    ("holders_down", "股东户数持续减少", 15),
    ("holds_on_down", "大盘跌它不跌", 20),
)
TRAP_WEIGHTS = (
    ("mid_or_high", "中高位（250日分位>30%）", 25),
    ("sudden_rally", "中高位突然放量拉升", 20),
    ("wick_or_reject", "长上影或冲高回落", 15),
    ("diverge", "量价背离（价创新高量未放大）", 15),
    ("weak_after", "放量后3-5日无力", 15),
)

CN_INDICES = (
    {"id": "sse", "name": "上证指数", "market": "cn", "kind": "index",
     "sina": "sh000001", "code": "000001", "bench_id": "csi300"},
    {"id": "csi300", "name": "沪深300", "market": "cn", "kind": "index",
     "sina": "sh000300", "code": "000300", "bench_id": "csi300", "is_bench": True},
    {"id": "sse50", "name": "上证50", "market": "cn", "kind": "index",
     "sina": "sh000016", "code": "000016", "bench_id": "csi300"},
    {"id": "csi1000", "name": "中证1000", "market": "cn", "kind": "index",
     "sina": "sh000852", "code": "000852", "bench_id": "csi300"},
)
US_INDICES = (
    {"id": "sp500", "name": "标普500", "market": "us", "kind": "index",
     "yahoo": "^GSPC", "bench_id": "sp500", "is_bench": True},
    {"id": "ndx", "name": "纳指100", "market": "us", "kind": "index",
     "yahoo": "^NDX", "bench_id": "sp500"},
    {"id": "rut", "name": "罗素2000", "market": "us", "kind": "index",
     "yahoo": "^RUT", "bench_id": "sp500"},
)

SOURCES = [
    {"label": "新浪财经 指数/个股日线 CN_MarketData.getKLineData",
     "url": "https://finance.sina.com.cn/"},
    {"label": "腾讯财经 日线兜底（A股）",
     "url": "https://gu.qq.com/"},
    {"label": "Yahoo Finance 美股/美指日线",
     "url": "https://finance.yahoo.com/quote/%5EGSPC/history/"},
    {"label": "东方财富数据中心 股东户数（仅A股个股，datacenter-web，非 push2）",
     "url": "https://data.eastmoney.com/gdhs/"},
]

_lock = threading.Lock()
_cache: dict | None = None


def _now() -> datetime:
    return datetime.now(CST)


def _r(v: Any, n: int = 4) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or math.isinf(x):
        return None
    return round(x, n)


def _to_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    text = str(v or "").strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and not math.isinf(x) else None


# ---------------------------------------------------------------- bars I/O
def bars_to_rows(df: pd.DataFrame | None) -> list[list]:
    if df is None or df.empty:
        return []
    out = []
    for idx, row in df.iterrows():
        d = _to_date(idx)
        o, h, l, c, v = (_num(row.get(k)) for k in ("open", "high", "low", "close", "volume"))
        if d is None or c is None:
            continue
        out.append([d.isoformat(), _r(o, 4), _r(h, 4), _r(l, 4), _r(c, 4), _r(v, 2)])
    return out[-BARS_KEEP:]


def rows_to_frame(rows: list | None) -> pd.DataFrame:
    recs = []
    for p in rows or []:
        if not isinstance(p, (list, tuple)) or len(p) < 5:
            continue
        d = _to_date(p[0])
        o, h, l, c = (_num(p[1]), _num(p[2]), _num(p[3]), _num(p[4]))
        v = _num(p[5]) if len(p) > 5 else None
        if d is None or c is None:
            continue
        recs.append({"date": pd.Timestamp(d), "open": o if o is not None else c,
                     "high": h if h is not None else c,
                     "low": l if l is not None else c,
                     "close": c, "volume": v})
    if not recs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()
    return df


def merge_frames(a: pd.DataFrame | None, b: pd.DataFrame | None) -> pd.DataFrame:
    parts = [x for x in (a, b) if x is not None and not x.empty]
    if not parts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.tail(BARS_KEEP)


def drop_incomplete(df: pd.DataFrame, now: datetime | None, market: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    now = now or _now()
    today = now.date() if now.tzinfo is None else now.astimezone(CST if market == "cn" else ET).date()
    last = _to_date(df.index[-1])
    if last != today:
        return df
    if market == "cn":
        cutoff_passed = now.astimezone(CST).time() >= dtime(15, 5)
    else:
        cutoff_passed = now.astimezone(ET).time() >= dtime(16, 15)
    return df if cutoff_passed else df.iloc[:-1]


# ---------------------------------------------------------------- features
def _rolling_prep(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    n = RULES["range_days"]
    vs, vl, cd, pl = RULES["vol_short"], RULES["vol_long"], RULES["consol_days"], RULES["pattern_lookback"]
    out["high_250"] = out["high"].rolling(n, min_periods=n).max()
    out["low_250"] = out["low"].rolling(n, min_periods=n).min()
    out["vol20"] = out["volume"].rolling(vs, min_periods=vs).mean()
    out["vol60"] = out["volume"].rolling(vl, min_periods=vl).mean()
    out["high_20"] = out["high"].rolling(pl, min_periods=pl).max()
    out["close_20_max"] = out["close"].rolling(pl, min_periods=pl).max()
    out["vol_20_max"] = out["volume"].rolling(pl, min_periods=pl).max()
    out["hi_20"] = out["high"].rolling(cd, min_periods=cd).max()
    out["lo_20"] = out["low"].rolling(cd, min_periods=cd).min()
    out["mean_20"] = out["close"].rolling(cd, min_periods=cd).mean()
    out["ret"] = out["close"].pct_change()
    return out


def _pos_bucket(pctile: float | None) -> str | None:
    if pctile is None:
        return None
    if pctile <= RULES["low_pctile"]:
        return "低位"
    if pctile >= RULES["high_pctile"]:
        return "高位"
    return "中位"


def _candle_flags(row: pd.Series, prev_high20: float | None, prev_close20: float | None,
                  prev_vol20max: float | None, vol20: float | None) -> dict:
    o, h, l, c = (_num(row.get(k)) for k in ("open", "high", "low", "close"))
    vol = _num(row.get("volume"))
    out = {"long_upper_shadow": False, "reject_high": False, "price_vol_diverge": False,
           "upper": None, "body": None, "range": None}
    if o is None or h is None or l is None or c is None:
        return out
    rng = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    out["upper"] = _r(upper, 4)
    out["body"] = _r(body, 4)
    out["range"] = _r(rng, 4)
    min_rng = abs(c) * RULES["min_range_pct"]
    if rng >= min_rng and rng > 0:
        body_den = body if body > 0 else rng * 1e-6
        out["long_upper_shadow"] = (upper / body_den >= RULES["upper_shadow_body"]
                                    and upper / rng >= RULES["upper_shadow_range"])
        at_high = prev_high20 is not None and h >= prev_high20
        lower_half = c <= (l + 0.40 * rng)
        out["reject_high"] = bool(at_high and lower_half)
    if prev_close20 is not None and c >= prev_close20:
        # 价创20日新高而量未放大（量低于20日均量，或根本没有放量）
        if vol20 is not None and vol is not None:
            out["price_vol_diverge"] = vol < vol20
        elif vol is None:
            out["price_vol_diverge"] = False
    return out


def _weak_after_spike(df: pd.DataFrame, i: int) -> bool:
    """i 为当日下标。回看近 3–5 日前是否有突然放量，且此后收盘无力（未站上放量日收盘/高点）。"""
    days = RULES["weak_after_days"]
    if i < days + RULES["vol_short"]:
        return False
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    vol = df["volume"].to_numpy()
    vol20 = df["vol20"].to_numpy()
    today_close = close[i]
    lo = i - days
    hi = i - 3
    if hi < 0 or lo < 0:
        return False
    for j in range(lo, hi + 1):
        v20 = vol20[j]
        if not (v20 == v20) or v20 <= 0:
            continue
        if not (vol[j] == vol[j]) or vol[j] <= RULES["sudden_vol"] * v20:
            continue
        # 放量日后至今日，最高收盘未能超过放量日收盘，或今日收在放量日收盘之下
        after = close[j + 1:i + 1]
        if after.size == 0:
            continue
        if today_close < close[j] and (after.max() <= close[j] or today_close < high[j]):
            return True
    return False


def estimate_chips(df: pd.DataFrame, end_i: int | None = None) -> dict:
    """成交量按当日 [low, high] 均匀摊入价位的估算分布。非真实持仓。"""
    look = RULES["chip_lookback"]
    bins = RULES["chip_bins"]
    frame = df.iloc[:None if end_i is None else end_i + 1].tail(look)
    empty = {"status": "missing", "reason": "无数据", "note": "估算，非真实持仓",
             "lookback": look, "peak_price": None, "peak_share": None,
             "single_peak": None, "low_zone": None, "low_single_peak": None}
    if frame is None or len(frame) < 10:
        return empty
    lo = _num(frame["low"].min())
    hi = _num(frame["high"].max())
    if lo is None or hi is None or hi <= lo:
        return empty
    edges = [lo + (hi - lo) * k / bins for k in range(bins + 1)]
    mass = [0.0] * bins
    used = 0.0
    for _, row in frame.iterrows():
        v = _num(row.get("volume"))
        a, b = _num(row.get("low")), _num(row.get("high"))
        if v is None or v <= 0 or a is None or b is None:
            continue
        if b <= a:
            # 全计入最接近的一档
            mid = (a + b) / 2 if b == a else a
            k = min(bins - 1, max(0, int((mid - lo) / (hi - lo) * bins)))
            mass[k] += v
            used += v
            continue
        # 均匀摊入覆盖到的档
        overlap = []
        for k in range(bins):
            left, right = edges[k], edges[k + 1]
            ov = min(b, right) - max(a, left)
            if ov > 0:
                overlap.append((k, ov))
        total = sum(ov for _, ov in overlap) or (b - a)
        for k, ov in overlap:
            mass[k] += v * (ov / total)
        used += v
    if used <= 0:
        return empty
    peak_i = max(range(bins), key=lambda k: mass[k])
    peak_share = mass[peak_i] / used
    neighbor = mass[peak_i]
    if peak_i > 0:
        neighbor += mass[peak_i - 1]
    if peak_i < bins - 1:
        neighbor += mass[peak_i + 1]
    single = (neighbor / used) >= RULES["chip_single_share"]
    peak_price = (edges[peak_i] + edges[peak_i + 1]) / 2
    # 低位：主峰相对近250日（若不足则用窗口）高低区间的分位
    win = df.iloc[:None if end_i is None else end_i + 1].tail(RULES["range_days"])
    rng_lo = _num(win["low"].min())
    rng_hi = _num(win["high"].max())
    low_zone = None
    if rng_lo is not None and rng_hi is not None and rng_hi > rng_lo:
        pctile = (peak_price - rng_lo) / (rng_hi - rng_lo)
        low_zone = pctile <= RULES["chip_low_pctile"]
    return {
        "status": "ok",
        "reason": None,
        "note": "估算，非真实持仓",
        "lookback": int(len(frame)),
        "peak_price": _r(peak_price, 4),
        "peak_share": _r(peak_share, 4),
        "single_peak": bool(single),
        "low_zone": low_zone,
        "low_single_peak": bool(single and low_zone),
    }


def _relative(df: pd.DataFrame, bench: pd.DataFrame | None, i: int) -> dict:
    empty = {"rs_20": None, "down_day_excess": None, "down_day_hold_rate": None,
             "holds_on_bench_down": None, "n_bench_down": 0}
    if bench is None or bench.empty or i < RULES["rs_days"]:
        return empty
    # 对齐日期
    sub = df.iloc[i - RULES["bench_down_lookback"] + 1:i + 1] if i + 1 >= RULES["bench_down_lookback"] else df.iloc[:i + 1]
    aligned = sub[["close"]].join(bench[["close"]].rename(columns={"close": "bench"}), how="inner")
    if len(aligned) < RULES["rs_days"] + 1:
        return empty
    aligned = aligned.sort_index()
    aligned["ret"] = aligned["close"].pct_change()
    aligned["bret"] = aligned["bench"].pct_change()
    last = aligned.iloc[-RULES["rs_days"]:]
    if last["close"].iloc[0] == 0 or last["bench"].iloc[0] == 0:
        return empty
    # 用区间首日到末日的累计收益差作为 20 日相对强弱
    r20 = last["close"].iloc[-1] / last["close"].iloc[0] - 1
    b20 = last["bench"].iloc[-1] / last["bench"].iloc[0] - 1
    window = aligned.dropna()
    down = window[window["bret"] < 0]
    n_down = int(len(down))
    if n_down <= 0:
        return {"rs_20": _r(r20 - b20, 4), "down_day_excess": None, "down_day_hold_rate": None,
                "holds_on_bench_down": None, "n_bench_down": 0}
    hold_rate = float((down["ret"] >= 0).mean())
    excess = float((down["ret"] - down["bret"]).mean())
    holds = (hold_rate >= RULES["hold_rate"]) or (excess > 0 and hold_rate >= 0.5)
    return {
        "rs_20": _r(r20 - b20, 4),
        "down_day_excess": _r(excess, 4),
        "down_day_hold_rate": _r(hold_rate, 4),
        "holds_on_bench_down": bool(holds),
        "n_bench_down": n_down,
    }


def holders_feature(series: list | None, *, applicable: bool) -> dict:
    """series: [[asof, count], ...] 升序。不适用或不足则如实标注。"""
    if not applicable:
        return {"status": "not_applicable", "reason": "不适用", "asof": None,
                "declining": None, "latest": None, "chg": None, "series": []}
    rows = []
    for p in series or []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        d, n = p[0], _num(p[1])
        if d and n is not None and n > 0:
            rows.append([str(d)[:10], _r(n, 0)])
    rows.sort()
    need = RULES["holder_decline_n"]
    if len(rows) < need:
        return {"status": "missing", "reason": "无数据", "asof": rows[-1][0] if rows else None,
                "declining": None, "latest": rows[-1][1] if rows else None, "chg": None, "series": rows}
    tail = rows[-need:]
    declining = all(tail[k][1] < tail[k - 1][1] for k in range(1, len(tail)))
    chg = _r(tail[-1][1] - tail[0][1], 0)
    return {"status": "ok", "reason": None, "asof": tail[-1][0], "declining": bool(declining),
            "latest": tail[-1][1], "chg": chg, "series": rows[-8:]}


def _score(hits: list[dict]) -> int | None:
    appl = [h for h in hits if h.get("applicable") is not False]
    denom = sum(h["max_points"] for h in appl)
    if denom <= 0:
        return None
    got = sum(h["points"] for h in appl if h.get("hit"))
    return int(round(100.0 * got / denom))


def _label(accum: int | None, trap: int | None, bucket: str | None) -> str:
    if accum is None and trap is None:
        return "无数据"
    a, t = accum or 0, trap or 0
    # 位置决定一切：疑似吸筹必须低位；疑似诱多必须中高位
    if (bucket == "低位" and a >= RULES["accum_label_min"]
            and a >= t + RULES["label_margin"]):
        return "疑似吸筹"
    if (bucket in ("中位", "高位") and t >= RULES["trap_label_min"]
            and t >= a + RULES["label_margin"]):
        return "疑似诱多"
    return "中性"


def analyze_bar_frame(df: pd.DataFrame, bench: pd.DataFrame | None,
                      holders: dict | None, *, i: int | None = None,
                      is_bench: bool = False) -> dict | None:
    """对截止下标 i（含）的日线计算特征与得分。i 默认最后一行。不使用未来数据。"""
    if df is None or df.empty:
        return None
    prep = df if df.columns is not None and "high_250" in getattr(df, "columns", []) else _rolling_prep(df)
    if i is None:
        i = len(prep) - 1
    if i < 0 or i >= len(prep):
        return None
    row = prep.iloc[i]
    close = _num(row.get("close"))
    high250, low250 = _num(row.get("high_250")), _num(row.get("low_250"))
    if close is None or high250 is None or low250 is None or high250 <= low250:
        return None
    pctile = (close - low250) / (high250 - low250)
    dd = close / high250 - 1
    rally = close / low250 - 1
    bucket = _pos_bucket(pctile)
    vol20, vol60 = _num(row.get("vol20")), _num(row.get("vol60"))
    today_vol = _num(row.get("volume"))
    ratio = (vol20 / vol60) if (vol20 and vol60 and vol60 > 0) else None
    vs20 = (today_vol / vol20) if (today_vol is not None and vol20 and vol20 > 0) else None
    amp = None
    hi20, lo20, mean20 = _num(row.get("hi_20")), _num(row.get("lo_20")), _num(row.get("mean_20"))
    if hi20 is not None and lo20 is not None and mean20 and mean20 > 0:
        amp = (hi20 - lo20) / mean20
    tight = amp is not None and amp <= RULES["consol_amplitude"]
    sudden = vs20 is not None and vs20 > RULES["sudden_vol"]
    mild = (ratio is not None and RULES["mild_vol_lo"] <= ratio <= RULES["mild_vol_hi"]
            and vs20 is not None and 0.9 <= vs20 <= RULES["sudden_vol"] and not sudden)
    shrink_consol = bool(tight and (ratio is None or ratio <= 1.0))
    tight_or_mild = bool(shrink_consol or mild)

    prev_high20 = _num(prep["high"].iloc[max(0, i - RULES["pattern_lookback"]):i].max()) if i > 0 else None
    prev_close20 = _num(prep["close"].iloc[max(0, i - RULES["pattern_lookback"]):i].max()) if i > 0 else None
    prev_vol20max = _num(prep["volume"].iloc[max(0, i - RULES["pattern_lookback"]):i].max()) if i > 0 else None
    flags = _candle_flags(row, prev_high20, prev_close20, prev_vol20max, vol20)
    weak = _weak_after_spike(prep, i)
    rel = _relative(prep, bench, i)
    chips = estimate_chips(prep, i)
    h = holders if isinstance(holders, dict) else holders_feature(None, applicable=False)

    # 当日涨跌（诱多「放量拉升」需要收阳）
    prev_close = _num(prep["close"].iloc[i - 1]) if i > 0 else None
    up_day = prev_close is not None and close > prev_close

    hits_accum = []
    factors = {
        "low_zone": bucket == "低位",
        "long_decline": dd <= RULES["long_decline_dd"],
        "tight_or_mild": tight_or_mild,
        "chip_low_single": bool(chips.get("status") == "ok" and chips.get("low_single_peak")),
        "holders_down": bool(h.get("status") == "ok" and h.get("declining")),
        "holds_on_down": bool(rel.get("holds_on_bench_down")),
    }
    applicable = {
        "holders_down": h.get("status") != "not_applicable",
        "chip_low_single": chips.get("status") == "ok",
        "holds_on_down": (not is_bench) and rel.get("holds_on_bench_down") is not None,
        "tight_or_mild": amp is not None or ratio is not None,
    }
    details = {
        "low_zone": f"250日分位 {pctile:.0%}" if pctile is not None else "无数据",
        "long_decline": f"回撤 {dd:.1%}" if dd is not None else "无数据",
        "tight_or_mild": (
            "；".join(x for x in (
                f"20日振幅 {amp:.1%}" if amp is not None else None,
                f"量比20/60={ratio:.2f}" if ratio is not None else None,
            ) if x) or "无数据"
        ),
        "chip_low_single": (
            f"主峰 {_r(chips.get('peak_price'), 2)}，单峰={chips.get('single_peak')}，低位={chips.get('low_zone')}"
            if chips.get("status") == "ok" else (chips.get("reason") or "无数据")
        ),
        "holders_down": (
            "不适用" if h.get("status") == "not_applicable"
            else (f"截至 {h.get('asof')} 近{RULES['holder_decline_n']}期"
                  + ("下降" if h.get("declining") else "未持续下降")
                  if h.get("status") == "ok" else (h.get("reason") or "无数据"))
        ),
        "holds_on_down": (
            f"基准下跌日不跌占比 {rel['down_day_hold_rate']:.0%}，超额 {rel['down_day_excess']:.2%}，n={rel['n_bench_down']}"
            if rel.get("holds_on_bench_down") is not None else "无数据"
        ),
    }
    for key, name, w in ACCUM_WEIGHTS:
        ap = applicable.get(key, True)
        hit = bool(ap and factors[key])
        hits_accum.append({"id": key, "name": name, "hit": hit, "applicable": ap,
                           "points": w if hit else 0, "max_points": w, "detail": details[key]})

    mid_or_high = bucket in ("中位", "高位")
    sudden_rally = bool(mid_or_high and sudden and up_day)
    wick_or_reject = bool(flags["long_upper_shadow"] or flags["reject_high"])
    hits_trap = []
    trap_factors = {
        "mid_or_high": mid_or_high,
        "sudden_rally": sudden_rally,
        "wick_or_reject": wick_or_reject,
        "diverge": bool(flags["price_vol_diverge"]),
        "weak_after": bool(weak),
    }
    trap_details = {
        "mid_or_high": f"{bucket or '无数据'}，分位 {pctile:.0%}",
        "sudden_rally": f"量/20日均={vs20:.2f}" if vs20 is not None else "无数据",
        "wick_or_reject": (
            f"上影/实体={(flags['upper'] / flags['body']) if flags['body'] else None}，冲高回落={flags['reject_high']}"
            if flags["range"] is not None else "无数据"
        ),
        "diverge": "价创20日新高且量低于20日均量" if flags["price_vol_diverge"] else "未命中",
        "weak_after": "近3–5日曾突然放量且此后收盘无力" if weak else "未命中",
    }
    for key, name, w in TRAP_WEIGHTS:
        hit = bool(trap_factors[key])
        hits_trap.append({"id": key, "name": name, "hit": hit, "applicable": True,
                          "points": w if hit else 0, "max_points": w, "detail": trap_details[key]})

    accum = _score(hits_accum)
    trap = _score(hits_trap)
    chg_1d = None
    if prev_close and prev_close > 0 and close is not None:
        chg_1d = close / prev_close - 1
    asof = _to_date(prep.index[i])
    return {
        "asof": asof.isoformat() if asof else None,
        "close": _r(close, 4),
        "chg_1d": _r(chg_1d, 4),
        "position": {
            "range_pctile": _r(pctile, 4),
            "drawdown_250": _r(dd, 4),
            "rally_from_low": _r(rally, 4),
            "high_250": _r(high250, 4),
            "low_250": _r(low250, 4),
            "bucket": bucket,
            "long_decline": bool(dd <= RULES["long_decline_dd"]),
        },
        "volume": {
            "vol20": _r(vol20, 2),
            "vol60": _r(vol60, 2),
            "ratio_20_60": _r(ratio, 3),
            "today_vs_vol20": _r(vs20, 3),
            "amplitude_20": _r(amp, 4),
            "tight_range": tight,
            "mild_expansion": mild,
            "sudden_spike": sudden,
            "shrink_consolidation": shrink_consol,
        },
        "patterns": {
            "long_upper_shadow": flags["long_upper_shadow"],
            "reject_high": flags["reject_high"],
            "price_vol_diverge": flags["price_vol_diverge"],
            "weak_after_spike": weak,
        },
        "relative": rel,
        "chips": chips,
        "holders": h,
        "accum_score": accum,
        "trap_score": trap,
        "label": _label(accum, trap, bucket),
        "hits": {"accum": hits_accum, "trap": hits_trap},
    }


def backtest_signals(df: pd.DataFrame, bench: pd.DataFrame | None,
                     holders: dict | None, *, is_bench: bool = False) -> dict:
    """无前视：第 i 日信号只用 ≤i 的数据，收益用 close[i+n]/close[i]-1。"""
    min_i = RULES["range_days"] - 1
    if df is None or len(df) < min_i + RULES["fwd_days"][-1] + 1:
        return {"accum": _empty_bt(), "trap": _empty_bt(),
                "min_n": RULES["backtest_min_n"], "insufficient": True,
                "note": "样本不足"}
    prep = _rolling_prep(df)
    n = len(prep)
    last_sig = {"疑似吸筹": -999, "疑似诱多": -999}
    buckets = {"疑似吸筹": [], "疑似诱多": []}
    cooldown = RULES["signal_cooldown"]
    fwd = RULES["fwd_days"]
    closes = df["close"].to_numpy()
    for i in range(min_i, n - fwd[-1]):
        feat = analyze_bar_frame(prep, bench, holders, i=i, is_bench=is_bench)
        if not feat:
            continue
        lab = feat["label"]
        if lab not in buckets:
            continue
        if i - last_sig[lab] < cooldown:
            continue
        last_sig[lab] = i
        rec = {"date": feat["asof"]}
        c0 = closes[i]
        if not c0:
            continue
        for d in fwd:
            rec[f"ret_{d}"] = float(closes[i + d] / c0 - 1)
        buckets[lab].append(rec)

    def _summ(rows: list) -> dict:
        if len(rows) < RULES["backtest_min_n"]:
            out = _empty_bt()
            out["n"] = len(rows)
            out["insufficient"] = True
            out["note"] = f"样本不足（n={len(rows)}，至少 {RULES['backtest_min_n']}）"
            # 仍给出已有样本的数字，避免丢掉信息
            if rows:
                _fill_bt(out, rows)
            return out
        out = _empty_bt()
        out["n"] = len(rows)
        out["insufficient"] = False
        out["note"] = None
        _fill_bt(out, rows)
        return out

    acc = _summ(buckets["疑似吸筹"])
    trp = _summ(buckets["疑似诱多"])
    insuf = bool(acc.get("insufficient") and trp.get("insufficient"))
    return {"accum": acc, "trap": trp, "min_n": RULES["backtest_min_n"],
            "insufficient": insuf, "note": "样本不足" if insuf else None}


def _empty_bt() -> dict:
    return {"n": 0, "ret_20_avg": None, "win_20": None, "ret_60_avg": None, "win_60": None,
            "insufficient": True, "note": "样本不足"}


def _fill_bt(out: dict, rows: list) -> None:
    for d in RULES["fwd_days"]:
        xs = [r[f"ret_{d}"] for r in rows if r.get(f"ret_{d}") is not None]
        if not xs:
            continue
        out[f"ret_{d}_avg"] = _r(sum(xs) / len(xs), 4)
        out[f"win_{d}"] = _r(sum(1 for x in xs if x > 0) / len(xs), 4)


# ---------------------------------------------------------------- universe / watchlist
def load_watchlist(path: Path | None = None) -> dict:
    p = path or WATCHLIST_PATH
    if not p.is_file():
        return {"note": "尚未配置个股。", "cn_stocks": [], "us_stocks": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"note": "自选列表无法读取", "cn_stocks": [], "us_stocks": []}
    if not isinstance(data, dict):
        return {"note": "自选列表格式错误", "cn_stocks": [], "us_stocks": []}
    def _items(key):
        out = []
        for raw in data.get(key) or []:
            if not isinstance(raw, dict) or not raw.get("symbol"):
                continue
            out.append({
                "symbol": str(raw["symbol"]).strip(),
                "name": str(raw.get("name") or raw["symbol"]).strip(),
                "example": bool(raw.get("example")),
            })
        return out
    return {
        "note": data.get("note") or "个股来自 data/accum_trap_watchlist.json；example=true 表示为示例，不是推荐。",
        "cn_stocks": _items("cn_stocks"),
        "us_stocks": _items("us_stocks"),
    }


def sina_symbol_for_stock(code: str) -> str | None:
    s = str(code or "").strip().lower().replace(".ss", "").replace(".sz", "")
    s = s.replace("sh", "").replace("sz", "")
    if not s.isdigit() or len(s) != 6:
        return None
    if s[0] in "569":
        return "sh" + s
    if s[0] in "03":
        return "sz" + s
    return None


def universe() -> list[dict]:
    items = [dict(x) for x in CN_INDICES] + [dict(x) for x in US_INDICES]
    wl = load_watchlist()
    for row in wl["cn_stocks"]:
        sina = sina_symbol_for_stock(row["symbol"])
        items.append({
            "id": "cn_" + row["symbol"],
            "name": row["name"],
            "market": "cn",
            "kind": "stock",
            "sina": sina or row["symbol"],
            "code": row["symbol"],
            "bench_id": "csi300",
            "example": row["example"],
            "holders_applicable": True,
        })
    for row in wl["us_stocks"]:
        items.append({
            "id": "us_" + row["symbol"].upper(),
            "name": row["name"],
            "market": "us",
            "kind": "stock",
            "yahoo": row["symbol"].upper(),
            "bench_id": "sp500",
            "example": row["example"],
        })
    return items


# ---------------------------------------------------------------- fetchers
def _get_json(url: str, params: dict | None = None, headers: dict | None = None) -> Any:
    import requests
    r = requests.get(url, params=params, headers=headers or {}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    text = (r.text or "").strip()
    if not text:
        raise ValueError("empty body")
    return json.loads(text)


def parse_sina_ohlcv(js: Any) -> pd.DataFrame:
    recs = []
    for row in js or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("day") or row.get("date"))
        o, h, l, c = (_num(row.get(k)) for k in ("open", "high", "low", "close"))
        v = _num(row.get("volume"))
        if d is None or c is None:
            continue
        recs.append({"date": pd.Timestamp(d), "open": o if o is not None else c,
                     "high": h if h is not None else c, "low": l if l is not None else c,
                     "close": c, "volume": v})
    if not recs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()


def parse_tencent_ohlcv(js: Any, symbol: str) -> pd.DataFrame:
    data = js.get("data") if isinstance(js, dict) else None
    if not isinstance(data, dict):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    block = data.get(symbol) or data.get(symbol.lower()) or data.get(symbol.upper())
    if not isinstance(block, dict) and len(data) == 1:
        only = next(iter(data.values()))
        block = only if isinstance(only, dict) else None
    if not isinstance(block, dict):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    rows = block.get("day") or block.get("qfqday") or block.get("qfqDay") or []
    recs = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        d = _to_date(row[0])
        o, c, h, l = _num(row[1]), _num(row[2]), _num(row[3]), _num(row[4])
        v = _num(row[5]) if len(row) > 5 else None
        if d is None or c is None:
            continue
        recs.append({"date": pd.Timestamp(d), "open": o if o is not None else c,
                     "high": h if h is not None else c, "low": l if l is not None else c,
                     "close": c, "volume": v})
    if not recs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()


def fetch_cn_ohlcv(sina_symbol: str, datalen: int = SINA_DATALEN, *,
                   get_json: Callable | None = None, now: datetime | None = None) -> pd.DataFrame:
    get_json = get_json or _get_json
    headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": UA}
    try:
        js = get_json(SINA_KLINE_URL,
                      {"symbol": sina_symbol, "scale": 240, "ma": "no", "datalen": int(datalen)},
                      headers)
        df = parse_sina_ohlcv(js)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sina ohlcv failed %s: %s", sina_symbol, exc)
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if df.empty:
        try:
            th = {"Referer": "https://gu.qq.com/", "User-Agent": UA}
            js = get_json(TENCENT_KLINE_URL, {"param": f"{sina_symbol},day,,,{int(datalen)},qfq"}, th)
            df = parse_tencent_ohlcv(js, sina_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tencent ohlcv failed %s: %s", sina_symbol, exc)
    return drop_incomplete(df, now, "cn")


def fetch_us_ohlcv(symbols: list[str], *, now: datetime | None = None) -> dict[str, pd.DataFrame]:
    import net_limits
    raw = net_limits.yf_download(symbols, period="max", auto_adjust=True,
                                 group_by="ticker", threads=True, progress=False)
    if raw is None or getattr(raw, "empty", True):
        raise ValueError("Yahoo 无数据")
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        f = _yahoo_frame(raw, s)
        if f is not None and not f.empty:
            out[s] = drop_incomplete(f, now, "us")
    return out


def _yahoo_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            if symbol in raw.columns.get_level_values(0):
                f = raw[symbol].copy()
            elif symbol in raw.columns.get_level_values(1):
                f = raw.xs(symbol, axis=1, level=1).copy()
            else:
                return None
        else:
            f = raw.copy()
    except Exception:  # noqa: BLE001
        return None
    colmap = {str(c).lower(): c for c in f.columns}
    need = {}
    for key in ("open", "high", "low", "close", "volume"):
        if key not in colmap:
            return None
        need[key] = colmap[key]
    out = pd.DataFrame({k: pd.to_numeric(f[need[k]], errors="coerce") for k in need})
    out.index = pd.to_datetime(f.index).tz_localize(None)
    out = out.dropna(subset=["close"]).sort_index()
    return out


def fetch_cn_holders(code: str, *, get_json: Callable | None = None) -> list[list]:
    """东财数据中心股东户数，季度。失败返回空列表（调用方标无数据）。"""
    get_json = get_json or _get_json
    sina = sina_symbol_for_stock(code) or ""
    secu = None
    if sina.startswith("sh"):
        secu = f"{sina[2:]}.SH"
    elif sina.startswith("sz"):
        secu = f"{sina[2:]}.SZ"
    if not secu:
        return []
    params = {
        "sortColumns": "END_DATE",
        "sortTypes": "-1",
        "pageSize": "50",
        "pageNumber": "1",
        "reportName": "RPT_HOLDERNUM_DET",
        "columns": "END_DATE,HOLDER_NUM,PRE_HOLDER_NUM,SECURITY_CODE,SECUCODE",
        "filter": f'(SECURITY_CODE="{secu.split(".")[0]}")',
        "source": "WEB",
        "client": "WEB",
    }
    headers = {"Referer": "https://data.eastmoney.com/gdhs/", "User-Agent": UA}
    js = get_json(EM_DC_URL, params, headers)
    result = (js or {}).get("result") if isinstance(js, dict) else None
    rows = (result or {}).get("data") if isinstance(result, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(str(row.get("END_DATE") or "")[:10])
        n = _num(row.get("HOLDER_NUM") if row.get("HOLDER_NUM") is not None else row.get("HOLDER_TOTAL_NUM"))
        if d is not None and n is not None:
            out.append([d.isoformat(), n])
    out.sort()
    return out


# ---------------------------------------------------------------- payload / snapshot
def load_bars_seed(path: Path | None = None) -> dict[str, pd.DataFrame]:
    p = path or BARS_PATH
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data = raw.get("bars") if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return {}
    return {k: rows_to_frame(v) for k, v in data.items()}


def write_bars_seed(frames: dict[str, pd.DataFrame], path: Path | None = None) -> None:
    p = path or BARS_PATH
    body = {"updated_at": _now().isoformat(timespec="seconds"),
            "bars": {k: bars_to_rows(v) for k, v in frames.items() if v is not None and not v.empty}}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _newest(cands: list[dict | None]) -> dict | None:
    ok = [c for c in cands if isinstance(c, dict) and c.get("status") == "ok"]
    return max(ok, key=lambda c: str(c.get("updated_at") or "")) if ok else None


def load_best() -> dict | None:
    return _newest([_cache, _load_json(RUNTIME_PATH)] + [_load_json(p) for p in SEED_PATHS])


def _write_runtime(payload: dict) -> None:
    try:
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUNTIME_PATH.with_suffix(".tmp")
        body = {k: v for k, v in payload.items() if k != "age_hours"}
        tmp.write_text(json.dumps(body, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8")
        tmp.replace(RUNTIME_PATH)
    except Exception:  # noqa: BLE001
        logger.info("accum_trap runtime write failed", exc_info=True)


def missing_item(meta: dict, reason: str = "无数据") -> dict:
    return {
        "id": meta.get("id"), "name": meta.get("name"), "symbol": meta.get("sina") or meta.get("yahoo") or meta.get("code"),
        "market": meta.get("market"), "kind": meta.get("kind"), "example": bool(meta.get("example")),
        "status": "missing", "reason": reason, "asof": None, "close": None, "chg_1d": None,
        "accum_score": None, "trap_score": None, "label": "无数据",
        "holders": holders_feature(None, applicable=bool(meta.get("holders_applicable"))),
    }


def build_item(meta: dict, df: pd.DataFrame | None, bench: pd.DataFrame | None,
               holder_rows: list | None) -> dict:
    applicable = bool(meta.get("holders_applicable"))
    h = holders_feature(holder_rows, applicable=applicable)
    if df is None or df.empty or len(df) < RULES["range_days"]:
        row = missing_item(meta, "无数据")
        row["holders"] = h
        if df is not None and not df.empty:
            row["reason"] = f"日线不足{RULES['range_days']}个交易日（现有{len(df)}）"
        return row
    feat = analyze_bar_frame(df, bench, h)
    if not feat:
        row = missing_item(meta, "无数据")
        row["holders"] = h
        return row
    bt = backtest_signals(df, bench, h, is_bench=bool(meta.get("is_bench")))
    bench_name = "沪深300" if meta.get("market") == "cn" else "标普500"
    feat["relative"] = dict(feat.get("relative") or {})
    feat["relative"]["bench_id"] = meta.get("bench_id")
    feat["relative"]["bench_name"] = bench_name
    return {
        "id": meta.get("id"),
        "name": meta.get("name"),
        "symbol": meta.get("sina") or meta.get("yahoo") or meta.get("code"),
        "code": meta.get("code"),
        "market": meta.get("market"),
        "kind": meta.get("kind"),
        "example": bool(meta.get("example")),
        "status": "ok",
        "reason": None,
        "is_bench": bool(meta.get("is_bench")),
        **feat,
        "backtest": bt,
    }


def _aggregate_backtest(items: list[dict], market: str | None = None) -> dict:
    acc_rows = []
    trap_rows = []
    # 只能用各标的已汇总的均值与样本加权
    acc_n = trap_n = 0
    acc20 = acc60 = tr20 = tr60 = 0.0
    accw20 = accw60 = trw20 = trw60 = 0.0
    wacc20 = wacc60 = wtr20 = wtr60 = 0
    for it in items:
        if market and it.get("market") != market:
            continue
        if it.get("kind") != "index":
            continue
        bt = it.get("backtest") or {}
        a, t = bt.get("accum") or {}, bt.get("trap") or {}
        if a.get("n"):
            acc_n += a["n"]
            if a.get("ret_20_avg") is not None:
                acc20 += a["ret_20_avg"] * a["n"]
                wacc20 += a["n"]
            if a.get("ret_60_avg") is not None:
                acc60 += a["ret_60_avg"] * a["n"]
                wacc60 += a["n"]
            if a.get("win_20") is not None:
                accw20 += a["win_20"] * a["n"]
            if a.get("win_60") is not None:
                accw60 += a["win_60"] * a["n"]
        if t.get("n"):
            trap_n += t["n"]
            if t.get("ret_20_avg") is not None:
                tr20 += t["ret_20_avg"] * t["n"]
                wtr20 += t["n"]
            if t.get("ret_60_avg") is not None:
                tr60 += t["ret_60_avg"] * t["n"]
                wtr60 += t["n"]
            if t.get("win_20") is not None:
                trw20 += t["win_20"] * t["n"]
            if t.get("win_60") is not None:
                trw60 += t["win_60"] * t["n"]

    def pack(n, s20, n20, s60, n60, w20, w60):
        if n < RULES["backtest_min_n"]:
            return {"n": n, "ret_20_avg": _r(s20 / n20, 4) if n20 else None,
                    "win_20": _r(w20 / n20, 4) if n20 else None,
                    "ret_60_avg": _r(s60 / n60, 4) if n60 else None,
                    "win_60": _r(w60 / n60, 4) if n60 else None,
                    "insufficient": True,
                    "note": f"样本不足（n={n}，至少 {RULES['backtest_min_n']}）"}
        return {"n": n, "ret_20_avg": _r(s20 / n20, 4) if n20 else None,
                "win_20": _r(w20 / n20, 4) if n20 else None,
                "ret_60_avg": _r(s60 / n60, 4) if n60 else None,
                "win_60": _r(w60 / n60, 4) if n60 else None,
                "insufficient": False, "note": None}

    return {
        "accum": pack(acc_n, acc20, wacc20, acc60, wacc60, accw20, accw60),
        "trap": pack(trap_n, tr20, wtr20, tr60, wtr60, trw20, trw60),
        "min_n": RULES["backtest_min_n"],
    }


def build_payload(frames: dict[str, pd.DataFrame], holders_map: dict[str, list] | None,
                  errors: dict | None, prev: dict | None, *, now: datetime | None = None) -> dict:
    now = now or _now()
    items = []
    uni = universe()
    by_id = {m["id"]: frames.get(m["id"]) for m in uni}
    for meta in uni:
        df = by_id.get(meta["id"])
        bench = by_id.get(meta.get("bench_id") or "")
        hrows = (holders_map or {}).get(meta["id"])
        try:
            item = build_item(meta, df, bench, hrows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyze failed %s: %s", meta.get("id"), exc)
            item = missing_item(meta, "无数据")
        items.append(item)
    ok = [it for it in items if it.get("status") == "ok"]
    asofs = [it["asof"] for it in ok if it.get("asof")]
    status = "ok" if ok else "no_data"
    note = None
    if errors:
        note = "部分数据源失败，已显示已有序列；缺项为无数据"
    return {
        "schema_version": 1,
        "status": status,
        "updated_at": now.isoformat(timespec="seconds"),
        "asof": max(asofs) if asofs else None,
        "disclaimer": DISCLAIMER,
        "theory": (
            "真正吸筹：低位（长期下跌后）、缩量横盘或温和放量、筹码低位单峰密集、"
            "股东户数持续减少、大盘跌它不跌。诱多：中高位突然放量拉升、长上影或冲高回落、"
            "量价背离、后续无力。位置决定一切，低位优先，再看量价是否持续。"
        ),
        "rules": RULES,
        "sources": SOURCES,
        "errors": errors or {},
        "note": note,
        "from_snapshot": False,
        "watchlist": load_watchlist(),
        "items": items,
        "backtest_summary": {
            "cn": _aggregate_backtest(items, "cn"),
            "us": _aggregate_backtest(items, "us"),
            "all_indices": _aggregate_backtest(items, None),
        },
    }


def public_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {"schema_version": 1, "status": "no_data", "error": "无数据",
                "disclaimer": DISCLAIMER, "items": []}
    out = {k: v for k, v in payload.items() if k not in ("bars", "age_hours")}
    return out


def analyze_from_frames(frames: dict[str, pd.DataFrame], holders_map: dict | None = None,
                        *, now: datetime | None = None, errors: dict | None = None) -> dict:
    return public_payload(build_payload(frames, holders_map, errors, None, now=now))


def refresh(fetchers: dict | None = None, *, now: datetime | None = None,
            write: bool = True) -> dict:
    """联网刷新（仅后台调用）。fetchers 可注入，便于测试。"""
    global _cache
    now = now or _now()
    prev = load_best()
    cached_bars = load_bars_seed()
    errors: dict[str, str] = {}
    frames: dict[str, pd.DataFrame] = dict(cached_bars)
    holders_map: dict[str, list] = {}

    fns = fetchers or {}
    fetch_cn = fns.get("cn") or fetch_cn_ohlcv
    fetch_us = fns.get("us") or fetch_us_ohlcv
    fetch_h = fns.get("holders") or fetch_cn_holders

    uni = universe()
    cn_meta = [m for m in uni if m["market"] == "cn"]
    us_meta = [m for m in uni if m["market"] == "us"]

    for meta in cn_meta:
        sym = meta.get("sina")
        if not sym:
            errors[meta["id"]] = "无数据"
            continue
        have = frames.get(meta["id"])
        datalen = 260 if (have is not None and len(have) >= RULES["range_days"]) else SINA_DATALEN
        try:
            fetched = fetch_cn(sym, datalen, now=now)
            frames[meta["id"]] = merge_frames(have, fetched)
        except Exception as exc:  # noqa: BLE001
            errors[meta["id"]] = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning("cn fetch failed %s: %s", meta["id"], errors[meta["id"]])

    us_syms = [m["yahoo"] for m in us_meta if m.get("yahoo")]
    if us_syms:
        try:
            fetched_us = fetch_us(us_syms, now=now)
            by_yahoo = {m["yahoo"]: m for m in us_meta}
            for ysym, f in (fetched_us or {}).items():
                meta = by_yahoo.get(ysym)
                if not meta:
                    continue
                frames[meta["id"]] = merge_frames(frames.get(meta["id"]), f)
        except Exception as exc:  # noqa: BLE001
            errors["yahoo"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning("yahoo fetch failed: %s", errors["yahoo"])

    for meta in uni:
        if not meta.get("holders_applicable"):
            continue
        code = meta.get("code")
        try:
            holders_map[meta["id"]] = fetch_h(code)
        except Exception as exc:  # noqa: BLE001
            errors[f"holders:{meta['id']}"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    payload = build_payload(frames, holders_map, errors, prev, now=now)
    if payload.get("status") != "ok" and prev and prev.get("status") == "ok":
        out = copy.deepcopy(prev)
        out["from_snapshot"] = True
        out["snapshot_asof"] = prev.get("asof") or (prev.get("updated_at") or "")[:10]
        out["note"] = "本轮刷新失败，显示上次快照"
        out["errors"] = errors
        return public_payload(out)
    if write and payload.get("status") == "ok":
        _cache = payload
        _write_runtime(payload)
        try:
            write_bars_seed(frames)
        except Exception:  # noqa: BLE001
            logger.warning("write accum_trap bars failed", exc_info=True)
    return public_payload(payload)


def snapshot() -> dict:
    best = load_best()
    if best:
        out = public_payload(copy.deepcopy(best))
        try:
            out["age_hours"] = _r((_now() - datetime.fromisoformat(out["updated_at"])).total_seconds() / 3600.0, 1)
        except (KeyError, TypeError, ValueError):
            out["age_hours"] = None
        return out
    return {"schema_version": 1, "status": "no_data", "error": "无数据",
            "updated_at": _now().isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER, "items": []}


def needs_refresh(value: dict | None) -> bool:
    if not value or value.get("status") != "ok":
        return True
    try:
        age = _now() - datetime.fromisoformat(value["updated_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return age > timedelta(hours=REFRESH_HOURS)


def write_seed(payload: dict) -> None:
    body = public_payload(payload)
    body.pop("age_hours", None)
    for p in SEED_PATHS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8")
        try:
            loc = p.relative_to(ROOT)
        except ValueError:
            loc = p
        print(f"wrote {loc} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = refresh(write=True)
    write_seed(data)
    print(json.dumps({
        "status": data.get("status"),
        "asof": data.get("asof"),
        "errors": data.get("errors"),
        "labels": {it["id"]: (it.get("label"), it.get("accum_score"), it.get("trap_score"))
                   for it in data.get("items") or []},
    }, ensure_ascii=False, indent=2))
