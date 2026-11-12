# -*- coding: utf-8 -*-
"""Semi-annual institutional ETF holdings vs A-share index returns (display only).

Request threads load the local seed only and never hit the network.
Background refresh may rebuild the series from already-fetched F10 cyrjg rows
plus Sina index klines; on failure the seed is kept. Never blanks 主力/申赎/holders.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cn_index_etf_holders import (
    HOLDER_INDICES,
    complete_rows,
    funds_from_payload,
    load_seed_payload,
)

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
HISTORY_SEED_PATHS = (
    ROOT / "data" / "cn_index_etf_holders_history.json",
    ROOT / "docs" / "api" / "cn_index_etf_holders_history.json",
)

INDEX_META: list[dict[str, str]] = [
    {"id": "csi300", "name": "沪深300", "index_code": "000300", "sina": "sh000300"},
    {"id": "sse50", "name": "上证50", "index_code": "000016", "sina": "sh000016"},
    {"id": "csi500", "name": "中证500", "index_code": "000905", "sina": "sh000905"},
    {"id": "csi1000", "name": "中证1000", "index_code": "000852", "sina": "sh000852"},
    {"id": "star50", "name": "科创50", "index_code": "000688", "sina": "sh000688"},
    {"id": "chinext", "name": "创业板指", "index_code": "399006", "sina": "sz399006"},
]
INDEX_META_BY_ID = {m["id"]: m for m in INDEX_META}

SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_DATALEN = 8000
SINA_TIMEOUT = 15
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")

PUBLISHED_NOTES = [
    "样本ETF的机构持仓变化和指数同期涨跌没有稳定关系，也不能当作下一期的交易规则。",
    "合样本同期：n=154，Pearson r=+0.074（p=0.36），同向 87/154（56%）。按半年把六个指数等权平均后，同期相关仍不显著（n=42，Pearson +0.087，p=0.58）。",
    "对下一期完整半年（不含 2026-06-30 之后未完结窗口），等权平均相关系数 +0.307（p=0.051），只是略偏正、处在边缘；单指数下一期 Pearson 全部不显著。",
    "2026年上半年多为反向：主要宽基ETF机构份额大幅下降，但同期中小盘、创业板、科创明显上涨。2026-06-30 至 2026-11-11 的窗口尚未走完，不能用来证明「减持预示下跌」。",
    "半年频、样本小；披露大约滞后 1–2 个月，不是当日可交易信号。篮子是主要跟踪ETF，不是全市场。",
    "双轴图左右刻度不同，看起来同向并不代表相关，请以散点图和相关系数为准。只展示，不计入评分。",
]

_lock = threading.Lock()
_cache: dict | None = None


def _r(x: float | None, n: int = 6) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or math.isinf(v):
        return None
    return round(v, n)


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s if fmt != "%Y%m%d" else s[:8], fmt).date()
        except ValueError:
            continue
    return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _period(d: date) -> str:
    return f"{d.year}H1" if d.month <= 6 else f"{d.year}H2"


def _is_period_end(d: date) -> bool:
    return (d.month, d.day) in {(6, 30), (12, 31)}


def close_on_or_before(series: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    lo, hi = 0, len(series) - 1
    ans = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= target:
            ans = series[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def parse_sina_index_kline(js: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for row in js or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("day") or row.get("date"))
        try:
            c = float(row["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if d is not None and c == c:
            out.append((d, c))
    out.sort()
    return out


def _etfs_for(index_id: str) -> list[str]:
    for meta in HOLDER_INDICES:
        if meta["id"] == index_id:
            return [c for c, _ in meta["etfs"]]
    return []


def build_history_from_funds_and_klines(
    funds: dict[str, list[dict]] | None,
    klines_by_id: dict[str, list[tuple[date, float]]] | None,
    *,
    stats: dict | None = None,
    notes: list[str] | None = None,
    fetched_at: str | None = None,
    partial_asof: str | date | None = None,
) -> dict:
    """Pure rebuild: F10 complete rows + index klines. No network. No ETF prices."""
    rows_by_code: dict[str, dict[date, dict]] = {}
    for code, rows in (funds or {}).items():
        by_d: dict[date, dict] = {}
        for r in complete_rows(rows):
            by_d[r["date"]] = r
        rows_by_code[str(code)] = by_d

    last_days = [s[-1][0] for s in (klines_by_id or {}).values() if s]
    inferred_partial = min(last_days) if last_days else None
    partial = _to_date(partial_asof) or inferred_partial

    indices: dict[str, Any] = {}
    for meta in INDEX_META:
        iid = meta["id"]
        codes = _etfs_for(iid)
        series_px = list((klines_by_id or {}).get(iid) or [])
        series_px.sort()
        dates = sorted({
            d for code in codes for d in (rows_by_code.get(code) or {})
            if _is_period_end(d)
        })
        levels: list[dict] = []
        for d in dates:
            present = []
            for code in codes:
                row = (rows_by_code.get(code) or {}).get(d)
                if row is not None:
                    present.append((code, row))
            if not present:
                continue
            inst_shares = sum(r["inst_shares"] for _, r in present)
            total_shares = sum(r["total_shares"] for _, r in present)
            inst_pct = (inst_shares / total_shares * 100.0) if total_shares else None
            ix = close_on_or_before(series_px, d) if series_px else None
            levels.append({
                "report_date": d,
                "period": _period(d),
                "inst_shares": inst_shares,
                "inst_pct": inst_pct,
                "total_shares": total_shares,
                "index_close_date": ix[0] if ix else None,
                "index_close": ix[1] if ix else None,
                "n_funds": len(present),
                "funds": [c for c, _ in present],
            })

        n = len(levels)
        out_levels: list[dict] = []
        for i, cur in enumerate(levels):
            d_shares = d_pct = n_overlap = None
            if i > 0:
                prev = levels[i - 1]
                both = [c for c in cur["funds"] if c in prev["funds"]]
                if both:
                    def _sum(level_date, codes_):
                        inst = total = 0.0
                        for code in codes_:
                            row = (rows_by_code.get(code) or {}).get(level_date)
                            if row is None:
                                continue
                            inst += row["inst_shares"]
                            total += row["total_shares"]
                        return inst, total

                    p_inst, p_total = _sum(prev["report_date"], both)
                    c_inst, c_total = _sum(cur["report_date"], both)
                    d_shares = c_inst - p_inst
                    if p_total and c_total:
                        d_pct = (c_inst / c_total * 100.0) - (p_inst / p_total * 100.0)
                    n_overlap = len(both)
            same_ret = None
            if i > 0 and cur.get("index_close") and levels[i - 1].get("index_close"):
                same_ret = cur["index_close"] / levels[i - 1]["index_close"] - 1.0
            next_ret = next_kind = next_end = None
            if i + 1 < n and cur.get("index_close") and levels[i + 1].get("index_close"):
                next_ret = levels[i + 1]["index_close"] / cur["index_close"] - 1.0
                next_kind = "full"
                next_end = levels[i + 1]["index_close_date"]
            elif i + 1 == n and partial and cur.get("index_close") and cur["report_date"] < partial:
                later = close_on_or_before(series_px, partial)
                if later and later[0] > (cur["index_close_date"] or cur["report_date"]):
                    next_ret = later[1] / cur["index_close"] - 1.0
                    next_kind = "partial"
                    next_end = later[0]

            cur_out = {
                "report_date": _iso(cur["report_date"]),
                "period": cur["period"],
                "inst_shares": _r(cur["inst_shares"], 6),
                "inst_pct": _r(cur["inst_pct"], 6),
                "total_shares": _r(cur["total_shares"], 6),
                "index_close_date": _iso(cur["index_close_date"]),
                "index_close": _r(cur["index_close"], 6),
                "d_inst_shares": _r(d_shares, 6),
                "d_inst_pct": _r(d_pct, 6),
                "same_ret": _r(same_ret, 8),
                "next_ret": _r(next_ret, 8),
                "next_ret_kind": next_kind,
                "next_end_date": _iso(next_end) if isinstance(next_end, date) else next_end,
                "n_funds": cur["n_funds"],
                "n_overlap": n_overlap,
            }
            out_levels.append(cur_out)

        indices[iid] = {
            "id": iid,
            "name": meta["name"],
            "index_code": meta["index_code"],
            "sina_symbol": meta["sina"],
            "series": out_levels,
        }

    seed = load_history_seed() or {}
    return {
        "status": "ok" if any(v.get("series") for v in indices.values()) else "missing",
        "in_score": False,
        "fetched_at": fetched_at or datetime.now(CST).isoformat(timespec="seconds"),
        "partial_asof": _iso(partial),
        "mark_date": "2026-06-30",
        "source": "天天基金 F10 持有人结构 + 新浪指数日线",
        "source_url": "https://fundf10.eastmoney.com/",
        "function": "fundf10 cyrjg + money.finance.sina.com.cn CN_MarketData.getKLineData",
        "method": "水平（占比/份额）用当期有数据的跟踪ETF合计；变化只用相邻两期都在的重叠篮子。指数收盘取报告日或之前最近交易日。同期涨跌=相邻报告期收盘比；下一期完整半年不含未完结窗口。",
        "level_rule": "funds present on the report date",
        "change_rule": "overlapping basket only",
        "order": [m["id"] for m in INDEX_META],
        "indices": indices,
        "stats": stats if stats is not None else (seed.get("stats") or {}),
        "notes": list(notes if notes is not None else (seed.get("notes") or PUBLISHED_NOTES)),
    }


# ---------------------------------------------------------------- seed / cache (no network)
def load_history_seed() -> dict | None:
    for path in HISTORY_SEED_PATHS:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("indices"):
                return data
        except Exception:  # noqa: BLE001
            logger.debug("holder history seed load failed %s", path, exc_info=True)
    return None


def save_history_seed(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    for path in HISTORY_SEED_PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body + "\n", encoding="utf-8")
        except OSError:
            logger.debug("holder history seed write skipped %s", path, exc_info=True)


def missing_history(reason: str = "无本地种子且未抓取") -> dict:
    return {
        "status": "missing",
        "reason": reason,
        "in_score": False,
        "indices": {},
        "order": [m["id"] for m in INDEX_META],
        "notes": list(PUBLISHED_NOTES),
        "stats": {},
        "mark_date": "2026-06-30",
    }


def load_history_bundle() -> dict:
    """Load seed only (no network). Safe for request threads."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    seed = load_history_seed()
    bundle = seed if seed else missing_history()
    with _lock:
        _cache = bundle
    return bundle


