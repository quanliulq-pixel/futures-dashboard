# -*- coding: utf-8 -*-
"""美股风险页（/api/us/risk）。研究参考，不能下单，不构成投资建议。

架构与美股ETF相同：请求只读内存/本地快照（秒回、无网络），过期时后台单飞刷新；
刷新失败则回退上次快照并标注「上次快照（日期）」，取不到显示「无数据」。绝不编造数字，
不使用东方财富实时推送接口。

口径
----
总市值：Yahoo Finance 威尔希尔 5000 全市场价格指数（^W5000，Wilshire 5000 Full Cap Price Index）。
该指数传统口径为 1 点约合 10 亿美元美股全市场市值（总市值 ≈ 指数点位 × 10 亿美元）。
FRED 的 WILL5000PRFC 已于 2024-06-03 停更，故不用 FRED 威尔希尔序列。

巴菲特指标：上述总市值 / 美国名义 GDP（季节性调整年率，SAAR）。
GDP 取美国经济分析局（BEA）NIPA Table 1.1.5「Gross Domestic Product」第 1 行（序列 A191RC，
百万美元；与 FRED GDP 为同一序列，本模块直接读 BEA 官方 xlsx，因部分环境访问 FRED 不稳定）。
某一交易日使用「该日及之前已结束季度」的 GDP（不含未来季，不做向前插值）。
历史分位为有重叠样本的交易日比率中，不高于当日比率的占比（含当日）。

指数：标普500（^GSPC）、纳指100（^NDX）、罗素2000（^RUT）、道指（^DJI）Yahoo 最新收盘与前一交易日涨跌幅。

回撤与高点：各指数历史最高收盘及日期、当前距高点回撤、今年以来最高点与回撤；
历年回撤表为每个日历年的年内最大回撤（高点日期→低点日期）与年度涨跌（标普500/道指自 1990 年，纳指100/罗素2000 从有数据起）；
重大回撤轮次给出峰值、谷底、跌幅、持续日历日、恢复到前高用时，以及该轮峰值日至谷底日的 Cboe VIX 收盘峰值。
当前这轮回撤与历年最大回撤、历史≥10%完成回撤的分布对比。

杠杆率：FINRA 官方 Customer Margin Balances（Debit Balances in Customers' Securities Margin Accounts，
单位百万美元）。文件 https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx
（页面会更新同一路径），官方页 https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics 。
环比/同比为融资余额本身；占总市值比例用对应月末（该月最后一个可用交易日）威尔希尔总市值。
2000/2007/2021 高点取官方序列在该日历年内的融资余额最高月份。

恐慌指数：Cboe 官方日频 CSV（VIX / VIX9D / VIX3M / VVIX）。
期限结构：同一日 VIX9D、VIX、VIX3M，若前短后长（VIX9D < VIX < VIX3M）为升水，
若前长后短（VIX9D > VIX > VIX3M，或至少 VIX > VIX3M）为倒挂。
VIX 1年分位：近 252 个交易日收盘中不高于当日的占比（含当日）。

综合判断（有分位才给，否则「无数据」）：
- 估值：巴菲特指标历史分位 ≥80% 偏热，≤20% 偏冷，否则中性。
- 杠杆：融资余额/月末总市值 的历史分位，同样 ≥80% 偏热，≤20% 偏冷，否则中性。
- 情绪：VIX 1年分位 ≥80%，或期限倒挂且 VIX≥25，为偏冷（恐慌）；
  分位 ≤20% 且升水为偏热（ complacency / 情绪宽松）；否则中性。
"""
from __future__ import annotations

import copy
import io
import json
import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
ET = ZoneInfo("America/New_York")
SEED_PATHS = (ROOT / "data" / "us_risk.json", ROOT / "docs" / "api" / "us_risk.json")
RUNTIME_PATH = ROOT / "data" / "us_risk_latest.json"
REMOTE_SEED_URL = "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/us_risk.json"
DISCLAIMER = "研究参考，不能下单，不构成投资建议"
REFRESH_HOURS = 2.0
MAX_SNAPSHOT_DAYS = 10
W5000_USD_PER_POINT = 1_000_000_000  # 威尔希尔全市场价格指数传统口径：1 点 ≈ 10 亿美元
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CBOE_CSV = {
    "vix": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "vix9d": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    "vix3m": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
    "vvix": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv",
}
BEA_SECTION1_XLSX = "https://apps.bea.gov/national/Release/XLS/Survey/Section1All_xls.xlsx"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
FINRA_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_WAYBACK_XLSX = (
    "https://web.archive.org/web/20261122060304id_/"
    "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
)
FINRA_WAYBACK_PAGE = (
    "https://web.archive.org/web/20261122060304/"
    "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
)
INDEX_META = (
    ("sp500", "^GSPC", "标普500"),
    ("ndx", "^NDX", "纳指100"),
    ("rut", "^RUT", "罗素2000"),
    ("dji", "^DJI", "道指"),
)
PEAK_YEARS = (2000, 2007, 2021)
# 历年回撤表起点：标普/道指从 1990 年；纳指100、罗素2000 从有数据起。
ANNUAL_START_YEAR = {"sp500": 1990, "dji": 1990, "ndx": None, "rut": None}
# 重大回撤标签（按峰值—谷底与窗口重叠匹配，取窗口内最深的一轮）
NAMED_DRAWDOWNS = (
    ("2000-02 互联网泡沫", "2000-01-01", "2003-03-31"),
    ("2007-09 金融危机", "2007-07-01", "2009-06-30"),
    ("2018Q4", "2018-09-01", "2018-12-31"),
    ("2020-03 新冠", "2020-01-01", "2020-06-30"),
    ("2022 熊市", "2021-11-01", "2023-12-31"),
    ("2025", "2025-01-01", "2025-06-30"),
)
# 列入「重大回撤」表的完成回撤深度阈值；当前这轮不论深度都列出。
MAJOR_DD_THRESHOLD = -0.10

_lock = threading.Lock()
_cache: dict | None = None


def _now() -> datetime:
    return datetime.now(CST)


