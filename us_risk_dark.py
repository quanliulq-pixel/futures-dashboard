# -*- coding: utf-8 -*-
"""美股暗盘：SqueezeMetrics DIX/GEX、Cboe 场外（FINRA TRF）成交占比、FINRA ATS 周度。

只使用已公布公开数据，缺序列则无数据，绝不编造。
"""
from __future__ import annotations

import io
import math
from datetime import date, timedelta
from typing import Any

import pandas as pd

DIX_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"
CBOE_VOLUME_PAGE = "https://www.cboe.com/markets/us/equities/market-statistics/historical-market-volume/"
CBOE_VOLUME_CSV = (
    "https://cdn.cboe.com/resources/us/equities/market-statistics/historical-market-volume/"
    "market_history_{year}.csv"
)
FINRA_WEEKLY_API = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
FINRA_OTC_PAGE = "https://otctransparency.finra.org/"

# 事件窗口可能用到的年份 + 近两年（1 年分位）
EVENT_VOLUME_YEARS = (2009, 2018, 2020, 2022, 2025)

DIX_NOTE = (
    "DIX（Dark Index）来自 SqueezeMetrics 公开日频 CSV，表示标普500 相关暗池成交中买入的占比。"
    "DIX 高=暗池买盘偏多（常被解读为机构在暗池吸纳）；DIX 低=暗池买盘偏少。"
    "GEX 为同一来源的经销商 gamma 敞口（美元）：正值表示经销商偏多 gamma，波动或更易被抑制；"
    "负值表示偏空 gamma，波动或更易被放大。序列约自 2011-05 起，更早为无数据。"
)
OFFEX_NOTE = (
    "场外成交占比用 Cboe 美股历史成交量统计：当日 FINRA TRF（Nasdaq Carteret / Chicago、NYSE TRF）"
    "成交股数 ÷ 全市场成交股数。该口径是全部场外成交（暗池 ATS + 批发商/内化等），不等于纯暗池占比。"
    "Cboe 日频文件约自 2009 年起；取不到的日期为无数据。"
)
ATS_NOTE = (
    "FINRA OTC Transparency 周度 ATS 数据（官方 API weeklySummary）仅覆盖另类交易系统暗池，"
    "通常在当周结束后约 2–5 周公布，周次以周一 weekStartDate 标识。"
    "前几大 ATS 占比为占当周 NMS（Tier 1+2）ATS 成交股数的比例。"
    "SPY/QQQ 只列 ATS 周成交股数与名义金额；因未并入该 ETF 全市场综合成交，占其总成交比例为无数据。"
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
    if isinstance(x, date) and not isinstance(x, pd.Timestamp):
        return x
    try:
        ts = pd.to_datetime(x, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def percentile_of(series: pd.Series, value: float) -> tuple[float | None, int]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty or value is None or not math.isfinite(value):
        return None, int(len(s))
    return _r(float((s <= value).mean() * 100.0), 1), int(len(s))


def close_on_or_before(series: pd.Series | None, asof: date, max_lag_days: int = 5) -> tuple[date, float] | None:
    if series is None or asof is None:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if s.empty:
        return None
    s.index = pd.DatetimeIndex(s.index).tz_localize(None)
    sl = s[s.index.date <= asof]
    if sl.empty:
        return None
    d = _to_date(sl.index[-1])
    if d is None or (asof - d).days > max_lag_days:
        return None
    return d, float(sl.iloc[-1])


def parse_dix_csv(raw: bytes | str) -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if "dix" not in text.lower()[:80]:
        raise ValueError("DIX.csv 格式异常")
    df = pd.read_csv(io.StringIO(text))
    cols = {c.strip().lower(): c for c in df.columns}
    if "date" not in cols or "dix" not in cols:
        raise ValueError("DIX.csv 缺少 date/dix 列")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[cols["date"]], errors="coerce"),
        "dix": pd.to_numeric(df[cols["dix"]], errors="coerce"),
        "gex": pd.to_numeric(df[cols["gex"]], errors="coerce") if "gex" in cols else pd.NA,
        "price": pd.to_numeric(df[cols["price"]], errors="coerce") if "price" in cols else pd.NA,
    }).dropna(subset=["date", "dix"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if out.empty:
        raise ValueError("DIX.csv 无有效行")
    return out


def is_offexchange_venue(name: str) -> bool:
    """识别 Cboe 文件中的场外场所，含历史命名（如 2009 年的 NASDAQ (DQ)、NYSE (DN)、ADF (D)）。"""
    import re
    n = str(name or "").upper()
    if "FINRA" in n or "TRF" in n or "OFF-EXCHANGE" in n or "OFF EXCHANGE" in n:
        return True
    if re.search(r"ADF", n):
        return True
    # DQ/DN/DB 为 FINRA TRF 市场代码；D 为历史 ADF
    return bool(re.search(r"\((DQ|DN|DB|D)\)", n))


def parse_cboe_market_history(raw: bytes | str) -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if text.lstrip().startswith("<"):
        raise ValueError("Cboe 成交量文件不是 CSV")
    df = pd.read_csv(io.StringIO(text))
    cols = {c.strip(): c for c in df.columns}
    day_col = cols.get("Day") or cols.get("Date") or cols.get("date")
    part_col = cols.get("Market Participant")
    share_col = cols.get("Total Shares")
    if not day_col or not part_col or not share_col:
        raise ValueError("Cboe 成交量文件缺少必要列")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[day_col], errors="coerce"),
        "participant": df[part_col].astype(str),
        "shares": pd.to_numeric(df[share_col], errors="coerce"),
    }).dropna(subset=["date", "shares"])
    return out


