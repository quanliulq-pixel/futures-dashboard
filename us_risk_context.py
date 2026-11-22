# -*- coding: utf-8 -*-
"""历次重大下跌窗口的总市值、巴菲特指标、融资余额、Z.1 持股与资金流、黄金与美债。

只使用已公布数据，不做前瞻；缺序列则该字段为无数据，绝不编造。
"""
from __future__ import annotations

import io
import math
import zipfile
from datetime import date, timedelta
from typing import Any

import pandas as pd

W5000_USD_PER_POINT = 1_000_000_000
GDP_PUBLISH_LAG_DAYS = 30  # 约提前估计公布（季末日后约一个月）才使用该季 GDP
Z1_PUBLISH_LAG_DAYS = 75  # Z.1 约在季日后 10–11 周发布
Z1_URLS = (
    "https://www.federalreserve.gov/releases/z1/current/z1_csv_files.zip",
    "https://www.federalreserve.gov/releases/z1/20260911/z1_csv_files.zip",
)
# F51.1.s / F51.1.t 公司股票（原 L.223 口径，2026:Q2 起表名改为 F51.1）
Z1_LEVEL = {
    "total": "LM893064105.Q",
    "hh_direct": "LM153064105.Q",
    "mf": "LM653064100.Q",
    "etf": "LM563064100.Q",
    "pens_priv": "LM573064105.Q",
    "pens_fed": "LM343064105.Q",
    "pens_sl": "LM223064145.Q",
    "foreign": "LM263064105.Q",
}
Z1_FLOW = {
    "hh_direct": "FA153064105.Q",
    "mf": "FA653064100.Q",
    "etf": "FA563064100.Q",
    "pens_priv": "FA573064105.Q",
    "pens_fed": "FA343064105.Q",
    "pens_sl": "FA223064145.Q",
    "foreign": "FA263064105.Q",
}
OWNERSHIP_NOTE = (
    "美联储 Z.1 Financial Accounts，表 F51.1.s Corporate equities（公司股票持有人结构，"
    "原 L.223）。分母为 All sectors; corporate equities; asset（LM893064105），"
    "含上市与非上市股权、以及美国居民持有的外国股票，与威尔希尔全市场公开股市值不是同一口径。"
    "家庭直接持股为家庭和非营利部门 corporate equities（残差项，不等于全部散户；间接持有计入基金/养老金等）。"
    "养老金=私人+联邦+州地养老金之和。取该日之前已公布的最近一季（按季末日+75 天近似发布时间，无前瞻）。"
)
FLOW_NOTE = (
    "ICI 共同基金/ETF 股票型资金流向：本环境无法抓取 ICI 官方页（HTTP 403），完整历史也常需订阅，故显示无数据。"
    "改用 Z.1 表 F51.1.t 公司股票交易（FA 序列，季节性调整年率 SAAR，百万美元）；"
    "与事件窗口相交、且在数据日（当前或快照日）已经公布的季度，净买入按 SAAR/4 加总。正数为净买入，负数为净卖出。短窗口不会用到当时尚未发布的季度以外的数字，但历史事件在季度发布后会回填期间累计。"
)
HAVEN_NOTE = (
    "黄金为 Yahoo GC=F（COMEX 黄金连续期货收盘，近似金价，非 LBMA 定盘；序列约从 2000-08 起，更早为无数据）。"
    "10 年期美债收益率为 Yahoo ^TNX（CBOE 10 年期国债收益率，单位百分点）。"
    "TLT 为 iShares 20 年+美债 ETF 复权前收盘（约 2002-07 起，更早为无数据）。"
)