def _r(x: Any, n: int = 4) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def _to_date(x: Any) -> date | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, pd.Timestamp):
        if pd.isna(x):
            return None
        return x.date()
    try:
        ts = pd.to_datetime(x, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def percentile_inclusive(series: pd.Series, value: float | None = None) -> float | None:
    """近样本中不高于 value 的占比（含当日）。返回 0–100。样本不足 20 则无数据。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 20:
        return None
    if value is None:
        value = float(s.iloc[-1])
    return _r(float((s <= value).mean()) * 100.0, 1)


def band_from_percentile(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct >= 80:
        return "偏热"
    if pct <= 20:
        return "偏冷"
    return "中性"


def quarter_end(period: str) -> date | None:
    """'2026Q3' 或 '2026-Q3' → 该季最后一天。"""
    text = str(period).strip().upper().replace("-", "")
    if len(text) < 6 or "Q" not in text:
        return None
    try:
        year = int(text.split("Q")[0])
        q = int(text.split("Q")[1])
        month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        return date(year, month, day)
    except (KeyError, TypeError, ValueError):
        return None


def gdp_for_date(gdp: pd.Series, asof: date) -> tuple[float, str] | None:
    """无前瞻：只用季末日 ≤ asof 的最近一季 GDP（百万美元）。"""
    if gdp is None or gdp.empty:
        return None
    s = pd.to_numeric(gdp, errors="coerce").dropna()
    eligible = []
    for period, value in s.items():
        end = period if isinstance(period, date) else quarter_end(str(period))
        if end is not None and end <= asof:
            eligible.append((end, str(period), float(value)))
    if not eligible:
        return None
    end, period, value = max(eligible, key=lambda x: x[0])
    return value, period


def market_cap_usd(w5000_close: float | None) -> float | None:
    if w5000_close is None or not math.isfinite(w5000_close) or w5000_close <= 0:
        return None
    return float(w5000_close) * W5000_USD_PER_POINT


def month_end_close(close: pd.Series, year_month: str) -> tuple[date, float] | None:
    """year_month='2026-10' → 该月最后一个可用收盘。"""
    c = pd.to_numeric(close, errors="coerce").dropna()
    if c.empty:
        return None
    try:
        start = pd.Timestamp(f"{year_month}-01")
    except ValueError:
        return None
    end = start + pd.offsets.MonthEnd(0)
    sl = c.loc[(c.index >= start) & (c.index <= end)]
    if sl.empty:
        return None
    d = sl.index[-1]
    return (_to_date(d), float(sl.iloc[-1]))


def term_structure(vix: float | None, vix9d: float | None, vix3m: float | None) -> str | None:
    if vix is None or vix3m is None:
        return None
    if vix9d is not None and vix9d < vix < vix3m:
        return "升水"
    if vix9d is not None and vix9d > vix > vix3m:
        return "倒挂"
    if vix > vix3m:
        return "倒挂"
    if vix < vix3m:
        return "升水"
    return "平坦"


def judge_sentiment(vix: float | None, pct_1y: float | None, term: str | None) -> str | None:
    if pct_1y is None and (vix is None or term is None):
        return None
    if (pct_1y is not None and pct_1y >= 80) or (term == "倒挂" and vix is not None and vix >= 25):
        return "偏冷"
    if pct_1y is not None and pct_1y <= 20 and term == "升水":
        return "偏热"
    if pct_1y is None:
        return None
    return "中性"


def index_quote(close: pd.Series, name: str, symbol: str) -> dict:
    c = pd.to_numeric(close, errors="coerce").dropna()
    if len(c) < 1:
        return {"status": "missing", "name": name, "symbol": symbol, "reason": "无数据"}
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2]) if len(c) >= 2 else None
    chg = (last / prev - 1.0) if prev and prev != 0 else None
    return {
        "status": "ok",
        "name": name,
        "symbol": symbol,
        "close": _r(last, 2),
        "prev_close": _r(prev, 2) if prev is not None else None,
        "chg_pct": _r(chg, 4) if chg is not None else None,
        "asof": str(_to_date(c.index[-1])),
    }


# ---------------------------------------------------------------- drawdowns (pure)

def _series_close(close: pd.Series) -> pd.Series:
    c = pd.to_numeric(close, errors="coerce").dropna().sort_index()
    if c.empty:
        return c
    c.index = pd.DatetimeIndex(c.index).tz_localize(None)
    return c


def vix_peak_in_range(vix: pd.Series | None, start: date | None, end: date | None) -> dict:
    """区间内（含端点）VIX 收盘最高值；缺序列或区间无点则为无数据。"""
    if vix is None or start is None or end is None:
        return {"status": "missing", "reason": "无数据"}
    s = _series_close(vix)
    if s.empty:
        return {"status": "missing", "reason": "无数据"}
    sl = s.loc[(s.index.date >= start) & (s.index.date <= end)]
    if sl.empty:
        return {"status": "missing", "reason": "该区间无 VIX"}
    px = float(sl.max())
    d = _to_date(sl.idxmax())
    return {"status": "ok", "close": _r(px, 2), "asof": str(d) if d else None}


def named_event_drawdown(close: pd.Series, start: date, end: date, lookback_days: int = 180) -> dict | None:
    """命名事件窗口内的峰值—谷底：谷底取窗口内收盘最低，峰值取窗口开始前 lookback 至谷底日的收盘最高。

    用于 2018Q4 / 2020-03 等可能被更长未收复前高轮次吞并的事件；不编造，窗口内无收盘则返回 None。
    """
    c = _series_close(close)
    if c.empty:
        return None
    win = c[(c.index.date >= start) & (c.index.date <= end)]
    if win.empty:
        return None
    trough = float(win.min())
    trough_d = _to_date(win.idxmin())
    peak_from = start - timedelta(days=lookback_days)
    pre = c[(c.index.date >= peak_from) & (c.index.date <= trough_d)]
    if pre.empty:
        return None
    peak = float(pre.max())
    peak_d = _to_date(pre.idxmax())
    if not peak or trough_d is None or peak_d is None:
        return None
    dd = trough / peak - 1.0
    rec_d = rec_days = None
    after = c[c.index.date > trough_d]
    hit = after[after >= peak]
    if len(hit):
        rec_d = _to_date(hit.index[0])
        rec_days = (rec_d - trough_d).days if rec_d else None
    return {
        "peak": _r(peak, 2),
        "peak_date": str(peak_d),
        "trough": _r(trough, 2),
        "trough_date": str(trough_d),
        "dd": _r(dd, 4),
        "duration_days": (trough_d - peak_d).days,
        "recovery_date": str(rec_d) if rec_d else None,
        "recovery_days": rec_days,
        "ongoing": rec_d is None,
        "named_window": True,
    }


def highs_and_current_drawdown(close: pd.Series) -> dict | None:

    """历史最高收盘、当前距高点回撤、今年以来最高与回撤。"""
    c = _series_close(close)
    if c.empty:
        return None
    last = float(c.iloc[-1])
    last_d = _to_date(c.index[-1])
    ath = float(c.max())
    ath_d = _to_date(c.idxmax())
    dd_ath = last / ath - 1.0 if ath else None
    ytd = c[c.index.year == last_d.year] if last_d else c.iloc[0:0]
    if ytd.empty:
        ytd_high = ytd_d = dd_ytd = None
    else:
        ytd_high = float(ytd.max())
        ytd_d = _to_date(ytd.idxmax())
        dd_ytd = last / ytd_high - 1.0 if ytd_high else None
    return {
        "status": "ok",
        "asof": str(last_d),
        "close": _r(last, 2),
        "ath": _r(ath, 2),
        "ath_date": str(ath_d) if ath_d else None,
        "dd_ath": _r(dd_ath, 4) if dd_ath is not None else None,
        "ytd_high": _r(ytd_high, 2) if ytd_high is not None else None,
        "ytd_high_date": str(ytd_d) if ytd_d else None,
        "dd_ytd": _r(dd_ytd, 4) if dd_ytd is not None else None,
        "history_start": str(_to_date(c.index[0])),
    }


def annual_drawdowns(close: pd.Series, start_year: int | None = None) -> list[dict]:
    """各日历年年内最大回撤（当年已出现收盘高点→后续低点，不把上年高点跨年带入）与年度涨跌。

    年度涨跌 = 该年最后收盘 / 上一年最后收盘 − 1；当前未完成年份为截至 asof 的年内涨跌。
    样本首年若没有上年收盘，年度涨跌为无数据。
    """
    c = _series_close(close)
    if c.empty:
        return []
    years = sorted({int(y) for y in c.index.year})
    if start_year is not None:
        years = [y for y in years if y >= start_year]
    out: list[dict] = []
    last_d = _to_date(c.index[-1])
    for year in years:
        sl = c[c.index.year == year]
        if sl.empty:
            continue
        run_peak = float(sl.iloc[0])
        run_peak_d = _to_date(sl.index[0])
        max_dd = 0.0
        dd_peak = run_peak
        dd_peak_d = run_peak_d
        dd_trough = run_peak
        dd_trough_d = run_peak_d
        for ts, px in sl.items():
            px = float(px)
            d = _to_date(ts)
            if px >= run_peak:
                run_peak = px
                run_peak_d = d
            dd = px / run_peak - 1.0 if run_peak else 0.0
            if dd < max_dd:
                max_dd = dd
                dd_peak = run_peak
                dd_peak_d = run_peak_d
                dd_trough = px
                dd_trough_d = d
        prior = c[c.index.year < year]
        year_ret = None
        if len(prior):
            base = float(prior.iloc[-1])
            year_ret = float(sl.iloc[-1]) / base - 1.0 if base else None
        incomplete = bool(last_d and year == last_d.year)
        out.append({
            "year": year,
            "max_dd": _r(max_dd, 4),
            "peak": _r(dd_peak, 2),
            "peak_date": str(dd_peak_d) if dd_peak_d else None,
            "trough": _r(dd_trough, 2),
            "trough_date": str(dd_trough_d) if dd_trough_d else None,
            "year_return": _r(year_ret, 4) if year_ret is not None else None,
            "incomplete": incomplete,
            "asof": str(_to_date(sl.index[-1])),
        })
    return out


def drawdown_episodes(close: pd.Series, min_dd: float = MAJOR_DD_THRESHOLD) -> list[dict]:
    """从官方收盘识别峰值—谷底—收复前高的回撤轮次。

    新高或收复前高时结束上一轮；深度浅于 min_dd 的完成轮次不保留。
    当前仍未收复前高的一轮无论深度都保留（ongoing=True，恢复用时无数据）。
    持续天数、恢复用时均为日历日（峰值→谷底，谷底→收复日）。
    """
    c = _series_close(close)
    if len(c) < 5:
        return []
    peak = float(c.iloc[0])
    peak_d = _to_date(c.index[0])
    trough = peak
    trough_d = peak_d
    in_dd = False
    episodes: list[dict] = []

    def _pack(*, recovered_on: date | None, ongoing: bool) -> dict:
        dd = trough / peak - 1.0 if peak else None
        rec_days = None
        if recovered_on is not None and trough_d is not None:
            rec_days = (recovered_on - trough_d).days
        dur = (trough_d - peak_d).days if trough_d and peak_d else None
        return {
            "peak": _r(peak, 2),
            "peak_date": str(peak_d) if peak_d else None,
            "trough": _r(trough, 2),
            "trough_date": str(trough_d) if trough_d else None,
            "dd": _r(dd, 4) if dd is not None else None,
            "duration_days": dur,
            "recovery_date": str(recovered_on) if recovered_on else None,
            "recovery_days": rec_days,
            "ongoing": ongoing,
        }

    for ts, px in c.items():
        px = float(px)
        d = _to_date(ts)
        if px >= peak:
            if in_dd:
                ep = _pack(recovered_on=d, ongoing=False)
                if ep["dd"] is not None and ep["dd"] <= min_dd:
                    episodes.append(ep)
            peak = px
            peak_d = d
            trough = px
            trough_d = d
            in_dd = False
        else:
            in_dd = True
            if px < trough:
                trough = px
                trough_d = d
    if in_dd:
        # 当前这轮：无论是否达到阈值都留下
        episodes.append(_pack(recovered_on=None, ongoing=True))
    return episodes


def label_episodes(episodes: list[dict], asof: date | None = None) -> list[dict]:
    """为重大窗口打标签；同一窗口多轮时保留最深的一轮。未匹配的深度回撤保持无标签（仍列出）。"""
    windows = []
    for name, a, b in NAMED_DRAWDOWNS:
        windows.append((name, date.fromisoformat(a), date.fromisoformat(b)))
    used: set[int] = set()
    labeled = [dict(ep) for ep in episodes]
    for name, a, b in windows:
        best_i = None
        best_dd = 1.0
        for i, ep in enumerate(labeled):
            if i in used:
                continue
            pd0 = date.fromisoformat(ep["peak_date"]) if ep.get("peak_date") else None
            td0 = date.fromisoformat(ep["trough_date"]) if ep.get("trough_date") else None
            if pd0 is None or td0 is None:
                continue
            # overlap of [peak, trough] with the named window
            if pd0 <= b and td0 >= a:
                dd = ep.get("dd")
                if dd is not None and dd < best_dd:
                    best_dd = dd
                    best_i = i
        if best_i is not None:
            labeled[best_i]["label"] = name
            used.add(best_i)
    for ep in labeled:
        if ep.get("ongoing") and not ep.get("label"):
            ep["label"] = "当前回撤"
        ep.setdefault("label", None)
    return labeled


def select_major_episodes(episodes: list[dict], threshold: float = MAJOR_DD_THRESHOLD) -> list[dict]:
    """重大表：已命名事件 + 深度达到阈值的轮次 + 当前这轮。按峰值日期排序。"""
    out = []
    for ep in episodes:
        if ep.get("label") or ep.get("ongoing") or (ep.get("dd") is not None and ep["dd"] <= threshold):
            out.append(ep)
    out.sort(key=lambda x: str(x.get("peak_date") or ""))
    return out


def merge_major_rows(named_rows: list[dict], episodes: list[dict], threshold: float = MAJOR_DD_THRESHOLD) -> list[dict]:
    """命名事件（按窗口独立计算）优先；再补深度达到阈值的完成轮次与当前这轮，按峰值日期去重。"""
    out: list[dict] = []
    seen: set[tuple] = set()

    def _key(ep: dict) -> tuple:
        return (str(ep.get("peak_date") or ""), str(ep.get("trough_date") or ""))

    def _add(ep: dict) -> None:
        k = _key(ep)
        if k != ("", "") and k in seen:
            return
        if k != ("", ""):
            seen.add(k)
        out.append(ep)

    named_labels = {ep.get("label") for ep in named_rows if ep.get("label")}
    for ep in named_rows:
        _add(ep)
    for ep in select_major_episodes(episodes, threshold=threshold):
        # 已有同名窗口行时不再重复列出被吞并的完整轮次
        if ep.get("label") in named_labels:
            continue
        _add(ep)
    out.sort(key=lambda x: (str(x.get("peak_date") or "9999"), str(x.get("label") or "")))
    return out


def attach_vix_peaks(episodes: list[dict], vix: pd.Series | None) -> list[dict]:
    out = []
    for ep in episodes:
        item = dict(ep)
        start = date.fromisoformat(ep["peak_date"]) if ep.get("peak_date") else None
        end = date.fromisoformat(ep["trough_date"]) if ep.get("trough_date") else None
        vp = vix_peak_in_range(vix, start, end)
        if vp.get("status") == "ok":
            item["vix_peak"] = vp.get("close")
            item["vix_peak_date"] = vp.get("asof")
        else:
            item["vix_peak"] = None
            item["vix_peak_date"] = None
        out.append(item)
    return out


def compare_to_history(current_dd: float | None, annual: list[dict], episodes: list[dict]) -> dict:
    """当前距高点回撤在历年最大回撤、以及历史≥10%完成回撤中的位置。分位 100%＝浅于或等于全部样本。"""
    if current_dd is None:
        return {"status": "missing", "reason": "无数据", "line": None}
    annual_dds = [r["max_dd"] for r in annual if r.get("max_dd") is not None]
    # 完成且已收复的深度回撤（不含当前 ongoing）
    hist_eps = [
        e["dd"] for e in episodes
        if e.get("dd") is not None and not e.get("ongoing") and e["dd"] <= MAJOR_DD_THRESHOLD
    ]
    def _pct_shallow(sample: list[float], value: float) -> float | None:
        if len(sample) < 5:
            return None
        return _r(float(sum(1 for x in sample if x >= value) / len(sample)) * 100.0, 1)

    n_ann = len(annual_dds)
    deeper_years = sum(1 for x in annual_dds if x < current_dd) if annual_dds else None
    p_ann = _pct_shallow(annual_dds, current_dd)
    p_ep = _pct_shallow(hist_eps, current_dd)
    deeper_eps = sum(1 for x in hist_eps if x < current_dd) if hist_eps else None
    parts = [f"当前距历史最高收盘回撤 {current_dd * 100:+.1f}%"]
    if n_ann and deeper_years is not None:
        parts.append(f"在 {n_ann} 个日历年的年内最大回撤中，有 {deeper_years} 年更深")
        if p_ann is not None:
            parts.append(f"分位 {p_ann:.0f}%（100%=最浅）")
    if hist_eps and deeper_eps is not None:
        parts.append(f"在 {len(hist_eps)} 次历史≥10%完成回撤中，有 {deeper_eps} 次更深")
        if p_ep is not None:
            parts.append(f"分位 {p_ep:.0f}%")
    if current_dd > MAJOR_DD_THRESHOLD:
        parts.append("尚未达到 10% 深度回撤阈值")
    return {
        "status": "ok",
        "current_dd": _r(current_dd, 4),
        "annual_sample": n_ann or None,
        "annual_deeper": deeper_years,
        "annual_percentile": p_ann,
        "episode_sample": len(hist_eps) or None,
        "episode_deeper": deeper_eps,
        "episode_percentile": p_ep,
        "line": "；".join(parts) + "。",
    }


def build_index_drawdown(close: pd.Series, *, key: str, name: str, symbol: str,
                         vix: pd.Series | None, start_year: int | None) -> dict:
    highs = highs_and_current_drawdown(close)
    if not highs:
        return {"status": "missing", "key": key, "name": name, "symbol": symbol, "reason": "无数据"}
    annual = annual_drawdowns(close, start_year=start_year)
    episodes = attach_vix_peaks(label_episodes(drawdown_episodes(close)), vix)
    named_rows = []
    for ev_name, a, b in NAMED_DRAWDOWNS:
        ev = named_event_drawdown(close, date.fromisoformat(a), date.fromisoformat(b))
        if ev is None:
            named_rows.append({"label": ev_name, "status": "missing", "reason": "无数据", "named_window": True})
        else:
            ev["label"] = ev_name
            ev["status"] = "ok"
            named_rows.append(ev)
    named_rows = attach_vix_peaks(named_rows, vix)
    major = merge_major_rows(named_rows, episodes)
    cmp_ = compare_to_history(highs.get("dd_ath"), annual, episodes)
    return {
        "status": "ok",
        "key": key,
        "name": name,
        "symbol": symbol,
        "asof": highs.get("asof"),
        "history_start": highs.get("history_start"),
        "annual_from": start_year,
        "highs": highs,
        "annual": annual,
        "major": major,
        "comparison": cmp_,
    }


def build_drawdowns(yahoo: dict[str, pd.Series] | None, cboe: dict[str, pd.Series] | None,
                    prev: dict | None, today: date, errors: dict) -> dict:
    yahoo = yahoo or {}
    vix = (cboe or {}).get("vix")
    items = []
    for key, sym, name in INDEX_META:
        if yahoo.get(sym) is None:
            items.append({"status": "missing", "key": key, "name": name, "symbol": sym, "reason": "无数据"})
            continue
        start = ANNUAL_START_YEAR.get(key)
        items.append(build_index_drawdown(yahoo[sym], key=key, name=name, symbol=sym, vix=vix, start_year=start))
    ok_items = [x for x in items if x.get("status") == "ok"]
    if not ok_items:
        reason = errors.get("yahoo") or "指数日线无数据"
        return _maybe_snapshot((prev or {}).get("drawdowns"), today, reason)
    asof = max(str(x.get("asof")) for x in ok_items if x.get("asof"))
    # 以标普500的对比作为总览一行；没有则用第一只
    spx = next((x for x in ok_items if x.get("key") == "sp500"), ok_items[0])
    lines = []
    for x in ok_items:
        h = x.get("highs") or {}
        if h.get("dd_ath") is not None:
            lines.append(f"{x['name']}距高点 {h['dd_ath'] * 100:+.1f}%（高点 {h.get('ath_date') or '无数据'}）")
    overview = (spx.get("comparison") or {}).get("line")
    return {
        "status": "ok",
        "block": "drawdowns",
        "asof": asof,
        "method": (
            "用 Yahoo 官方未复权收盘计算。年内最大回撤只在该日历年的收盘序列上取当年高点到后续低点，"
            "不把上年高点跨年带入。年度涨跌以上一年最后收盘为基准；未结束年份为截至最新收盘。"
            "重大回撤为收盘峰值到谷底的完整轮次（新高或收复前高视为结束）；持续天数与恢复用时为日历日。"
            "VIX 峰值为该轮峰值日至谷底日（含）Cboe 收盘最高值。"
        ),
        "source": "Yahoo Finance ^GSPC / ^NDX / ^RUT / ^DJI close；VIX 来自 Cboe 官方日频 CSV",
        "overview": overview,
        "lines": lines,
        "indices": items,
    }


# ---------------------------------------------------------------- parsers (pure)

def parse_cboe_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    cols = {str(c).strip().upper(): c for c in df.columns}
    date_col = cols.get("DATE") or cols.get("TRADE DATE") or list(df.columns)[0]
    close_col = cols.get("CLOSE") or cols.get("VXCLS") or list(df.columns)[-1]
    out = pd.Series(
        pd.to_numeric(df[close_col], errors="coerce").values,
        index=pd.to_datetime(df[date_col], errors="coerce"),
        name="close",
    )
    out = out[~out.index.isna()].dropna().sort_index()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None)
    return out


def parse_finra_xlsx(data: bytes) -> pd.Series:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError("FINRA 表为空")
    # Year-Month in col 0, debit in col 1
    items: list[tuple[str, float]] = []
    for row in rows[1:]:
        if not row or row[0] is None or row[1] is None:
            continue
        ym = str(row[0]).strip()
        if len(ym) < 7 or ym[4] != "-":
            continue
        try:
            items.append((ym[:7], float(row[1])))
        except (TypeError, ValueError):
            continue
    if not items:
        raise ValueError("FINRA 表无有效融资余额")
    s = pd.Series({k: v for k, v in items}, dtype=float).sort_index()
    s.name = "debit_usd_millions"
    return s


def parse_finra_html(html: str) -> pd.Series:
    """官方页近13个月表（Month/Year 如 Oct-26，单位百万美元）。"""
    from html.parser import HTMLParser
    import re

    class _T(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.in_td = False
            self.in_table = 0
            self.text = ""
            self.row: list[str] = []
            self.rows: list[list[str]] = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.in_table += 1
            if tag in ("td", "th") and self.in_table:
                self.in_td = True
                self.text = ""
            if tag == "tr" and self.in_table:
                self.row = []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.in_table:
                self.in_td = False
                self.row.append(re.sub(r"\s+", " ", self.text).strip())
            if tag == "tr" and self.in_table and self.row:
                self.rows.append(self.row)
            if tag == "table" and self.in_table:
                self.in_table -= 1

        def handle_data(self, data):
            if self.in_td:
                self.text += data

    p = _T()
    p.feed(html)
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    items: list[tuple[str, float]] = []
    for row in p.rows:
        if len(row) < 2:
            continue
        m = re.fullmatch(r"([A-Za-z]{3})-(\d{2})", row[0].replace(" ", ""))
        if not m:
            continue
        mon = months.get(m.group(1).lower())
        if not mon:
            continue
        year = 2000 + int(m.group(2))
        try:
            val = float(row[1].replace(",", ""))
        except ValueError:
            continue
        items.append((f"{year:04d}-{mon:02d}", val))
    if not items:
        raise ValueError("FINRA 页面表无有效融资余额")
    s = pd.Series({k: v for k, v in items}, dtype=float).sort_index()
    s.name = "debit_usd_millions"
    return s


def parse_bea_gdp(data: bytes) -> tuple[pd.Series, dict]:
    """BEA Table 1.1.5 季调年率名义 GDP，百万美元。返回 (period→value, meta)。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    if "T10105-Q" not in wb.sheetnames:
        wb.close()
        raise ValueError("BEA 工作簿缺少 T10105-Q")
    ws = wb["T10105-Q"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = None
    gdp_row = None
    published = None
    for row in rows:
        if not row:
            continue
        a = str(row[0] or "")
        if a.startswith("Data published"):
            published = a.replace("Data published", "").strip()
        if a == "Line" or (row[3] and str(row[3]).endswith("Q1")):
            header = row
        if str(row[1] or "").strip() == "Gross domestic product" or str(row[2] or "") == "A191RC":
            gdp_row = row
    if header is None or gdp_row is None:
        raise ValueError("BEA Table 1.1.5 未找到 GDP 行")
    items: list[tuple[str, float]] = []
    for cell, val in zip(header, gdp_row):
        if cell is None or val is None:
            continue
        label = str(cell).strip().upper().replace(" ", "")
        if "Q" not in label or not label[:4].isdigit():
            continue
        # normalize 1947Q1
        if "-" in label:
            label = label.replace("-", "")
        try:
            items.append((label, float(val)))
        except (TypeError, ValueError):
            continue
    if not items:
        raise ValueError("BEA GDP 序列为空")
    s = pd.Series({k: v for k, v in items}, dtype=float)
    # chronological
    s = s.loc[sorted(s.index, key=lambda p: quarter_end(p) or date.min)]
    s.name = "gdp_usd_millions"
    meta = {
        "units": "millions of dollars, seasonally adjusted at annual rates",
        "table": "BEA NIPA Table 1.1.5 line 1 (A191RC)",
        "published": published,
        "latest_period": str(s.index[-1]),
        "latest_value": float(s.iloc[-1]),
    }
    return s, meta


# ---------------------------------------------------------------- fetch

def _http_get(url: str, timeout: float = 25.0, retries: int = 2) -> bytes:
    import requests

    last: Exception | None = None
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
            if r.status_code == 200 and r.content:
                return r.content
            last = RuntimeError(f"HTTP {r.status_code} {url}")
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise last


def fetch_cboe() -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for key, url in CBOE_CSV.items():
        text = _http_get(url, timeout=20).decode("utf-8", errors="replace")
        out[key] = parse_cboe_csv(text)
    if "vix" not in out or out["vix"].empty:
        raise ValueError("Cboe VIX 无数据")
    return out


def fetch_finra() -> tuple[pd.Series, str]:
    errors: list[str] = []
    for url, kind in (
        (FINRA_XLSX, "xlsx"),
        (FINRA_WAYBACK_XLSX, "xlsx"),
        (FINRA_PAGE, "html"),
        (FINRA_WAYBACK_PAGE, "html"),
    ):
        try:
            raw = _http_get(url, timeout=30)
            if kind == "xlsx" and raw[:2] == b"PK":
                return parse_finra_xlsx(raw), url
            if kind == "html" and b"<" in raw[:200].lower() + raw[:1]:
                return parse_finra_html(raw.decode("utf-8", errors="replace")), url
            # sometimes wayback wraps xlsx
            if raw[:2] == b"PK":
                return parse_finra_xlsx(raw), url
            errors.append(f"{url}: 非预期内容")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:120]}")
    raise ValueError("FINRA 官方表不可用：" + "；".join(errors[:4]))