def offexchange_daily(volume: pd.DataFrame | None) -> pd.DataFrame:
    if volume is None or volume.empty:
        return pd.DataFrame(columns=["date", "off_shares", "total_shares", "share"])
    rows = []
    for dt, g in volume.groupby("date", sort=True):
        tot = float(g["shares"].sum())
        mask = g["participant"].map(is_offexchange_venue)
        if not bool(mask.any()) or not tot:
            # 识别不到 TRF/ADF 场所时不把占比写成 0
            rows.append({"date": pd.Timestamp(dt), "off_shares": None, "total_shares": tot, "share": None})
            continue
        off = float(g.loc[mask, "shares"].sum())
        rows.append({
            "date": pd.Timestamp(dt),
            "off_shares": off,
            "total_shares": tot,
            "share": off / tot,
        })
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def volume_years_needed(today: date | None = None) -> list[int]:
    today = today or date.today()
    years = {today.year, today.year - 1, *EVENT_VOLUME_YEARS}
    return [y for y in sorted(years) if 2009 <= y <= today.year]


def fetch_dix(http_get) -> pd.DataFrame:
    raw = http_get(DIX_URL, timeout=30)
    return parse_dix_csv(raw)


def fetch_cboe_volume(http_get, today: date | None = None) -> pd.DataFrame:
    frames = []
    errors = []
    for year in volume_years_needed(today):
        url = CBOE_VOLUME_CSV.format(year=year)
        try:
            raw = http_get(
                url,
                timeout=30,
                headers={"Referer": CBOE_VOLUME_PAGE, "Accept": "text/csv,text/plain,*/*"},
            )
            frames.append(parse_cboe_market_history(raw))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{year}: {type(exc).__name__}: {str(exc)[:80]}")
    if not frames:
        raise RuntimeError("Cboe 场外成交量文件不可用：" + "；".join(errors[:4]))
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "participant"], keep="last")
    return out


