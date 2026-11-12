# -*- coding: utf-8 -*-
"""A股指数主要 ETF 的机构持仓（基金半年报/年报持有人结构）。

只做展示，不进评分。数据一年更新两次，页面加载不得依赖实时抓取。
机构持仓亿份 = 机构持有比例 × 总份额；绝不把「内部持有」加进机构。
单位是亿份，不同 ETF 净值不同，不能跨指数直接比规模。

数据源：天天基金 F10 FundArchivesDatas.aspx?type=cyrjg（HTTPS + Referer）。
不走东方财富 push2 / push2his。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
SEED_PATHS = (
    ROOT / "data" / "cn_index_etf_holders.json",
    ROOT / "docs" / "api" / "cn_index_etf_holders.json",
)
HTTP_TIMEOUT = 15
HTTP_RETRIES = 3
HOLDERS_SPACING_SEC = 0.6
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")

CYRJG_API = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
CYRJG_PAGE = "https://fundf10.eastmoney.com/cyrjg_{code}.html"

SRC = {
    "label": "天天基金 F10 持有人结构",
    "url": "https://fundf10.eastmoney.com/",
    "function": "fundf10.eastmoney.com FundArchivesDatas.aspx type=cyrjg",
}

# 比每日净申赎覆盖更广（含深市 ETF）。FLOW_INDICES.etfs 保持不变。
HOLDER_INDICES: list[dict] = [
    {"id": "csi300", "name": "沪深300", "etfs": [
        ("510300", "华泰柏瑞沪深300ETF"), ("510310", "易方达沪深300ETF"),
        ("510330", "华夏沪深300ETF"), ("159919", "嘉实沪深300ETF"),
        ("510360", "广发沪深300ETF"), ("515310", "汇添富沪深300ETF"),
        ("159925", "南方沪深300ETF"), ("510350", "工银沪深300ETF"),
    ]},
    {"id": "sse50", "name": "上证50", "etfs": [
        ("510050", "华夏上证50ETF"), ("510100", "易方达上证50ETF"),
        ("510800", "建信上证50ETF"), ("159711", "华泰柏瑞上证50ETF"),
    ]},
    {"id": "csi500", "name": "中证500", "etfs": [
        ("510500", "南方中证500ETF"), ("512500", "华夏中证500ETF"),
        ("159922", "嘉实中证500ETF"), ("510510", "广发中证500ETF"),
        ("510580", "易方达中证500ETF"),
    ]},
    {"id": "csi1000", "name": "中证1000", "etfs": [
        ("512100", "南方中证1000ETF"), ("560010", "广发中证1000ETF"),
        ("159845", "华夏中证1000ETF"), ("159633", "易方达中证1000ETF"),
    ]},
    {"id": "star50", "name": "科创50", "etfs": [
        ("588000", "华夏科创50ETF"), ("588080", "易方达科创50ETF"),
        ("588050", "工银科创50ETF"), ("588090", "华泰柏瑞科创50ETF"),
    ]},
    {"id": "chinext", "name": "创业板指", "etfs": [
        ("159915", "易方达创业板ETF"), ("159948", "南方创业板ETF"),
        ("159977", "天弘创业板ETF"), ("159952", "广发创业板ETF"),
    ]},
]

HOLDER_NOTES = [
    "机构ETF持仓来自基金半年报/年报持有人结构，不是每日数据；报告期见各卡片",
    "机构ETF持仓是主要跟踪ETF合计，不是该指数全市场所有ETF",
    "联接基金在东财口径里记在「内部持有」，有时与机构重叠、有时被拆成单独一栏，不把机构与内部再加总",
    "机构持仓单位是亿份，不同ETF净值不同，不要跨指数直接比较规模",
]

_lock = threading.Lock()
_cache: dict | None = None


def _r(x: float | None, n: int = 2) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "---", "—", "–", "None"):
        return None
    try:
        x = float(s)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


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


def _missing(reason: str, **kw) -> dict:
    out = {"status": "missing", "reason": reason,
           "source": SRC["label"], "source_url": SRC["url"]}
    out.update(kw)
    return out


def cyrjg_url(code: str) -> str:
    return CYRJG_PAGE.format(code=str(code).strip())


# ---------------------------------------------------------------- parser (pure)
def parse_cyrjg(text: str | None) -> list[dict]:
    """天天基金 F10 cyrjg JS/HTML → [{date, inst_pct, indiv_pct, internal_pct, total_shares}]，按日期升序。

    ``---`` 或缺失的比例/份额记为 None；无完整机构比例+总份额的行仍保留，聚合时再跳过。
    """
    if not text:
        return []
    raw = text
    m = re.search(r"content\s*:\s*\"(.*?)\"\s*,\s*summary", raw, re.S)
    if not m:
        m = re.search(r"content\s*:\s*\"(.*?)\"", raw, re.S)
    html = unescape(m.group(1) if m else raw)
    rows: list[dict] = []
    seen: set[date] = set()
    for tr in re.findall(r"<tr>(.*?)</tr>", html, flags=re.I | re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.I | re.S)
        if len(cells) < 5:
            continue
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        d = _to_date(cells[0])
        if d is None or d in seen:
            continue
        seen.add(d)
        rows.append({
            "date": d,
            "inst_pct": _num(cells[1]),
            "indiv_pct": _num(cells[2]),
            "internal_pct": _num(cells[3]),
            "total_shares": _num(cells[4]),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def complete_rows(rows: list[dict] | None) -> list[dict]:
    """只保留同时有机构持有比例和总份额的行（季报缺完整结构的不会进来）。"""
    out = []
    for r in rows or []:
        d = r["date"] if isinstance(r.get("date"), date) else _to_date(r.get("date"))
        inst, total = _num(r.get("inst_pct")), _num(r.get("total_shares"))
        if d is None or inst is None or total is None:
            continue
        out.append({
            "date": d,
            "inst_pct": inst,
            "indiv_pct": _num(r.get("indiv_pct")),
            "internal_pct": _num(r.get("internal_pct")),
            "total_shares": total,
            "inst_shares": inst / 100.0 * total,
        })
    out.sort(key=lambda r: r["date"])
    return out


def _mode_date(dates: list[date]) -> date | None:
    if not dates:
        return None
    cnt = Counter(dates)
    best = max(cnt.values())
    return max(d for d, c in cnt.items() if c == best)


def _sum_on(rows_by_code: dict[str, list[dict]], when: date, codes: list[str] | None = None) -> tuple[float, float, list[str]]:
    inst = total = 0.0
    used: list[str] = []
    for code, rows in rows_by_code.items():
        if codes is not None and code not in codes:
            continue
        row = next((r for r in rows if r["date"] == when), None)
        if row is None:
            continue
        # 只用机构比例 × 总份额，不加内部持有
        inst += row["inst_shares"]
        total += row["total_shares"]
        used.append(code)
    return inst, total, used


def aggregate_index(etfs: list[tuple[str, str]], funds: dict[str, list[dict]]) -> dict:
    """把若干只 ETF 的持有人结构加总到同一报告期。缺该期的基金不纳入，不记 0。"""
    names = {c: n for c, n in etfs}
    order = [c for c, _ in etfs]
    rows_by_code: dict[str, list[dict]] = {}
    for code, _name in etfs:
        rows_by_code[code] = complete_rows(funds.get(code) or [])
    all_dates = [r["date"] for rows in rows_by_code.values() for r in rows]
    if not all_dates:
        return _missing("接口无完整持有人结构（需机构比例与总份额）")
    report = max(all_dates)
    inst, total, used = _sum_on(rows_by_code, report)
    if not used:
        return _missing(f"{report.isoformat()} 无完整持有人结构")
    # 上一报告期：在已有本期数据的基金里，取其各自上一完整期的众数日期
    prev_candidates = []
    for code in used:
        earlier = [r["date"] for r in rows_by_code[code] if r["date"] < report]
        if earlier:
            prev_candidates.append(max(earlier))
    prev = _mode_date(prev_candidates)
    both = []
    if prev is not None:
        both = [c for c in used if any(r["date"] == prev for r in rows_by_code[c])]
    lead = used[0] if used else (order[0] if order else "")
    out: dict[str, Any] = {
        "status": "ok",
        "report_date": _iso(report),
        "prev_date": _iso(prev) if prev else None,
        "inst_shares": _r(inst, 2),
        "total_shares": _r(total, 2),
        "inst_pct": _r(inst / total * 100.0, 2) if total else None,
        "chg_shares": None,
        "chg_pct_pt": None,
        "unit": "亿份",
        "frequency": "半年报/年报",
        "coverage": f"{len(used)}/{len(etfs)} 只有数据",
        "codes": used,
        "source": SRC["label"],
        "source_url": cyrjg_url(lead) if lead else SRC["url"],
        "method": "半年报/年报持有人结构；机构持仓=机构持有比例×总份额；不把内部持有加进机构",
        "in_score": False,
    }
    if prev is not None and both:
        p_inst, p_total, _ = _sum_on(rows_by_code, prev, both)
        c_inst, c_total, _ = _sum_on(rows_by_code, report, both)
        out["chg_shares"] = _r(c_inst - p_inst, 2)
        c_pct = c_inst / c_total * 100.0 if c_total else None
        p_pct = p_inst / p_total * 100.0 if p_total else None
        out["chg_pct_pt"] = _r((c_pct - p_pct) if c_pct is not None and p_pct is not None else None, 2)
        out["prev_inst_shares"] = _r(p_inst, 2)
        out["prev_total_shares"] = _r(p_total, 2)
        out["prev_inst_pct"] = _r(p_pct, 2)
        out["chg_coverage"] = f"{len(both)}/{len(etfs)} 可比"
    items = []
    for code in order:
        name = names.get(code) or code
        rec = {"code": code, "name": name, "source_url": cyrjg_url(code)}
        row = next((r for r in rows_by_code.get(code) or [] if r["date"] == report), None)
        if row is None:
            rec.update({"status": "missing", "reason": f"无 {report.isoformat()} 完整记录"})
        else:
            rec.update({
                "status": "ok",
                "report_date": _iso(report),
                "inst_pct": _r(row["inst_pct"], 2),
                "total_shares": _r(row["total_shares"], 2),
                "inst_shares": _r(row["inst_shares"], 2),
                "internal_pct": _r(row.get("internal_pct"), 2),
            })
            prow = next((r for r in rows_by_code[code] if prev and r["date"] == prev), None)
            if prow:
                rec["prev_date"] = _iso(prev)
                rec["prev_inst_shares"] = _r(prow["inst_shares"], 2)
                rec["prev_inst_pct"] = _r(prow["inst_pct"], 2)
                rec["prev_total_shares"] = _r(prow["total_shares"], 2)
        items.append(rec)
    out["items"] = items
    return out


def serialize_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        d = r["date"] if isinstance(r.get("date"), date) else _to_date(r.get("date"))
        out.append({
            "date": _iso(d),
            "inst_pct": _num(r.get("inst_pct")),
            "indiv_pct": _num(r.get("indiv_pct")),
            "internal_pct": _num(r.get("internal_pct")),
            "total_shares": _num(r.get("total_shares")),
        })
    return out


def funds_from_payload(payload: dict | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for code, rec in ((payload or {}).get("funds") or {}).items():
        rows = rec.get("rows") if isinstance(rec, dict) else rec
        out[str(code)] = list(rows or [])
    return out


def build_holder_bundle(payload: dict | None, *, from_seed: bool = False) -> dict:
    funds = funds_from_payload(payload)
    indices = {}
    for meta in HOLDER_INDICES:
        indices[meta["id"]] = aggregate_index(meta["etfs"], funds)
    fetched_at = (payload or {}).get("fetched_at") or datetime.now(CST).isoformat(timespec="seconds")
    return {
        "fetched_at": fetched_at,
        "from_seed": from_seed,
        "source": SRC["label"],
        "source_url": SRC["url"],
        "function": SRC["function"],
        "indices": indices,
        "errors": dict((payload or {}).get("errors") or {}),
        "in_score": False,
        "notes": list(HOLDER_NOTES),
    }


# ---------------------------------------------------------------- seed / cache
def load_seed_payload() -> dict | None:
    for path in SEED_PATHS:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("funds"):
                return data
        except Exception:  # noqa: BLE001
            logger.debug("etf holders seed load failed %s", path, exc_info=True)
    return None


def save_seed_payload(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    for path in SEED_PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body + "\n", encoding="utf-8")
        except OSError:
            logger.debug("etf holders seed write skipped %s", path, exc_info=True)


def load_holder_bundle() -> dict:
    """Load seed only (no network). Safe for request threads."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    payload = load_seed_payload()
    if payload is None:
        bundle = {
            "fetched_at": None,
            "from_seed": True,
            "source": SRC["label"],
            "source_url": SRC["url"],
            "indices": {m["id"]: _missing("无本地种子且未抓取") for m in HOLDER_INDICES},
            "errors": {},
            "in_score": False,
            "notes": list(HOLDER_NOTES),
            "reason": "无本地种子且未抓取",
        }
    else:
        bundle = build_holder_bundle(payload, from_seed=True)
    with _lock:
        _cache = bundle
    return bundle


