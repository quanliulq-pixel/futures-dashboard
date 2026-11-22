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

指数：标普500（^GSPC）、纳指100（^NDX）、罗素2000（^RUT）Yahoo 最新收盘与前一交易日涨跌幅。

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
)
PEAK_YEARS = (2000, 2007, 2021)

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


def fetch_yahoo_closes() -> dict[str, pd.Series]:
    import net_limits

    symbols = ["^W5000", "^GSPC", "^NDX", "^RUT"]
    raw = net_limits.yf_download(
        symbols, period="max", auto_adjust=True, group_by="ticker", threads=True, progress=False
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
    judgment = build_judgment(market, leverage, vix)
    asofs = [b.get("asof") for b in (market, vix) if b.get("status") == "ok" and b.get("asof")]
    asof = max(asofs) if asofs else (leverage.get("month") if leverage.get("status") == "ok" else None)
    from_snap = bool(
        market.get("from_snapshot") or leverage.get("from_snapshot") or vix.get("from_snapshot")
    )
    ok = any(b.get("status") == "ok" for b in (market, leverage, vix))
    notes = [
        DISCLAIMER,
        "总市值口径：Yahoo ^W5000（Wilshire 5000 Full Cap Price Index）× 10亿美元；FRED 威尔希尔序列已停更。",
        "巴菲特指标分母为 BEA Table 1.1.5 名义 GDP（SAAR）；交易日使用已结束季度，无前瞻。",
        "融资余额为 FINRA 官方月度 Debit Balances，单位百万美元；占比用对应月末总市值。",
        "VIX 族来自 Cboe 官方日频 CSV，不是 Yahoo 估算。",
        "红涨绿跌。研究参考，不能下单。",
    ]
    sources = [
        {"label": "Yahoo Finance ^W5000 / ^GSPC / ^NDX / ^RUT", "url": "https://finance.yahoo.com/quote/%5EW5000/history/"},
        {"label": "BEA NIPA Table 1.1.5（Section1All_xls.xlsx，T10105-Q / A191RC）", "url": BEA_SECTION1_XLSX},
        {"label": "BEA GDP 新闻稿（当期口径说明）", "url": "https://www.bea.gov/data/gdp/gross-domestic-product"},
        {"label": "FINRA Margin Statistics 官方表", "url": FINRA_PAGE},
        {"label": "FINRA margin-statistics.xlsx", "url": FINRA_XLSX},
        {"label": "Cboe VIX History CSV", "url": CBOE_CSV["vix"]},
        {"label": "Cboe VIX9D History CSV", "url": CBOE_CSV["vix9d"]},
        {"label": "Cboe VIX3M History CSV", "url": CBOE_CSV["vix3m"]},
        {"label": "Cboe VVIX History CSV", "url": CBOE_CSV["vvix"]},
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
        yahoo = cboe = gdp = margin = None
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
        if yahoo is None and cboe is None and margin is None and gdp is None:
            if prev:
                out = copy.deepcopy(prev)
                out.update(errors=errors, note="本轮刷新失败，显示上次快照", from_snapshot=True)
                return out
            return {**build_payload(errors=errors, prev=None, now=now), "status": "no_data"}
        payload = build_payload(
            yahoo=yahoo, cboe=cboe, margin=margin, margin_source=margin_source,
            gdp=gdp, gdp_meta=gdp_meta, errors=errors, prev=prev, now=now,
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
    }, ensure_ascii=False, indent=2, default=str))
    if "--write-seed" in sys.argv and data.get("status") == "ok":
        write_seed(data)