def fetch_bea_gdp() -> tuple[pd.Series, dict]:
    raw = _http_get(BEA_SECTION1_XLSX, timeout=40)
    return parse_bea_gdp(raw)


def fetch_z1() -> dict:
    import us_risk_context

    return us_risk_context.fetch_z1(_http_get)


def fetch_yahoo_closes() -> dict[str, pd.Series]:
    import net_limits

    # 指数高低点/回撤用未复权收盘（官方点位）；价格型指数通常与复权相同。
    symbols = ["^W5000", "^GSPC", "^NDX", "^RUT", "^DJI", "GC=F", "^TNX", "TLT"]
    raw = net_limits.yf_download(
        symbols, period="max", auto_adjust=False, group_by="ticker", threads=True, progress=False
    )
    if raw is None or raw.empty:
        raise ValueError("Yahoo 无数据")
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        if df is None or "Close" not in df:
            continue
        s = pd.to_numeric(df["Close"], errors="coerce").dropna().sort_index()
        if s.empty:
            continue
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        out[sym] = s
    if "^W5000" not in out and "^GSPC" not in out:
        raise ValueError("Yahoo 指数无数据")
    return out


def default_fetchers() -> dict[str, Callable[[], Any]]:
    return {
        "cboe": fetch_cboe,
        "finra": fetch_finra,
        "bea": fetch_bea_gdp,
        "yahoo": fetch_yahoo_closes,
        "z1": fetch_z1,
    }


