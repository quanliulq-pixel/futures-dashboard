# -*- coding: utf-8 -*-
"""沪深300 / 上证50 / 中证1000 的两融余额与比例。

优先使用东方财富数据中心「板块融资融券」日频报告 RPTA_WEB_BKJYMX（datacenter-web，
非 push2）：沪深300 = BOARD_CODE 500（HS300_），上证50 = 611（上证50_）。
该报告直接给出两融余额（MARGIN_BALANCE=融资+融券）、融资余额、融券余额，
以及分母「流通A股市值」NOTLIMITED_MARKETCAP_A。历史按东财当时板块成员逐日统计。

中证1000：东财板块两融名单中无对应板块（已核验概念/行业/地域排行及邻近 BOARD_CODE），
改为用中证指数官方当前成分股（000852cons.xls）叠加东财个股两融明细
RPTA_WEB_RZRQ_GGMX（同样是 datacenter-web，非 push2）按日合计。
分母为当日有两融明细的成分股流通市值（GGMX 字段 SZ）之和；覆盖家数一并给出。
当前成分名单套用于全部历史日期（成分快照口径，调样后回看会有偏差）。

只在后台 refresh 时联网；请求线程只读已提交的 seed / 运行时缓存。
缺数据一律「无数据」，不插补，不回退东方财富 push2。
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from margin_history import (
    CST,
    EM_DC_URL,
    EM_PAGE_SIZE,
    HTTP_RETRIES,
    UA,
    YI,
    _get_json,
    _num,
    _r,
    _to_date,
    save_history_seed,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / "data" / "index_margin_history.json"
RUNTIME_PATH = ROOT / "data" / "index_margin_history_latest.json"
NO_DATA = "无数据"
BACKFILL_PATH = ROOT / "data" / "index_margin_csi1000_backfill.json"

EM_BK_PAGE = "https://data.eastmoney.com/rzrq/bk.html"
EM_GGMX_PAGE = "https://data.eastmoney.com/rzrq/detail/all.html"
CSINDEX_CONS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{code}cons.xls"
)
CSINDEX_CONS_REFERER = "https://www.csindex.com.cn/"

# 中证1000 无板块报告，首次建种子抓最近若干交易日，后台再增量延伸。
AGG_SEED_DAYS = 60
WINDOWS = (1, 5, 20)

BOARD_DENOM = "成分股流通A股市值（东方财富板块字段 NOTLIMITED_MARKETCAP_A，无限售流通A股）"
AGG_DENOM = (
    "当日有两融明细的成分股流通市值合计（东方财富 RPTA_WEB_RZRQ_GGMX 字段 SZ）；"
    "未出现在两融明细中的成分股未计入分母"
)


INDEX_MARGIN: dict[str, dict] = {
    "csi300": {
        "id": "csi300",
        "name": "沪深300",
        "index_code": "000300",
        "method": "em_board",
        "board_code": "500",
        "board_name": "HS300_",
        "board_url": "https://data.eastmoney.com/rzrq/BK0500.html",
        "denominator": BOARD_DENOM,
    },
    "sse50": {
        "id": "sse50",
        "name": "上证50",
        "index_code": "000016",
        "method": "em_board",
        "board_code": "611",
        "board_name": "上证50_",
        "board_url": "https://data.eastmoney.com/rzrq/BK0611.html",
        "denominator": BOARD_DENOM,
    },
    "csi1000": {
        "id": "csi1000",
        "name": "中证1000",
        "index_code": "000852",
        "method": "aggregate_constituents",
        "board_code": None,
        "denominator": AGG_DENOM,
        "missing_board_reason": (
            "东方财富板块融资融券未提供中证1000板块（概念/行业/地域排行均无该名称，"
            "已核验邻近 BOARD_CODE），改用中证官方当前成分股合计个股两融明细"
        ),
    },
}

SRC_BOARD = {
    "label": "东方财富数据中心 板块融资融券",
    "url": EM_BK_PAGE,
    "function": "datacenter-web RPTA_WEB_BKJYMX",
}
SRC_AGG = {
    "label": "中证指数官方成分股 + 东方财富个股两融明细",
    "url": EM_GGMX_PAGE,
    "function": "CSIndex 000852cons.xls + datacenter-web RPTA_WEB_RZRQ_GGMX",
}
SRC = {
    "label": "东方财富数据中心板块两融 / 中证官方成分合计",
    "url": EM_BK_PAGE,
    "definition": (
        "两融余额=融资余额+融券余额（亿元）。"
        "比例=两融余额/成分股流通市值，沪深300与上证50的分母为东方财富板块「流通A股市值」"
        "（NOTLIMITED_MARKETCAP_A）；中证1000的分母为当日有两融明细的官方成分股流通市值"
        "（GGMX 字段 SZ）合计。融资余额占比=融资余额/两融余额。"
        "沪深300、上证50直接采用东财板块日频（历史按当时板块成员）；"
        "中证1000无板块报告，用当前中证官方成分名单套用于历史日期（调样后回看会有偏差）。"
        "数据来自 datacenter-web，不使用东方财富 push2。"
    ),
}



# 指数收盘（新浪日线），只用于图表与关系统计；缺当日收盘为 None，不拿邻日顶替。
INDEX_SYMBOLS = {"csi300": "sh000300", "sse50": "sh000016", "csi1000": "sh000852"}


def index_closes_from_seed(seed: dict | None) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    raw = (seed or {}).get("index") or {}
    for iid in INDEX_MARGIN:
        rows = []
        for item in raw.get(iid) or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                d, v = _to_date(item[0]), _num(item[1])
                if d is not None and v is not None:
                    rows.append((d, v))
        out[iid] = sorted(rows)
    return out


def _merge_closes(*seqs) -> list[tuple[date, float]]:
    by: dict[date, float] = {}
    for s in seqs:
        for d, v in s or []:
            by[d] = v
    return sorted(by.items())


@dataclass(frozen=True)
class DayPoint:
    d: date
    margin: float
    fin: float
    loan: float
    circ: float
    margin_ratio: float | None
    fin_ratio: float | None
    n_cons: int | None = None
    n_mgn: int | None = None


def definition_for(meta: dict) -> str:
    name = meta["name"]
    denom = meta.get("denominator") or ""
    if meta.get("method") == "em_board":
        return (
            f"{name}两融余额=融资余额+融券余额（亿元），来自东方财富板块融资融券"
            f"（BOARD_CODE={meta.get('board_code')} {meta.get('board_name')}，"
            f"RPTA_WEB_BKJYMX，非 push2）。"
            f"比例=两融余额/分母，分母为{denom}。"
            f"另列融资余额及融资余额/两融余额、融资余额/同一分母。"
            f"历史按东财当时板块成员逐日统计，不是用当前成分回灌。"
        )
    return (
        f"{name}两融余额=当前中证官方成分股的个股融资余额+融券余额合计（亿元），"
        f"个股明细来自东方财富 RPTA_WEB_RZRQ_GGMX（非 push2）。"
        f"比例=两融余额/分母，分母为{denom}。"
        f"另列融资余额及融资余额/两融余额、融资余额/同一分母。"
        f"{meta.get('missing_board_reason') or ''}。"
        f"注意：用当前成分名单套用于全部历史日期，指数调样后回看历史会有偏差。"
    )


def source_for(meta: dict) -> dict:
    if meta.get("method") == "em_board":
        return {
            "label": SRC_BOARD["label"],
            "url": meta.get("board_url") or SRC_BOARD["url"],
            "function": SRC_BOARD["function"],
        }
    return dict(SRC_AGG)


def _missing(meta: dict, reason: str | None = None) -> dict:
    src = source_for(meta)
    return {
        "status": "missing",
        "reason": reason or NO_DATA,
        "asof": None,
        "margin_balance": None,
        "fin_balance": None,
        "loan_balance": None,
        "circ_mv": None,
        "margin_ratio": None,
        "fin_ratio": None,
        "fin_share": None,
        "chg_1d": None,
        "chg_5d": None,
        "chg_20d": None,
        "ratio_chg_1d": None,
        "ratio_chg_5d": None,
        "ratio_chg_20d": None,
        "unit": "亿元",
        "ratio_unit": "%",
        "denominator": meta.get("denominator"),
        "method": meta.get("method"),
        "source": src["label"],
        "source_url": src["url"],
        "definition": definition_for(meta),
        "index_code": meta.get("index_code"),
        "board_code": meta.get("board_code"),
    }


def _chg(points: list[DayPoint], n: int, attr: str) -> float | None:
    if len(points) <= n:
        return None
    a = getattr(points[-1], attr)
    b = getattr(points[-1 - n], attr)
    if a is None or b is None:
        return None
    return a - b


def _ratios(margin: float | None, fin: float | None, circ: float | None
            ) -> tuple[float | None, float | None, float | None]:
    m_ratio = (margin / circ * 100.0) if margin is not None and circ else None
    f_ratio = (fin / circ * 100.0) if fin is not None and circ else None
    f_share = (fin / margin * 100.0) if fin is not None and margin else None
    return m_ratio, f_ratio, f_share


def stats_for_index(points: list[DayPoint], meta: dict, *,
                    extra: dict | None = None) -> dict:
    out = _missing(meta, reason="无数据" if meta.get("method") == "em_board"
                   else (meta.get("missing_board_reason") or "无数据"))
    if not points:
        if extra:
            out.update(extra)
        return out
    points = sorted(points, key=lambda p: p.d)
    last = points[-1]
    m_ratio, f_ratio, f_share = _ratios(last.margin, last.fin, last.circ)
    if last.margin_ratio is not None:
        m_ratio = last.margin_ratio
    if last.fin_ratio is not None:
        f_ratio = last.fin_ratio
    out.update({
        "status": "ok",
        "reason": None,
        "asof": last.d.isoformat(),
        "margin_balance": _r(last.margin, 4),
        "fin_balance": _r(last.fin, 4),
        "loan_balance": _r(last.loan, 4),
        "circ_mv": _r(last.circ, 4),
        "margin_ratio": _r(m_ratio, 4),
        "fin_ratio": _r(f_ratio, 4),
        "fin_share": _r(f_share, 4),
        "chg_1d": _r(_chg(points, 1, "margin"), 4),
        "chg_5d": _r(_chg(points, 5, "margin"), 4),
        "chg_20d": _r(_chg(points, 20, "margin"), 4),
        "ratio_chg_1d": _r(_chg(points, 1, "margin_ratio"), 4),
        "ratio_chg_5d": _r(_chg(points, 5, "margin_ratio"), 4),
        "ratio_chg_20d": _r(_chg(points, 20, "margin_ratio"), 4),
        "hist_n": len(points),
        "hist_coverage_start": points[0].d.isoformat(),
        "hist_coverage_end": last.d.isoformat(),
        "n_constituents": last.n_cons,
        "n_with_margin": last.n_mgn,
    })
    if extra:
        out.update(extra)
    return out


def point_from_board_row(row: dict) -> DayPoint | None:
    d = _to_date(row.get("TRADE_DATE") or row.get("DATE"))
    margin = _num(row.get("MARGIN_BALANCE"))
    fin = _num(row.get("FIN_BALANCE"))
    loan = _num(row.get("LOAN_BALANCE"))
    circ = _num(row.get("NOTLIMITED_MARKETCAP_A"))
    if d is None or margin is None:
        return None
    if loan is None and fin is not None:
        loan = margin - fin
    if fin is None and loan is not None:
        fin = margin - loan
    m_ratio, f_ratio, _ = _ratios(
        None if margin is None else margin / YI,
        None if fin is None else fin / YI,
        None if circ is None else circ / YI,
    )
    # 东财 FIN_BALANCE_RATIO 已是融资/流通A（%）；若在则优先使用
    em_fin_ratio = _num(row.get("FIN_BALANCE_RATIO"))
    if em_fin_ratio is not None:
        f_ratio = em_fin_ratio
    return DayPoint(
        d=d,
        margin=(margin or 0.0) / YI,
        fin=(fin or 0.0) / YI,
        loan=(loan or 0.0) / YI,
        circ=(circ or 0.0) / YI,
        margin_ratio=m_ratio,
        fin_ratio=f_ratio,
    )


def parse_board_page(js: Any) -> tuple[list[DayPoint], int, int]:
    if not isinstance(js, dict):
        return [], 0, 0
    result = js.get("result") or {}
    rows = result.get("data") or []
    out: list[DayPoint] = []
    for row in rows:
        if isinstance(row, dict):
            pt = point_from_board_row(row)
            if pt:
                out.append(pt)
    try:
        pages = int(result.get("pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    try:
        count = int(result.get("count") or len(out))
    except (TypeError, ValueError):
        count = len(out)
    return out, max(1, pages), count


def parse_csindex_codes(rows: Iterable[Any], *, date_out: list | None = None) -> list[str]:
    """Parse official cons.xls rows (DataFrame or list of mappings) → 6-digit codes."""
    codes: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            # pandas Series
            try:
                row = dict(row)
            except Exception:  # noqa: BLE001
                continue
        code = None
        dval = None
        for k, v in row.items():
            ks = str(k)
            if "成份券代码" in ks or "Constituent Code" in ks or ks in {"code", "SCODE"}:
                code = v
            if ks.startswith("日期") or "Date" in ks:
                dval = v
        if date_out is not None and dval is not None and not date_out:
            dd = _to_date(dval)
            if dd:
                date_out.append(dd)
        if code is None or code == "":
            continue
        try:
            s = f"{int(str(code).strip()):06d}"
        except (TypeError, ValueError):
            s = str(code).strip()
            if not s.isdigit():
                continue
            s = s.zfill(6)
        if s not in seen:
            seen.add(s)
            codes.append(s)
    return codes


def parse_ggmx_page(js: Any) -> tuple[list[dict], int, int]:
    if not isinstance(js, dict):
        return [], 0, 0
    result = js.get("result") or {}
    rows = result.get("data") or []
    if not isinstance(rows, list):
        rows = []
    try:
        pages = int(result.get("pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    try:
        count = int(result.get("count") or len(rows))
    except (TypeError, ValueError):
        count = len(rows)
    return rows, max(1, pages), count


def aggregate_ggmx_rows(rows: list[dict], codes: set[str], d: date,
                        n_cons: int) -> DayPoint | None:
    margin = fin = loan = circ = 0.0
    n_mgn = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("SCODE") or row.get("SECURITY_CODE") or "").zfill(6)
        if code not in codes:
            continue
        m = _num(row.get("RZRQYE"))
        f = _num(row.get("RZYE"))
        lo = _num(row.get("RQYE"))
        sz = _num(row.get("SZ"))
        if m is None and f is None and lo is None:
            continue
        n_mgn += 1
        if m is None:
            m = (f or 0.0) + (lo or 0.0)
        if f is None and lo is not None:
            f = m - lo
        if lo is None and f is not None:
            lo = m - f
        margin += m or 0.0
        fin += f or 0.0
        loan += lo or 0.0
        if sz is not None:
            circ += sz
    if n_mgn == 0 or margin == 0.0 and circ == 0.0:
        return None
    m_ratio, f_ratio, _ = _ratios(margin / YI, fin / YI, circ / YI if circ else None)
    return DayPoint(
        d=d,
        margin=margin / YI,
        fin=fin / YI,
        loan=loan / YI,
        circ=circ / YI,
        margin_ratio=m_ratio,
        fin_ratio=f_ratio,
        n_cons=n_cons,
        n_mgn=n_mgn,
    )


def rows_from_points(points: list[DayPoint]) -> list[list]:
    out: list[list] = []
    for p in sorted(points, key=lambda x: x.d):
        row: list[Any] = [
            p.d.isoformat(),
            _r(p.margin, 4),
            _r(p.fin, 4),
            _r(p.loan, 4),
            _r(p.circ, 4),
            _r(p.margin_ratio, 4),
            _r(p.fin_ratio, 4),
        ]
        if p.n_cons is not None or p.n_mgn is not None:
            row.extend([p.n_cons, p.n_mgn])
        out.append(row)
    return out


def points_from_rows(rows: Any) -> list[DayPoint]:
    out: list[DayPoint] = []
    for item in rows or []:
        if isinstance(item, dict):
            d = _to_date(item.get("date") or item.get("d"))
            if d is None:
                continue
            out.append(DayPoint(
                d=d,
                margin=float(item.get("margin") or 0.0),
                fin=float(item.get("fin") or 0.0),
                loan=float(item.get("loan") or 0.0),
                circ=float(item.get("circ") or 0.0),
                margin_ratio=_num(item.get("margin_ratio")),
                fin_ratio=_num(item.get("fin_ratio")),
                n_cons=item.get("n_cons"),
                n_mgn=item.get("n_mgn"),
            ))
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        d = _to_date(item[0])
        margin = _num(item[1])
        if d is None or margin is None:
            continue
        fin = _num(item[2]) if len(item) > 2 else None
        loan = _num(item[3]) if len(item) > 3 else None
        circ = _num(item[4]) if len(item) > 4 else None
        m_ratio = _num(item[5]) if len(item) > 5 else None
        f_ratio = _num(item[6]) if len(item) > 6 else None
        n_cons = int(item[7]) if len(item) > 7 and item[7] is not None else None
        n_mgn = int(item[8]) if len(item) > 8 and item[8] is not None else None
        if m_ratio is None or f_ratio is None:
            calc_m, calc_f, _ = _ratios(margin, fin, circ)
            if m_ratio is None:
                m_ratio = calc_m
            if f_ratio is None:
                f_ratio = calc_f
        out.append(DayPoint(
            d=d,
            margin=margin,
            fin=fin or 0.0,
            loan=loan or 0.0,
            circ=circ or 0.0,
            margin_ratio=m_ratio,
            fin_ratio=f_ratio,
            n_cons=n_cons,
            n_mgn=n_mgn,
        ))
    out.sort(key=lambda p: p.d)
    return out


def merge_points(*seqs: list[DayPoint]) -> list[DayPoint]:
    by: dict[date, DayPoint] = {}
    for seq in seqs:
        for p in seq or []:
            by[p.d] = p
    return [by[d] for d in sorted(by)]


def load_index_margin_seed(paths: tuple[Path, ...] | None = None) -> dict | None:
    cands: list[dict] = []
    for p in paths or (RUNTIME_PATH, SEED_PATH):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("index margin seed unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, dict) and (data.get("series") or data.get("stats")):
            data = dict(data)
            data["_path"] = str(p)
            cands.append(data)
    if not cands:
        return None

    def _asof_key(d: dict) -> str:
        a = d.get("asof")
        if isinstance(a, dict):
            return max((str(v) for v in a.values() if v), default="")
        return str(a or "")

    best = max(cands, key=_asof_key)
    have: dict[str, list[DayPoint]] = {}
    closes: dict[str, list[tuple[date, float]]] = {}
    for c in cands:
        pts = seed_points_map(c)
        cl = index_closes_from_seed(c)
        for iid in INDEX_MARGIN:
            have[iid] = merge_points(have.get(iid) or [], pts.get(iid) or [])
            closes[iid] = _merge_closes(closes.get(iid) or [], cl.get(iid) or [])
    best = dict(best)
    best["series"] = {iid: rows_from_points(have.get(iid) or []) for iid in INDEX_MARGIN}
    best["index"] = {iid: [[d.isoformat(), _r(v, 2)] for d, v in closes.get(iid) or []]
                     for iid in INDEX_MARGIN}
    return best


def seed_points_map(seed: dict | None) -> dict[str, list[DayPoint]]:
    out: dict[str, list[DayPoint]] = {}
    if not isinstance(seed, dict):
        return out
    raw = seed.get("series") or {}
    for iid in INDEX_MARGIN:
        out[iid] = points_from_rows(raw.get(iid))
    return out


def fetch_board_history(board_code: str, *, start: date | None = None,
                        get_json: Callable[..., Any] | None = None) -> list[DayPoint]:
    """东财板块两融全历史或增量（亿元 / %）。pageSize 上限 500。"""
    get_json = get_json or _get_json
    headers = {"User-Agent": UA, "Referer": EM_BK_PAGE}
    filt = f'(BOARD_CODE="{board_code}")'
    if start is not None:
        filt += f"(TRADE_DATE>='{start.isoformat()}')"
    all_pts: list[DayPoint] = []
    page, pages = 1, 1
    while page <= pages:
        params = {
            "reportName": "RPTA_WEB_BKJYMX",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageNumber": str(page),
            "pageSize": str(EM_PAGE_SIZE),
            "filter": filt,
        }
        js = get_json(EM_DC_URL, params, headers)
        part, pages, _count = parse_board_page(js)
        if not js.get("success") and not part:
            raise ValueError(js.get("message") or f"东财板块两融无返回 BOARD_CODE={board_code}")
        all_pts.extend(part)
        if not part:
            break
        page += 1
    return merge_points(all_pts)


def fetch_csindex_constituents(index_code: str, *,
                               get_bytes: Callable[..., bytes] | None = None
                               ) -> tuple[list[str], date | None]:
    """中证官方当前成分股代码（6 位）及名单日期。"""
    url = CSINDEX_CONS_URL.format(code=index_code)
    if get_bytes is None:
        import requests

        last_exc: Exception | None = None
        content = b""
        for i in range(max(1, HTTP_RETRIES)):
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": UA, "Referer": CSINDEX_CONS_REFERER},
                    timeout=30,
                )
                r.raise_for_status()
                content = r.content
                if len(content) < 1000:
                    raise ValueError("成分股文件过短")
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                content = b""
                import time
                time.sleep(1.0 * (i + 1))
        else:
            raise last_exc or RuntimeError("CSIndex constituent download failed")
    else:
        content = get_bytes(url)
    import pandas as pd

    df = pd.read_excel(io.BytesIO(content))
    date_out: list[date] = []
    codes = parse_csindex_codes(df.to_dict(orient="records"), date_out=date_out)
    if len(codes) < 50:
        raise ValueError(f"CSIndex {index_code} 成分股过少：{len(codes)}")
    return codes, (date_out[0] if date_out else None)


def fetch_ggmx_day(d: date, codes: set[str], *,
                   get_json: Callable[..., Any] | None = None) -> DayPoint | None:
    """合计某一日成分股的个股两融明细。"""
    get_json = get_json or _get_json
    headers = {"User-Agent": UA, "Referer": EM_GGMX_PAGE}
    filt = f"(DATE='{d.isoformat()}')"
    rows: list[dict] = []
    page, pages = 1, 1
    while page <= pages:
        params = {
            "reportName": "RPTA_WEB_RZRQ_GGMX",
            "columns": "DATE,SCODE,RZRQYE,RZYE,RQYE,SZ",
            "source": "WEB",
            "client": "WEB",
            "sortColumns": "SCODE",
            "sortTypes": "1",
            "pageNumber": str(page),
            "pageSize": str(EM_PAGE_SIZE),
            "filter": filt,
        }
        js = get_json(EM_DC_URL, params, headers)
        part, pages, _count = parse_ggmx_page(js)
        rows.extend(part)
        if not part:
            break
        page += 1
    return aggregate_ggmx_rows(rows, codes, d, n_cons=len(codes))


def _cons_from_seed(seed: dict | None, iid: str) -> tuple[list[str], date | None, str | None]:
    if not isinstance(seed, dict):
        return [], None, None
    block = (seed.get("constituents") or {}).get(iid) or {}
    codes = [str(c).zfill(6) for c in (block.get("codes") or []) if str(c).strip()]
    return codes, _to_date(block.get("date")), block.get("source")



def load_backfill_points(path: Path | None = None) -> list[DayPoint]:
    p = path or BACKFILL_PATH
    if not p.is_file():
        return []
    try:
        return points_from_rows(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        logger.warning("csi1000 backfill checkpoint unreadable: %s", p, exc_info=True)
        return []


def save_backfill_points(points: list[DayPoint], path: Path | None = None) -> Path:
    """原子写入检查点，中断后续跑会跳过已完成日期。"""
    from margin_history import save_history_seed
    path = path or BACKFILL_PATH
    # reuse atomic writer: wrap list as JSON via save? it expects dict.
    text = json.dumps(rows_from_points(points), ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def backfill_csi1000_history(*, start: date, codes: set[str], calendar: list[date],
                             existing: list[DayPoint] | None = None,
                             fetch_day: Callable[..., DayPoint | None] | None = None,
                             checkpoint: Path | None = None,
                             write_every: int = 20) -> list[DayPoint]:
    """按交易日合计中证1000两融。跳过 seed/检查点已有日期，每 write_every 天原子落盘，可续跑。"""
    have = merge_points(existing or [], load_backfill_points(checkpoint))
    done = {p.d for p in have}
    todo = [d for d in calendar if d >= start and d not in done]
    fn = fetch_day or fetch_ggmx_day
    for i, d in enumerate(todo, 1):
        pt = fn(d, set(codes))
        if pt:
            have = merge_points(have, [pt])
        if i % max(1, write_every) == 0 or i == len(todo):
            save_backfill_points(have, checkpoint)
    return have

def refresh_index_margin_history(*, live: bool = True,
                                 fetch_board: Callable[..., list[DayPoint]] | None = None,
                                 fetch_cons: Callable[..., tuple[list[str], date | None]] | None = None,
                                 fetch_day: Callable[..., DayPoint | None] | None = None,
                                 fetch_index: Callable[..., list[tuple[date, float]]] | None = None,
                                 seed: dict | None = None,
                                 today: date | None = None,
                                 write: bool = True,
                                 agg_days: int = AGG_SEED_DAYS) -> dict:
    """合并 seed 与（可选）增量抓取。live=False 时不联网。"""
    today = today or datetime.now(CST).date()
    seed = seed if seed is not None else load_index_margin_seed()
    have = seed_points_map(seed)
    have["csi1000"] = merge_points(have.get("csi1000") or [], load_backfill_points())
    cons_codes, cons_date, cons_src = _cons_from_seed(seed, "csi1000")
    closes = index_closes_from_seed(seed)
    live_ok_ids: list[str] = []
    errors: dict[str, str] = {}

    if live:
        board_fn = fetch_board or fetch_board_history
        for iid, meta in INDEX_MARGIN.items():
            if meta.get("method") != "em_board":
                continue
            existing = have.get(iid) or []
            start = existing[-1].d if existing else None
            # 重叠一天，避免时区/收盘当日漏更新
            if start is not None:
                start = start - timedelta(days=0)
            try:
                part = board_fn(meta["board_code"], start=start)
                have[iid] = merge_points(existing, part)
                if part:
                    live_ok_ids.append(iid)
            except Exception as exc:  # noqa: BLE001
                errors[iid] = f"{type(exc).__name__}: {str(exc)[:120]}"
                logger.warning("index margin board %s fetch failed: %s", iid, errors[iid])

        meta1000 = INDEX_MARGIN["csi1000"]
        try:
            cons_fn = fetch_cons or fetch_csindex_constituents
            new_codes, new_date = cons_fn(meta1000["index_code"])
            if new_codes:
                cons_codes, cons_date = new_codes, new_date
                cons_src = "中证指数官方成分股 000852cons.xls"
        except Exception as exc:  # noqa: BLE001
            errors["csi1000_constituents"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning("CSI1000 constituents fetch failed: %s", errors["csi1000_constituents"])

        if cons_codes:
            existing = have.get("csi1000") or []
            # 交易日日历：优先用沪深300板块日期（同一数据源）
            calendar = [p.d for p in (have.get("csi300") or [])]
            if not calendar:
                calendar = [p.d for p in existing]
            last = existing[-1].d if existing else None
            if last is None:
                want = calendar[-agg_days:] if calendar else []
            else:
                want = [d for d in calendar if d >= last]
                if last not in want and calendar:
                    # 保证至少尝试刷新最后一日
                    want = [d for d in calendar if d >= last] or [calendar[-1]]
            # 去重保序
            seen_d: set[date] = set()
            dates: list[date] = []
            for d in want:
                if d not in seen_d and d <= today:
                    seen_d.add(d)
                    dates.append(d)
            day_fn = fetch_day or fetch_ggmx_day
            code_set = set(cons_codes)
            got: list[DayPoint] = []
            for d in dates:
                try:
                    pt = day_fn(d, code_set)
                    if pt:
                        got.append(pt)
                        live_ok_ids.append("csi1000")
                except Exception as exc:  # noqa: BLE001
                    errors[f"csi1000_{d.isoformat()}"] = f"{type(exc).__name__}: {str(exc)[:80]}"
                    logger.warning("CSI1000 GGMX %s failed: %s", d, errors[f"csi1000_{d.isoformat()}"])
            if got:
                have["csi1000"] = merge_points(existing, got)
                # 只记一次 live_ok
                live_ok_ids = list(dict.fromkeys(live_ok_ids))
        else:
            if "csi1000_constituents" not in errors:
                errors["csi1000"] = "无中证1000成分股名单，无法合计"

    if live:
        from margin_history import fetch_index_closes
        idx_fn = fetch_index or fetch_index_closes
        for iid, sym in INDEX_SYMBOLS.items():
            have_c = closes.get(iid) or []
            try:
                part = idx_fn(sym, datalen=8000 if not have_c else 60)
                closes[iid] = _merge_closes(have_c, part)
            except Exception as exc:  # noqa: BLE001
                errors[f"index_{iid}"] = f"{type(exc).__name__}: {str(exc)[:80]}"
                logger.warning("index close %s fetch failed: %s", sym, errors[f"index_{iid}"])

    cons_extra = {}
    if cons_codes:
        cons_extra = {
            "constituent_source": cons_src or "中证指数官方成分股 000852cons.xls",
            "constituent_date": cons_date.isoformat() if cons_date else None,
            "constituent_n": len(cons_codes),
            "constituent_caveat": (
                "当前中证官方成分名单套用于全部历史日期，指数调样后回看历史会有偏差"
            ),
        }

    stats = {}
    for iid, meta in INDEX_MARGIN.items():
        extra = dict(cons_extra) if iid == "csi1000" else None
        stats[iid] = stats_for_index(have.get(iid) or [], meta, extra=extra)

    asof = {iid: st.get("asof") for iid, st in stats.items() if st.get("asof")}
    coverage = None
    for seq in have.values():
        if seq:
            d0 = seq[0].d.isoformat()
            coverage = min(coverage, d0) if coverage else d0

    payload = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "coverage_start": coverage,
        "asof": asof,
        "sources": {
            "board": SRC_BOARD,
            "aggregate": SRC_AGG,
            "definition": SRC["definition"],
        },
        "boards": {
            iid: {
                "name": m["name"],
                "index_code": m.get("index_code"),
                "method": m.get("method"),
                "board_code": m.get("board_code"),
                "denominator": m.get("denominator"),
            }
            for iid, m in INDEX_MARGIN.items()
        },
        "constituents": {
            "csi1000": {
                "date": cons_date.isoformat() if cons_date else None,
                "source": cons_src,
                "url": CSINDEX_CONS_URL.format(code="000852"),
                "n": len(cons_codes),
                "codes": cons_codes,
                "caveat": (
                    "当前中证官方成分名单套用于全部历史日期，指数调样后回看历史会有偏差"
                ) if cons_codes else None,
            }
        },
        "series": {iid: rows_from_points(have.get(iid) or []) for iid in INDEX_MARGIN},
        "index": {iid: [[d.isoformat(), _r(v, 2)] for d, v in closes.get(iid) or []]
                  for iid in INDEX_MARGIN},
        "index_source": {"label": "新浪日线收盘价", "function": "CN_MarketData.getKLineData",
                         "symbols": INDEX_SYMBOLS},
        "stats": stats,
        "live_ok": bool(live and live_ok_ids),
        "errors": errors,
        "unit": "亿元",
        "ratio_unit": "%",
    }
    if not payload["live_ok"]:
        if any(have.values()):
            payload["from_snapshot"] = True
            payload["snapshot_asof"] = max(asof.values()) if asof else None
        else:
            payload["from_snapshot"] = False
    else:
        payload["from_snapshot"] = False
        if write and any(have.values()):
            try:
                save_history_seed(payload, RUNTIME_PATH)
            except Exception:  # noqa: BLE001
                logger.warning("write index margin runtime cache failed", exc_info=True)
    return payload


def attach_index_margins(bundle: dict | None, payload: dict | None) -> dict | None:
    """把各指数两融挂到 bundle.indices[*].margin，并在 两融 区使用 index_margins。"""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("indices"), dict):
        return bundle
    if not isinstance(payload, dict) or not payload.get("stats"):
        return bundle
    out = dict(bundle)
    indices = dict(out["indices"])
    snap = bool(payload.get("from_snapshot"))
    snap_asof = payload.get("snapshot_asof")
    index_margins: dict[str, dict] = {}
    for iid, meta in INDEX_MARGIN.items():
        row = dict(indices.get(iid) or {"id": iid, "name": meta["name"]})
        st = dict(payload["stats"].get(iid) or _missing(meta))
        if snap and st.get("status") == "ok":
            st["from_snapshot"] = True
            if snap_asof:
                st["snapshot_asof"] = snap_asof
        else:
            st.pop("from_snapshot", None)
            st.pop("snapshot_asof", None)
        row["margin"] = st
        indices[iid] = row
        index_margins[iid] = st
    out["indices"] = indices
    out["index_margins"] = index_margins
    out["index_margin_source"] = SRC["label"]
    out["index_margin_definition"] = SRC["definition"]
    if snap:
        out["index_margin_from_snapshot"] = True
        if snap_asof:
            out["index_margin_snapshot_asof"] = snap_asof
    else:
        out.pop("index_margin_from_snapshot", None)
        out.pop("index_margin_snapshot_asof", None)
    return out


def ensure_index_margins(bundle: dict | None, *, live: bool = False) -> dict | None:
    if not isinstance(bundle, dict):
        return bundle
    try:
        payload = refresh_index_margin_history(live=live, write=live)
        return attach_index_margins(bundle, payload)
    except Exception:  # noqa: BLE001
        logger.warning("ensure index margin failed", exc_info=True)
        return bundle


def public_history_bundle(*, live: bool = False, seed: dict | None = None,
                          today: date | None = None) -> dict:
    """请求路径默认 live=False，只读 seed / 运行时缓存，不联网。"""
    payload = refresh_index_margin_history(live=live, write=live, seed=seed, today=today)
    # 公开接口不回传全部 1000 只成分代码，避免 payload 过大。
    out = dict(payload)
    cons = dict(out.get("constituents") or {})
    if "csi1000" in cons:
        block = dict(cons["csi1000"])
        block.pop("codes", None)
        cons["csi1000"] = block
        out["constituents"] = cons
    from index_margin_relation import chart_series, relation_for_index
    pts = seed_points_map(payload)
    cl = index_closes_from_seed(payload)
    out["chart"] = {}
    for iid, meta in INDEX_MARGIN.items():
        rel = relation_for_index(pts.get(iid) or [], cl.get(iid) or [])
        if iid == "csi1000" and rel.get("status") == "ok":
            rel["constituent_caveat"] = "当前中证官方成分名单套用于全部历史日期，指数调样后回看历史会有偏差"
        out["chart"][iid] = {
            "name": meta["name"],
            "index_symbol": INDEX_SYMBOLS.get(iid),
            "series": chart_series(pts.get(iid) or [], cl.get(iid) or []),
            "relation": rel,
        }
    out.pop("series", None)
    out.pop("index", None)
    out["status"] = "ok" if any(
        (st or {}).get("status") == "ok" for st in (out.get("stats") or {}).values()
    ) else "missing"
    return out


__all__ = [
    "INDEX_MARGIN", "SRC", "SEED_PATH", "RUNTIME_PATH",
    "DayPoint", "definition_for", "stats_for_index",
    "parse_board_page", "parse_csindex_codes", "parse_ggmx_page",
    "aggregate_ggmx_rows", "points_from_rows", "rows_from_points", "merge_points",
    "load_index_margin_seed", "refresh_index_margin_history",
    "attach_index_margins", "ensure_index_margins", "public_history_bundle",
    "fetch_board_history", "fetch_csindex_constituents", "fetch_ggmx_day",
    "backfill_csi1000_history", "load_backfill_points", "BACKFILL_PATH",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_seed = os.environ.get("WRITE_INDEX_MARGIN_SEED") == "1"
    payload = refresh_index_margin_history(live=True, write=True)
    if write_seed:
        save_history_seed(payload, SEED_PATH, pretty=True)
        print("wrote", SEED_PATH)
    for iid, st in (payload.get("stats") or {}).items():
        print("====", iid, INDEX_MARGIN[iid].get("method"))
        print({k: st.get(k) for k in (
            "status", "asof", "margin_balance", "fin_balance", "loan_balance",
            "circ_mv", "margin_ratio", "fin_ratio", "fin_share",
            "chg_1d", "chg_5d", "chg_20d", "ratio_chg_1d", "ratio_chg_5d",
            "ratio_chg_20d", "hist_n", "hist_coverage_start", "n_with_margin",
            "constituent_n", "constituent_date", "reason",
        )})
    print("coverage_start", payload.get("coverage_start"), "live_ok", payload.get("live_ok"),
          "errors", payload.get("errors"))