def fetch_finra_ats_weekly(http_post, asof: date | None = None) -> dict:
    """拉取最近一周的 ATS 总量、前几大 ATS，以及 SPY/QQQ 的 ATS 周成交。"""
    asof = asof or date.today()
    start = (asof - timedelta(days=400)).isoformat()
    vol = http_post(
        FINRA_WEEKLY_API,
        {
            "limit": 5000,
            "compareFilters": [
                {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": "ATS_W_VOL_STATS"},
                {"compareType": "GREATER", "fieldName": "weekStartDate", "fieldValue": start},
            ],
        },
        timeout=40,
    )
    if not isinstance(vol, list) or not vol:
        raise ValueError("FINRA ATS 周度总量无数据")
    # 用 NMS 上市股票：CTS（Tape A/B）+ UTP（Tape C）；排除 OTCE
    nms = [r for r in vol if (r.get("productTypeCode") or "") in ("CTS", "UTP")]
    if not nms:
        raise ValueError("FINRA ATS 周度缺少 CTS/UTP")
    week = max(str(r.get("weekStartDate") or "") for r in nms)
    week_rows = [r for r in nms if str(r.get("weekStartDate")) == week]
    shares = sum(float(r.get("totalWeeklyShareQuantity") or 0) for r in week_rows)
    notional = sum(float(r.get("totalNotionalSum") or 0) for r in week_rows)
    trades = sum(float(r.get("totalWeeklyTradeCount") or 0) for r in week_rows)
    published = max((r.get("initialPublishedDate") or "") for r in week_rows) or None
    updated = max((r.get("lastUpdateDate") or "") for r in week_rows) or None

    firm = http_post(
        FINRA_WEEKLY_API,
        {
            "limit": 5000,
            "compareFilters": [
                {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": "ATS_W_FIRM"},
                {"compareType": "EQUAL", "fieldName": "weekStartDate", "fieldValue": week},
            ],
        },
        timeout=40,
    )
    top = []
    if isinstance(firm, list):
        buckets: dict[str, dict] = {}
        for r in firm:
            if r.get("tierIdentifier") not in ("T1", "T2"):
                continue
            mpid = str(r.get("MPID") or "")
            if not mpid:
                continue
            b = buckets.setdefault(mpid, {"mpid": mpid, "name": r.get("marketParticipantName") or mpid, "shares": 0.0, "notional": 0.0})
            b["shares"] += float(r.get("totalWeeklyShareQuantity") or 0)
            b["notional"] += float(r.get("totalNotionalSum") or 0)
            if r.get("marketParticipantName"):
                b["name"] = r["marketParticipantName"]
        tot = sum(b["shares"] for b in buckets.values()) or shares
        top = sorted(buckets.values(), key=lambda x: -x["shares"])[:8]
        for b in top:
            b["share_of_ats"] = _r(b["shares"] / tot, 4) if tot else None
            b["share_of_ats_pct"] = _r((b["shares"] / tot) * 100.0, 2) if tot else None
            b["shares"] = _r(b["shares"], 0)
            b["notional"] = _r(b["notional"], 0)

    def _symbol(sym: str) -> dict:
        rows = http_post(
            FINRA_WEEKLY_API,
            {
                "limit": 20,
                "compareFilters": [
                    {"compareType": "EQUAL", "fieldName": "issueSymbolIdentifier", "fieldValue": sym},
                    {"compareType": "EQUAL", "fieldName": "weekStartDate", "fieldValue": week},
                    {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": "ATS_W_SMBL"},
                ],
            },
            timeout=30,
        )
        if not isinstance(rows, list) or not rows:
            return {"status": "missing", "symbol": sym, "reason": "无数据"}
        sh = sum(float(r.get("totalWeeklyShareQuantity") or 0) for r in rows)
        notion = sum(float(r.get("totalNotionalSum") or 0) for r in rows)
        return {
            "status": "ok",
            "symbol": sym,
            "week_start": week,
            "ats_shares": _r(sh, 0),
            "ats_notional": _r(notion, 0),
            "total_volume_share_pct": None,
            "total_volume_share_reason": "未并入该 ETF 全市场综合成交，占比无数据",
        }

    week_end = (date.fromisoformat(week) + timedelta(days=4)).isoformat() if week else None
    lag_days = None
    if published:
        try:
            lag_days = (date.fromisoformat(published) - date.fromisoformat(week)).days
        except ValueError:
            lag_days = None
    return {
        "status": "ok",
        "week_start": week,
        "week_end": week_end,
        "week_label": f"{week} 当周" if week else None,
        "initial_published": published,
        "last_update": updated,
        "lag_days": lag_days,
        "lag_note": (
            f"周次 {week}（周一至周五约 {week_end}），首次公布 {published or '无数据'}"
            + (f"，滞后约 {lag_days} 天" if lag_days is not None else "")
            + "。FINRA ATS 周报通常延迟约 2–5 周。"
        ),
        "nms_ats_shares": _r(shares, 0),
        "nms_ats_notional": _r(notional, 0),
        "nms_ats_trades": _r(trades, 0),
        "units": "股数与名义金额为当周 NMS（CTS+UTP）ATS 合计",
        "top_ats": top,
        "spy": _symbol("SPY"),
        "qqq": _symbol("QQQ"),
        "source": "FINRA OTC Transparency API weeklySummary",
        "note": ATS_NOTE,
    }


def _spark(series: pd.Series, n: int = 20, ndigits: int = 4) -> list[dict]:
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index().tail(n)
    out = []
    for idx, val in s.items():
        d = _to_date(idx)
        if d is None:
            continue
        out.append({"asof": str(d), "value": _r(val, ndigits)})
    return out


def _metric_block(series: pd.Series | None, *, kind: str, asof: date, scale: float = 1.0, digits: int = 4) -> dict:
    if series is None or series.empty:
        return {"status": "missing", "reason": "无数据", "kind": kind}
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if s.empty:
        return {"status": "missing", "reason": "无数据", "kind": kind}
    last_d, last_v = _to_date(s.index[-1]), float(s.iloc[-1])
    prev_v = float(s.iloc[-2]) if len(s) >= 2 else None
    chg = (last_v - prev_v) if prev_v is not None else None
    chg_pct = (last_v / prev_v - 1.0) if prev_v not in (None, 0) else None
    window = s[s.index >= (pd.Timestamp(last_d) - pd.Timedelta(days=365))]
    pctile, n = percentile_of(window, last_v)
    out = {
        "status": "ok",
        "kind": kind,
        "asof": str(last_d),
        "value": _r(last_v, digits),
        "prev_asof": str(_to_date(s.index[-2])) if len(s) >= 2 else None,
        "prev": _r(prev_v, digits) if prev_v is not None else None,
        "chg_1d": _r(chg, digits) if chg is not None else None,
        "chg_1d_pct": _r(chg_pct, 4) if chg_pct is not None else None,
        "percentile_1y": pctile,
        "percentile_sample": n,
        "spark": _spark(s, 20, digits),
    }
    if scale != 1.0:
        out["value_scaled"] = _r(last_v / scale, 3)
        out["chg_1d_scaled"] = _r((chg / scale) if chg is not None else None, 3)
        out["scale"] = scale
    return out


def build_dix_gex(dix_df: pd.DataFrame | None) -> dict:
    if dix_df is None or dix_df.empty:
        return {"status": "missing", "reason": "无数据", "block": "dix_gex"}
    df = dix_df.copy()
    df = df.dropna(subset=["date"]).set_index(pd.DatetimeIndex(df["date"]))
    dix = _metric_block(df["dix"], kind="dix", asof=_to_date(df.index[-1]) or date.today(), digits=6)
    gex = _metric_block(df["gex"], kind="gex", asof=_to_date(df.index[-1]) or date.today(), scale=1e9, digits=0)
    if dix.get("status") == "ok":
        dix["value_pct"] = _r((dix["value"] or 0) * 100.0, 2)
        dix["prev_pct"] = _r((dix["prev"] or 0) * 100.0, 2) if dix.get("prev") is not None else None
        dix["chg_1d_pp"] = _r((dix["chg_1d"] or 0) * 100.0, 2) if dix.get("chg_1d") is not None else None
    if gex.get("status") == "ok":
        gex["value_billion"] = gex.get("value_scaled")
        gex["chg_1d_billion"] = gex.get("chg_1d_scaled")
        # 负值之间的百分比易误导，界面只用十亿美元变化
        gex["chg_1d_pct"] = None
    asof = dix.get("asof") or gex.get("asof")
    return {
        "status": "ok" if dix.get("status") == "ok" else "missing",
        "block": "dix_gex",
        "asof": asof,
        "dix": dix,
        "gex": gex,
        "history_start": str(_to_date(df.index[0])),
        "note": DIX_NOTE,
    }


def build_offexchange(volume: pd.DataFrame | None) -> dict:
    daily = offexchange_daily(volume)
    if daily.empty:
        return {"status": "missing", "reason": "无数据", "block": "off_exchange"}
    s = daily.set_index("date")["share"]
    m = _metric_block(s, kind="off_exchange", asof=_to_date(daily["date"].iloc[-1]) or date.today(), digits=6)
    if m.get("status") != "ok":
        return {**m, "block": "off_exchange"}
    last = daily.iloc[-1]
    m.update({
        "block": "off_exchange",
        "share": m["value"],
        "share_pct": _r((m["value"] or 0) * 100.0, 2),
        "chg_1d_pp": _r((m["chg_1d"] or 0) * 100.0, 2) if m.get("chg_1d") is not None else None,
        "off_shares": _r(last["off_shares"], 0),
        "total_shares": _r(last["total_shares"], 0),
        "venues": "FINRA / Nasdaq TRF Carteret (DQ)、FINRA / Nasdaq TRF Chicago (DB)、FINRA / NYSE TRF (DN)",
        "note": OFFEX_NOTE,
        "history_start": str(_to_date(daily["date"].iloc[0])),
    })
    return m


def dark_at(asof: date, dix_df: pd.DataFrame | None, offex_daily: pd.DataFrame | None) -> dict:
    """峰值日或谷底日的 DIX/GEX 与场外占比；取不到为无数据。"""
    out: dict[str, Any] = {"asof_requested": str(asof)}
    dix_s = gex_s = None
    if dix_df is not None and not dix_df.empty:
        dfi = dix_df.dropna(subset=["date"]).set_index(pd.DatetimeIndex(dix_df["date"]))
        dix_s, gex_s = dfi["dix"], dfi["gex"] if "gex" in dfi else None
    d = close_on_or_before(dix_s, asof) if dix_s is not None else None
    g = close_on_or_before(gex_s, asof) if gex_s is not None else None
    if d:
        out["dix"] = {"status": "ok", "asof": str(d[0]), "value": _r(d[1], 6), "value_pct": _r(d[1] * 100.0, 2)}
    else:
        out["dix"] = {"status": "missing", "reason": "无数据（DIX 约自 2011-05 起）"}
    if g:
        out["gex"] = {
            "status": "ok", "asof": str(g[0]), "value": _r(g[1], 0),
            "value_billion": _r(g[1] / 1e9, 3),
        }
    else:
        out["gex"] = {"status": "missing", "reason": "无数据（GEX 约自 2011-05 起）"}
    off_s = None
    if offex_daily is not None and not offex_daily.empty:
        off_s = offex_daily.set_index("date")["share"]
    o = close_on_or_before(off_s, asof) if off_s is not None else None
    if o:
        out["off_exchange"] = {"status": "ok", "asof": str(o[0]), "share": _r(o[1], 6), "share_pct": _r(o[1] * 100.0, 2)}
    else:
        out["off_exchange"] = {"status": "missing", "reason": "无数据（Cboe 场外统计约自 2009 年起，且需有该年文件）"}
    return out


def attach_dark_to_cycles(event_context: dict | None, dix_df: pd.DataFrame | None, volume: pd.DataFrame | None) -> dict | None:
    if not event_context or event_context.get("status") != "ok":
        return event_context
    daily = offexchange_daily(volume) if volume is not None else None
    out = dict(event_context)
    cycles = []
    for c in out.get("cycles") or []:
        row = dict(c)
        peak = _to_date(c.get("peak_date"))
        trough = _to_date(c.get("trough_date"))
        row["dark"] = {
            "peak": dark_at(peak, dix_df, daily) if peak else {"dix": {"status": "missing", "reason": "无数据"}},
            "trough": dark_at(trough, dix_df, daily) if trough else {"dix": {"status": "missing", "reason": "无数据"}},
        }
        cycles.append(row)
    out["cycles"] = cycles
    return out


def judge_dark(dix_block: dict | None, gex_block: dict | None) -> dict:
    """综合判断用的暗盘信号。缺数据则该项为无数据。"""
    dix = dix_block or {}
    gex = gex_block or {}
    band = None
    if dix.get("status") == "ok":
        p = dix.get("percentile_1y")
        if p is not None:
            if p >= 80:
                band = "偏多"
            elif p <= 20:
                band = "偏少"
            else:
                band = "中性"
    parts = []
    if band == "偏多":
        parts.append("暗池买盘偏多")
    elif band == "偏少":
        parts.append("暗池买盘偏少")
    elif band == "中性":
        parts.append("暗池买盘中性")
    else:
        parts.append("暗盘无数据")
    gex_sign = None
    if gex.get("status") == "ok" and gex.get("value") is not None:
        if gex["value"] < 0:
            gex_sign = "负"
            parts.append("GEX 为负（波动或更易放大）")
        elif gex["value"] > 0:
            gex_sign = "正"
            parts.append("GEX 为正（波动或更易被抑制）")
        else:
            gex_sign = "零"
            parts.append("GEX 为零")
    return {
        "dix_band": band,
        "gex_sign": gex_sign,
        "line": "，".join(parts),
        "rules": "暗盘：DIX 近 1 年分位≥80% 为暗池买盘偏多，≤20% 为偏少，否则中性；再注明 GEX 正负。缺数据不填造。",
    }


def build_dark_payload(
    *,
    dix_df: pd.DataFrame | None = None,
    volume: pd.DataFrame | None = None,
    ats_weekly: dict | None = None,
    errors: dict | None = None,
) -> dict:
    dix_gex = build_dix_gex(dix_df)
    offex = build_offexchange(volume)
    ats = ats_weekly if ats_weekly and ats_weekly.get("status") == "ok" else {
        "status": "missing",
        "reason": (errors or {}).get("finra_ats") or "无数据",
        "note": ATS_NOTE,
    }
    asofs = [b.get("asof") for b in (dix_gex, offex) if b.get("status") == "ok" and b.get("asof")]
    ok = any(b.get("status") == "ok" for b in (dix_gex, offex, ats))
    if not ok:
        return {"status": "missing", "reason": "无数据", "block": "dark"}
    return {
        "status": "ok",
        "block": "dark",
        "asof": max(asofs) if asofs else ats.get("week_start"),
        "dix_gex": dix_gex,
        "off_exchange": offex,
        "ats_weekly": ats,
        "method": DIX_NOTE + OFFEX_NOTE + ATS_NOTE,
    }