# ---------------------------------------------------------------- payload builders

def _missing_block(kind: str, reason: str | None = None) -> dict:
    out = {"status": "missing", "reason": reason or "无数据"}
    if kind:
        out["block"] = kind
    return out


def _maybe_snapshot(prev_block: dict | None, today: date, reason: str) -> dict:
    if not prev_block or prev_block.get("status") != "ok":
        return _missing_block(prev_block.get("block") if prev_block else "", reason)
    asof = prev_block.get("asof") or prev_block.get("month")
    if not asof:
        return _missing_block(prev_block.get("block") or "", reason)
    try:
        if len(str(asof)) == 7:  # YYYY-MM
            y, m = str(asof).split("-")
            asof_d = date(int(y), int(m), 1)
            # month-level: compare on year-month, allow 10 months
            age_months = (today.year - asof_d.year) * 12 + (today.month - asof_d.month)
            stale = age_months > MAX_SNAPSHOT_DAYS  # reuse day cap as loose month cap? No.
            # 10 months would be too long; use 10 months only if we mis-use.
            # For monthly, allow 120 days from month end approx: age_months <= 4
            if age_months > 4:
                stale = True
            else:
                stale = False
        else:
            age = (today - date.fromisoformat(str(asof)[:10])).days
            stale = age > MAX_SNAPSHOT_DAYS
    except ValueError:
        stale = True
    if stale:
        return _missing_block(prev_block.get("block") or "", reason)
    out = copy.deepcopy(prev_block)
    out.update(snapshot=True, snapshot_date=str(asof), snapshot_reason=reason, from_snapshot=True)
    return out


