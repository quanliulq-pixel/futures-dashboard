# -*- coding: utf-8 -*-
"""US Superstocks research screen (weekly rules inspired by Stine, not a copy of the book).

Research display only: no orders, not investment advice. Missing fields stay None
and are shown as 无数据. Never invent prices, fundamentals, or insider prints.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "us_superstocks"
SEED_PATH = ROOT / "data" / "us_superstocks.json"
DOCS_SEED_PATH = ROOT / "docs" / "api" / "us_superstocks.json"
CACHE_DIR = DATA_DIR / "cache"
UNI_PATH = DATA_DIR / "universe.json"
WEEKLY_PKL = DATA_DIR / "weekly.pkl"

SHANGHAI = ZoneInfo("Asia/Shanghai")

CACHE_TTL = 6 * 3600
UNI_TTL = 7 * 24 * 3600
INSIDER_TTL = 24 * 3600
PROGRESS_PATH = DATA_DIR / "progress.json"
WEEKLY_RESUME_MAX_AGE_SEC = 36 * 3600
STALE_RUNNING_SEC = 15 * 60
REMOTE_SEED_URL = (
    "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/us_superstocks.json"
)
REMOTE_SEED_TIMEOUT = 5.0
REMOTE_SEED_TTL = 30 * 60

# Saturday 08:00 Asia/Shanghai = 00:00 UTC Saturday (cron 0 0 * * 6).
WEEKLY_SCAN_NOTE = (
    "每周自动扫描：北京时间周六 08:00（UTC 周六 00:00）。"
    "后台重算是按需补扫，全市场约五千只周线在网页进程内可能因限流或超时无法一次跑完；"
    "完整名单以每周自动扫描写入的快照为准。"
)
WEEKLY_SCAN_INFO = {
    "note": WEEKLY_SCAN_NOTE,
    "cron_utc": "0 0 * * 6",
    "local": "周六 08:00",
    "timezone": "Asia/Shanghai (UTC+8)",
}

PHASE_LABELS = {
    "queued": "排队中",
    "universe": "宇宙名单",
    "weekly": "周线行情",
    "fundamentals": "基本面",
    "insiders": "内部人申报",
    "scoring": "打分与写快照",
}
# Inclusive overall percent span for each phase (determinate, not a spinner).
PHASE_WEIGHTS = {
    "queued": (0, 1),
    "universe": (1, 5),
    "weekly": (5, 75),
    "fundamentals": (75, 88),
    "insiders": (88, 97),
    "scoring": (97, 100),
}

# Numeric defaults (documented in UI and docs/us_superstocks_method.md).
DEFAULTS: dict[str, Any] = {
    "base_weeks_strict": 12,
    "base_weeks_weak": 8,
    "base_weeks_max": 40,
    "tightness_pass": 0.30,
    "tightness_near": 0.40,
    "quiet_volume_ratio": 0.85,
    "ma_long": 30,
    "ma_magic": 10,
    "vol_expand_strict": 5.0,
    "vol_expand_soft": 3.0,
    "angle_lookback_min": 3,
    "angle_lookback_max": 6,
    "angle_pct_per_week": 8.0,
    "angle_impulse_week_pct": 10.0,
    "price_max_core": 15.0,
    "price_sweet_low": 4.0,
    "price_sweet_high": 10.0,
    "price_caution_low": 2.0,
    "price_exclude_below": 2.0,
    "min_history_weeks": 52,
    "breakout_lookback_weeks": 78,
    "extended_pct_above_magic": 60.0,
    "extended_weeks_window": 6,
    "buy_zone_magic_band": 0.05,
    "buy_zone_tight_weeks": 3,
    "buy_zone_tightness": 0.12,
    "buy_zone_volume_vs_peak": 0.70,
    "pe_run_rate_max": 10.0,
    "float_best_low": 4_000_000,
    "float_best_high": 8_000_000,
    "float_good_max": 10_000_000,
    "cap_best_max": 100_000_000,
    "cap_ok_max": 500_000_000,
    "cap_allow_max": 1_000_000_000,
    "insider_own_plus": 0.20,
    "short_interest_max": 0.20,
    "token_buy_usd": 10_000.0,
    "insider_post_weeks": 4,
    "exit_review_weeks_min": 7,
    "exit_review_weeks_max": 10,
    "exit_age_months_min": 9,
    "exit_age_months_max": 15,
    "exit_price_high": 25.0,
    "exit_price_high_top": 30.0,
    "earnings_gap_wait_min": 2,
    "earnings_gap_wait_max": 3,
    "bcd_weeks": 5,
    "bcd_lower_lows": 3,
    "hold_30wma_min_frac": 0.80,
    "stop_primary": "weekly_close_below_10wma",
    "stop_alternate": "weekly_close_below_base_low",
    "sizing_sleeve_min": 0.05,
    "sizing_sleeve_max": 0.10,
    "sizing_max_names": 1,
}

# 45-degree visual slope is chart-scale dependent. Encoded rule:
ANGLE_FORMULA_CN = (
    "突破后取 k 周（k 为 3–6，不足 6 周则用已有周数且至少 3 周）："
    "slope_pct_per_week = (close[breakout+k] / breakout_level - 1) / k × 100。"
    "高攻角：slope_pct_per_week ≥ 8，且突破后至多 6 周内至少有一周收盘涨幅 ≥ 10%。"
    "这是把约 45 度视觉斜角编码成可检验的多周百分比推进，不是慢磨。"
    "不足 3 周则攻角为无数据，不能算严格通过。"
)

PRIMARY_STOP_CN = (
    "默认止损：周线收盘重新跌破 10 周均线（魔法线）。备选：周线收盘跌破突破前平台低点。"
    "入场前预设，禁止向不利方向移动止损。"
)

MANUAL_CHECKLIST = [
    "管理层是否保守、少说多做（需人工判断）",
    "是否具备清晰的超级主题 / 产品拐点（需人工判断）",
    "是否在发空洞或夸大的新闻稿（需人工判断）",
    "股票论坛语气是否异常狂热或协同（需人工判断）",
    "代码本身是否被当成卖点（所谓 great ticker，需人工判断）",
    "是否出现“战略选项 / strategic alternatives”等易被炒作的措辞（需人工判断）",
    "财报质量、一次性收益、会计政策是否干净（$2–$4 尤其要人工复核）",
]

COMMODITY_HINTS = (
    "oil", "gas", "petroleum", "crude", "coal", "gold", "silver", "copper",
    "steel", "iron", "aluminum", "lithium", "uranium", "mining", "miner",
    "precious metal", "agriculture", "grain", "wheat", "corn", "soy",
    "fertilizer", "timber", "lumber", "shipping dry", "royalty trust",
    "石油", "天然气", "煤炭", "黄金", "白银", "铜", "钢铁", "铁矿", "农产品",
)

C_LEVEL_RE = re.compile(
    r"\b(CEO|CFO|COO|CTO|CMO|CIO|PRESIDENT|CHIEF|FOUNDER|CHAIR|CHAIRMAN|DIRECTOR)\b",
    re.I,
)

OTC_EXCHANGES = {
    "OTC", "OTCQB", "OTCQX", "OTCBB", "PINK", "PINKSHEET", "PINKSHEETS",
    "GREY", "GRAY", "EXPERT", "PFTS", "OTHER-OTC",
}

_cache: tuple[float, dict] | None = None
_lock = threading.Lock()


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def _iso_shanghai(dt: datetime | None = None) -> str:
    d = dt or _now_shanghai()
    if d.tzinfo is None:
        d = d.replace(tzinfo=SHANGHAI)
    return d.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S") + " CST"


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _r(v: Any, n: int = 4) -> float | None:
    x = _num(v)
    return None if x is None else round(x, n)

def weekly_sma(closes: pd.Series, window: int) -> pd.Series:
    """Simple moving average on weekly closes. window <= 0 raises."""
    if window <= 0:
        raise ValueError("window must be positive")
    return pd.to_numeric(closes, errors="coerce").rolling(int(window), min_periods=int(window)).mean()


def _as_weekly_frame(bars: pd.DataFrame | list[dict] | None) -> pd.DataFrame:
    if bars is None:
        return pd.DataFrame()
    if isinstance(bars, list):
        df = pd.DataFrame(bars)
    else:
        df = bars.copy()
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    cols = {str(c).strip().lower(): c for c in df.columns}
    rename = {}
    for want in ("date", "open", "high", "low", "close", "volume"):
        if want in cols:
            rename[cols[want]] = want
        elif want.title() in df.columns:
            rename[want.title()] = want
    df = df.rename(columns=rename)
    if "date" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "date"})
        else:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_convert("America/New_York").dt.tz_localize(None)
    for c in ("open", "high", "low", "close", "volume"):
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    return df.reset_index(drop=True)


def tightness(highs: pd.Series, lows: pd.Series, closes: pd.Series) -> float | None:
    mid = _num(pd.to_numeric(closes, errors="coerce").median())
    if mid is None or mid <= 0:
        return None
    hi = _num(pd.to_numeric(highs, errors="coerce").max())
    lo = _num(pd.to_numeric(lows, errors="coerce").min())
    if hi is None or lo is None:
        return None
    return (hi - lo) / mid


def price_bucket(price: float | None, d: dict | None = None) -> str | None:
    cfg = d or DEFAULTS
    px = _num(price)
    if px is None:
        return None
    if px < cfg["price_exclude_below"]:
        return "too_low"
    if px < cfg["price_sweet_low"]:
        return "caution_low"
    if px <= cfg["price_sweet_high"]:
        return "sweet"
    if px <= cfg["price_max_core"]:
        return "core_other"
    return "above_core"


def pe_run_rate(price: float | None, quarterly_eps: float | None, ttm_eps: float | None = None) -> dict:
    """Annualized PE run-rate = price / (latest quarter EPS x 4). Missing if EPS<=0."""
    px = _num(price)
    q = _num(quarterly_eps)
    ttm = _num(ttm_eps)
    run = None
    if px is not None and px > 0 and q is not None and q > 0:
        run = px / (q * 4.0)
    ttm_pe = None
    if px is not None and px > 0 and ttm is not None and ttm > 0:
        ttm_pe = px / ttm
    return {
        "price": _r(px, 4),
        "quarterly_eps": _r(q, 6),
        "ttm_eps": _r(ttm, 6),
        "pe_run_rate": _r(run, 3),
        "pe_ttm": _r(ttm_pe, 3),
        "run_rate_pass": bool(run is not None and run <= DEFAULTS["pe_run_rate_max"]),
        "missing": [k for k, v in (("price", px), ("quarterly_eps", q), ("ttm_eps", ttm)) if v is None],
    }


def _local_name(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    for child in list(node):
        if _local_name(child.tag).lower() == "value" and (child.text or "").strip():
            return child.text.strip()
    return (node.text or "").strip()


def _find_first(node: ET.Element, names: Iterable[str]) -> ET.Element | None:
    want = {n.lower() for n in names}
    for child in node.iter():
        if _local_name(child.tag).lower() in want:
            return child
    return None


def _child(node: ET.Element, name: str) -> ET.Element | None:
    lname = name.lower()
    for child in list(node):
        if _local_name(child.tag).lower() == lname:
            return child
    return None


def is_open_market_purchase_code(code: Any) -> bool:
    """SEC Form 4 transactionCode P only. Awards, exercises, gifts and sales are excluded."""
    return str(code or "").strip().upper() == "P"

def parse_form4_xml(xml_text: str, *, symbol: str | None = None) -> list[dict]:
    """Parse one ownership XML. Only non-derivative transactions are returned.

    Open-market (or private) purchase is transactionCode P with acquired code A.
    Grants (A), exercises (M), gifts (G), sales (S), tax (F), conversions (C),
    other (J) and the rest are kept only so callers can see they were excluded.
    """
    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(str(xml_text).strip())
    except ET.ParseError:
        logger.debug("Form 4 XML parse failed", exc_info=True)
        return []
    issuer_sym = _xml_text(_find_first(root, ("issuerTradingSymbol",))) or symbol
    owner = ""
    title = ""
    is_officer = False
    is_director = False
    is_ten = False
    owner_el = _find_first(root, ("reportingOwner",))
    if owner_el is not None:
        owner = _xml_text(_find_first(owner_el, ("rptOwnerName",)))
        rel = _find_first(owner_el, ("reportingOwnerRelationship",))
        if rel is not None:
            title = _xml_text(_child(rel, "officerTitle"))
            is_officer = _xml_text(_child(rel, "isOfficer")) in {"1", "true", "True"}
            is_director = _xml_text(_child(rel, "isDirector")) in {"1", "true", "True"}
            is_ten = _xml_text(_child(rel, "isTenPercentOwner")) in {"1", "true", "True"}
    rows: list[dict] = []
    for tx in root.iter():
        if _local_name(tx.tag) != "nonDerivativeTransaction":
            continue
        coding = _find_first(tx, ("transactionCoding",))
        if coding is None:
            coding = tx
        code = _xml_text(_find_first(coding, ("transactionCode",)))
        amounts = _find_first(tx, ("transactionAmounts",))
        if amounts is None:
            amounts = tx
        shares = _num(_xml_text(_find_first(amounts, ("transactionShares",))))
        px = _num(_xml_text(_find_first(amounts, ("transactionPricePerShare",))))
        acq = _xml_text(_find_first(amounts, ("transactionAcquiredDisposedCode",))) or _xml_text(
            _find_first(tx, ("transactionAcquiredDisposedCode",))
        )
        date_el = _find_first(tx, ("transactionDate",))
        date_s = _xml_text(_find_first(date_el, ("value",))) if date_el is not None else _xml_text(date_el)
        value = None if shares is None or px is None else shares * px
        token = bool(value is not None and value < DEFAULTS["token_buy_usd"])
        c_level = bool(is_officer or is_director or C_LEVEL_RE.search(title or ""))
        rows.append(
            {
                "symbol": issuer_sym,
                "owner": owner or None,
                "title": title or None,
                "is_officer": is_officer,
                "is_director": is_director,
                "is_ten_percent": is_ten,
                "c_level_or_director": c_level,
                "code": (code or "").upper() or None,
                "open_market_buy": is_open_market_purchase_code(code) and (not acq or acq.upper() == "A"),
                "acquired_disposed": acq.upper() if acq else None,
                "shares": _r(shares, 4),
                "price": _r(px, 4),
                "value_usd": _r(value, 2),
                "token_buy": token if is_open_market_purchase_code(code) else False,
                "date": date_s or None,
            }
        )
    return rows


def summarize_insider_buys(
    transactions: list[dict] | None,
    *,
    base_start: str | None = None,
    breakout_date: str | None = None,
    post_weeks: int | None = None,
) -> dict:
    """Keep only true open-market buys (code P). Never invent a buy."""
    txs = [t for t in (transactions or []) if isinstance(t, dict)]
    buys = [t for t in txs if t.get("open_market_buy")]
    post = int(post_weeks if post_weeks is not None else DEFAULTS["insider_post_weeks"])
    window_end = None
    if breakout_date:
        try:
            window_end = (pd.Timestamp(breakout_date) + pd.Timedelta(weeks=post)).date()
        except Exception:
            window_end = None
    window_start = None
    if base_start:
        try:
            window_start = pd.Timestamp(base_start).date()
        except Exception:
            window_start = None

    def _in_window(t: dict) -> bool | None:
        if not t.get("date"):
            return None
        try:
            d = pd.Timestamp(t["date"]).date()
        except Exception:
            return None
        if window_start and d < window_start:
            return False
        if window_end and d > window_end:
            return False
        if window_start is None and window_end is None:
            return None
        return True

    timed = []
    for t in buys:
        flag = _in_window(t)
        item = dict(t)
        item["in_base_or_post_breakout"] = flag
        timed.append(item)
    in_win = [t for t in timed if t.get("in_base_or_post_breakout") is True]
    pool = in_win if in_win else timed
    owners = {(t.get("owner") or "").strip() for t in pool if t.get("c_level_or_director") and (t.get("owner") or "").strip()}
    any_token_only = bool(pool) and all(t.get("token_buy") for t in pool)
    meaningful = [t for t in pool if not t.get("token_buy")]
    if not buys:
        return {
            "status": "无数据" if not txs else "ok",
            "source": "SEC EDGAR Form 4",
            "open_market_buys": [],
            "buyer_count": 0,
            "c_level_buyer_count": 0,
            "token_flag": False,
            "meaningful_buy_count": 0,
            "timing": "无数据",
            "note": "无代码为 P 的公开市场买入；期权授予/行权/赠与等已排除" if txs else "无数据",
        }
    timing = "无数据"
    if in_win:
        timing = "during_base_or_just_after_breakout"
    elif window_start or window_end:
        timing = "outside_base_window"
    return {
        "status": "ok",
        "source": "SEC EDGAR Form 4",
        "open_market_buys": timed,
        "buyer_count": len({(t.get("owner") or "").strip() for t in pool if (t.get("owner") or "").strip()}),
        "c_level_buyer_count": len(owners),
        "token_flag": any_token_only,
        "meaningful_buy_count": len(meaningful),
        "timing": timing,
        "note": None,
    }

def detect_base_breakout(weekly: pd.DataFrame | list[dict], d: dict | None = None) -> dict:
    """Find the most recent tight, quiet base and breakout on weekly bars."""
    cfg = d or DEFAULTS
    df = _as_weekly_frame(weekly)
    empty = {
        "found": False,
        "base_weeks": None,
        "base_weak": None,
        "base_start": None,
        "base_end": None,
        "base_high": None,
        "base_low": None,
        "tightness": None,
        "quiet_volume": None,
        "breakout_date": None,
        "breakout_index": None,
        "breakout_level": None,
        "breakout_close": None,
        "vol_expand": None,
        "vol_expand_pass": None,
        "weeks_since_breakout": None,
    }
    n = len(df)
    if n < cfg["base_weeks_weak"] + 2:
        return dict(empty)
    vol = pd.to_numeric(df["volume"], errors="coerce")
    hi = pd.to_numeric(df["high"], errors="coerce")
    lo = pd.to_numeric(df["low"], errors="coerce")
    cl = pd.to_numeric(df["close"], errors="coerce")
    look = min(n - 1, int(cfg["breakout_lookback_weeks"]))
    t0 = n - look
    best = None
    for t in range(n - 1, t0 - 1, -1):
        if pd.isna(cl.iloc[t]):
            continue
        for L in range(int(cfg["base_weeks_strict"]), int(cfg["base_weeks_max"]) + 1):
            cand = _base_at(df, hi, lo, cl, vol, t, L, cfg, weak=False)
            if cand:
                best = cand
                break
        if best:
            break
        for L in range(int(cfg["base_weeks_weak"]), int(cfg["base_weeks_strict"])):
            cand = _base_at(df, hi, lo, cl, vol, t, L, cfg, weak=True)
            if cand:
                best = cand
                break
        if best:
            break
    if not best:
        return dict(empty)
    best["weeks_since_breakout"] = int(n - 1 - best["breakout_index"])
    return best


def _base_at(df, hi, lo, cl, vol, t: int, L: int, cfg: dict, *, weak: bool) -> dict | None:
    start = t - L
    if start < 0:
        return None
    base_hi = hi.iloc[start:t]
    base_lo = lo.iloc[start:t]
    base_cl = cl.iloc[start:t]
    base_vol = vol.iloc[start:t]
    if base_cl.notna().sum() < L * 0.8:
        return None
    tight = tightness(base_hi, base_lo, base_cl)
    limit = cfg["tightness_near"] if weak else cfg["tightness_pass"]
    if tight is None or tight > limit:
        return None
    med_vol = _num(base_vol.median())
    quiet = None
    prior_start = start - L
    if prior_start >= 0 and med_vol is not None:
        prior_med = _num(vol.iloc[prior_start:start].median())
        if prior_med is not None and prior_med > 0:
            quiet = med_vol <= cfg["quiet_volume_ratio"] * prior_med
    if quiet is False and not weak:
        return None
    base_high = _num(base_hi.max())
    base_low = _num(base_lo.min())
    bclose = _num(cl.iloc[t])
    bvol = _num(vol.iloc[t])
    if base_high is None or bclose is None or bclose <= base_high:
        return None
    expand = None
    if med_vol is not None and med_vol > 0 and bvol is not None:
        expand = bvol / med_vol
    if expand is None or expand < cfg["vol_expand_soft"]:
        return None
    if expand >= cfg["vol_expand_strict"]:
        grade = "strict"
    else:
        grade = "soft"
        weak = True
    bdate = df["date"].iloc[t]
    bstart = df["date"].iloc[start]
    bend = df["date"].iloc[t - 1]
    return {
        "found": True,
        "base_weeks": int(L),
        "base_weak": bool(weak or L < cfg["base_weeks_strict"] or grade == "soft"),
        "base_start": pd.Timestamp(bstart).date().isoformat(),
        "base_end": pd.Timestamp(bend).date().isoformat(),
        "base_high": _r(base_high, 4),
        "base_low": _r(base_low, 4),
        "tightness": _r(tight, 4),
        "quiet_volume": quiet,
        "breakout_date": pd.Timestamp(bdate).date().isoformat(),
        "breakout_index": int(t),
        "breakout_level": _r(base_high, 4),
        "breakout_close": _r(bclose, 4),
        "vol_expand": _r(expand, 3),
        "vol_expand_pass": grade,
    }


def angle_of_attack(weekly: pd.DataFrame | list[dict], breakout: dict, d: dict | None = None) -> dict:
    """Steep rise off the base. See ANGLE_FORMULA_CN for the encoded rule."""
    cfg = d or DEFAULTS
    df = _as_weekly_frame(weekly)
    out = {
        "slope_pct_per_week": None,
        "k_weeks": None,
        "impulse": None,
        "pass": False,
        "formula": ANGLE_FORMULA_CN,
        "missing": False,
    }
    if df.empty or not breakout or not breakout.get("found"):
        out["missing"] = True
        return out
    t = breakout.get("breakout_index")
    level = _num(breakout.get("breakout_level"))
    if t is None or level is None or level <= 0:
        out["missing"] = True
        return out
    cl = pd.to_numeric(df["close"], errors="coerce")
    last = len(df) - 1
    k = min(int(cfg["angle_lookback_max"]), max(0, last - int(t)))
    if k < int(cfg["angle_lookback_min"]):
        out["missing"] = True
        out["k_weeks"] = int(k)
        return out
    end_px = _num(cl.iloc[int(t) + k])
    if end_px is None:
        out["missing"] = True
        return out
    slope = (end_px / level - 1.0) / k * 100.0
    rets = []
    for i in range(int(t) + 1, min(int(t) + int(cfg["angle_lookback_max"]), last) + 1):
        a = _num(cl.iloc[i - 1])
        b = _num(cl.iloc[i])
        if a and a > 0 and b is not None:
            rets.append((b / a - 1.0) * 100.0)
    impulse = bool(rets) and max(rets) >= cfg["angle_impulse_week_pct"]
    out.update(
        {
            "slope_pct_per_week": _r(slope, 3),
            "k_weeks": int(k),
            "impulse": impulse,
            "pass": bool(slope >= cfg["angle_pct_per_week"] and impulse),
            "missing": False,
        }
    )
    return out

def classify_entry_exit(
    weekly: pd.DataFrame | list[dict],
    breakout: dict,
    *,
    wma10: pd.Series | None = None,
    wma30: pd.Series | None = None,
    daily: pd.DataFrame | list[dict] | None = None,
    earnings_dates: list[str] | None = None,
    splits: list[dict] | None = None,
    secondary_offering: bool | None = None,
    eps_ramp_ended: bool | None = None,
    massive_insider_selling: bool | None = None,
    d: dict | None = None,
) -> dict:
    """Entry / extension / exit flags from weekly (and optional daily) bars."""
    cfg = d or DEFAULTS
    df = _as_weekly_frame(weekly)
    entry = {
        "in_buy_zone": False,
        "setups": [],
        "secondary": [],
        "stop_primary": cfg["stop_primary"],
        "stop_alternate": cfg["stop_alternate"],
        "stop_level": None,
        "stop_note": PRIMARY_STOP_CN,
    }
    exit_info = {"signals": [], "for_watch_only": True}
    extended = False
    if df.empty:
        return {"entry": entry, "exit": exit_info, "extended": False, "pct_above_magic": None}
    cl = pd.to_numeric(df["close"], errors="coerce")
    hi = pd.to_numeric(df["high"], errors="coerce")
    lo = pd.to_numeric(df["low"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce")
    if wma10 is None:
        wma10 = weekly_sma(cl, int(cfg["ma_magic"]))
    if wma30 is None:
        wma30 = weekly_sma(cl, int(cfg["ma_long"]))
    last = len(df) - 1
    last_close = _num(cl.iloc[last])
    magic = _num(wma10.iloc[last]) if last < len(wma10) else None
    pct_above = None
    if last_close is not None and magic is not None and magic > 0:
        pct_above = (last_close / magic - 1.0) * 100.0
        entry["stop_level"] = _r(magic, 4)
    t = breakout.get("breakout_index") if breakout and breakout.get("found") else None
    weeks_since = breakout.get("weeks_since_breakout") if breakout else None

    if (
        pct_above is not None
        and pct_above >= cfg["extended_pct_above_magic"]
        and weeks_since is not None
        and weeks_since <= cfg["extended_weeks_window"]
    ):
        extended = True
        exit_info["signals"].append("early_60pct_above_magic")

    tight_n = int(cfg["buy_zone_tight_weeks"])
    if last + 1 >= tight_n and magic is not None and last_close is not None:
        recent_t = tightness(hi.iloc[-tight_n:], lo.iloc[-tight_n:], cl.iloc[-tight_n:])
        near = abs(last_close / magic - 1.0) <= cfg["buy_zone_magic_band"]
        rising = False
        if last >= 3 and pd.notna(wma10.iloc[last]) and pd.notna(wma10.iloc[last - 3]):
            rising = float(wma10.iloc[last]) >= float(wma10.iloc[last - 3])
        light = None
        if t is not None:
            adv = vol.iloc[int(t) : max(int(t) + 1, last - tight_n + 1)]
            peak = _num(adv.max()) if len(adv) else None
            rec = _num(vol.iloc[-tight_n:].median())
            if peak and peak > 0 and rec is not None:
                light = rec <= cfg["buy_zone_volume_vs_peak"] * peak
        if (
            near
            and rising
            and recent_t is not None
            and recent_t <= cfg["buy_zone_tightness"]
            and light is not False
        ):
            entry["setups"].append("magic_line_tight_light_volume")

    if earnings_dates and t is not None:
        for raw in earnings_dates:
            try:
                ed = pd.Timestamp(raw).date()
            except Exception:
                continue
            gap_idx = None
            for i, dt in enumerate(df["date"]):
                if pd.Timestamp(dt).date() >= ed:
                    gap_idx = i
                    break
            if gap_idx is None or gap_idx == 0:
                continue
            prior_high = _num(hi.iloc[gap_idx - 1])
            gap_low = _num(lo.iloc[gap_idx])
            if prior_high is None or gap_low is None or gap_low < prior_high:
                continue
            wait = last - gap_idx
            if cfg["earnings_gap_wait_min"] <= wait <= cfg["earnings_gap_wait_max"]:
                recent_low = _num(lo.iloc[-3:].min())
                if recent_low is not None and recent_low <= gap_low * 1.05:
                    entry["setups"].append("earnings_gap_retest")
                    break

    if magic is not None and last_close is not None and not entry["setups"]:
        if last_close <= magic * 1.08 and last_close >= magic * 0.97 and weeks_since and weeks_since >= 2:
            entry["secondary"].append("pullback_to_rising_10wma")

    w = int(cfg["bcd_weeks"])
    if last + 1 >= w and breakout and breakout.get("found"):
        lows = list(lo.iloc[-w:])
        lower = 0
        for i in range(1, len(lows)):
            a, b = _num(lows[i - 1]), _num(lows[i])
            if a is not None and b is not None and b < a:
                lower += 1
        long_ma = _num(wma30.iloc[last]) if last < len(wma30) else None
        if lower >= int(cfg["bcd_lower_lows"]) and long_ma is not None and last_close is not None and last_close > long_ma:
            entry["secondary"].append("bcd_third_lower_low")

    daily_df = _as_weekly_frame(daily) if daily is not None else pd.DataFrame()
    if not daily_df.empty and len(daily_df) >= 25:
        dcl = pd.to_numeric(daily_df["close"], errors="coerce")
        ma = dcl.rolling(20, min_periods=20).mean()
        sd = dcl.rolling(20, min_periods=20).std()
        lower_bb = ma - 2 * sd
        if pd.notna(dcl.iloc[-1]) and pd.notna(lower_bb.iloc[-1]) and pd.notna(dcl.iloc[-2]) and pd.notna(lower_bb.iloc[-2]):
            if float(dcl.iloc[-2]) < float(lower_bb.iloc[-2]) and float(dcl.iloc[-1]) > float(lower_bb.iloc[-1]):
                entry["secondary"].append("daily_close_back_above_lower_bollinger")
        ma50 = dcl.rolling(50, min_periods=50).mean()
        if pd.notna(ma50.iloc[-1]) and pd.notna(dcl.iloc[-1]):
            m = float(ma50.iloc[-1])
            c = float(dcl.iloc[-1])
            if m > 0 and abs(c / m - 1.0) <= 0.04 and len(ma50.dropna()) >= 5:
                if float(ma50.dropna().iloc[-1]) >= float(ma50.dropna().iloc[-5]):
                    entry["secondary"].append("pullback_to_rising_50dma")

    if entry["setups"] and not extended:
        entry["in_buy_zone"] = True

    if breakout and breakout.get("found") and weeks_since is not None:
        if cfg["exit_review_weeks_min"] <= weeks_since <= cfg["exit_review_weeks_max"] and t is not None:
            bounced = False
            for i in range(int(t), last + 1):
                if pd.notna(wma10.iloc[i]) and pd.notna(cl.iloc[i]) and pd.notna(lo.iloc[i]):
                    if float(lo.iloc[i]) <= float(wma10.iloc[i]) * 1.02 and float(cl.iloc[i]) >= float(wma10.iloc[i]):
                        weeks_after = last - i
                        if cfg["exit_review_weeks_min"] <= weeks_after <= cfg["exit_review_weeks_max"]:
                            bounced = True
                            break
            if bounced:
                exit_info["signals"].append("review_7_to_10w_after_magic_bounce")
        months = weeks_since / 4.345
        if cfg["exit_age_months_min"] <= months <= cfg["exit_age_months_max"] + 0.5:
            exit_info["signals"].append("9_to_15_months_after_breakout")
        if weeks_since >= 12 and magic is not None and last_close is not None and last_close < magic:
            flat = False
            if last >= 4 and pd.notna(wma10.iloc[last]) and pd.notna(wma10.iloc[last - 4]):
                prev = float(wma10.iloc[last - 4])
                flat = prev > 0 and (float(wma10.iloc[last]) - prev) / prev <= 0.005
            if flat:
                exit_info["signals"].append("close_below_flattening_magic")
        if t is not None and weeks_since >= 8:
            surges = 0
            last_touch = None
            for i in range(int(t), last + 1):
                if pd.isna(wma10.iloc[i]) or pd.isna(lo.iloc[i]) or pd.isna(cl.iloc[i]):
                    continue
                m = float(wma10.iloc[i])
                if float(lo.iloc[i]) <= m * 1.03 and float(cl.iloc[i]) >= m:
                    if last_touch is None or i - last_touch >= 2:
                        surges += 1
                        last_touch = i
            if surges >= 4 and last_close is not None and magic is not None and last_close < magic:
                exit_info["signals"].append("fourth_or_fifth_surge_failed")
        if weeks_since >= 8:
            rng = (hi - lo).astype(float)
            if pd.notna(rng.iloc[last]) and _num(rng.max()) and float(rng.iloc[last]) >= float(rng.max()) * 0.99:
                med = _num(vol.iloc[max(0, last - 26) : last].median()) if last > 0 else None
                last_v = _num(vol.iloc[last])
                if med and last_v and last_v >= 3 * med:
                    exit_info["signals"].append("parabolic_wide_range_huge_volume")
            if last >= 1 and weeks_since >= 10:
                prior_high = _num(hi.iloc[last - 1])
                this_low = _num(lo.iloc[last])
                this_high = _num(hi.iloc[last])
                hist_high = _num(hi.iloc[:last].max())
                if (
                    prior_high
                    and this_low
                    and hist_high
                    and this_low > prior_high
                    and this_high is not None
                    and this_high >= hist_high
                ):
                    exit_info["signals"].append("late_exhaustion_gap")
        if last_close is not None and last_close >= cfg["exit_price_high"]:
            exit_info["signals"].append("price_into_25_30")

    if secondary_offering is True:
        exit_info["signals"].append("secondary_offering")
    if splits:
        exit_info["signals"].append("stock_split")
    if eps_ramp_ended is True:
        exit_info["signals"].append("end_of_sequential_eps_ramp")
    if massive_insider_selling is True:
        exit_info["signals"].append("massive_insider_selling")

    return {"entry": entry, "exit": exit_info, "extended": extended, "pct_above_magic": _r(pct_above, 2)}

def score_soft_factors(
    *,
    pe: dict | None = None,
    eps_quarters: list | None = None,
    revenue_quarters: list | None = None,
    margin_quarters: list | None = None,
    market_cap: float | None = None,
    float_shares: float | None = None,
    long_term_debt: float | None = None,
    short_interest_pct: float | None = None,
    analyst_count: float | None = None,
    insider_ownership_pct: float | None = None,
    insider_summary: dict | None = None,
    commodity_flag: bool | None = None,
    easy_comps: bool | None = None,
    d: dict | None = None,
) -> dict:
    """Soft / bonus score. More is better; nothing here is required for a candidate."""
    cfg = d or DEFAULTS
    parts: dict[str, float] = {}
    notes: list[str] = []

    def _seq_score(vals: list | None, key: str) -> None:
        nums = [_num(x) for x in (vals or [])]
        nums = [x for x in nums if x is not None]
        if len(nums) < 2:
            notes.append(f"{key}无数据")
            return
        improved = 0
        for a, b in zip(nums[-3:-1], nums[-2:]):
            if b > a > 0:
                improved += 1
        jump = nums[-1] / nums[-2] - 1.0 if nums[-2] and nums[-2] > 0 else None
        sc = 5 * improved
        if jump is not None and jump >= 0.25:
            sc += 5
        parts[key] = float(min(15, sc) if key == "eps" else min(10, sc))

    _seq_score(eps_quarters, "eps")
    _seq_score(revenue_quarters, "revenue")
    if margin_quarters:
        m = [_num(x) for x in margin_quarters if _num(x) is not None]
        if len(m) >= 2 and m[-1] > m[-2]:
            parts["margins"] = 8.0 if (len(m) >= 3 and m[-1] > m[-2] > m[-3]) else 5.0
        elif len(m) < 2:
            notes.append("利润率无数据")
        else:
            parts["margins"] = 0.0
    else:
        notes.append("利润率无数据")

    run = (pe or {}).get("pe_run_rate")
    if run is None:
        notes.append("年化PE run-rate无数据")
        parts["pe_run_rate"] = 0.0
    elif run <= cfg["pe_run_rate_max"]:
        parts["pe_run_rate"] = 15.0
    elif run <= 15:
        parts["pe_run_rate"] = 8.0
    else:
        parts["pe_run_rate"] = 0.0

    if easy_comps is True:
        parts["easy_comps"] = 5.0
    elif easy_comps is False:
        parts["easy_comps"] = 0.0
    else:
        notes.append("同比基数无数据")

    cap = _num(market_cap)
    if cap is None:
        notes.append("市值无数据")
        parts["market_cap"] = 0.0
        cap_tier = None
    elif cap <= cfg["cap_best_max"]:
        parts["market_cap"] = 12.0
        cap_tier = "best_under_100m"
    elif cap <= cfg["cap_ok_max"]:
        parts["market_cap"] = 7.0
        cap_tier = "ok_under_500m"
    elif cap <= cfg["cap_allow_max"]:
        parts["market_cap"] = 3.0
        cap_tier = "allow_under_1b"
    else:
        parts["market_cap"] = 0.0
        cap_tier = "above_1b"

    fl = _num(float_shares)
    if fl is None:
        notes.append("流通股本无数据")
        parts["float"] = 0.0
    elif cfg["float_best_low"] <= fl <= cfg["float_best_high"]:
        parts["float"] = 15.0
    elif fl <= cfg["float_good_max"]:
        parts["float"] = 10.0
    elif fl <= 20_000_000:
        parts["float"] = 4.0
    else:
        parts["float"] = 0.0

    debt = _num(long_term_debt)
    if debt is None:
        notes.append("长期负债无数据")
        parts["debt"] = 0.0
    elif debt <= 0:
        parts["debt"] = 8.0
    elif cap and cap > 0 and debt / cap <= 0.15:
        parts["debt"] = 4.0
    else:
        parts["debt"] = 0.0

    si = _num(short_interest_pct)
    if si is None:
        notes.append("空头占流通股无数据")
        parts["short_interest"] = 0.0
    elif si < cfg["short_interest_max"]:
        parts["short_interest"] = 5.0
    else:
        parts["short_interest"] = 0.0

    ac = _num(analyst_count)
    if ac is None:
        notes.append("分析师覆盖无数据")
        parts["analysts"] = 0.0
    elif ac <= 1:
        parts["analysts"] = 5.0
    elif ac <= 3:
        parts["analysts"] = 2.0
    else:
        parts["analysts"] = 0.0

    own = _num(insider_ownership_pct)
    if own is None:
        notes.append("内部人持股无数据")
        parts["insider_ownership"] = 0.0
    else:
        own_p = own * 100 if own <= 1.5 else own
        parts["insider_ownership"] = 7.0 if own_p >= cfg["insider_own_plus"] * 100 else 0.0

    ins = insider_summary or {}
    if ins.get("status") == "ok" and ins.get("c_level_buyer_count", 0) >= 2 and ins.get("meaningful_buy_count", 0) >= 1:
        bonus = 20.0 if ins.get("timing") == "during_base_or_just_after_breakout" else 10.0
        if ins.get("token_flag"):
            bonus = min(bonus, 5.0)
        parts["insider_buys"] = bonus
    elif ins.get("status") == "ok" and ins.get("meaningful_buy_count", 0) >= 1:
        parts["insider_buys"] = 8.0 if ins.get("timing") == "during_base_or_just_after_breakout" else 4.0
    elif not ins or ins.get("status") == "无数据":
        notes.append("内部人买入无数据")
        parts["insider_buys"] = 0.0
    else:
        parts["insider_buys"] = 0.0

    if commodity_flag is True:
        parts["commodity"] = -8.0
        notes.append("疑似商品价格驱动业务（仅标记，不自动剔除）")
    elif commodity_flag is False:
        parts["commodity"] = 0.0
    else:
        notes.append("行业分类无数据")

    total = round(sum(parts.values()), 2)
    return {
        "score": total,
        "parts": parts,
        "market_cap": _r(cap, 0),
        "market_cap_tier": cap_tier,
        "float_shares": _r(fl, 0),
        "notes": notes,
    }


def classify_state(*, hard_pass: bool, near: bool, entry: dict, extended: bool, exit_signals: list, watched: bool) -> str:
    if watched and exit_signals:
        return "退出信号"
    if hard_pass and extended:
        return "已延伸勿追"
    if hard_pass and entry.get("in_buy_zone") and not extended:
        return "买点区"
    if hard_pass or near:
        return "观察"
    return "观察"

def evaluate_ticker(
    weekly: pd.DataFrame | list[dict],
    *,
    symbol: str,
    name: str | None = None,
    exchange: str | None = None,
    first_date: str | None = None,
    fundamentals: dict | None = None,
    insider_transactions: list[dict] | None = None,
    insider_status: str | None = None,
    daily: pd.DataFrame | list[dict] | None = None,
    earnings_dates: list[str] | None = None,
    splits: list[dict] | None = None,
    secondary_offering: bool | None = None,
    watched: bool = False,
    d: dict | None = None,
) -> dict:
    """Full rule evaluation for one name. Missing inputs stay None / 无数据."""
    cfg = d or DEFAULTS
    df = _as_weekly_frame(weekly)
    fund = fundamentals or {}
    exch = (exchange or fund.get("exchange") or "").strip()
    exch_u = exch.upper()
    last_close = _num(df["close"].iloc[-1]) if not df.empty else _num(fund.get("price"))
    bucket = price_bucket(last_close, cfg)
    history_weeks = int(len(df))
    first = first_date
    if not first and not df.empty:
        first = pd.Timestamp(df["date"].iloc[0]).date().isoformat()

    fail: list[str] = []
    near_reasons: list[str] = []
    if exch_u in OTC_EXCHANGES or "PINK" in exch_u or "OTC" in exch_u:
        fail.append("非NYSE/Nasdaq（OTC/Pink已排除）")
    if history_weeks < int(cfg["min_history_weeks"]):
        fail.append("上市或可交易历史不足约1年")
    if last_close is None:
        fail.append("价格无数据")
    elif bucket == "too_low":
        fail.append("价格低于2美元，已排除")
    elif bucket == "above_core":
        fail.append("价格高于15美元，不进入核心列表")

    cl = pd.to_numeric(df["close"], errors="coerce") if not df.empty else pd.Series(dtype=float)
    wma10 = weekly_sma(cl, int(cfg["ma_magic"])) if not df.empty else pd.Series(dtype=float)
    wma30 = weekly_sma(cl, int(cfg["ma_long"])) if not df.empty else pd.Series(dtype=float)
    last_magic = _num(wma10.iloc[-1]) if len(wma10) else None
    last_long = _num(wma30.iloc[-1]) if len(wma30) else None
    above_30 = bool(last_close is not None and last_long is not None and last_close > last_long)
    if last_long is None:
        fail.append("30周均线无数据")
    elif not above_30:
        fail.append("收盘未站上并守住30周均线")

    brk = detect_base_breakout(df, cfg) if not df.empty else {"found": False}
    angle = (
        angle_of_attack(df, brk, cfg)
        if brk.get("found")
        else {"pass": False, "missing": True, "formula": ANGLE_FORMULA_CN, "slope_pct_per_week": None, "k_weeks": None, "impulse": None}
    )

    vol_grade = brk.get("vol_expand_pass") if brk.get("found") else None
    if not brk.get("found"):
        fail.append("未检测到合格平台突破")
    else:
        if brk.get("base_weak") and (brk.get("base_weeks") or 0) < cfg["base_weeks_strict"] and vol_grade == "strict":
            near_reasons.append("平台仅约8–11周（弱标记）")
        if vol_grade == "soft":
            near_reasons.append("突破周量能为平台中位量的3–5倍（接近，非严格通过）")
        if vol_grade is None:
            fail.append("突破周量能无数据")
        if angle.get("missing"):
            fail.append("攻角不足3周或无数据")
        elif not angle.get("pass"):
            fail.append("攻角不足（推进偏慢或缺少单周冲刺）")
        t = brk.get("breakout_index")
        if t is not None and above_30 and last_long is not None:
            sl = wma30.iloc[int(t) :]
            cs = cl.iloc[int(t) :]
            ok = sl.notna() & cs.notna()
            if ok.any():
                frac = float((cs[ok] > sl[ok]).mean())
                if frac < cfg["hold_30wma_min_frac"]:
                    fail.append("突破后未能持续守在30周均线之上")
                    above_30 = False

    prior_runner = False
    if bucket == "above_core" and brk.get("found") and (brk.get("breakout_close") or 0) <= cfg["price_max_core"]:
        prior_runner = True

    eps_q = fund.get("eps_quarters")
    rev_q = fund.get("revenue_quarters")
    pe = pe_run_rate(last_close, fund.get("quarterly_eps"), fund.get("ttm_eps"))
    easy = fund.get("easy_comps")
    if easy is None and isinstance(rev_q, list) and len(rev_q) >= 5:
        yoy = _num(rev_q[-5])
        prev = _num(rev_q[-6]) if len(rev_q) >= 6 else None
        if yoy is not None and prev is not None and prev > 0 and yoy < prev:
            easy = True
        elif yoy is not None and prev is not None:
            easy = False
    ins_txs = insider_transactions
    if insider_status == "无数据" and not ins_txs:
        ins = {
            "status": "无数据",
            "source": "SEC EDGAR Form 4",
            "open_market_buys": [],
            "buyer_count": 0,
            "c_level_buyer_count": 0,
            "token_flag": False,
            "meaningful_buy_count": 0,
            "timing": "无数据",
            "note": "无数据",
        }
    else:
        ins = summarize_insider_buys(
            ins_txs,
            base_start=brk.get("base_start"),
            breakout_date=brk.get("breakout_date"),
        )
        if insider_status == "无数据":
            ins["status"] = "无数据"
            ins["note"] = ins.get("note") or "无数据"

    commodity = fund.get("commodity_flag")
    if commodity is None and (fund.get("sector") or fund.get("industry")):
        blob = f"{fund.get('sector') or ''} {fund.get('industry') or ''}".lower()
        commodity = any(h in blob for h in COMMODITY_HINTS)

    soft = score_soft_factors(
        pe=pe,
        eps_quarters=eps_q if isinstance(eps_q, list) else None,
        revenue_quarters=rev_q if isinstance(rev_q, list) else None,
        margin_quarters=fund.get("margin_quarters") if isinstance(fund.get("margin_quarters"), list) else None,
        market_cap=fund.get("market_cap"),
        float_shares=fund.get("float_shares"),
        long_term_debt=fund.get("long_term_debt"),
        short_interest_pct=fund.get("short_interest_pct"),
        analyst_count=fund.get("analyst_count"),
        insider_ownership_pct=fund.get("insider_ownership_pct"),
        insider_summary=ins,
        commodity_flag=commodity,
        easy_comps=easy,
        d=cfg,
    )

    setups = classify_entry_exit(
        df,
        brk,
        wma10=wma10,
        wma30=wma30,
        daily=daily,
        earnings_dates=earnings_dates or fund.get("earnings_dates"),
        splits=splits or fund.get("splits"),
        secondary_offering=secondary_offering if secondary_offering is not None else fund.get("secondary_offering"),
        eps_ramp_ended=fund.get("eps_ramp_ended"),
        massive_insider_selling=fund.get("massive_insider_selling"),
        d=cfg,
    )

    hard_blockers = [x for x in fail if x != "价格高于15美元，不进入核心列表"]
    vol_ok = vol_grade == "strict"
    base_ok = bool(brk.get("found") and not brk.get("base_weak") and (brk.get("base_weeks") or 0) >= cfg["base_weeks_strict"])
    price_ok = bucket in {"sweet", "core_other", "caution_low"}
    hard_pass = (
        not hard_blockers
        and vol_ok
        and base_ok
        and price_ok
        and above_30
        and angle.get("pass") is True
        and history_weeks >= int(cfg["min_history_weeks"])
    )
    near = False
    if not hard_pass and brk.get("found") and above_30 and history_weeks >= int(cfg["min_history_weeks"]) and price_ok:
        soft_vol = vol_grade in {"soft", "strict"}
        weak_base = bool(brk.get("base_weak"))
        if soft_vol and (base_ok or weak_base) and (angle.get("pass") or angle.get("missing") is False):
            if vol_grade == "soft" or weak_base or not angle.get("pass"):
                near = True
                if not angle.get("pass") and not angle.get("missing"):
                    near_reasons.append("攻角接近但未达严格阈值")
    if prior_runner:
        near = False
        hard_pass = False

    state = classify_state(
        hard_pass=hard_pass,
        near=near or prior_runner,
        entry=setups["entry"],
        extended=setups["extended"],
        exit_signals=setups["exit"]["signals"],
        watched=watched,
    )
    if prior_runner and state != "退出信号":
        state = "观察"

    missing = []
    for label, ok in (
        ("short_interest_pct", fund.get("short_interest_pct") is not None),
        ("float_shares", fund.get("float_shares") is not None),
        ("market_cap", fund.get("market_cap") is not None),
        ("long_term_debt", fund.get("long_term_debt") is not None),
        ("pe_run_rate", pe.get("pe_run_rate") is not None),
        ("insider_buys", ins.get("status") == "ok"),
        ("analyst_count", fund.get("analyst_count") is not None),
        ("insider_ownership_pct", fund.get("insider_ownership_pct") is not None),
    ):
        if not ok:
            missing.append(label)

    chart = _chart_payload(df, wma10, wma30, brk)
    return {
        "symbol": symbol,
        "name": name or fund.get("name") or symbol,
        "exchange": exch or None,
        "price": _r(last_close, 4),
        "price_bucket": bucket,
        "price_caution": bucket == "caution_low",
        "prior_runner": prior_runner,
        "state": state,
        "hard_pass": hard_pass,
        "near": near,
        "near_miss_reasons": near_reasons,
        "fail_reasons": fail,
        "score": soft["score"],
        "score_parts": soft["parts"],
        "technicals": {
            "base_weeks": brk.get("base_weeks"),
            "base_weak": brk.get("base_weak"),
            "base_start": brk.get("base_start"),
            "base_end": brk.get("base_end"),
            "base_high": brk.get("base_high"),
            "base_low": brk.get("base_low"),
            "tightness": brk.get("tightness"),
            "quiet_volume": brk.get("quiet_volume"),
            "breakout_date": brk.get("breakout_date"),
            "breakout_level": brk.get("breakout_level"),
            "vol_expand": brk.get("vol_expand"),
            "vol_expand_pass": vol_grade,
            "angle_pct_per_week": angle.get("slope_pct_per_week"),
            "angle_k_weeks": angle.get("k_weeks"),
            "angle_impulse": angle.get("impulse"),
            "angle_pass": angle.get("pass"),
            "angle_formula": ANGLE_FORMULA_CN,
            "close": _r(last_close, 4),
            "wma10": _r(last_magic, 4),
            "wma30": _r(last_long, 4),
            "above_30wma": above_30,
            "history_weeks": history_weeks,
            "first_bar": first,
            "pct_above_magic": setups["pct_above_magic"],
            "weeks_since_breakout": brk.get("weeks_since_breakout"),
        },
        "entry": setups["entry"],
        "exit": setups["exit"],
        "extended": setups["extended"],
        "fundamentals": {
            "market_cap": soft["market_cap"],
            "market_cap_tier": soft["market_cap_tier"],
            "float_shares": soft["float_shares"],
            "pe_run_rate": pe.get("pe_run_rate"),
            "pe_ttm": pe.get("pe_ttm"),
            "quarterly_eps": pe.get("quarterly_eps"),
            "ttm_eps": pe.get("ttm_eps"),
            "eps_quarters": eps_q if isinstance(eps_q, list) else None,
            "revenue_quarters": rev_q if isinstance(rev_q, list) else None,
            "margin_quarters": fund.get("margin_quarters") if isinstance(fund.get("margin_quarters"), list) else None,
            "long_term_debt": _r(fund.get("long_term_debt"), 0),
            "short_interest_pct": _r(fund.get("short_interest_pct"), 4),
            "analyst_count": _r(fund.get("analyst_count"), 0),
            "insider_ownership_pct": _r(fund.get("insider_ownership_pct"), 4),
            "commodity_flag": commodity,
            "easy_comps": easy,
            "sector": fund.get("sector"),
            "industry": fund.get("industry"),
        },
        "insiders": ins,
        "weekly_bars": chart,
        "missing": missing,
        "manual_checklist": MANUAL_CHECKLIST,
    }


def _chart_payload(df: pd.DataFrame, wma10: pd.Series, wma30: pd.Series, brk: dict, limit: int = 80) -> list[dict]:
    if df is None or df.empty:
        return []
    start = max(0, len(df) - limit)
    out = []
    for i in range(start, len(df)):
        rec = {
            "date": pd.Timestamp(df["date"].iloc[i]).date().isoformat(),
            "open": _r(df["open"].iloc[i], 4),
            "high": _r(df["high"].iloc[i], 4),
            "low": _r(df["low"].iloc[i], 4),
            "close": _r(df["close"].iloc[i], 4),
            "volume": _r(df["volume"].iloc[i], 0),
            "wma10": _r(wma10.iloc[i], 4) if i < len(wma10) else None,
            "wma30": _r(wma30.iloc[i], 4) if i < len(wma30) else None,
        }
        if brk.get("found") and brk.get("breakout_index") == i:
            rec["breakout"] = True
        if brk.get("found") and brk.get("base_start") and brk.get("base_end"):
            day = rec["date"]
            if brk["base_start"] <= day <= brk["base_end"]:
                rec["in_base"] = True
        out.append(rec)
    return out

def empty_seed_payload(*, note: str | None = None, scanned: int = 0, universe: int = 0) -> dict:
    now = _iso_shanghai()
    return {
        "ok": True,
        "source": "seed",
        "updated_at": now,
        "timezone": "Asia/Shanghai (UTC+8)",
        "asof_session": None,
        "asof_session_tz": "America/New_York",
        "disclaimer": (
            "研究展示，不能下单，不构成投资建议。书中历史收益率声称未在本系统独立验证。"
            "作者曾提到高度集中的凯利仓位和60%–100%量级回撤；本系统默认研究仓位为专项资金的5%–10%，"
            "同一时间最多一只，且必须预设止损，绝不推荐满仓或90%凯利。"
        ),
        "defaults": dict(DEFAULTS),
        "angle_formula": ANGLE_FORMULA_CN,
        "stop_note": PRIMARY_STOP_CN,
        "sizing": {
            "sleeve_min": DEFAULTS["sizing_sleeve_min"],
            "sleeve_max": DEFAULTS["sizing_sleeve_max"],
            "max_names": DEFAULTS["sizing_max_names"],
            "note": "默认研究仓位为专项资金的5%–10%，最多一只；禁止把本书讨论的高集中凯利仓位当作系统默认。",
        },
        "counts": {
            "universe": universe,
            "scanned": scanned,
            "candidates": 0,
            "near": 0,
        },
        "candidates": [],
        "near": [],
        "manual_checklist": MANUAL_CHECKLIST,
        "insider_source": "优先官方 SEC EDGAR Form 4（transactionCode=P 的公开市场买入）。无申报或解析失败时显示无数据，绝不编造。",
        "historical_edge": "未做消除幸存者偏差的历史检验，不声称存在可重复优势。书中 14,972% 的历史声称未在本系统独立验证。",
        "limitations": [
            "核心技术全部使用周线；日线 50 均与布林规则仅为次要信号，无日线时为无数据。",
            "内部人数据来自 Form 4，代码 P 仍可能包含部分私下成交；期权授予/行权/赠与等已排除。",
            "空头占比、部分负债与分析师覆盖若源数据缺失则显示无数据。",
            "Yahoo 与 EDGAR 均有延迟，且可能限流；页签默认先展示快照，不在打开时强行全市场刷新。",
            "平台/突破/攻角是可检验的数值近似，不能替代看图，也不是对原书段落的逐字复制。",
            "本快照不是点对点历史回测，没有投资绩效承诺。",
            "完整名单以每周六 08:00（北京时间，UTC 周六 00:00）自动扫描写入的快照为准；网页「后台重算」不一定能一次跑完全市场。",
        ],
        "note": note or "尚无实盘筛选结果，名单为空，不是编造的零收益。",
    }


def snapshot_view(snap: dict | None, watch_symbols: Iterable[str] | None = None) -> dict:
    """Normalize a payload for the UI. Missing fields stay null (UI shows 无数据)."""
    if not isinstance(snap, dict):
        out = empty_seed_payload(note="无数据：快照缺失")
        out["ok"] = False
        return out
    out = json.loads(json.dumps(snap, default=str))
    watch = {str(s).strip().upper() for s in (watch_symbols or []) if str(s).strip()}
    for key in ("candidates", "near"):
        rows = out.get(key)
        out[key] = [_normalize_row(r, watch) for r in rows] if isinstance(rows, list) else []
    out.setdefault("defaults", dict(DEFAULTS))
    out.setdefault("manual_checklist", MANUAL_CHECKLIST)
    out.setdefault("angle_formula", ANGLE_FORMULA_CN)
    out.setdefault("stop_note", PRIMARY_STOP_CN)
    out.setdefault("disclaimer", empty_seed_payload()["disclaimer"])
    out.setdefault("historical_edge", empty_seed_payload()["historical_edge"])
    out.setdefault("insider_source", empty_seed_payload()["insider_source"])
    out.setdefault("sizing", empty_seed_payload()["sizing"])
    out.setdefault("limitations", empty_seed_payload()["limitations"])
    counts = out.get("counts") if isinstance(out.get("counts"), dict) else {}
    counts["candidates"] = sum(1 for r in out["candidates"] if r.get("hard_pass"))
    counts["near"] = len(out["near"])
    out["counts"] = counts
    if not out.get("updated_at"):
        out["updated_at"] = None
    if "timezone" not in out:
        out["timezone"] = "Asia/Shanghai (UTC+8)"
    if "asof_session_tz" not in out:
        out["asof_session_tz"] = "America/New_York"
    return out


def _normalize_row(row: Any, watch: set[str]) -> dict:
    if not isinstance(row, dict):
        return {"symbol": None, "state": "观察", "missing": ["all"], "insiders": {"status": "无数据"}}
    r = dict(row)
    sym = str(r.get("symbol") or "").upper() or None
    r["symbol"] = sym
    for k in ("name", "exchange", "price", "price_bucket", "score"):
        r.setdefault(k, None)
    r.setdefault("hard_pass", False)
    r.setdefault("near", False)
    r.setdefault("fail_reasons", [])
    r.setdefault("near_miss_reasons", [])
    r.setdefault("missing", [])
    tech = dict(r.get("technicals") or {})
    for k in (
        "base_weeks", "breakout_date", "vol_expand", "angle_pct_per_week",
        "wma10", "wma30", "close", "pct_above_magic", "weeks_since_breakout",
    ):
        tech.setdefault(k, None)
    r["technicals"] = tech
    fund = dict(r.get("fundamentals") or {})
    for k in (
        "market_cap", "market_cap_tier", "float_shares", "pe_run_rate", "pe_ttm",
        "long_term_debt", "short_interest_pct", "analyst_count", "insider_ownership_pct",
        "commodity_flag", "easy_comps",
    ):
        fund.setdefault(k, None)
    r["fundamentals"] = fund
    ins = dict(r.get("insiders") or {})
    ins.setdefault("status", "无数据")
    ins.setdefault("open_market_buys", [])
    ins.setdefault("source", "SEC EDGAR Form 4")
    if not ins.get("open_market_buys"):
        if ins.get("status") not in {"ok", "无数据"}:
            ins["status"] = "无数据"
    r["insiders"] = ins
    r.setdefault("weekly_bars", [])
    r.setdefault("entry", {
        "in_buy_zone": False,
        "setups": [],
        "secondary": [],
        "stop_primary": DEFAULTS["stop_primary"],
        "stop_level": None,
    })
    r.setdefault("exit", {"signals": [], "for_watch_only": True})
    r.setdefault("manual_checklist", MANUAL_CHECKLIST)
    watched = bool(sym and sym in watch)
    r["watched"] = watched
    r["display_state"] = classify_state(
        hard_pass=bool(r.get("hard_pass")),
        near=bool(r.get("near") or r.get("prior_runner")),
        entry=r.get("entry") or {},
        extended=bool(r.get("extended")),
        exit_signals=list((r.get("exit") or {}).get("signals") or []),
        watched=watched,
    )
    r["state"] = r["display_state"]
    return r



_progress_lock = threading.Lock()
_progress_state: dict[str, Any] | None = None
_job_running = False
_refresh_gate = threading.Lock()
_remote_lock = threading.Lock()
_remote_next_check = 0.0
_remote_fetching = False


def empty_progress(*, state: str = "idle") -> dict:
    return {
        "ok": True,
        "state": state,
        "phase": None,
        "phase_label": None,
        "percent": 0,
        "processed": 0,
        "total": 0,
        "message": "",
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "error": None,
        "job_id": None,
        "last_run_at": None,
        "last_run_state": None,
    }


def _clamp_pct(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    return max(0, min(100, n))


def phase_percent(phase: str | None, processed: int = 0, total: int = 0) -> int:
    lo, hi = PHASE_WEIGHTS.get(phase or "queued", (0, 100))
    if not total:
        return int(lo)
    frac = max(0.0, min(1.0, float(processed) / float(total)))
    return _clamp_pct(lo + frac * (hi - lo))


def _progress_from_disk() -> dict | None:
    if not PROGRESS_PATH.exists():
        return None
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**empty_progress(), **data}
    except Exception:
        logger.debug("progress file unreadable", exc_info=True)
    return None


def _persist_progress(cur: dict) -> None:
    global _progress_state
    with _progress_lock:
        _progress_state = dict(cur)
        try:
            PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PROGRESS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(_progress_state, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(PROGRESS_PATH)
        except Exception:
            logger.debug("progress write skipped", exc_info=True)


def _updated_age_seconds(updated_at: Any) -> float | None:
    ts = _parse_ts(updated_at)
    if ts is None:
        return None
    return max(0.0, (datetime.now(SHANGHAI) - ts).total_seconds())


def load_progress(*, persist_stale: bool = True) -> dict:
    """Current job progress. Idle has no fake percent. Stale in-flight jobs are marked failed."""
    global _progress_state
    with _progress_lock:
        cur = dict(_progress_state) if _progress_state else None
    if cur is None:
        cur = _progress_from_disk()
    if cur is None:
        cur = empty_progress()
    if cur.get("state") == "running" and not _job_running:
        age = _updated_age_seconds(cur.get("updated_at"))
        if age is None or age > STALE_RUNNING_SEC:
            cur["state"] = "error"
            cur["error"] = "任务中断或进程已退出"
            cur["message"] = "任务中断或进程已退出，未编造扫描结果。"
            cur["finished_at"] = _iso_shanghai()
            cur["last_run_at"] = cur["finished_at"]
            cur["last_run_state"] = "error"
            if persist_stale:
                _persist_progress(cur)
    with _progress_lock:
        if _progress_state is None or persist_stale:
            _progress_state = dict(cur)
    return dict(cur)


def set_progress(
    *,
    phase: str | None = None,
    processed: int | None = None,
    total: int | None = None,
    message: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> dict:
    cur = load_progress(persist_stale=False)
    if state:
        cur["state"] = state
    if phase is not None:
        cur["phase"] = phase
        cur["phase_label"] = PHASE_LABELS.get(phase, phase)
    if processed is not None:
        cur["processed"] = int(processed)
    if total is not None:
        cur["total"] = int(total)
    if message is not None:
        cur["message"] = message
    if error is not None:
        cur["error"] = error
    cur["updated_at"] = _iso_shanghai()
    if cur.get("state") == "running":
        cur["percent"] = phase_percent(cur.get("phase"), cur.get("processed") or 0, cur.get("total") or 0)
    elif cur.get("state") == "done":
        cur["percent"] = 100
    _persist_progress(cur)
    return dict(cur)


def begin_job(job_id: str | None = None) -> dict:
    global _job_running
    prev = load_progress(persist_stale=False)
    if _job_running and prev.get("state") == "running":
        return dict(prev)
    _job_running = True
    now = _iso_shanghai()
    cur = empty_progress(state="running")
    cur["phase"] = "queued"
    cur["phase_label"] = PHASE_LABELS["queued"]
    cur["percent"] = 0
    cur["message"] = "已开始后台扫描…"
    cur["started_at"] = now
    cur["updated_at"] = now
    cur["job_id"] = job_id or now
    cur["last_run_at"] = prev.get("last_run_at")
    cur["last_run_state"] = prev.get("last_run_state")
    _persist_progress(cur)
    return dict(cur)


def finish_job(*, ok: bool = True, message: str = "", error: str | None = None) -> dict:
    global _job_running
    cur = load_progress(persist_stale=False)
    now = _iso_shanghai()
    cur["finished_at"] = now
    cur["updated_at"] = now
    cur["last_run_at"] = now
    if ok:
        cur["state"] = "done"
        cur["percent"] = 100
        cur["phase"] = "scoring"
        cur["phase_label"] = PHASE_LABELS["scoring"]
        cur["message"] = message or "扫描完成"
        cur["error"] = None
        cur["last_run_state"] = "done"
    else:
        cur["state"] = "error"
        cur["error"] = error or "扫描失败"
        cur["message"] = message or (error or "扫描失败")
        cur["last_run_state"] = "error"
    _job_running = False
    _persist_progress(cur)
    return dict(cur)


def is_job_running() -> bool:
    return bool(_job_running)


def _reset_progress_for_tests() -> None:
    global _job_running, _progress_state, _remote_next_check, _remote_fetching
    _job_running = False
    _progress_state = None
    _remote_next_check = 0.0
    _remote_fetching = False
    try:
        if PROGRESS_PATH.exists():
            PROGRESS_PATH.unlink()
    except Exception:
        pass


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=SHANGHAI)
    return ts


def seed_is_newer(candidate: dict | None, baseline: dict | None) -> bool:
    """True iff candidate has a strictly newer updated_at than baseline (or baseline missing)."""
    if not isinstance(candidate, dict):
        return False
    ta = _parse_ts(candidate.get("updated_at"))
    if ta is None:
        return False
    if not isinstance(baseline, dict):
        return True
    tb = _parse_ts(baseline.get("updated_at"))
    if tb is None:
        return True
    return ta > tb


def _remote_seed_allowed() -> bool:
    if os.environ.get("SS_SEED_REMOTE") == "0":
        return False
    if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("SS_SEED_REMOTE") != "1":
        return False
    return True


def fetch_remote_seed(timeout: float = REMOTE_SEED_TIMEOUT) -> dict | None:
    """Optional GitHub raw snapshot so Render can pick up weekly Actions seeds without a deploy.

    Short timeout. On any failure return None (caller keeps the bundled seed).
    Disabled when SS_SEED_REMOTE=0, and during pytest unless SS_SEED_REMOTE=1.
    """
    if not _remote_seed_allowed():
        return None
    try:
        import requests

        r = requests.get(
            REMOTE_SEED_URL,
            timeout=timeout,
            headers={"User-Agent": "futures-dashboard us-superstocks research"},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "candidates" in data:
            return data
    except Exception as exc:  # noqa: BLE001
        logger.info("remote superstocks seed fetch failed; keeping local: %s", exc)
    return None


def maybe_refresh_remote_seed(local: dict | None = None, *, blocking: bool = False) -> None:
    """Kick a single-flight remote seed check. Does not invent candidates."""
    if not _remote_seed_allowed():
        return
    global _remote_next_check, _remote_fetching
    now = time.time()
    with _remote_lock:
        if _remote_fetching or now < _remote_next_check:
            return
        _remote_fetching = True

    def _go() -> None:
        global _remote_next_check, _remote_fetching
        try:
            remote = fetch_remote_seed()
            current = local if local is not None else load_local_seed()
            if remote is not None and seed_is_newer(remote, current):
                try:
                    write_seed(remote, SEED_PATH)
                except Exception:
                    logger.debug("remote seed local write skipped", exc_info=True)
                _store_cache(remote)
        finally:
            _remote_next_check = time.time() + REMOTE_SEED_TTL
            _remote_fetching = False

    if blocking:
        _go()
        return
    try:
        threading.Thread(target=_go, name="ss-remote-seed", daemon=True).start()
    except Exception:
        _remote_fetching = False
        logger.debug("remote seed thread failed to start", exc_info=True)


def load_local_seed() -> dict | None:
    for path in (SEED_PATH, DOCS_SEED_PATH):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.debug("superstocks seed unreadable: %s", path, exc_info=True)
    return None


def load_seed() -> dict | None:
    local = load_local_seed()
    maybe_refresh_remote_seed(local, blocking=False)
    return local


def write_seed(payload: dict, path: Path | None = None) -> None:
    target = path or SEED_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")


def _attach_meta(view: dict, *, force: bool = False) -> dict:
    out = dict(view)
    out["progress"] = load_progress()
    out["weekly_scan"] = dict(WEEKLY_SCAN_INFO)
    if force and "已请求后台重算" not in (out.get("note") or ""):
        out["note"] = (out.get("note") or "") + " · 已请求后台重算，先返回快照"
    return out


def snapshot(force: bool = False) -> dict:
    """Return in-memory cache or the shipped seed. Never blocks on a full scan."""
    global _cache
    seed = load_seed()
    with _lock:
        cached = _cache
    if seed is not None and cached and seed_is_newer(seed, cached[1]):
        view = snapshot_view(seed)
        view["source"] = seed.get("source") or "seed"
        return _attach_meta(view, force=force)
    if not force and cached and time.time() - cached[0] < CACHE_TTL:
        return _attach_meta(snapshot_view(dict(cached[1])), force=force)
    if seed is not None:
        view = snapshot_view(seed)
        view["source"] = seed.get("source") or "seed"
        return _attach_meta(view, force=force)
    return _attach_meta(snapshot_view(empty_seed_payload()), force=force)


def _store_cache(payload: dict) -> dict:
    global _cache
    view = snapshot_view(payload)
    with _lock:
        _cache = (time.time(), view)
    return view

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_UA = (
    "futures-dashboard us-superstocks research "
    "(https://github.com/quanliulq-pixel/futures-dashboard; "
    "quanliulq-pixel@users.noreply.github.com)"
)


def _http_get(url: str, *, timeout: int = 20, user_agent: str | None = None) -> bytes:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent or "Mozilla/5.0 futures-dashboard research",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_nasdaq_listed(text: str) -> list[dict]:
    """Parse nasdaqlisted.txt / otherlisted.txt into NYSE/Nasdaq common stocks."""
    rows: list[dict] = []
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if not lines:
        return rows
    header = [c.strip() for c in lines[0].split("|")]
    idx = {c.lower(): i for i, c in enumerate(header)}

    def col(parts, *names):
        for n in names:
            i = idx.get(n.lower())
            if i is not None and i < len(parts):
                return parts[i].strip()
        return ""

    for line in lines[1:]:
        if line.startswith("File Creation") or line.startswith("Symbol|"):
            continue
        parts = line.split("|")
        sym = col(parts, "Symbol", "ACT Symbol", "NASDAQ Symbol")
        if not re.fullmatch(r"[A-Z]{1,5}", sym or ""):
            continue
        test = col(parts, "Test Issue")
        etf = col(parts, "ETF")
        name = col(parts, "Security Name", "Company Name")
        exch = col(parts, "Exchange", "Market Category")
        nexts = col(parts, "NextShares")
        if test.upper() == "Y" or etf.upper() == "Y" or nexts.upper() == "Y":
            continue
        blob = f"{name}".lower()
        if any(w in blob for w in ("warrant", "right", "unit", "preferred", "note", "debenture")):
            continue
        market = None
        if "market category" in idx:
            market = "NASDAQ"
        else:
            code = exch.upper()
            if code == "N":
                market = "NYSE"
            elif code == "A":
                market = "NYSE American"
            else:
                continue
        if any(x in sym for x in (".", "/", "^", "+", " ")):
            continue
        rows.append({"symbol": sym, "name": name, "exchange": market or exch or None})
    return rows


def load_universe(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and UNI_PATH.exists() and now - UNI_PATH.stat().st_mtime < UNI_TTL:
        try:
            data = json.loads(UNI_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            logger.debug("universe cache unreadable", exc_info=True)
    items: list[dict] = []
    try:
        raw = _http_get(NASDAQ_LISTED, timeout=25).decode("latin-1")
        items.extend(parse_nasdaq_listed(raw))
    except Exception:
        logger.warning("nasdaqlisted fetch failed", exc_info=True)
    try:
        raw = _http_get(OTHER_LISTED, timeout=25).decode("latin-1")
        items.extend(parse_nasdaq_listed(raw))
    except Exception:
        logger.warning("otherlisted fetch failed", exc_info=True)
    out: dict[str, dict] = {}
    for r in items:
        out[r["symbol"]] = r
    rows = list(out.values())
    if rows:
        UNI_PATH.parent.mkdir(parents=True, exist_ok=True)
        UNI_PATH.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows
    if UNI_PATH.exists():
        try:
            data = json.loads(UNI_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _extract_ohlcv(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a yfinance group_by=ticker download into per-symbol weekly frames."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in symbols:
            try:
                if sym not in raw.columns.get_level_values(0):
                    continue
                part = raw[sym].copy()
            except Exception:
                continue
            part = part.rename(columns={c: str(c).lower() for c in part.columns})
            part = part.reset_index()
            datecol = "date" if "date" in part.columns else part.columns[0]
            part = part.rename(columns={datecol: "date"})
            frame = _as_weekly_frame(part)
            if not frame.empty:
                out[sym] = frame
        return out
    part = raw.rename(columns={c: str(c).lower() for c in raw.columns}).reset_index()
    datecol = "date" if "date" in part.columns else part.columns[0]
    part = part.rename(columns={datecol: "date"})
    frame = _as_weekly_frame(part)
    if not frame.empty and symbols:
        out[symbols[0]] = frame
    return out


def _weekly_chunk_size() -> int:
    try:
        n = int(os.environ.get("SS_WEEKLY_CHUNK", "60"))
    except (TypeError, ValueError):
        n = 60
    return max(5, min(n, 80))


def _weekly_pause_seconds(pause: float | None = None) -> float:
    if pause is not None:
        return max(0.0, float(pause))
    try:
        n = float(os.environ.get("SS_WEEKLY_SLEEP", "1.0"))
    except (TypeError, ValueError):
        n = 1.0
    return max(0.0, min(n, 10.0))


def _write_weekly_checkpoint(weekly: dict[str, pd.DataFrame]) -> None:
    if not weekly:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = WEEKLY_PKL.with_suffix(".pkl.tmp")
        pd.to_pickle(weekly, tmp)
        tmp.replace(WEEKLY_PKL)
    except Exception:
        logger.debug("weekly checkpoint write skipped", exc_info=True)


def _wait_yahoo_cooldown(max_wait: float = 1200.0) -> bool:
    """Sleep through an in-process Yahoo cooldown. Return True if traffic is allowed."""
    import bg_tasks

    waited = 0.0
    step = 15.0
    while bg_tasks.in_cooldown("yahoo") and waited < max_wait:
        time.sleep(step)
        waited += step
    return not bg_tasks.in_cooldown("yahoo")


def load_weekly_checkpoint(*, max_age: float | None = WEEKLY_RESUME_MAX_AGE_SEC) -> dict[str, pd.DataFrame]:
    if not WEEKLY_PKL.exists():
        return {}
    if max_age is not None:
        try:
            if time.time() - WEEKLY_PKL.stat().st_mtime > max_age:
                return {}
        except Exception:
            return {}
    try:
        obj = pd.read_pickle(WEEKLY_PKL)
        if isinstance(obj, dict):
            return obj
    except Exception:
        logger.debug("weekly checkpoint unreadable", exc_info=True)
    return {}


def should_resume_weekly(force: bool, resume: bool | None = None) -> bool:
    if resume is not None:
        return bool(resume)
    if not WEEKLY_PKL.exists():
        return False
    try:
        age = time.time() - WEEKLY_PKL.stat().st_mtime
    except Exception:
        return False
    if age <= WEEKLY_RESUME_MAX_AGE_SEC:
        return True
    return not force


def download_weekly_panel(
    symbols: list[str],
    *,
    period: str = "4y",
    existing: dict[str, pd.DataFrame] | None = None,
    on_progress=None,
    pause: float | None = None,
    retry_on_cooldown: bool | None = None,
    checkpoint: bool = True,
) -> dict[str, pd.DataFrame]:
    import net_limits
    import bg_tasks

    if retry_on_cooldown is None:
        retry_on_cooldown = os.environ.get("SS_WEEKLY_RETRY") == "1"
    out: dict[str, pd.DataFrame] = dict(existing or {})
    pending = [s for s in symbols if s not in out]
    if not pending:
        if on_progress:
            on_progress(phase="weekly", processed=len(symbols), total=len(symbols) or 1,
                        message="周线已从检查点恢复，无需再拉")
        return out
    if bg_tasks.in_cooldown("yahoo"):
        if not (retry_on_cooldown and _wait_yahoo_cooldown()):
            logger.warning("yahoo cooldown; skip weekly download")
            return out
    chunk = _weekly_chunk_size()
    sleep_s = _weekly_pause_seconds(pause)
    done_prior = len(symbols) - len(pending)
    for i in range(0, len(pending), chunk):
        if bg_tasks.in_cooldown("yahoo"):
            if not (retry_on_cooldown and _wait_yahoo_cooldown()):
                break
        batch = pending[i : i + chunk]
        try:
            raw = net_limits.yf_download(
                batch,
                period=period,
                interval="1wk",
                auto_adjust=True,
                group_by="ticker",
                threads=False,
                progress=False,
            )
            out.update(_extract_ohlcv(raw, batch))
        except Exception as exc:
            if bg_tasks.is_rate_limit_error(exc):
                bg_tasks.note_rate_limited("yahoo")
                if retry_on_cooldown and _wait_yahoo_cooldown():
                    try:
                        raw = net_limits.yf_download(
                            batch,
                            period=period,
                            interval="1wk",
                            auto_adjust=True,
                            group_by="ticker",
                            threads=False,
                            progress=False,
                        )
                        out.update(_extract_ohlcv(raw, batch))
                    except Exception as exc2:
                        if bg_tasks.is_rate_limit_error(exc2):
                            bg_tasks.note_rate_limited("yahoo")
                            break
                        logger.exception("weekly batch retry failed (%s…)", batch[0])
                else:
                    break
            else:
                logger.exception("weekly batch failed (%s…)", batch[0])
        processed = done_prior + min(i + len(batch), len(pending))
        if on_progress:
            on_progress(
                phase="weekly",
                processed=processed,
                total=len(symbols) or 1,
                message=f"周线 {processed}/{len(symbols)}",
            )
        if checkpoint:
            _write_weekly_checkpoint(out)
        if sleep_s:
            time.sleep(sleep_s)
    return out

def _yahoo_fundamentals(symbol: str) -> dict:
    """Best-effort Yahoo info / statements. Missing keys stay absent (无数据)."""
    import yfinance as yf
    import bg_tasks

    if bg_tasks.in_cooldown("yahoo"):
        return {}
    out: dict[str, Any] = {}
    try:
        t = yf.Ticker(symbol)
        info = {}
        try:
            info = t.info or {}
        except Exception as exc:
            if bg_tasks.is_rate_limit_error(exc):
                bg_tasks.note_rate_limited("yahoo")
                return {}
            info = {}

        def pick(*keys):
            for k in keys:
                v = _num(info.get(k))
                if v is not None:
                    return v
            return None

        out["name"] = info.get("shortName") or info.get("longName")
        out["exchange"] = info.get("exchange") or info.get("fullExchangeName")
        out["sector"] = info.get("sector")
        out["industry"] = info.get("industry")
        out["market_cap"] = pick("marketCap")
        out["float_shares"] = pick("floatShares")
        out["shares_outstanding"] = pick("sharesOutstanding")
        out["long_term_debt"] = pick("longTermDebt")
        if out["long_term_debt"] is None:
            td = pick("totalDebt")
            if td is not None:
                out["long_term_debt"] = td
                out["debt_is_total"] = True
        out["short_interest_pct"] = pick("shortPercentOfFloat")
        if out["short_interest_pct"] is not None and out["short_interest_pct"] > 1:
            out["short_interest_pct"] = out["short_interest_pct"] / 100.0
        out["analyst_count"] = pick("numberOfAnalystOpinions")
        out["insider_ownership_pct"] = pick("heldPercentInsiders")
        out["ttm_eps"] = pick("trailingEps")
        try:
            q = t.quarterly_income_stmt
        except Exception:
            q = None
        if q is not None and not getattr(q, "empty", True):
            def row(*names):
                for n in names:
                    if n in q.index:
                        return q.loc[n]
                lower = {str(i).lower(): i for i in q.index}
                for n in names:
                    if n.lower() in lower:
                        return q.loc[lower[n.lower()]]
                return None

            def series_chrono(s):
                vals = [_num(x) for x in list(s)]
                return list(reversed(vals))

            eps_row = row("Diluted EPS", "Basic EPS", "DilutedEPS")
            rev_row = row("Total Revenue", "Operating Revenue", "TotalRevenue")
            op_row = row("Operating Income", "OperatingIncome")
            if eps_row is not None:
                out["eps_quarters"] = series_chrono(eps_row)
                if out["eps_quarters"]:
                    out["quarterly_eps"] = out["eps_quarters"][-1]
            if rev_row is not None:
                out["revenue_quarters"] = series_chrono(rev_row)
            if op_row is not None and rev_row is not None:
                ops = series_chrono(op_row)
                revs = series_chrono(rev_row)
                margins = []
                for o, rv in zip(ops, revs):
                    if o is not None and rv and rv > 0:
                        margins.append(o / rv)
                    else:
                        margins.append(None)
                out["margin_quarters"] = margins
        dates: list[str] = []
        try:
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and not getattr(ed, "empty", True):
                for x in list(ed.index)[:8]:
                    try:
                        dates.append(pd.Timestamp(x).date().isoformat())
                    except Exception:
                        continue
        except Exception:
            pass
        if dates:
            out["earnings_dates"] = dates
        try:
            acts = t.splits
            if acts is not None and not getattr(acts, "empty", True):
                recents = []
                for dt, val in list(acts.items())[-4:]:
                    recents.append({"date": pd.Timestamp(dt).date().isoformat(), "ratio": _num(val)})
                if recents:
                    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=550)).date().isoformat()
                    out["splits"] = [x for x in recents if x["date"] >= cutoff]
        except Exception:
            pass
    except Exception as exc:
        if bg_tasks.is_rate_limit_error(exc):
            bg_tasks.note_rate_limited("yahoo")
        logger.debug("yahoo fundamentals failed for %s", symbol, exc_info=True)
        return {}
    return {k: v for k, v in out.items() if v is not None}


def _sec_cik_map() -> dict[str, str]:
    path = CACHE_DIR / "sec_tickers.json"
    if path.exists() and time.time() - path.stat().st_mtime < UNI_TTL:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        raw = _http_get(SEC_TICKERS, timeout=25, user_agent=SEC_UA)
        data = json.loads(raw.decode("utf-8"))
        mapping = {}
        rows = data.values() if isinstance(data, dict) else data
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            sym = str(rec.get("ticker") or "").upper().replace(".", "-")
            cik = str(rec.get("cik_str") or rec.get("cik") or "").zfill(10)
            if sym and cik.isdigit():
                mapping[sym] = cik
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping), encoding="utf-8")
        return mapping
    except Exception:
        logger.warning("SEC ticker map failed", exc_info=True)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}


def fetch_form4_transactions(symbol: str, *, cik: str | None = None, limit: int = 8) -> tuple[str, list[dict]]:
    """Official EDGAR Form 4. Returns (status, transactions). status is ok or 无数据."""
    cache_path = CACHE_DIR / f"form4_{symbol.upper()}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < INSIDER_TTL:
        try:
            rec = json.loads(cache_path.read_text(encoding="utf-8"))
            return rec.get("status") or "无数据", rec.get("transactions") or []
        except Exception:
            pass
    mapping = _sec_cik_map()
    cik = cik or mapping.get(symbol.upper()) or mapping.get(symbol.upper().replace("-", "."))
    if not cik:
        return "无数据", []
    cik10 = str(cik).zfill(10)
    try:
        raw = _http_get(
            f"https://data.sec.gov/submissions/CIK{cik10}.json",
            timeout=25,
            user_agent=SEC_UA,
        )
        time.sleep(0.2)
        sub = json.loads(raw.decode("utf-8"))
    except Exception:
        logger.debug("SEC submissions failed for %s", symbol, exc_info=True)
        return "无数据", []
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    picked: list[tuple[str, str]] = []
    for form, acc, doc in zip(forms, accs, docs):
        if str(form).strip() not in {"4", "4/A"}:
            continue
        picked.append((str(acc).replace("-", ""), str(doc)))
        if len(picked) >= limit:
            break
    if not picked:
        status, txs = "无数据", []
    else:
        txs = []
        cik_nolead = str(int(cik10))
        for acc, doc in picked:
            candidates = [doc]
            if doc.lower().endswith((".htm", ".html")):
                stem = re.sub(r"\.(htm|html)$", "", doc, flags=re.I)
                candidates.append(stem + ".xml")
                candidates.append("primary_doc.xml")
                candidates.append("form4.xml")
            xml_text = None
            for name in candidates:
                url = f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{acc}/{name}"
                try:
                    body = _http_get(url, timeout=20, user_agent=SEC_UA)
                    time.sleep(0.15)
                    text = body.decode("utf-8", errors="replace")
                    if "<ownershipDocument" in text or "nonDerivativeTransaction" in text:
                        xml_text = text
                        break
                except Exception:
                    continue
            if xml_text:
                txs.extend(parse_form4_xml(xml_text, symbol=symbol))
        status = "ok" if txs else "无数据"
    rec = {"status": status, "transactions": txs, "fetched_at": _iso_shanghai()}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        logger.debug("form4 cache write skipped", exc_info=True)
    return status, txs

def build_snapshot(
    *,
    symbols: list[str] | None = None,
    weekly_map: dict[str, pd.DataFrame] | None = None,
    fund_map: dict[str, dict] | None = None,
    insider_map: dict[str, tuple[str, list[dict]]] | None = None,
    universe: list[dict] | None = None,
    fetch_live: bool = False,
    max_live_fund: int = 40,
    asof_session: str | None = None,
    on_progress=None,
) -> dict:
    """Screen a universe. Live fetches are opt-in and bounded."""
    uni = universe if universe is not None else (load_universe() if fetch_live else [])
    meta = {r["symbol"]: r for r in uni} if uni else {}
    if symbols is None:
        symbols = list(meta) if meta else []
    weekly_map = dict(weekly_map or {})
    if fetch_live and not weekly_map and symbols:
        weekly_map.update(download_weekly_panel(symbols))
    fund_map = dict(fund_map or {})
    insider_map = dict(insider_map or {})

    pre: list[dict] = []
    asof = asof_session
    for sym in symbols:
        df = weekly_map.get(sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            continue
        info = meta.get(sym) or {"symbol": sym, "name": sym, "exchange": None}
        st, txs = insider_map.get(sym, ("无数据", []))
        row = evaluate_ticker(
            df,
            symbol=sym,
            name=info.get("name"),
            exchange=info.get("exchange"),
            fundamentals=fund_map.get(sym),
            insider_status=st if sym in insider_map else "无数据",
            insider_transactions=txs if sym in insider_map else [],
        )
        if asof is None and row.get("weekly_bars"):
            asof = row["weekly_bars"][-1]["date"]
        if row["hard_pass"] or row["near"] or row.get("prior_runner"):
            pre.append(row)

    enrich = [r for r in pre if r["hard_pass"] or r["near"]][: max(0, int(max_live_fund))]
    if fetch_live:
        need_fund = [r for r in enrich if r["symbol"] not in fund_map]
        need_ins = [r for r in enrich if r["symbol"] not in insider_map]
        for i, r in enumerate(need_fund, 1):
            if on_progress:
                on_progress(
                    phase="fundamentals",
                    processed=i - 1,
                    total=len(need_fund) or 1,
                    message=f"基本面 {i}/{len(need_fund)} {r['symbol']}",
                )
            fund_map[r["symbol"]] = _yahoo_fundamentals(r["symbol"])
            time.sleep(0.4)
        if need_fund and on_progress:
            on_progress(phase="fundamentals", processed=len(need_fund), total=len(need_fund) or 1, message="基本面完成")
        for i, r in enumerate(need_ins, 1):
            if on_progress:
                on_progress(
                    phase="insiders",
                    processed=i - 1,
                    total=len(need_ins) or 1,
                    message=f"内部人 {i}/{len(need_ins)} {r['symbol']}",
                )
            insider_map[r["symbol"]] = fetch_form4_transactions(r["symbol"])
        if need_ins and on_progress:
            on_progress(phase="insiders", processed=len(need_ins), total=len(need_ins) or 1, message="内部人申报完成")
        for r in enrich:
            sym = r["symbol"]
            info = meta.get(sym) or {"symbol": sym, "name": r.get("name"), "exchange": r.get("exchange")}
            df = weekly_map.get(sym)
            st, txs = insider_map.get(sym, ("无数据", []))
            rebuilt = evaluate_ticker(
                df,
                symbol=sym,
                name=info.get("name"),
                exchange=info.get("exchange") or (fund_map.get(sym) or {}).get("exchange"),
                fundamentals=fund_map.get(sym),
                insider_transactions=txs,
                insider_status=st,
            )
            r.clear()
            r.update(rebuilt)
    if on_progress:
        on_progress(phase="scoring", processed=0, total=1, message="正在汇总名单…")

    candidates = [r for r in pre if r.get("hard_pass")]
    near = [r for r in pre if r.get("near") and not r.get("hard_pass")]
    prior = [r for r in pre if r.get("prior_runner") and not r.get("hard_pass")]
    candidates.sort(key=lambda x: (-(x.get("score") or 0), x.get("symbol") or ""))
    near.sort(key=lambda x: (-(x.get("score") or 0), x.get("symbol") or ""))
    payload = empty_seed_payload(note=None, scanned=len(weekly_map), universe=len(symbols) or len(uni))
    payload["source"] = "live" if fetch_live else "computed"
    payload["updated_at"] = _iso_shanghai()
    payload["asof_session"] = asof
    payload["candidates"] = candidates
    payload["near"] = near + prior
    payload["counts"] = {
        "universe": len(symbols) or len(uni),
        "scanned": len(weekly_map),
        "candidates": len(candidates),
        "near": len(near) + len(prior),
    }
    if not candidates and not near and not prior:
        payload["note"] = (
            f"已扫描 {payload['counts']['scanned']} 只（宇宙 {payload['counts']['universe']}），"
            "当前没有严格通过或接近的名字。名单为空，不是编造的候选。"
        )
    else:
        payload["note"] = (
            f"严格候选 {len(candidates)} 只，接近/此前突破观察 {len(near)+len(prior)} 只。"
            "研究名单，不能下单。"
        )
    return snapshot_view(payload)


def _progress_cb(**kwargs) -> dict:
    kwargs.setdefault("state", "running")
    return set_progress(**kwargs)


def refresh(force: bool = True, *, resume: bool | None = None) -> dict:
    """Background rebuild. Respects Yahoo cooldowns; writes the seed snapshot."""
    import bg_tasks

    if not _refresh_gate.acquire(blocking=False):
        return snapshot()
    try:
        if not _job_running:
            begin_job()
        set_progress(phase="universe", processed=0, total=1, message="正在加载 NYSE/Nasdaq 宇宙…", state="running")
        uni = load_universe(force=False)
        symbols = [r["symbol"] for r in uni]
        set_progress(
            phase="universe",
            processed=1,
            total=1,
            message=f"宇宙 {len(symbols)} 只",
            state="running",
        )

        weekly: dict[str, pd.DataFrame] = {}
        if should_resume_weekly(force, resume):
            weekly = load_weekly_checkpoint(max_age=None if resume else WEEKLY_RESUME_MAX_AGE_SEC)
            if weekly:
                set_progress(
                    phase="weekly",
                    processed=len(weekly),
                    total=len(symbols) or 1,
                    message=f"已从检查点恢复 {len(weekly)} 只周线",
                    state="running",
                )
        missing = [s for s in symbols if s not in weekly]
        retry = os.environ.get("SS_WEEKLY_RETRY") == "1"
        if missing and (not bg_tasks.in_cooldown("yahoo") or retry):
            weekly.update(
                download_weekly_panel(
                    symbols,
                    existing=weekly,
                    on_progress=_progress_cb,
                    retry_on_cooldown=retry,
                )
            )
            _write_weekly_checkpoint(weekly)
        elif not missing:
            set_progress(
                phase="weekly",
                processed=len(symbols),
                total=len(symbols) or 1,
                message="周线已齐全，进入价格过滤",
                state="running",
            )

        set_progress(
            phase="weekly",
            processed=len(symbols) or len(weekly),
            total=len(symbols) or 1,
            message="按价格过滤 $2–$40…",
            state="running",
        )
        priced = {}
        for s, df in weekly.items():
            frame = _as_weekly_frame(df)
            if frame.empty:
                continue
            px = _num(frame["close"].iloc[-1])
            if px is None:
                continue
            if 2.0 <= px <= 40.0:
                priced[s] = frame
        payload = build_snapshot(
            symbols=list(priced),
            weekly_map=priced,
            universe=uni,
            fetch_live=True,
            max_live_fund=40,
            on_progress=_progress_cb,
        )
        payload["source"] = "live"
        write_seed(payload, SEED_PATH)
        try:
            write_seed(payload, DOCS_SEED_PATH)
        except Exception:
            logger.debug("docs seed write skipped", exc_info=True)
        view = _store_cache(payload)
        finish_job(ok=True, message=payload.get("note") or "扫描完成")
        return _attach_meta(view)
    except Exception as exc:  # noqa: BLE001
        finish_job(
            ok=False,
            error=str(exc),
            message=f"扫描失败：{exc}。未编造候选名单。",
        )
        raise
    finally:
        _refresh_gate.release()


def main(argv: list[str] | None = None) -> int:
    """CLI for the weekly GitHub Actions scan. Does not start a scan on import."""
    import argparse

    parser = argparse.ArgumentParser(description="US Superstocks research scan")
    parser.add_argument("--weekly-scan", action="store_true", help="Full weekly universe scan; write seed JSON")
    parser.add_argument("--resume", action="store_true", help="Resume from weekly.pkl if present")
    args = parser.parse_args(argv)
    if not args.weekly_scan:
        parser.print_help()
        return 2
    os.environ.setdefault("SS_WEEKLY_CHUNK", "40")
    os.environ.setdefault("SS_WEEKLY_SLEEP", "2")
    os.environ.setdefault("SS_WEEKLY_RETRY", "1")
    payload = refresh(force=True, resume=True)
    print(
        json.dumps(
            {
                "ok": True,
                "counts": payload.get("counts"),
                "updated_at": payload.get("updated_at"),
                "note": payload.get("note"),
                "candidates": len(payload.get("candidates") or []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