def _set_cache(bundle: dict) -> dict:
    global _cache
    with _lock:
        _cache = bundle
    return bundle


# ---------------------------------------------------------------- network (background / export only)
def fetch_cyrjg(code: str) -> str:
    import requests

    code = str(code).strip()
    url = CYRJG_API
    params = {"type": "cyrjg", "code": code}
    headers = {"User-Agent": UA, "Referer": cyrjg_url(code)}
    last_exc: Exception | None = None
    for i in range(max(1, HTTP_RETRIES)):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError("empty body")
            return r.text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1.0 * (i + 1))
    raise last_exc or RuntimeError("cyrjg request failed")


def refresh_holder_bundle(*, live: bool = True) -> dict:
    """Try a gentle live F10 fetch; on failure keep the seed. Never raises out a blank bundle."""
    if os.environ.get("SKIP_INDEX_HOLDERS") == "1":
        return load_holder_bundle()
    seed = load_seed_payload() or {}
    funds: dict[str, Any] = dict(seed.get("funds") or {})
    errors: dict[str, str] = {}
    live_ok = 0
    if live:
        jobs = [(c, n) for m in HOLDER_INDICES for c, n in m["etfs"]]
        for i, (code, name) in enumerate(jobs):
            if i:
                time.sleep(HOLDERS_SPACING_SEC)
            try:
                rows = parse_cyrjg(fetch_cyrjg(code))
                if not rows:
                    errors[code] = "接口无持有人结构表"
                    continue
                funds[code] = {"name": name, "rows": serialize_rows(rows)}
                live_ok += 1
            except Exception as exc:  # noqa: BLE001
                errors[code] = f"{type(exc).__name__}: {str(exc)[:80]}"
                logger.info("cyrjg fetch failed %s: %s", code, errors[code])
    if live_ok:
        payload = {
            "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
            "source": SRC["label"],
            "source_url": SRC["url"],
            "function": SRC["function"],
            "funds": funds,
            "errors": errors,
        }
        save_seed_payload(payload)
        return _set_cache(build_holder_bundle(payload, from_seed=False))
    if seed.get("funds"):
        payload = dict(seed)
        payload["from_seed"] = True
        if errors:
            payload = dict(payload)
            payload["errors"] = errors
        bundle = build_holder_bundle(payload, from_seed=True)
        bundle["errors"] = errors
        return _set_cache(bundle)
    bundle = build_holder_bundle({"funds": {}, "errors": errors, "fetched_at": None}, from_seed=False)
    bundle["reason"] = "直播与本地种子均无数据"
    bundle["errors"] = errors
    return _set_cache(bundle)


__all__ = [
    "HOLDER_INDICES", "HOLDER_NOTES", "SRC", "SEED_PATHS",
    "parse_cyrjg", "complete_rows", "aggregate_index", "build_holder_bundle",
    "load_seed_payload", "save_seed_payload", "load_holder_bundle",
    "refresh_holder_bundle", "fetch_cyrjg", "cyrjg_url", "serialize_rows",
]