def build_market(yahoo: dict[str, pd.Series] | None, gdp: pd.Series | None, gdp_meta: dict | None,
                 prev: dict | None, today: date, errors: dict) -> dict:
    if not yahoo or "^W5000" not in (yahoo or {}):
        reason = errors.get("yahoo") or "威尔希尔 5000 无数据"
        return _maybe_snapshot((prev or {}).get("market"), today, reason)

    w = yahoo["^W5000"]
    last = float(w.iloc[-1])
    asof = _to_date(w.index[-1])
    cap = market_cap_usd(last)
    out: dict[str, Any] = {
        "status": "ok",
        "block": "market",
        "asof": str(asof),
        "w5000": _r(last, 2),
        "total_cap_usd": cap,
        "total_cap_trillion": _r(cap / 1e12, 3) if cap is not None else None,
        "method": (
            "Wilshire 5000 Full Cap Price Index（Yahoo ^W5000）× 10亿美元；"
            "该指数传统口径为 1 点约合 10 亿美元全市场市值。"
            "FRED WILL5000PRFC 已于 2024-06-03 停更，故不用 FRED 威尔希尔序列。"
        ),
        "source": "Yahoo Finance ^W5000",
        "indices": {},
    }
    for key, sym, name in INDEX_META:
        if yahoo.get(sym) is not None:
            out["indices"][key] = index_quote(yahoo[sym], name, sym)
        else:
            out["indices"][key] = {"status": "missing", "name": name, "symbol": sym, "reason": "无数据"}

    buffett: dict[str, Any] = {"status": "missing", "reason": "无数据"}
    if gdp is not None and asof is not None and cap is not None:
        g = gdp_for_date(gdp, asof)
        if g:
            gdp_mil, period = g
            gdp_usd = gdp_mil * 1_000_000.0
            ratio = cap / gdp_usd if gdp_usd else None
            # historical daily ratios, no lookahead
            ratios = []
            for dt, px in w.items():
                d = _to_date(dt)
                if d is None:
                    continue
                gg = gdp_for_date(gdp, d)
                cap_i = market_cap_usd(float(px))
                if gg and cap_i:
                    ratios.append((d, cap_i / (gg[0] * 1_000_000.0)))
            hist = pd.Series({d: r for d, r in ratios}, dtype=float).sort_index()
            pct = percentile_inclusive(hist, ratio) if ratio is not None and len(hist) else None
            buffett = {
                "status": "ok",
                "ratio": _r(ratio, 4) if ratio is not None else None,
                "ratio_pct": _r((ratio or 0) * 100.0, 1) if ratio is not None else None,
                "gdp_usd": gdp_usd,
                "gdp_trillion": _r(gdp_usd / 1e12, 3),
                "gdp_period": period,
                "gdp_units": "SAAR, millions of dollars → 已换算为美元",
                "gdp_source": "BEA NIPA Table 1.1.5 line 1 (A191RC)",
                "gdp_published": (gdp_meta or {}).get("published"),
                "percentile": pct,
                "percentile_sample": int(len(hist)) if len(hist) else None,
                "percentile_start": str(hist.index[0]) if len(hist) else None,
                "percentile_end": str(hist.index[-1]) if len(hist) else None,
            }
        else:
            buffett = {"status": "missing", "reason": "无对应季度 GDP"}
    elif "bea" in errors:
        buffett = {"status": "missing", "reason": "GDP 未取到"}
    out["buffett"] = buffett
    return out


