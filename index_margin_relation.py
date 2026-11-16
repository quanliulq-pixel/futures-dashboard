# -*- coding: utf-8 -*-
"""沪深300 / 上证50 / 中证1000：两融余额、两融比例历史与指数收盘的关系（研究参考，不能下单）。

纯计算，不联网。输入为 index_margin 的逐日点（亿元 / %）与新浪日线收盘。
- 分位：最新值在窗口内的百分位（<= 最新值的样本占比）。
- 历史高/低：余额与比例各自的最高/最低（并列取最早日期），附当日指数收盘（只取同日，缺则 None）。
- 相关：同日对齐。水平相关=余额与收盘的 Pearson；20日变化相关=余额 20 日变化率与指数 20 日涨跌幅
  （每 5 个交易日取一个样本，减少重叠）；周相关=周末实际交易日收益率相关。
- 领先滞后：余额过去 20 日变化率 vs 指数未来 20 日涨跌幅（同样每 5 日取样）。
样本少于 MIN_N 一律 None（无数据），不插补。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from margin_history import MARGIN_LOW_START

WINDOWS = {"1y": 365, "3y": 365 * 3, "all": None}
MIN_N = 20
CHG_N = 20
STEP = 5
NOTE = ("研究参考，不能下单。相关不代表因果；20日变化每5个交易日取一个样本；"
        "中证1000为当前成分股回溯，调样后历史有偏差。")


def _r(x: float | None, n: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < MIN_N:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    return sxy / math.sqrt(sxx * syy)


def percentile(values: list[float], latest: float | None) -> float | None:
    vals = [v for v in values if v is not None]
    if latest is None or len(vals) < MIN_N:
        return None
    return 100.0 * sum(1 for v in vals if v <= latest) / len(vals)


def _extreme(rows: list[tuple[date, float]], want_max: bool) -> tuple[date, float] | None:
    best = None
    for d, v in rows:
        if v is None:
            continue
        if best is None or (v > best[1] if want_max else v < best[1]):
            best = (d, v)
    return best


def _pt(ext, closes: dict[date, float]) -> dict | None:
    if not ext:
        return None
    d, v = ext
    return {"date": d.isoformat(), "value": _r(v), "index_close": _r(closes.get(d), 2)}


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _iso_week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def window_stats(rows: list[tuple[date, float, float | None, float | None]], start: date | None) -> dict:
    """rows: (date, margin, ratio, close) aligned, sorted. close may be None."""
    sub = [r for r in rows if start is None or r[0] >= start]
    out: dict[str, Any] = {"n": len(sub), "start": sub[0][0].isoformat() if sub else None,
                           "end": sub[-1][0].isoformat() if sub else None}
    if not sub:
        return out
    last = sub[-1]
    out["margin_pctile"] = _r(percentile([r[1] for r in sub], last[1]), 1)
    out["ratio_pctile"] = _r(percentile([r[2] for r in sub], last[2]), 1)
    both = [r for r in sub if r[3] is not None]
    out["n_aligned"] = len(both)
    out["corr_level"] = _r(pearson([r[1] for r in both], [r[3] for r in both]), 3)
    out["corr_ratio_level"] = _r(pearson([r[2] for r in both], [r[3] for r in both]), 3)
    # 20 日变化（每 STEP 日取样）
    m20, i20, lead_m, lead_i = [], [], [], []
    for k in range(CHG_N, len(both), STEP):
        m20.append(_pct(both[k][1], both[k - CHG_N][1]))
        i20.append(_pct(both[k][3], both[k - CHG_N][3]))
        if k + CHG_N < len(both):
            lead_m.append(m20[-1])
            lead_i.append(_pct(both[k + CHG_N][3], both[k][3]))
    out["corr_chg20"] = _r(pearson(m20, i20), 3)
    out["n_chg20"] = len(m20)
    out["corr_lead20"] = _r(pearson(lead_m, lead_i), 3)
    out["n_lead20"] = len(lead_m)
    # 周收益相关（每周最后一个实际交易日）
    wk: dict[tuple[int, int], tuple] = {}
    for r in both:
        wk[_iso_week(r[0])] = r
    w = [wk[k] for k in sorted(wk)]
    wm = [_pct(w[i][1], w[i - 1][1]) for i in range(1, len(w))]
    wi = [_pct(w[i][3], w[i - 1][3]) for i in range(1, len(w))]
    out["corr_weekly"] = _r(pearson(wm, wi), 3)
    out["n_weekly"] = len(wm)
    return out


def relation_for_index(points: list, closes: list[tuple[date, float]]) -> dict:
    """points: index_margin.DayPoint list. closes: [(date, close)]."""
    cmap = {d: c for d, c in closes or []}
    rows = sorted((p.d, p.margin, p.margin_ratio, cmap.get(p.d)) for p in points or [])
    if not rows:
        return {"status": "missing", "reason": "无数据", "note": NOTE}
    end = rows[-1][0]
    windows = {}
    for key, days in WINDOWS.items():
        start = None if days is None else end - timedelta(days=days)
        windows[key] = window_stats(rows, start)
    m = [(r[0], r[1]) for r in rows]
    q = [(r[0], r[2]) for r in rows]
    # 历史最高用全部覆盖；历史最低自 2014-09-22（两融扩容至 900 只）起，避免试点初期极小余额。
    # 若序列起点晚于该日（如中证1000），则用序列自身起点。
    low_from = max(rows[0][0], MARGIN_LOW_START)
    m_low = [(d, v) for d, v in m if d >= low_from]
    q_low = [(d, v) for d, v in q if d >= low_from]
    return {
        "status": "ok",
        "asof": end.isoformat(),
        "coverage_start": rows[0][0].isoformat(),
        "low_coverage_start": low_from.isoformat(),
        "n": len(rows),
        "latest": {"margin": _r(rows[-1][1]), "ratio": _r(rows[-1][2]), "index_close": _r(rows[-1][3], 2)},
        "margin_high": _pt(_extreme(m, True), cmap),
        "margin_low": _pt(_extreme(m_low, False), cmap),
        "ratio_high": _pt(_extreme(q, True), cmap),
        "ratio_low": _pt(_extreme(q_low, False), cmap),
        "windows": windows,
        "note": NOTE,
    }


def chart_series(points: list, closes: list[tuple[date, float]]) -> list[list]:
    """[[date, margin_yi, ratio_pct, close_or_null], ...]"""
    cmap = {d: c for d, c in closes or []}
    return [[p.d.isoformat(), _r(p.margin), _r(p.margin_ratio), _r(cmap.get(p.d), 2)]
            for p in sorted(points or [], key=lambda x: x.d)]


__all__ = ["pearson", "percentile", "window_stats", "relation_for_index", "chart_series", "NOTE"]