def _set_cache(bundle: dict) -> dict:
    global _cache
    with _lock:
        _cache = bundle
    return bundle


def attach_holder_history(payload: dict | None, history: dict | None = None) -> dict | None:
    """Attach history at the top level. Never mutates or blanks index_flows fields."""
    if not isinstance(payload, dict):
        return payload
    try:
        hist = history if history is not None else load_history_bundle()
    except Exception:  # noqa: BLE001
        logger.debug("load holder history failed", exc_info=True)
        return payload
    if not hist:
        return payload
    out = dict(payload)
    out["holder_history"] = hist
    return out


# ---------------------------------------------------------------- background refresh (optional)
def fetch_sina_index_kline(symbol: str, datalen: int = SINA_DATALEN) -> list[tuple[date, float]]:
    import requests

    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": datalen}
    headers = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}
    r = requests.get(SINA_KLINE_URL, params=params, headers=headers, timeout=SINA_TIMEOUT)
    r.raise_for_status()
    return parse_sina_index_kline(r.json())


def refresh_history_bundle(*, live_klines: bool = False, klines_by_id: dict | None = None) -> dict:
    """Rebuild from already-fetched F10 rows + optional Sina index klines.

    On failure keep the existing seed. Never raises a blank bundle.
    """
    seed = load_history_seed()
    if os.environ.get("SKIP_INDEX_HOLDERS") == "1":
        return seed or missing_history("跳过更新")
    try:
        funds_payload = load_seed_payload() or {}
        funds = funds_from_payload(funds_payload)
        if not funds:
            return _set_cache(seed) if seed else missing_history("无持仓种子，无法重建历史")
        kl = dict(klines_by_id or {})
        if live_klines:
            for meta in INDEX_META:
                if kl.get(meta["id"]):
                    continue
                try:
                    kl[meta["id"]] = fetch_sina_index_kline(meta["sina"])
                except Exception as exc:  # noqa: BLE001
                    logger.info("sina index kline failed %s: %s", meta["sina"], exc)
        if not any(kl.values()):
            # No klines → keep seed (do not drop index closes / returns)
            return _set_cache(seed) if seed else missing_history("无指数日线，保留原种子")
        built = build_history_from_funds_and_klines(
            funds,
            kl,
            stats=(seed or {}).get("stats"),
            notes=(seed or {}).get("notes") or PUBLISHED_NOTES,
            fetched_at=datetime.now(CST).isoformat(timespec="seconds"),
        )
        if built.get("status") == "ok" and any((v.get("series") or []) for v in built["indices"].values()):
            save_history_seed(built)
            return _set_cache(built)
    except Exception:  # noqa: BLE001
        logger.warning("holder history refresh failed; keeping seed", exc_info=True)
    if seed:
        return _set_cache(seed)
    return missing_history("重建失败且无本地种子")


__all__ = [
    "HISTORY_SEED_PATHS", "INDEX_META", "PUBLISHED_NOTES",
    "load_history_seed", "save_history_seed", "load_history_bundle",
    "attach_holder_history", "build_history_from_funds_and_klines",
    "refresh_history_bundle", "parse_sina_index_kline", "close_on_or_before",
    "missing_history", "fetch_sina_index_kline",
]
