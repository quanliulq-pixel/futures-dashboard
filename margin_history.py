# -*- coding: utf-8 -*-
"""两融融资余额历史高低及对应指数收盘。

上交所走交易所官方 queryMargin.do（可按日期区间分页）；深交所官方接口只能按日取，
全历史改用东方财富数据中心 datacenter-web（RPTA_WEB_RZRQ_LSSH，SCDM=001，非 push2），
该口径与深交所当日汇总交叉核对一致。沪深合计 = 两市同一交易日相加，不含北交所
（东财「全部」含北交所，故不直接用）。指数收盘用新浪日线：上交所→上证指数 000001，
深交所→深证成指 399001，沪深合计→沪深300 000300。

只在后台 refresh 时联网；请求线程只读已提交的 seed / 运行时缓存。缺历史一律「无数据」，不插补。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
YI = 1e8
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")

# A 股融资融券试点开始日（上交所/深交所官方序列均始于此）
MARGIN_HISTORY_START = date(2010, 3, 31)
# 2014-09-22 两融标的扩容至 900 只；历史最低只在此日及之后计算，避免起步阶段极小余额
MARGIN_LOW_START = date(2014, 9, 22)
SSE_PAGE_SIZE = 2000          # 上交所实际上限 2000
EM_PAGE_SIZE = 500            # 东财数据中心实际上限 500

SSE_MARGIN_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
SSE_MARGIN_PAGE = "https://www.sse.com.cn/market/othersdata/margin/sum/"
EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_RZRQ_PAGE = "https://data.eastmoney.com/rzrq/total.html"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SZSE_MARGIN_PAGE = "https://www.szse.cn/disclosure/margin/margin/index.html"

INDEX_SPEC = {
    "margin_sh": {"symbol": "sh000001", "name": "上证指数", "code": "000001"},
    "margin_sz": {"symbol": "sz399001", "name": "深证成指", "code": "399001"},
    "margin_total": {"symbol": "sh000300", "name": "沪深300", "code": "000300"},
}
MARGIN_KEYS = ("margin_sh", "margin_sz", "margin_total")

SRC = {
    "sh": {"label": "上交所 融资融券汇总", "url": SSE_MARGIN_PAGE,
           "function": "query.sse.com.cn queryMargin.do"},
    "sz": {"label": "东方财富数据中心 深市两融历史（与深交所当日汇总同口径）",
           "url": EM_RZRQ_PAGE, "function": "datacenter-web RPTA_WEB_RZRQ_LSSH SCDM=001"},
    "total": {"label": "上交所+深交所 同一交易日融资余额合计（不含北交所）",
              "url": SSE_MARGIN_PAGE, "function": "sh+sz common dates"},
    "index": {"label": "新浪日线收盘价", "url": "https://finance.sina.com.cn/",
              "function": "CN_MarketData.getKLineData"},
}

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / "data" / "margin_history.json"
RUNTIME_PATH = ROOT / "data" / "margin_history_latest.json"

NO_DATA = "无数据"


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("/", "-")
    if " " in s:
        s = s.split(" ")[0]
    if s.isdigit() and len(s) == 8:
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        if isinstance(v, str):
            v = v.replace(",", "")
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _r(x: float | None, n: int = 4) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _get_json(url: str, params: dict, headers: dict | None = None,
              retries: int = HTTP_RETRIES) -> Any:
    import requests

    h = {"User-Agent": UA}
    h.update(headers or {})
    last_exc: Exception | None = None
    for i in range(max(1, retries)):
        try:
            r = requests.get(url, params=params, headers=h, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError("empty body")
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1.0 * (i + 1))
    raise last_exc or RuntimeError("request failed")


# ---------------------------------------------------------------- parsers (pure)
def parse_sse_margin_page(js: Any) -> tuple[list[tuple[date, float]], int, int]:
    """上交所 queryMargin.do 一页 → (序列亿元, 本页条数, pageCount)。"""
    if not isinstance(js, dict):
        return [], 0, 0
    rows = js.get("result") or []
    out: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("opDate") or row.get("信用交易日期"))
        v = _num(row.get("rzye") if row.get("rzye") is not None else row.get("融资余额"))
        if d and v is not None:
            out.append((d, v / YI))
    ph = js.get("pageHelp") or {}
    try:
        page_count = int(ph.get("pageCount") or 1)
    except (TypeError, ValueError):
        page_count = 1
    return sorted(out), len(rows) if isinstance(rows, list) else 0, max(1, page_count)


def parse_em_margin_page(js: Any) -> tuple[list[tuple[date, float]], dict[date, float], int]:
    """东财 RPTA_WEB_RZRQ_LSSH / RPTA_RZRQ_LSHJ 一页 → (余额亿元, NEW 指数收盘, 总页数)。"""
    if not isinstance(js, dict):
        return [], {}, 0
    result = js.get("result") or {}
    rows = result.get("data") or []
    out: list[tuple[date, float]] = []
    idx: dict[date, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("DIM_DATE") or row.get("DATE"))
        v = _num(row.get("RZYE"))
        if d and v is not None:
            out.append((d, v / YI))
            close = _num(row.get("NEW"))
            if close is not None:
                idx[d] = close
    try:
        pages = int(result.get("pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    return sorted(out), idx, max(1, pages)


def parse_sina_kline(js: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for row in js or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("day") or row.get("date"))
        c = _num(row.get("close"))
        if d is not None and c is not None:
            out.append((d, c))
    out.sort()
    return out


def merge_series(*seqs: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """后写覆盖先写（增量新值优先），按日期排序去重。"""
    by: dict[date, float] = {}
    for seq in seqs:
        for d, v in seq or []:
            by[d] = v
    return sorted(by.items())


def series_from_rows(rows: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for item in rows or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        d, v = _to_date(item[0]), _num(item[1])
        if d is not None and v is not None:
            out.append((d, v))
    return sorted(out)


def rows_from_series(series: list[tuple[date, float]], *, n: int = 6) -> list[list]:
    return [[d.isoformat(), _r(v, n)] for d, v in series]


def total_series(sh: list[tuple[date, float]], sz: list[tuple[date, float]]) -> list[tuple[date, float]]:
    sh_by, sz_by = dict(sh or []), dict(sz or [])
    return sorted((d, sh_by[d] + sz_by[d]) for d in set(sh_by) & set(sz_by))


def close_on(index_series: list[tuple[date, float]], d: date | None) -> float | None:
    """只取同一天收盘；缺当天不拿邻日顶替。"""
    if d is None:
        return None
    for xd, xv in index_series or []:
        if xd == d:
            return xv
    return None


def _extreme(series: list[tuple[date, float]], *, want_max: bool) -> tuple[date, float] | None:
    if not series:
        return None
    best_d, best_v = series[0]
    for d, v in series[1:]:
        if want_max:
            if v > best_v or (v == best_v and d < best_d):
                best_d, best_v = d, v
        else:
            if v < best_v or (v == best_v and d < best_d):
                best_d, best_v = d, v
    return best_d, best_v


def _point(ext: tuple[date, float] | None, index_series: list[tuple[date, float]],
           spec: dict) -> dict | None:
    if not ext:
        return None
    d, v = ext
    return {
        "date": d.isoformat(),
        "balance": _r(v, 4),
        "index_close": _r(close_on(index_series, d), 2),
        "index_name": spec["name"],
        "index_code": spec["code"],
    }


def extrema_for(series: list[tuple[date, float]], index_series: list[tuple[date, float]],
                key: str, *, low_start: date | None = None) -> dict:
    """融资余额序列 + 对应指数收盘 → 历史最高/最低/当前指数。空序列则无数据。

    历史最高用全样本（默认自 2010-03-31）；历史最低只在 low_start（默认 2014-09-22，
    两融标的扩容至 900 只）及之后计算。
    """
    spec = INDEX_SPEC[key]
    low_start = MARGIN_LOW_START if low_start is None else low_start
    base = {
        "index_name": spec["name"], "index_code": spec["code"],
        "hist_high": None, "hist_low": None, "index_now": None,
        "hist_coverage_start": None, "hist_coverage_end": None, "hist_n": 0,
        "hist_low_coverage_start": low_start.isoformat(), "hist_low_n": 0,
    }
    if not series:
        return base
    series = sorted(series)
    high = _extreme(series, want_max=True)
    low_series = [p for p in series if p[0] >= low_start]
    low = _extreme(low_series, want_max=False)
    idx_now = None
    if index_series:
        ld, lv = sorted(index_series)[-1]
        idx_now = {
            "date": ld.isoformat(),
            "close": _r(lv, 2),
            "index_name": spec["name"],
            "index_code": spec["code"],
        }
    return {
        "index_name": spec["name"], "index_code": spec["code"],
        "hist_high": _point(high, index_series, spec),
        "hist_low": _point(low, index_series, spec),
        "index_now": idx_now,
        "hist_coverage_start": series[0][0].isoformat(),
        "hist_coverage_end": series[-1][0].isoformat(),
        "hist_n": len(series),
        "hist_low_coverage_start": low_start.isoformat(),
        "hist_low_n": len(low_series),
    }



def _fill_index_from_cached(extrema: dict[str, dict], cached: dict) -> dict[str, dict]:
    """保留新计算的高低点日期/余额；仅在当日指数缺失时回填 seed 中同一天的收盘。"""
    for key, ext in extrema.items():
        old = cached.get(key) if isinstance(cached, dict) else None
        if not isinstance(ext, dict) or not isinstance(old, dict):
            continue
        for field in ("hist_high", "hist_low"):
            cur, prev = ext.get(field), old.get(field)
            if (isinstance(cur, dict) and isinstance(prev, dict)
                    and cur.get("date") == prev.get("date") and cur.get("index_close") is None
                    and prev.get("index_close") is not None):
                cur["index_close"] = prev.get("index_close")
        if ext.get("index_now") is None and isinstance(old.get("index_now"), dict):
            ext["index_now"] = dict(old["index_now"])
    return extrema


def build_extrema(sh: list[tuple[date, float]], sz: list[tuple[date, float]],
                  index_by_key: dict[str, list[tuple[date, float]]]) -> dict[str, dict]:
    tot = total_series(sh, sz)
    return {
        "margin_sh": extrema_for(sh, index_by_key.get("margin_sh") or [], "margin_sh"),
        "margin_sz": extrema_for(sz, index_by_key.get("margin_sz") or [], "margin_sz"),
        "margin_total": extrema_for(tot, index_by_key.get("margin_total") or [], "margin_total"),
    }



MARKET_LABELS = {
    "margin_sh": "上交所",
    "margin_sz": "深交所",
    "margin_total": "沪深合计",
}


def index_from_seed(seed: dict | None) -> dict[str, list[tuple[date, float]]]:
    """seed['index'] 按新浪代码或 market key 存放 [date, close] 行。"""
    raw = (seed or {}).get("index") if isinstance(seed, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, list[tuple[date, float]]] = {}
    for key, spec in INDEX_SPEC.items():
        out[key] = series_from_rows(raw.get(spec["symbol"]) or raw.get(key) or [])
    return out


def index_rows(index_by_key: dict[str, list[tuple[date, float]]]) -> dict[str, list]:
    out: dict[str, list] = {}
    for key, spec in INDEX_SPEC.items():
        seq = [p for p in (index_by_key.get(key) or []) if p[0] >= MARGIN_HISTORY_START]
        out[spec["symbol"]] = rows_from_series(seq, n=4)
    return out


def series_for_chart(balance: list[tuple[date, float]],
                     index_series: list[tuple[date, float]]) -> list[list]:
    """[date, 融资余额亿元, 当日指数收盘或 null]。缺当日收盘不插补。"""
    imap = {d: v for d, v in (index_series or [])}
    return [
        [d.isoformat(), _r(v, 4), _r(imap[d], 2) if d in imap else None]
        for d, v in (balance or [])
    ]


def downsample_weekly(points: list) -> list:
    """每个 ISO 周保留最后一个实际点，不插补。"""
    by: dict[tuple[int, int], list] = {}
    for p in points or []:
        if not isinstance(p, (list, tuple)) or not p:
            continue
        d = _to_date(p[0])
        if d is None:
            continue
        iso = d.isocalendar()
        by[(iso[0], iso[1])] = list(p)
    return [by[k] for k in sorted(by)]


def insert_extrema_points(points: list, daily: list, *dates: str | None) -> list:
    """周抽样后把真实高低点日期加回去（仍是 daily 中的实际点）。daily 应已按区间筛过。"""
    pts = list(points or [])
    have = {p[0] for p in pts}
    in_daily = {}
    for p in daily or []:
        if isinstance(p, (list, tuple)) and p:
            in_daily[p[0]] = list(p)
    extra = []
    for d in dates:
        if d and d not in have and d in in_daily:
            extra.append(in_daily[d])
            have.add(d)
    if not extra:
        return pts
    return sorted(pts + extra, key=lambda p: p[0])


def chart_bundle(payload: dict | None) -> dict:
    """请求线程 / 静态页用的走势 payload，不含原始 seed 全字段。"""
    if not isinstance(payload, dict) or not (payload.get("sh") or payload.get("sz") or payload.get("extrema")):
        return {"status": "missing", "reason": "无数据", "markets": {}}
    sh, sz = seed_series(payload)
    tot = total_series(sh, sz)
    index_by_key = index_from_seed(payload)
    bal = {"margin_sh": sh, "margin_sz": sz, "margin_total": tot}
    markets: dict[str, dict] = {}
    for key in MARGIN_KEYS:
        ext = (payload.get("extrema") or {}).get(key) or {}
        series = series_for_chart(bal[key], index_by_key.get(key) or [])
        markets[key] = {
            "label": MARKET_LABELS[key],
            "index_name": ext.get("index_name") or INDEX_SPEC[key]["name"],
            "index_code": ext.get("index_code") or INDEX_SPEC[key]["code"],
            "asof": series[-1][0] if series else None,
            "hist_high": ext.get("hist_high"),
            "hist_low": ext.get("hist_low"),
            "index_now": ext.get("index_now"),
            "hist_coverage_start": ext.get("hist_coverage_start") or payload.get("coverage_start"),
            "hist_low_coverage_start": (
                ext.get("hist_low_coverage_start")
                or payload.get("low_coverage_start")
                or MARGIN_LOW_START.isoformat()
            ),
            "hist_coverage_end": ext.get("hist_coverage_end"),
            "hist_n": ext.get("hist_n") or len(series),
            "hist_low_n": ext.get("hist_low_n") or 0,
            "series": series,
        }
    any_series = any(m.get("series") for m in markets.values())
    out = {
        "status": "ok" if any_series else "missing",
        "coverage_start": payload.get("coverage_start") or MARGIN_HISTORY_START.isoformat(),
        "low_coverage_start": payload.get("low_coverage_start") or MARGIN_LOW_START.isoformat(),
        "asof": payload.get("asof"),
        "fetched_at": payload.get("fetched_at"),
        "from_snapshot": bool(payload.get("from_snapshot")),
        "sources": payload.get("sources") or SRC,
        "markets": markets,
    }
    if payload.get("from_snapshot"):
        out["snapshot_asof"] = payload.get("snapshot_asof")
    if not any_series:
        out["reason"] = "无数据"
    return out


def public_history_bundle(*, live: bool = False, seed: dict | None = None,
                          today: date | None = None) -> dict:
    """请求路径默认 live=False，只读 seed / 运行时缓存，不联网。"""
    payload = refresh_history(live=live, write=live, seed=seed, today=today)
    return chart_bundle(payload)


# ---------------------------------------------------------------- seed I/O
def load_history_seed(paths: tuple[Path, ...] | None = None) -> dict | None:
    """运行时缓存优先（asof 不早于提交的 seed），否则读提交的 seed。"""
    cands: list[dict] = []
    for p in paths or (RUNTIME_PATH, SEED_PATH):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("margin history seed unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, dict) and (data.get("sh") or data.get("sz")):
            data = dict(data)
            data["_path"] = str(p)
            cands.append(data)
    if not cands:
        return None

    def _asof_key(d: dict) -> str:
        a = d.get("asof") or {}
        if isinstance(a, dict):
            return max((str(v) for v in a.values() if v), default="")
        return str(a or "")

    best = max(cands, key=_asof_key)
    # 运行时缓存可能早于本次提交、没有 index 序列：从其他候选合并进来。
    idx = index_from_seed(best)
    for other in cands:
        other_idx = index_from_seed(other)
        for key in INDEX_SPEC:
            idx[key] = merge_series(other_idx.get(key) or [], idx.get(key) or [])
    if any(idx.values()):
        best = dict(best)
        best["index"] = index_rows(idx)
    return best


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def save_history_seed(payload: dict, path: Path | None = None, *, pretty: bool = False) -> Path:
    path = path or RUNTIME_PATH
    body = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    text = json.dumps(body, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":"))
    _atomic_write(path, text + ("\n" if pretty else ""))
    return path


def seed_series(seed: dict | None) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    if not isinstance(seed, dict):
        return [], []
    return series_from_rows(seed.get("sh")), series_from_rows(seed.get("sz"))


# ---------------------------------------------------------------- fetchers
def fetch_margin_sh_history(start: date | None = None, end: date | None = None,
                            get_json: Callable[..., Any] | None = None) -> list[tuple[date, float]]:
    """上交所官方融资余额全历史（亿元）。pageSize 上限 2000，自动翻页。"""
    get_json = get_json or _get_json
    start = start or MARGIN_HISTORY_START
    end = end or datetime.now(CST).date()
    headers = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
    all_rows: list[tuple[date, float]] = []
    page, pages = 1, 1
    while page <= pages:
        params = {
            "isPagination": "true",
            "beginDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
            "tabType": "",
            "stockCode": "",
            "pageHelp.pageSize": str(SSE_PAGE_SIZE),
            "pageHelp.pageNo": str(page),
            "pageHelp.beginPage": str(page),
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": str(page),
        }
        js = get_json(SSE_MARGIN_URL, params, headers)
        part, n, pages = parse_sse_margin_page(js)
        all_rows.extend(part)
        if n == 0:
            break
        page += 1
    return merge_series(all_rows)


def fetch_margin_sz_history(start: date | None = None, end: date | None = None,
                            get_json: Callable[..., Any] | None = None
                            ) -> tuple[list[tuple[date, float]], dict[date, float]]:
    """东财数据中心深市两融历史（亿元）+ 深证成指收盘（NEW，作指数备用）。"""
    get_json = get_json or _get_json
    start = start or MARGIN_HISTORY_START
    end = end or datetime.now(CST).date()
    headers = {"Referer": EM_RZRQ_PAGE, "User-Agent": UA}
    filt = f"(SCDM=001)(DIM_DATE>='{start.isoformat()}')(DIM_DATE<='{end.isoformat()}')"
    all_rows: list[tuple[date, float]] = []
    idx: dict[date, float] = {}
    page, pages = 1, 1
    while page <= pages:
        params = {
            "reportName": "RPTA_WEB_RZRQ_LSSH",
            "columns": "ALL",
            "source": "WEB",
            "sortColumns": "DIM_DATE",
            "sortTypes": "-1",
            "pageNumber": str(page),
            "pageSize": str(EM_PAGE_SIZE),
            "filter": filt,
        }
        js = get_json(EM_DC_URL, params, headers)
        part, part_idx, pages = parse_em_margin_page(js)
        if not js.get("success") and not part:
            raise ValueError(js.get("message") or "东财深市两融历史无返回")
        all_rows.extend(part)
        idx.update(part_idx)
        if not part:
            break
        page += 1
    return merge_series(all_rows), idx


def fetch_index_closes(symbol: str, datalen: int = 8000,
                       get_json: Callable[..., Any] | None = None) -> list[tuple[date, float]]:
    get_json = get_json or _get_json
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": int(datalen)}
    js = get_json(SINA_KLINE_URL, params, {"Referer": "https://finance.sina.com.cn/", "User-Agent": UA})
    return parse_sina_kline(js)


def _next_day(d: date | None) -> date | None:
    return (d + timedelta(days=1)) if d else None


def refresh_history(*, live: bool = True,
                    fetch_sh: Callable[..., list[tuple[date, float]]] | None = None,
                    fetch_sz: Callable[..., tuple[list[tuple[date, float]], dict[date, float]]] | None = None,
                    fetch_index: Callable[..., list[tuple[date, float]]] | None = None,
                    seed: dict | None = None,
                    today: date | None = None,
                    write: bool = True) -> dict:
    """合并 seed 与（可选）增量抓取，计算高低点。live=False 时不联网。"""
    today = today or datetime.now(CST).date()
    seed = seed if seed is not None else load_history_seed()
    sh, sz = seed_series(seed)
    sh_live = sz_live = False
    errors: dict[str, str] = {}
    index_by_key: dict[str, list[tuple[date, float]]] = index_from_seed(seed)

    if live:
        sh_start = _next_day(sh[-1][0]) if sh else MARGIN_HISTORY_START
        sz_start = _next_day(sz[-1][0]) if sz else MARGIN_HISTORY_START
        try:
            fn = fetch_sh or fetch_margin_sh_history
            if sh_start is None or sh_start <= today:
                sh = merge_series(sh, fn(start=sh_start or MARGIN_HISTORY_START, end=today))
            sh_live = True
        except Exception as exc:  # noqa: BLE001
            errors["sh"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            logger.warning("margin sh history fetch failed: %s", errors["sh"])
        try:
            fn_sz = fetch_sz or fetch_margin_sz_history
            if sz_start is None or sz_start <= today:
                part, _idx = fn_sz(start=sz_start or MARGIN_HISTORY_START, end=today)
                sz = merge_series(sz, part)
            sz_live = True
        except Exception as exc:  # noqa: BLE001
            errors["sz"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            logger.warning("margin sz history fetch failed: %s", errors["sz"])
        # 指数：seed 已有从 2010-03-31 起的序列时只拉近一年做增量合并；否则拉满
        fn_ix = fetch_index or fetch_index_closes
        for key, spec in INDEX_SPEC.items():
            have = index_by_key.get(key) or []
            need_full = (not have) or have[0][0] > MARGIN_HISTORY_START
            datalen = 8000 if need_full else 260
            try:
                fetched = fn_ix(spec["symbol"], datalen)
                index_by_key[key] = merge_series(have, fetched)
            except Exception as exc:  # noqa: BLE001
                errors[f"index:{key}"] = f"{type(exc).__name__}: {str(exc)[:80]}"
                logger.warning("index close fetch failed %s: %s", spec["symbol"], errors[f"index:{key}"])

    extrema = build_extrema(sh, sz, index_by_key)
    # 未新拉指数时（请求线程 / 指数接口失败），沿用 seed 里同日的收盘，不拿邻日顶替。
    cached_ext = (seed or {}).get("extrema") if isinstance(seed, dict) else None
    if cached_ext:
        extrema = _fill_index_from_cached(extrema, cached_ext)
    coverage_start = None
    for seq in (sh, sz):
        if seq:
            d = seq[0][0].isoformat()
            coverage_start = min(coverage_start, d) if coverage_start else d
    asof = {
        "sh": sh[-1][0].isoformat() if sh else None,
        "sz": sz[-1][0].isoformat() if sz else None,
    }
    payload = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "coverage_start": coverage_start or MARGIN_HISTORY_START.isoformat(),
        "low_coverage_start": MARGIN_LOW_START.isoformat(),
        "asof": asof,
        "sources": SRC,
        "sh": rows_from_series(sh),
        "sz": rows_from_series(sz),
        "index": index_rows(index_by_key),
        "extrema": extrema,
        "live_ok": bool(live and (sh_live or sz_live)),
        "errors": errors,
    }
    # live 失败但 seed 有数据：标上次快照
    if not payload["live_ok"]:
        if sh or sz:
            payload["from_snapshot"] = True
            payload["snapshot_asof"] = max((v for v in asof.values() if v), default=None)
        else:
            payload["from_snapshot"] = False
    else:
        payload["from_snapshot"] = False
        if write and (sh or sz):
            try:
                save_history_seed(payload, RUNTIME_PATH)
            except Exception:  # noqa: BLE001
                logger.warning("write margin history runtime cache failed", exc_info=True)
    return payload


def attach_margin_history(bundle: dict | None, payload: dict | None) -> dict | None:
    """把历史高低/指数挂到已有资金流 bundle 的两融行上，不改余额与 1/5/20 日变化。"""
    if not isinstance(bundle, dict):
        return bundle
    if not isinstance(payload, dict) or not payload.get("extrema"):
        return bundle
    out = dict(bundle)
    market = dict(out.get("market") or {})
    snap = bool(payload.get("from_snapshot"))
    snap_asof = payload.get("snapshot_asof")
    coverage = payload.get("coverage_start") or MARGIN_HISTORY_START.isoformat()
    for key in MARGIN_KEYS:
        row = dict(market.get(key) or {})
        ext = payload["extrema"].get(key) or {}
        row["hist_high"] = ext.get("hist_high")
        row["hist_low"] = ext.get("hist_low")
        row["index_now"] = ext.get("index_now")
        row["index_name"] = ext.get("index_name")
        row["index_code"] = ext.get("index_code")
        row["hist_coverage_start"] = ext.get("hist_coverage_start") or coverage
        row["hist_low_coverage_start"] = ext.get("hist_low_coverage_start") or MARGIN_LOW_START.isoformat()
        row["hist_coverage_end"] = ext.get("hist_coverage_end")
        row["hist_n"] = ext.get("hist_n") or 0
        row["hist_low_n"] = ext.get("hist_low_n") or 0
        if snap:
            row["hist_from_snapshot"] = True
            if snap_asof:
                row["hist_snapshot_asof"] = snap_asof
        else:
            row.pop("hist_from_snapshot", None)
            row.pop("hist_snapshot_asof", None)
        market[key] = row
    out["market"] = market
    out["margin_history_coverage_start"] = coverage
    out["margin_history_low_coverage_start"] = payload.get("low_coverage_start") or MARGIN_LOW_START.isoformat()
    if snap:
        out["margin_history_from_snapshot"] = True
        if snap_asof:
            out["margin_history_snapshot_asof"] = snap_asof
    else:
        out.pop("margin_history_from_snapshot", None)
        out.pop("margin_history_snapshot_asof", None)
    return out


def ensure_margin_history(bundle: dict | None, *, live: bool = False) -> dict | None:
    """后台可 live 增量更新；请求线程 live=False，只读 seed。已挂载且非强制刷新则保持。"""
    if not isinstance(bundle, dict):
        return bundle
    if not live:
        mk = bundle.get("market") or {}
        if isinstance(mk, dict) and all(
            isinstance(mk.get(k), dict) and (mk[k].get("hist_high") or mk[k].get("hist_low"))
            for k in MARGIN_KEYS
        ):
            return bundle
    try:
        payload = refresh_history(live=live, write=live)
        return attach_margin_history(bundle, payload)
    except Exception:  # noqa: BLE001
        logger.warning("ensure margin history failed", exc_info=True)
        return bundle


__all__ = [
    "MARGIN_HISTORY_START", "MARGIN_LOW_START", "INDEX_SPEC", "SRC", "SEED_PATH", "RUNTIME_PATH",
    "parse_sse_margin_page", "parse_em_margin_page", "parse_sina_kline",
    "merge_series", "total_series", "close_on", "extrema_for", "build_extrema",
    "series_from_rows", "rows_from_series", "load_history_seed", "save_history_seed",
    "index_from_seed", "index_rows", "series_for_chart", "downsample_weekly",
    "insert_extrema_points", "chart_bundle", "public_history_bundle",
    "fetch_margin_sh_history", "fetch_margin_sz_history", "fetch_index_closes",
    "refresh_history", "attach_margin_history", "ensure_margin_history",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_seed = os.environ.get("WRITE_MARGIN_SEED") == "1"
    payload = refresh_history(live=True, write=True)
    if write_seed:
        save_history_seed(payload, SEED_PATH)
        print("wrote", SEED_PATH, "sh", len(payload.get("sh") or []), "sz", len(payload.get("sz") or []))
    import pprint
    ext = payload.get("extrema") or {}
    for k, v in ext.items():
        print("====", k)
        pprint.pp({kk: v[kk] for kk in ("hist_high", "hist_low", "index_now",
                                        "hist_coverage_start", "hist_coverage_end", "hist_n")})
    print("coverage_start", payload.get("coverage_start"), "asof", payload.get("asof"),
          "live_ok", payload.get("live_ok"), "errors", payload.get("errors"))