def build_leverage(margin: pd.Series | None, margin_source: str | None,
                   w5000: pd.Series | None, prev: dict | None, today: date, errors: dict) -> dict:
    if margin is None or margin.empty:
        reason = errors.get("finra") or "FINRA 融资余额无数据"
        return _maybe_snapshot((prev or {}).get("leverage"), today, reason)
    m = pd.to_numeric(margin, errors="coerce").dropna().sort_index()
    month = str(m.index[-1])
    debit = float(m.iloc[-1])
    prev_m = float(m.iloc[-2]) if len(m) >= 2 else None
    yoy = None
    # previous year same month
    yoy_key = f"{int(month[:4]) - 1}{month[4:]}"
    if yoy_key in m.index:
        base = float(m.loc[yoy_key])
        yoy = (debit / base - 1.0) if base else None
    mom = (debit / prev_m - 1.0) if prev_m else None

    cap_ratio = None
    cap_asof = None
    cap_usd = None
    cap_w5000 = None
    if w5000 is not None:
        me = month_end_close(w5000, month)
        if me:
            cap_asof, px = me
            cap_w5000 = _r(px, 2)
            cap_usd = market_cap_usd(px)
            if cap_usd:
                cap_ratio = (debit * 1_000_000.0) / cap_usd

    # historical monthly ratios for percentile
    ratios = []
    if w5000 is not None:
        for ym, val in m.items():
            me = month_end_close(w5000, str(ym))
            if not me:
                continue
            cusd = market_cap_usd(me[1])
            if cusd:
                ratios.append((str(ym), (float(val) * 1_000_000.0) / cusd))
    hist = pd.Series({k: v for k, v in ratios}, dtype=float).sort_index()
    pct = percentile_inclusive(hist, cap_ratio) if cap_ratio is not None and len(hist) else None

    peaks = []
    for year in PEAK_YEARS:
        sl = m[m.index.astype(str).str.startswith(str(year))]
        if sl.empty:
            peaks.append({"year": year, "status": "missing", "reason": "无数据"})
            continue
        pk_month = str(sl.idxmax())
        pk_val = float(sl.max())
        peaks.append({
            "year": year,
            "status": "ok",
            "peak_month": pk_month,
            "peak_usd_millions": pk_val,
            "chg_pct": _r(debit / pk_val - 1.0, 4) if pk_val else None,
        })

    return {
        "status": "ok",
        "block": "leverage",
        "month": month,
        "month_label": f"{month[:4]}年{int(month[5:7])}月",
        "asof": month,
        "debit_usd_millions": debit,
        "debit_trillion": _r(debit / 1e6, 3),
        "units": "百万美元（FINRA 官方口径 Debit Balances in Customers' Securities Margin Accounts）",
        "mom_pct": _r(mom, 4) if mom is not None else None,
        "yoy_pct": _r(yoy, 4) if yoy is not None else None,
        "cap_ratio": _r(cap_ratio, 6) if cap_ratio is not None else None,
        "cap_ratio_pct": _r((cap_ratio or 0) * 100.0, 2) if cap_ratio is not None else None,
        "cap_asof": str(cap_asof) if cap_asof else None,
        "cap_w5000": cap_w5000,
        "cap_usd": cap_usd,
        "cap_ratio_percentile": pct,
        "cap_ratio_sample": int(len(hist)) if len(hist) else None,
        "peaks": peaks,
        "source": "FINRA official margin-statistics",
        "source_url": margin_source or FINRA_XLSX,
    }