def _r(x: Any, n: int = 4) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def _to_date(x: Any) -> date | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    if isinstance(x, date) and not isinstance(x, type(pd.NaT)):
        return x if not isinstance(x, pd.Timestamp) else x.date()
    try:
        ts = pd.to_datetime(x, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def parse_z1_period(label: str) -> date | None:
    text = str(label).strip().upper().replace(":", "Q").replace("-", "")
    while "QQ" in text:
        text = text.replace("QQ", "Q")
    if "Q" not in text:
        return None
    try:
        year = int(text.split("Q")[0])
        q = int(text.split("Q")[1])
        month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        return date(year, month, day)
    except (KeyError, TypeError, ValueError):
        return None


def format_z1_period(label: str) -> str:
    return str(label).strip().upper().replace(":", "Q").replace("Q0", "Q") if label else ""


def normalize_period_label(label: str) -> str:
    text = str(label).strip().upper().replace(":", "Q").replace("-", "Q")
    while "QQ" in text:
        text = text.replace("QQ", "Q")
    return text


def parse_z1_table(csv_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(csv_bytes))
    if "date" not in df.columns:
        raise ValueError("Z.1 表缺少 date 列")
    df = df.copy()
    df["period"] = df["date"].map(normalize_period_label)
    df["quarter_end"] = df["period"].map(parse_z1_period)
    df = df.dropna(subset=["quarter_end"]).sort_values("quarter_end")
    return df


def parse_z1_zip(data: bytes) -> dict[str, pd.DataFrame]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = {n.lower(): n for n in zf.namelist()}
    level_name = names.get("csv/f51_1_s.csv")
    flow_name = names.get("csv/f51_1_t.csv")
    if not level_name or not flow_name:
        raise ValueError("Z.1 压缩包缺少 F51.1.s / F51.1.t")
    return {
        "levels": parse_z1_table(zf.read(level_name)),
        "flows": parse_z1_table(zf.read(flow_name)),
    }


def fetch_z1(http_get) -> dict[str, pd.DataFrame]:
    last = None
    for url in Z1_URLS:
        try:
            raw = http_get(url, timeout=45.0)
            if raw[:2] == b"PK":
                out = parse_z1_zip(raw)
                out["source_url"] = url
                return out
            last = RuntimeError(f"非 zip：{url}")
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last or RuntimeError("Z.1 下载失败")


def close_on_or_before(series: pd.Series | None, asof: date) -> tuple[date, float] | None:
    if series is None or asof is None:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if s.empty:
        return None
    s.index = pd.DatetimeIndex(s.index).tz_localize(None)
    sl = s[s.index.date <= asof]
    if sl.empty:
        return None
    return (_to_date(sl.index[-1]), float(sl.iloc[-1]))


def gdp_published_asof(gdp: pd.Series | None, asof: date, lag_days: int = GDP_PUBLISH_LAG_DAYS) -> tuple[float, str] | None:
    """只用当时已公布的 GDP：季末日 + lag_days 之后才使用该季。"""
    if gdp is None or gdp.empty or asof is None:
        return None
    eligible = []
    for period, value in pd.to_numeric(gdp, errors="coerce").dropna().items():
        end = period if isinstance(period, date) else parse_z1_period(str(period)) or _quarter_end_bea(str(period))
        if end is None:
            continue
        published = end + timedelta(days=lag_days)
        if published <= asof:
            eligible.append((end, str(period).replace(":", "Q"), float(value)))
    if not eligible:
        return None
    end, period, value = max(eligible, key=lambda x: x[0])
    return value, period


def _quarter_end_bea(period: str) -> date | None:
    text = str(period).strip().upper().replace("-", "")
    if "Q" not in text:
        return None
    try:
        year = int(text.split("Q")[0])
        q = int(text.split("Q")[1])
        month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        return date(year, month, day)
    except (KeyError, TypeError, ValueError):
        return None


def z1_row_published(df: pd.DataFrame | None, asof: date, lag_days: int = Z1_PUBLISH_LAG_DAYS) -> pd.Series | None:
    if df is None or df.empty or asof is None:
        return None
    ok = df[df["quarter_end"].map(lambda d: d is not None and (d + timedelta(days=lag_days)) <= asof)]
    if ok.empty:
        return None
    return ok.iloc[-1]


def _share(row: pd.Series, col: str, total: float) -> float | None:
    if total is None or total == 0 or col not in row or pd.isna(row[col]):
        return None
    try:
        return float(row[col]) / total
    except (TypeError, ValueError):
        return None


def ownership_from_row(row: pd.Series | None) -> dict:
    if row is None:
        return {"status": "missing", "reason": "无数据"}
    total_col = Z1_LEVEL["total"]
    if total_col not in row or pd.isna(row[total_col]):
        return {"status": "missing", "reason": "无数据"}
    total = float(row[total_col])
    pens = 0.0
    pens_ok = False
    for k in ("pens_priv", "pens_fed", "pens_sl"):
        col = Z1_LEVEL[k]
        if col in row and pd.notna(row[col]):
            pens += float(row[col])
            pens_ok = True
    mf = _share(row, Z1_LEVEL["mf"], total)
    etf = _share(row, Z1_LEVEL["etf"], total)
    mf_etf = None if mf is None and etf is None else (mf or 0.0) + (etf or 0.0)
    return {
        "status": "ok",
        "quarter": str(row.get("period") or row.get("date") or "").replace(":", "Q"),
        "quarter_end": str(row["quarter_end"]) if row.get("quarter_end") else None,
        "total_usd": total * 1_000_000.0,
        "total_trillion": _r(total / 1e6, 3),
        "hh_direct_pct": _r(_share(row, Z1_LEVEL["hh_direct"], total) * 100.0, 1) if _share(row, Z1_LEVEL["hh_direct"], total) is not None else None,
        "mf_pct": _r(mf * 100.0, 1) if mf is not None else None,
        "etf_pct": _r(etf * 100.0, 1) if etf is not None else None,
        "mf_etf_pct": _r(mf_etf * 100.0, 1) if mf_etf is not None else None,
        "pension_pct": _r(pens / total * 100.0, 1) if pens_ok else None,
        "foreign_pct": _r(_share(row, Z1_LEVEL["foreign"], total) * 100.0, 1) if _share(row, Z1_LEVEL["foreign"], total) is not None else None,
        "units": "占公司股票持有总额（Z.1 LM893064105）的百分比",
    }


def market_at(w5000: pd.Series | None, gdp: pd.Series | None, asof: date) -> dict:
    got = close_on_or_before(w5000, asof)
    if not got:
        return {"status": "missing", "reason": "无威尔希尔收盘", "asof_requested": str(asof)}
    d, px = got
    cap = px * W5000_USD_PER_POINT
    g = gdp_published_asof(gdp, d)
    buffett = None
    gdp_period = gdp_usd = None
    if g:
        gdp_mil, gdp_period = g
        gdp_usd = gdp_mil * 1_000_000.0
        buffett = cap / gdp_usd if gdp_usd else None
    return {
        "status": "ok",
        "asof": str(d),
        "w5000": _r(px, 2),
        "total_cap_usd": cap,
        "total_cap_trillion": _r(cap / 1e12, 3),
        "buffett_ratio": _r(buffett, 4) if buffett is not None else None,
        "buffett_ratio_pct": _r(buffett * 100.0, 1) if buffett is not None else None,
        "gdp_period": gdp_period,
        "gdp_trillion": _r(gdp_usd / 1e12, 3) if gdp_usd else None,
        "gdp_rule": f"只用季末日+{GDP_PUBLISH_LAG_DAYS}天及以前已可视为公布的名义 GDP（SAAR）",
    }


def leverage_at(margin: pd.Series | None, w5000: pd.Series | None, asof: date) -> dict:
    if margin is None or margin.empty or asof is None:
        return {"status": "missing", "reason": "无融资余额", "asof_requested": str(asof)}
    m = pd.to_numeric(margin, errors="coerce").dropna()
    month = f"{asof.year:04d}-{asof.month:02d}"
    # 该月尚未公布则退到上一已公布月
    if month in m.index:
        used = month
    else:
        prior = [k for k in m.index.astype(str) if str(k) <= month]
        if not prior:
            return {"status": "missing", "reason": "该月尚无 FINRA 数据", "month": month}
        used = max(prior)
    debit = float(m.loc[used])
    cap_ratio = cap_asof = None
    if w5000 is not None:
        try:
            start = pd.Timestamp(f"{used}-01")
            end = start + pd.offsets.MonthEnd(0)
            sl = pd.to_numeric(w5000, errors="coerce").dropna()
            sl = sl[(sl.index >= start) & (sl.index <= end)]
            if len(sl):
                cap_asof = _to_date(sl.index[-1])
                cap = float(sl.iloc[-1]) * W5000_USD_PER_POINT
                cap_ratio = (debit * 1_000_000.0) / cap if cap else None
        except (ValueError, TypeError):
            pass
    return {
        "status": "ok",
        "month": used,
        "month_requested": month,
        "debit_usd_millions": debit,
        "debit_trillion": _r(debit / 1e6, 3),
        "cap_ratio": _r(cap_ratio, 6) if cap_ratio is not None else None,
        "cap_ratio_pct": _r((cap_ratio or 0) * 100.0, 2) if cap_ratio is not None else None,
        "cap_asof": str(cap_asof) if cap_asof else None,
    }


def _flow_quarterly(row: pd.Series, col: str) -> float | None:
    if col not in row or pd.isna(row[col]):
        return None
    try:
        return float(row[col]) / 4.0  # SAAR → 当季
    except (TypeError, ValueError):
        return None


def z1_flow_window(flows: pd.DataFrame | None, start: date, end: date, published_asof: date | None = None) -> dict:
    """与 [start,end] 相交、且在 published_asof（默认=end）已公布的季度，SAAR/4 累计。"""
    if flows is None or flows.empty or start is None or end is None:
        return {"status": "missing", "reason": "无数据"}
    cutoff = published_asof or end
    # 包含与 [start, end] 相交的季度
    rows = []
    for _, row in flows.iterrows():
        qend = row.get("quarter_end")
        if not qend:
            continue
        # 季度起点
        q = (qend.month - 1) // 3 + 1
        qstart = date(qend.year, {1: 1, 2: 4, 3: 7, 4: 10}[q], 1)
        if qend < start or qstart > end:
            continue
        # 无前瞻：只用截止 published_asof 已公布的季度（历史事件在季报发布后回填）
        if qend + timedelta(days=Z1_PUBLISH_LAG_DAYS) > cutoff:
            continue
        rows.append(row)
    if not rows:
        return {"status": "missing", "reason": "窗口内无已公布季度", "quarters": []}

    def _sum(col: str) -> float | None:
        vals = [_flow_quarterly(r, col) for r in rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    hh = _sum(Z1_FLOW["hh_direct"])
    mf = _sum(Z1_FLOW["mf"])
    etf = _sum(Z1_FLOW["etf"])
    pens = None
    parts = [_sum(Z1_FLOW[k]) for k in ("pens_priv", "pens_fed", "pens_sl")]
    if any(v is not None for v in parts):
        pens = sum(v or 0.0 for v in parts)
    foreign = _sum(Z1_FLOW["foreign"])
    fund = None if mf is None and etf is None else (mf or 0.0) + (etf or 0.0)
    quarters = [str(r.get("period") or "") for r in rows]
    return {
        "status": "ok",
        "quarters": quarters,
        "units": "百万美元；SAAR/4 后按季加总",
        "hh_direct_usd_millions": _r(hh, 1) if hh is not None else None,
        "mf_usd_millions": _r(mf, 1) if mf is not None else None,
        "etf_usd_millions": _r(etf, 1) if etf is not None else None,
        "mf_etf_usd_millions": _r(fund, 1) if fund is not None else None,
        "pension_usd_millions": _r(pens, 1) if pens is not None else None,
        "foreign_usd_millions": _r(foreign, 1) if foreign is not None else None,
    }


def series_window(series: pd.Series | None, start: date, end: date) -> dict:
    a = close_on_or_before(series, start)
    b = close_on_or_before(series, end)
    if not a or not b:
        return {
            "status": "missing",
            "reason": "无数据",
            "peak": None if not a else {"asof": str(a[0]), "close": _r(a[1], 4)},
            "trough": None if not b else {"asof": str(b[0]), "close": _r(b[1], 4)},
        }
    chg = (b[1] / a[1] - 1.0) if a[1] else None
    chg_pp = b[1] - a[1]
    return {
        "status": "ok",
        "peak": {"asof": str(a[0]), "close": _r(a[1], 4)},
        "trough": {"asof": str(b[0]), "close": _r(b[1], 4)},
        "chg_pct": _r(chg, 4) if chg is not None else None,
        "chg_pp": _r(chg_pp, 3),
    }


def build_cycle_row(
    label: str,
    peak_date: date,
    trough_date: date,
    *,
    w5000: pd.Series | None,
    gdp: pd.Series | None,
    margin: pd.Series | None,
    z1_levels: pd.DataFrame | None,
    z1_flows: pd.DataFrame | None,
    gold: pd.Series | None,
    tnx: pd.Series | None,
    tlt: pd.Series | None,
    ongoing: bool = False,
    published_asof: date | None = None,
) -> dict:
    mkt_p = market_at(w5000, gdp, peak_date)
    mkt_t = market_at(w5000, gdp, trough_date)
    cap_chg = None
    if mkt_p.get("status") == "ok" and mkt_t.get("status") == "ok" and mkt_p.get("total_cap_usd") and mkt_t.get("total_cap_usd"):
        cap_chg = mkt_t["total_cap_usd"] / mkt_p["total_cap_usd"] - 1.0
    lev_p = leverage_at(margin, w5000, peak_date)
    lev_t = leverage_at(margin, w5000, trough_date)
    debit_chg = None
    trough_month_published = (
        lev_t.get("status") == "ok"
        and (not lev_t.get("month_requested") or lev_t.get("month") == lev_t.get("month_requested"))
    )
    same_unpublished = (
        lev_p.get("status") == "ok" and lev_t.get("status") == "ok"
        and lev_p.get("month") == lev_t.get("month")
        and not trough_month_published
    )
    if (
        lev_p.get("status") == "ok" and lev_t.get("status") == "ok"
        and lev_p.get("debit_usd_millions") and lev_t.get("debit_usd_millions")
        and not same_unpublished
    ):
        debit_chg = lev_t["debit_usd_millions"] / lev_p["debit_usd_millions"] - 1.0
    ratio_chg_pp = None
    if (
        lev_p.get("cap_ratio_pct") is not None and lev_t.get("cap_ratio_pct") is not None
        and not same_unpublished
    ):
        ratio_chg_pp = lev_t["cap_ratio_pct"] - lev_p["cap_ratio_pct"]
    own_p = ownership_from_row(z1_row_published(z1_levels, peak_date))
    own_t = ownership_from_row(z1_row_published(z1_levels, trough_date))
    flows = z1_flow_window(z1_flows, peak_date, trough_date, published_asof=published_asof or trough_date)
    gold_w = series_window(gold, peak_date, trough_date)
    tnx_w = series_window(tnx, peak_date, trough_date)
    tlt_w = series_window(tlt, peak_date, trough_date)
    return {
        "label": label,
        "peak_date": str(peak_date),
        "trough_date": str(trough_date),
        "ongoing": ongoing,
        "window_index": "标普500 峰值日至谷底日（市场整体窗口）",
        "market": {"peak": mkt_p, "trough": mkt_t, "cap_chg_pct": _r(cap_chg, 4) if cap_chg is not None else None},
        "leverage": {
            "peak": lev_p,
            "trough": lev_t,
            "debit_chg_pct": _r(debit_chg, 4) if debit_chg is not None else None,
            "ratio_chg_pp": _r(ratio_chg_pp, 2) if ratio_chg_pp is not None else None,
            "units": "FINRA 月末 Debit Balances，百万美元；占比用该月末威尔希尔总市值",
        },
        "ownership": {"peak": own_p, "trough": own_t, "note": OWNERSHIP_NOTE},
        "flows": {
            "ici": {"status": "missing", "reason": "ICI 官方页无法抓取（403），完整历史常需订阅"},
            "z1": flows,
            "note": FLOW_NOTE,
        },
        "haven": {
            "gold": {**gold_w, "name": "黄金 GC=F", "source": "Yahoo GC=F"},
            "ust10y": {**tnx_w, "name": "10年期美债收益率", "source": "Yahoo ^TNX", "units": "百分点；chg_pp 为收益率变化"},
            "tlt": {**tlt_w, "name": "TLT 长债ETF", "source": "Yahoo TLT"},
            "note": HAVEN_NOTE,
        },
    }


def current_extras(
    *,
    asof: date | None,
    equity_peak: date | None,
    w5000: pd.Series | None,
    z1_levels: pd.DataFrame | None,
    z1_flows: pd.DataFrame | None,
    gold: pd.Series | None,
    tnx: pd.Series | None,
    tlt: pd.Series | None,
) -> dict:
    asof = asof or date.today()
    own = ownership_from_row(z1_row_published(z1_levels, asof))
    # 最近已公布一季 + 最近四个已公布季度累计
    latest_flow = {"status": "missing", "reason": "无数据"}
    last4 = {"status": "missing", "reason": "无数据"}
    if z1_flows is not None and not z1_flows.empty:
        pub = z1_flows[z1_flows["quarter_end"].map(lambda d: d is not None and (d + timedelta(days=Z1_PUBLISH_LAG_DAYS)) <= asof)]
        if len(pub):
            last = pub.iloc[-1]
            qend = last["quarter_end"]
            qstart = date(qend.year, ((qend.month - 1) // 3) * 3 + 1, 1)
            latest_flow = z1_flow_window(z1_flows, qstart, qend, published_asof=asof)
            latest_flow["scope"] = "最新已公布单季"
            q4start = pub.iloc[-4]["quarter_end"] if len(pub) >= 4 else pub.iloc[0]["quarter_end"]
            qs = date(q4start.year, ((q4start.month - 1) // 3) * 3 + 1, 1)
            last4 = z1_flow_window(z1_flows, qs, qend, published_asof=asof)
            last4["scope"] = "最近四个已公布季度累计"
    def _now_and_chg(series, start):
        now = close_on_or_before(series, asof)
        out = {"status": "missing", "reason": "无数据"}
        if not now:
            return out
        prev = None
        s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None)
        sl = s[s.index.date <= asof]
        if len(sl) >= 2:
            prev = float(sl.iloc[-2])
            prev_d = _to_date(sl.index[-2])
        chg1 = (now[1] / prev - 1.0) if prev else None
        out = {
            "status": "ok",
            "asof": str(now[0]),
            "close": _r(now[1], 4),
            "prev_close": _r(prev, 4) if prev is not None else None,
            "prev_asof": str(prev_d) if prev is not None else None,
            "chg_1d_pct": _r(chg1, 4) if chg1 is not None else None,
            "chg_1d_pp": _r(now[1] - prev, 3) if prev is not None else None,
        }
        if start:
            a = close_on_or_before(series, start)
            if a:
                out["from_equity_peak_asof"] = str(a[0])
                out["from_equity_peak_close"] = _r(a[1], 4)
                out["from_equity_peak_chg_pct"] = _r(now[1] / a[1] - 1.0, 4) if a[1] else None
                out["from_equity_peak_chg_pp"] = _r(now[1] - a[1], 3)
        return out
    return {
        "status": "ok",
        "asof": str(asof),
        "ownership_latest": {**own, "note": OWNERSHIP_NOTE},
        "flows_latest_quarter": latest_flow,
        "flows_last_four_quarters": last4,
        "flows_note": FLOW_NOTE,
        "gold": {**_now_and_chg(gold, equity_peak), "name": "黄金 GC=F", "source": "Yahoo GC=F"},
        "ust10y": {**_now_and_chg(tnx, equity_peak), "name": "10年期美债收益率", "source": "Yahoo ^TNX", "units": "百分点"},
        "tlt": {**_now_and_chg(tlt, equity_peak), "name": "TLT 长债ETF", "source": "Yahoo TLT"},
        "haven_note": HAVEN_NOTE,
        "equity_peak_date": str(equity_peak) if equity_peak else None,
    }


def spx_event_windows(drawdowns: dict | None) -> list[dict]:
    """从标普500 重大回撤行取出命名事件与当前这轮的峰值/谷底日。"""
    if not drawdowns or drawdowns.get("status") != "ok":
        return []
    items = drawdowns.get("indices") or []
    spx = next((x for x in items if x.get("key") == "sp500"), None)
    if not spx:
        return []
    out = []
    for ep in spx.get("major") or []:
        lab = ep.get("label")
        if not lab or ep.get("status") == "missing":
            # 2025 未命名的近期回撤
            pd0 = ep.get("peak_date") or ""
            if str(pd0).startswith("2025") and not ep.get("ongoing"):
                lab = "2025"
            else:
                if not ep.get("ongoing"):
                    continue
                lab = lab or "当前回撤"
        if not ep.get("peak_date") or not ep.get("trough_date"):
            continue
        out.append({
            "label": lab,
            "peak_date": date.fromisoformat(str(ep["peak_date"])[:10]),
            "trough_date": date.fromisoformat(str(ep["trough_date"])[:10]),
            "ongoing": bool(ep.get("ongoing")),
        })
    # 去重同标签（保留第一条，通常为窗口计算的命名行）
    seen = set()
    uniq = []
    for ev in out:
        if ev["label"] in seen:
            continue
        seen.add(ev["label"])
        uniq.append(ev)
    return uniq


def build_event_context(
    drawdowns: dict | None,
    *,
    w5000: pd.Series | None,
    gdp: pd.Series | None,
    margin: pd.Series | None,
    z1: dict | None,
    gold: pd.Series | None,
    tnx: pd.Series | None,
    tlt: pd.Series | None,
    errors: dict | None = None,
    prev: dict | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    errors = errors or {}
    windows = spx_event_windows(drawdowns)
    z1_levels = (z1 or {}).get("levels")
    z1_flows = (z1 or {}).get("flows")
    if not windows:
        prev_block = (prev or {}).get("event_context")
        if prev_block and prev_block.get("status") == "ok":
            out = dict(prev_block)
            out.update(from_snapshot=True, snapshot_reason=errors.get("yahoo") or "无回撤窗口")
            return out
        return {"status": "missing", "reason": "无重大回撤窗口", "block": "event_context"}
    asof = today
    if (drawdowns or {}).get("asof"):
        try:
            asof = date.fromisoformat(str(drawdowns["asof"])[:10])
        except ValueError:
            asof = today
    cycles = [
        build_cycle_row(
            ev["label"], ev["peak_date"], ev["trough_date"],
            w5000=w5000, gdp=gdp, margin=margin,
            z1_levels=z1_levels, z1_flows=z1_flows,
            gold=gold, tnx=tnx, tlt=tlt, ongoing=ev["ongoing"],
            published_asof=asof,
        )
        for ev in windows
    ]
    current = next((ev for ev in windows if ev.get("ongoing")), windows[-1] if windows else None)
    extras = current_extras(
        asof=asof,
        equity_peak=current["peak_date"] if current else None,
        w5000=w5000, z1_levels=z1_levels, z1_flows=z1_flows,
        gold=gold, tnx=tnx, tlt=tlt,
    )
    return {
        "status": "ok",
        "block": "event_context",
        "asof": (drawdowns or {}).get("asof"),
        "method": (
            "以标普500 各轮峰值日→谷底日为市场窗口。"
            "总市值用窗口日（或此前最近交易日）Yahoo ^W5000×10亿美元；"
            "巴菲特指标只用当时已公布 GDP（季末日+30天）。"
            "融资余额取峰值月、谷底月的 FINRA 月末值。"
            "持股取当时已公布最近一季 Z.1（滞后约75天，无前瞻）；资金流为与窗口相交且在数据日已公布季度的 Z.1 净买入（SAAR/4 累计）。"
            "ICI 流向无数据。黄金 GC=F、10年收益率 ^TNX、长债 TLT。"
        ),
        "cycles": cycles,
        "current": extras,
        "z1_source_url": (z1 or {}).get("source_url"),
        "errors_note": {k: errors[k] for k in ("z1", "finra", "bea", "yahoo") if k in (errors or {})},
    }