def build_vix(cboe: dict[str, pd.Series] | None, prev: dict | None, today: date, errors: dict) -> dict:
    if not cboe or cboe.get("vix") is None or cboe["vix"].empty:
        reason = errors.get("cboe") or "Cboe VIX 无数据"
        return _maybe_snapshot((prev or {}).get("vix"), today, reason)
    v = cboe["vix"]
    asof = _to_date(v.index[-1])
    last = float(v.iloc[-1])
    prev_c = float(v.iloc[-2]) if len(v) >= 2 else None
    chg = (last - prev_c) if prev_c is not None else None
    chg_pct = (last / prev_c - 1.0) if prev_c else None
    window = v.iloc[-252:] if len(v) >= 20 else v
    pct = percentile_inclusive(window, last)

    def _on_date(series: pd.Series | None) -> float | None:
        if series is None or series.empty or asof is None:
            return None
        s = series.loc[series.index.date == asof] if hasattr(series.index, "date") else series
        # align by date
        idx = [i for i in series.index if _to_date(i) == asof]
        if not idx:
            return None
        return float(series.loc[idx[-1]])

    v9 = _on_date(cboe.get("vix9d"))
    v3 = _on_date(cboe.get("vix3m"))
    vv = _on_date(cboe.get("vvix"))
    term = term_structure(last, v9, v3)
    detail = None
    parts = []
    if v9 is not None:
        parts.append(f"VIX9D {v9:.2f}")
    parts.append(f"VIX {last:.2f}")
    if v3 is not None:
        parts.append(f"VIX3M {v3:.2f}")
    if parts:
        joiner = " > " if term == "倒挂" else " < " if term == "升水" else " / "
        # rebuild with actual order
        vals = [("VIX9D", v9), ("VIX", last), ("VIX3M", v3)]
        present = [(n, x) for n, x in vals if x is not None]
        detail = " / ".join(f"{n} {x:.2f}" for n, x in present)
        if term:
            detail = detail + f"（{term}）"
    return {
        "status": "ok",
        "block": "vix",
        "asof": str(asof),
        "close": _r(last, 2),
        "prev_close": _r(prev_c, 2) if prev_c is not None else None,
        "chg": _r(chg, 2) if chg is not None else None,
        "chg_pct": _r(chg_pct, 4) if chg_pct is not None else None,
        "vix9d": _r(v9, 2) if v9 is not None else None,
        "vix3m": _r(v3, 2) if v3 is not None else None,
        "vvix": _r(vv, 2) if vv is not None else None,
        "term": term,
        "term_detail": detail,
        "percentile_1y": pct,
        "percentile_sample": int(len(window)),
        "source": "Cboe daily prices CSV (VIX / VIX9D / VIX3M / VVIX)",
    }


def build_judgment(market: dict, leverage: dict, vix: dict) -> dict:
    rules = (
        "估值：巴菲特指标历史分位≥80%偏热、≤20%偏冷，否则中性。"
        "杠杆：FINRA 融资余额/月末威尔希尔总市值 的历史分位，同样 80/20。"
        "情绪：VIX 1年分位≥80%，或期限倒挂且 VIX≥25 为偏冷；分位≤20%且升水为偏热；否则中性。"
        "缺分位或关键数据时该项为无数据，不填造。"
    )
    val = None
    if (market or {}).get("status") == "ok":
        b = market.get("buffett") or {}
        if b.get("status") == "ok":
            val = band_from_percentile(b.get("percentile"))
    lev = None
    if (leverage or {}).get("status") == "ok":
        lev = band_from_percentile(leverage.get("cap_ratio_percentile"))
    sent = None
    if (vix or {}).get("status") == "ok":
        sent = judge_sentiment(vix.get("close"), vix.get("percentile_1y"), vix.get("term"))

    def _lab(x: str | None, title: str) -> str:
        return f"{title}{x or '无数据'}"

    line = "，".join([_lab(val, "估值"), _lab(lev, "杠杆"), _lab(sent, "情绪")])
    return {
        "valuation": val,
        "leverage": lev,
        "sentiment": sent,
        "line": line,
        "rules": rules,
    }


def build_payload(
    *,
    yahoo: dict[str, pd.Series] | None = None,
    cboe: dict[str, pd.Series] | None = None,
    margin: pd.Series | None = None,
    margin_source: str | None = None,
    gdp: pd.Series | None = None,
    gdp_meta: dict | None = None,
    z1: dict | None = None,
    errors: dict | None = None,
    prev: dict | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or _now()
    today = now.date()
    errors = dict(errors or {})
    market = build_market(yahoo, gdp, gdp_meta, prev, today, errors)
    leverage = build_leverage(margin, margin_source, (yahoo or {}).get("^W5000"), prev, today, errors)
    vix = build_vix(cboe, prev, today, errors)
    drawdowns = build_drawdowns(yahoo, cboe, prev, today, errors)
    import us_risk_context
    yahoo = yahoo or {}
    event_context = us_risk_context.build_event_context(
        drawdowns,
        w5000=yahoo.get("^W5000"),
        gdp=gdp,
        margin=margin,
        z1=z1,
        gold=yahoo.get("GC=F"),
        tnx=yahoo.get("^TNX"),
        tlt=yahoo.get("TLT"),
        errors=errors,
        prev=prev,
        today=today,
    )
    judgment = build_judgment(market, leverage, vix)
    asofs = [b.get("asof") for b in (market, vix, drawdowns) if b.get("status") == "ok" and b.get("asof")]
    asof = max(asofs) if asofs else (leverage.get("month") if leverage.get("status") == "ok" else None)
    from_snap = bool(
        market.get("from_snapshot") or leverage.get("from_snapshot") or vix.get("from_snapshot")
        or drawdowns.get("from_snapshot") or event_context.get("from_snapshot")
    )
    ok = any(b.get("status") == "ok" for b in (market, leverage, vix, drawdowns))
    notes = [
        DISCLAIMER,
        "总市值口径：Yahoo ^W5000（Wilshire 5000 Full Cap Price Index）× 10亿美元；FRED 威尔希尔序列已停更。",
        "巴菲特指标分母为 BEA Table 1.1.5 名义 GDP（SAAR）；交易日使用已结束季度，无前瞻。",
        "融资余额为 FINRA 官方月度 Debit Balances，单位百万美元；占比用对应月末总市值。",
        "VIX 族来自 Cboe 官方日频 CSV，不是 Yahoo 估算。",
        "回撤与高点用 Yahoo 官方未复权收盘；年内最大回撤不跨年带入上年高点；重大回撤的持续/恢复天数为日历日。",
        "历次下跌对比：总市值为窗口日威尔希尔口径；巴菲特指标只用当时已公布 GDP（季末日+30天）；融资余额为峰值月/谷底月 FINRA 月末值；持股与资金流为美联储 Z.1 季度（已公布滞后约75天）；ICI 流向因无法抓取官方页记为无数据；黄金 Yahoo GC=F，10年期收益率 Yahoo ^TNX，长债 TLT。",
        "红涨绿跌。研究参考，不能下单。",
    ]
    sources = [
        {"label": "Yahoo Finance ^W5000 / ^GSPC / ^NDX / ^RUT / ^DJI", "url": "https://finance.yahoo.com/quote/%5EGSPC/history/"},
        {"label": "BEA NIPA Table 1.1.5（Section1All_xls.xlsx，T10105-Q / A191RC）", "url": BEA_SECTION1_XLSX},
        {"label": "BEA GDP 新闻稿（当期口径说明）", "url": "https://www.bea.gov/data/gdp/gross-domestic-product"},
        {"label": "FINRA Margin Statistics 官方表", "url": FINRA_PAGE},
        {"label": "FINRA margin-statistics.xlsx", "url": FINRA_XLSX},
        {"label": "Cboe VIX History CSV", "url": CBOE_CSV["vix"]},
        {"label": "Cboe VIX9D History CSV", "url": CBOE_CSV["vix9d"]},
        {"label": "Cboe VIX3M History CSV", "url": CBOE_CSV["vix3m"]},
        {"label": "Cboe VVIX History CSV", "url": CBOE_CSV["vvix"]},
        {"label": "Federal Reserve Z.1 Financial Accounts（F51.1.s / F51.1.t Corporate equities）", "url": "https://www.federalreserve.gov/releases/z1/"},
        {"label": "Yahoo Finance GC=F（COMEX 黄金连续期货）", "url": "https://finance.yahoo.com/quote/GC%3DF/history/"},
        {"label": "Yahoo Finance ^TNX（CBOE 10年期国债收益率）", "url": "https://finance.yahoo.com/quote/%5ETNX/history/"},
        {"label": "Yahoo Finance TLT（20年+美债 ETF）", "url": "https://finance.yahoo.com/quote/TLT/history/"},
    ]
    return {
        "schema_version": 1,
        "status": "ok" if ok else "no_data",
        "asof": asof,
        "updated_at": now.isoformat(timespec="seconds"),
        "from_snapshot": from_snap,
        "disclaimer": DISCLAIMER,
        "market": market,
        "leverage": leverage,
        "vix": vix,
        "drawdowns": drawdowns,
        "event_context": event_context,
        "judgment": judgment,
        "notes": notes,
        "sources": sources,
        "errors": errors,
    }


# ---------------------------------------------------------------- cache / snapshot

def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _newest(cands: list) -> dict | None:
    ok = [c for c in cands if isinstance(c, dict) and c.get("status") == "ok"]
    return max(ok, key=lambda c: str(c.get("updated_at") or "")) if ok else None


def load_best() -> dict | None:
    return _newest([_cache, _load_json(RUNTIME_PATH)] + [_load_json(p) for p in SEED_PATHS])


def _write_runtime(payload: dict) -> None:
    try:
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUNTIME_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(RUNTIME_PATH)
    except Exception:  # noqa: BLE001
        logger.info("us_risk runtime write failed", exc_info=True)


def fetch_remote_seed() -> dict | None:
    try:
        import requests

        js = requests.get(REMOTE_SEED_URL, timeout=10, headers={"User-Agent": USER_AGENT}).json()
        return js if isinstance(js, dict) and js.get("status") == "ok" else None
    except Exception:  # noqa: BLE001
        return None


def refresh(fetchers: dict | None = None, *, use_remote: bool = True, now: datetime | None = None) -> dict:
    global _cache
    with _lock:
        prev = load_best()
        if use_remote:
            prev = _newest([prev, fetch_remote_seed()]) or prev
        errors: dict = {}
        yahoo = cboe = gdp = margin = z1 = None
        gdp_meta: dict | None = None
        margin_source = None
        fns = fetchers or default_fetchers()
        if "yahoo" in fns:
            try:
                yahoo = fns["yahoo"]()
            except Exception as exc:  # noqa: BLE001
                errors["yahoo"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "cboe" in fns:
            try:
                cboe = fns["cboe"]()
            except Exception as exc:  # noqa: BLE001
                errors["cboe"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "bea" in fns:
            try:
                gdp, gdp_meta = fns["bea"]()
            except Exception as exc:  # noqa: BLE001
                errors["bea"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "finra" in fns:
            try:
                got = fns["finra"]()
                if isinstance(got, tuple):
                    margin, margin_source = got
                else:
                    margin = got
            except Exception as exc:  # noqa: BLE001
                errors["finra"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "z1" in fns:
            try:
                z1 = fns["z1"]()
            except Exception as exc:  # noqa: BLE001
                errors["z1"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if yahoo is None and cboe is None and margin is None and gdp is None:
            if prev:
                out = copy.deepcopy(prev)
                out.update(errors=errors, note="本轮刷新失败，显示上次快照", from_snapshot=True)
                return out
            return {**build_payload(errors=errors, prev=None, now=now), "status": "no_data"}
        payload = build_payload(
            yahoo=yahoo, cboe=cboe, margin=margin, margin_source=margin_source,
            gdp=gdp, gdp_meta=gdp_meta, z1=z1, errors=errors, prev=prev, now=now,
        )
        if payload.get("status") == "ok":
            _cache = payload
            _write_runtime(payload)
        elif prev:
            out = copy.deepcopy(prev)
            out.update(errors=errors, note="本轮刷新失败，显示上次快照", from_snapshot=True)
            return out
        return payload


def snapshot() -> dict:
    best = load_best()
    if best:
        out = copy.deepcopy(best)
        try:
            out["age_hours"] = _r((_now() - datetime.fromisoformat(out["updated_at"])).total_seconds() / 3600.0, 1)
        except (KeyError, TypeError, ValueError):
            out["age_hours"] = None
        return out
    return {
        "schema_version": 1,
        "status": "no_data",
        "error": "无数据",
        "updated_at": _now().isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
    }


def needs_refresh(value: dict | None) -> bool:
    if not value or value.get("status") != "ok":
        return True
    try:
        age = _now() - datetime.fromisoformat(value["updated_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return age > timedelta(hours=REFRESH_HOURS)


def write_seed(payload: dict) -> None:
    for p in SEED_PATHS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    data = refresh(use_remote=False)
    print(json.dumps({
        "status": data.get("status"),
        "asof": data.get("asof"),
        "from_snapshot": data.get("from_snapshot"),
        "errors": data.get("errors"),
        "market_asof": (data.get("market") or {}).get("asof"),
        "w5000": (data.get("market") or {}).get("w5000"),
        "cap_tn": (data.get("market") or {}).get("total_cap_trillion"),
        "buffett": (data.get("market") or {}).get("buffett"),
        "indices": (data.get("market") or {}).get("indices"),
        "leverage": {k: (data.get("leverage") or {}).get(k) for k in
                     ("month", "debit_usd_millions", "mom_pct", "yoy_pct", "cap_ratio_pct",
                      "cap_asof", "peaks", "status", "reason", "snapshot")},
        "vix": {k: (data.get("vix") or {}).get(k) for k in
                ("asof", "close", "chg", "vix9d", "vix3m", "vvix", "term", "term_detail",
                 "percentile_1y", "status", "reason", "snapshot")},
        "judgment": data.get("judgment"),
        "event_context_asof": (data.get("event_context") or {}).get("asof"),
        "event_labels": [c.get("label") for c in ((data.get("event_context") or {}).get("cycles") or [])],
        "current_ownership": ((data.get("event_context") or {}).get("current") or {}).get("ownership_latest"),
        "current_gold": ((data.get("event_context") or {}).get("current") or {}).get("gold"),
        "current_ust10y": ((data.get("event_context") or {}).get("current") or {}).get("ust10y"),
        "drawdowns": {
            "asof": (data.get("drawdowns") or {}).get("asof"),
            "overview": (data.get("drawdowns") or {}).get("overview"),
            "lines": (data.get("drawdowns") or {}).get("lines"),
            "indices": [
                {
                    "key": ix.get("key"),
                    "name": ix.get("name"),
                    "highs": ix.get("highs"),
                    "comparison": (ix.get("comparison") or {}).get("line"),
                    "annual_n": len(ix.get("annual") or []),
                    "annual_tail": (ix.get("annual") or [])[-5:],
                    "major": ix.get("major"),
                }
                for ix in ((data.get("drawdowns") or {}).get("indices") or [])
            ],
        },
    }, ensure_ascii=False, indent=2, default=str))
    if "--write-seed" in sys.argv and data.get("status") == "ok":
        write_seed(data)
