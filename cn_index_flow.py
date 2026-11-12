# -*- coding: utf-8 -*-
"""A股指数资金进出：主力净流入 / 指数ETF份额申赎 / 两融融资余额 / 北向 / 机构ETF持仓（只展示，不进评分）。

原则
- 只用公开免费源，逐项带数据日期 asof、来源与 URL；取不到或过期 ⇒ status="missing"（前端显示「无数据」），绝不插补。
- 单位统一为 亿元（ETF 份额为 亿份）；正 = 流入（前端红色），负 = 流出（前端绿色）。
- 只在后台任务 / 静态导出里联网；请求线程只读缓存或快照。
- 资金流只做展示，不并入基本面/政策评分：两融余额已在「流动性」组内，主力净流入为东方财富按成交单大小划分的口径，
  噪声大，若再加权会和流动性因子重复计数，也会稀释用户要求的政策高权重。

数据源
- 主力净流入：东方财富 push2his fflow/daykline（指数成分股合计；主力=超大单+大单），近 30 个交易日日线；
  沪深两市合计用 secid=上证指数 + secid2=深证成指（与东方财富「大盘资金流」页面同口径）。
- ETF 份额：上交所 ETF 规模（commonQuery COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L，按统计日期）；
  净申赎金额（估算）= 份额变化 × 区间平均收盘价（新浪日线）。深交所只提供当日份额、没有历史，深市 ETF 不纳入。
- 两融：上交所融资余额（akshare.stock_margin_sse，元）+ 深交所（akshare.stock_margin_szse，亿元）。
- 北向：2024 年 8 月起交易所不再披露北向每日净买额，标「无数据」。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
YI = 1e8

FLOW_CACHE_TTL = 3 * 3600          # 日频数据，3 小时足够
FLOW_BATCH_TIMEOUT_SEC = 90.0
FLOW_WORKERS = 6
FLOW_MAX_AGE_DAYS = 10             # 数据日期超过 10 个自然日视为过期 → 无数据
FLOW_LMT = 30                      # 拉近 30 个交易日（20 日合计需要 ≥20）
HTTP_TIMEOUT = 15
HTTP_RETRIES = 3
WINDOWS = (1, 5, 20)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")

# tracked=True 的三只与页面指数卡片对应；其余只出现在资金总览表。
FLOW_INDICES: list[dict] = [
    {"id": "csi300", "name": "沪深300", "secid": "1.000300", "tracked": True,
     "etfs": [("510300", "华泰柏瑞沪深300ETF"), ("510310", "易方达沪深300ETF"), ("510330", "华夏沪深300ETF")]},
    {"id": "sse50", "name": "上证50", "secid": "1.000016", "tracked": True,
     "etfs": [("510050", "华夏上证50ETF"), ("510100", "易方达上证50ETF")]},
    {"id": "csi1000", "name": "中证1000", "secid": "1.000852", "tracked": True,
     "etfs": [("512100", "南方中证1000ETF"), ("560010", "广发中证1000ETF")]},
    {"id": "csi500", "name": "中证500", "secid": "1.000905", "tracked": False,
     "etfs": [("510500", "南方中证500ETF"), ("512500", "华夏中证500ETF")]},
    {"id": "star50", "name": "科创50", "secid": "1.000688", "tracked": False,
     "etfs": [("588000", "华夏科创50ETF"), ("588080", "易方达科创50ETF")]},
    {"id": "chinext", "name": "创业板指", "secid": "0.399006", "tracked": False, "etfs": [],
     "etf_note": "创业板ETF在深交所上市，深交所不提供历史份额，无法计算申赎"},
]
MARKET_SECIDS = ("1.000001", "0.399001")

FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
SSE_ETF_URL = "https://query.sse.com.cn/commonQuery.do"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

SRC = {
    "main": {"label": "东方财富 指数资金流（主力=超大单+大单）", "url": "https://data.eastmoney.com/zjlx/dpzjlx.html",
             "function": "push2his.eastmoney.com fflow/daykline"},
    "market": {"label": "东方财富 大盘资金流（沪深两市）", "url": "https://data.eastmoney.com/zjlx/dpzjlx.html",
               "function": "push2his.eastmoney.com fflow/daykline secid=1.000001&secid2=0.399001"},
    "etf": {"label": "上交所 ETF 规模（份额）+ 新浪日线收盘价", "url": "https://www.sse.com.cn/assortment/fund/etf/list/scale/",
            "function": "query.sse.com.cn COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"},
    "margin_sh": {"label": "上交所 融资融券汇总", "url": "https://www.sse.com.cn/market/othersdata/margin/sum/",
                  "function": "akshare.stock_margin_sse"},
    "margin_sz": {"label": "深交所 融资融券汇总", "url": "https://www.szse.cn/disclosure/margin/margin/index.html",
                  "function": "akshare.stock_margin_szse"},
    "northbound": {"label": "东方财富 沪深港通资金", "url": "https://data.eastmoney.com/hsgt/index.html",
                   "function": "akshare.stock_hsgt_fund_flow_summary_em"},
}
NORTHBOUND_REASON = "2024年8月起交易所不再披露北向资金每日净买额，免费源无可靠数据"
NO_DATA = "无数据"


# ---------------------------------------------------------------- helpers
def _r(x: float | None, n: int = 2) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        x = float(v)
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


def _stale(asof: date | None, today: date) -> bool:
    return asof is None or (today - asof).days > FLOW_MAX_AGE_DAYS


def _missing(reason: str, **kw) -> dict:
    out = {"status": "missing", "reason": reason}
    out.update(kw)
    return out


# ---------------------------------------------------------------- parsers (pure, tested with fixtures)
def parse_fflow(js: Any) -> list[dict]:
    """东方财富 fflow/daykline JSON → [{date, main, super, large, mid, small, main_pct}]（元），按日期升序。"""
    data = (js or {}).get("data") if isinstance(js, dict) else None
    klines = (data or {}).get("klines") or []
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        d = _to_date(parts[0])
        main = _num(parts[1])
        if d is None or main is None:
            continue
        rows.append({
            "date": d, "main": main, "small": _num(parts[2]), "mid": _num(parts[3]),
            "large": _num(parts[4]), "super": _num(parts[5]), "main_pct": _num(parts[6]),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def window_sum(rows: list[dict], n: int, key: str = "main") -> float | None:
    """最近 n 个交易日合计；不足 n 天返回 None（不拿短窗口冒充）。"""
    if len(rows) < n:
        return None
    vals = [r.get(key) for r in rows[-n:]]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def main_flow_summary(rows: list[dict], today: date) -> dict:
    if not rows:
        return _missing("接口无返回")
    last = rows[-1]["date"]
    if _stale(last, today):
        return _missing(f"数据过期（最新 {last.isoformat()}）", asof=_iso(last))
    out: dict[str, Any] = {"status": "ok", "asof": _iso(last), "unit": "亿元", "days": len(rows)}
    for n in WINDOWS:
        s = window_sum(rows, n)
        out[f"net_{n}d"] = _r(s / YI) if s is not None else None
    out["super_1d"] = _r(rows[-1]["super"] / YI) if rows[-1].get("super") is not None else None
    out["large_1d"] = _r(rows[-1]["large"] / YI) if rows[-1].get("large") is not None else None
    out["main_pct_1d"] = _r(rows[-1].get("main_pct"))
    up = sum(1 for r in rows[-5:] if r["main"] > 0) if len(rows) >= 5 else None
    out["inflow_days_5"] = up
    return out


def parse_sse_etf_scale(js: Any) -> dict[str, tuple[date | None, float]]:
    """上交所 ETF 规模 JSON → {code: (统计日期, 份额[份])}；TOT_VOL 单位为万份。"""
    res = (js or {}).get("result") if isinstance(js, dict) else None
    out: dict[str, tuple[date | None, float]] = {}
    for row in res or []:
        code = str(row.get("SEC_CODE") or "").strip()
        vol = _num(row.get("TOT_VOL"))
        if not code or vol is None:
            continue
        out[code] = (_to_date(row.get("STAT_DATE")), vol * 10000.0)
    return out


def parse_sina_kline(js: Any) -> list[tuple[date, float]]:
    out = []
    for row in js or []:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("day"))
        c = _num(row.get("close"))
        if d and c is not None:
            out.append((d, c))
    out.sort()
    return out


def etf_flow(code: str, name: str, shares_by_date: dict[date, dict[str, tuple[date | None, float]]],
             cal: list[date], prices: list[tuple[date, float]]) -> dict:
    """单只 ETF：最新份额与 1/5/20 日份额变化（亿份）、估算净申赎（亿元）= Δ份额 × 区间平均收盘价。"""
    if not cal:
        return _missing("无交易日历", code=code, name=name)
    t = cal[-1]
    cur = (shares_by_date.get(t) or {}).get(code)
    if cur is None:
        return _missing("上交所无该日份额", code=code, name=name, asof=_iso(t))
    s_t = cur[1]
    out: dict[str, Any] = {"status": "ok", "code": code, "name": name, "asof": _iso(t),
                           "shares_yi": _r(s_t / YI, 2), "unit": "亿元"}
    for n in WINDOWS:
        if len(cal) <= n:
            out[f"shares_chg_{n}d"] = None
            out[f"net_{n}d"] = None
            continue
        d0 = cal[-1 - n]
        prev = (shares_by_date.get(d0) or {}).get(code)
        if prev is None:
            out[f"shares_chg_{n}d"] = None
            out[f"net_{n}d"] = None
            continue
        dsh = s_t - prev[1]
        px = [c for d, c in prices if d0 < d <= t]
        avg = sum(px) / len(px) if px else None
        out[f"shares_chg_{n}d"] = _r(dsh / YI, 3)
        out[f"net_{n}d"] = _r(dsh * avg / YI, 2) if avg is not None else None
    return out


def sum_etf(items: list[dict], key: str) -> float | None:
    vals = [i.get(key) for i in items if i.get("status") == "ok"]
    vals = [v for v in vals if v is not None]
    return _r(sum(vals), 2) if vals else None


def margin_summary(series: list[tuple[date, float]], today: date, src_key: str,
                   cal: list[date] | None = None) -> dict:
    """融资余额序列（亿元）→ 最新余额与 1/5/20 个交易日变化。

    有交易日历时按日历取 T-n 那一天（序列里没有那天 ⇒ None，不拿相邻日顶替）；没有日历时按序列位置。
    """
    base = {"source": SRC[src_key]["label"], "source_url": SRC[src_key]["url"]}
    if not series:
        return _missing("接口无返回", **base)
    series = sorted(series)
    by_d = dict(series)
    last_d, last_v = series[-1]
    if _stale(last_d, today):
        return _missing(f"数据过期（最新 {last_d.isoformat()}）", asof=_iso(last_d), **base)
    out: dict[str, Any] = {"status": "ok", "asof": _iso(last_d), "balance": _r(last_v, 1), "unit": "亿元", **base}
    c = [d for d in (cal or []) if d <= last_d]
    for n in WINDOWS:
        if c and c[-1] == last_d:
            prev = by_d.get(c[-1 - n]) if len(c) > n else None
        else:
            prev = series[-1 - n][1] if len(series) > n else None
        out[f"chg_{n}d"] = _r(last_v - prev, 1) if prev is not None else None
    return out


# ---------------------------------------------------------------- network fetchers (background only)
class SourceRateLimited(RuntimeError):
    """东方财富 push2 断开连接 = 临时限流；立即停止后续请求，避免延长封禁。"""


def _get_json(url: str, params: dict, headers: dict | None = None, retries: int = HTTP_RETRIES) -> Any:
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
        except requests.exceptions.ConnectionError as exc:
            if "eastmoney" in url:
                raise SourceRateLimited("东方财富 push2 断开连接（疑似限流），本轮停止请求") from exc
            last_exc = exc
        except Exception as exc:  # noqa: BLE001  偶发空包/5xx：退避后重试
            last_exc = exc
        time.sleep(1.0 * (i + 1))
    raise last_exc or RuntimeError("request failed")


def fetch_fflow(secid: str, secid2: str | None = None, lmt: int = FLOW_LMT) -> list[dict]:
    params = {"lmt": lmt, "klt": 101, "secid": secid, "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
    if secid2:
        params["secid2"] = secid2
    rows = parse_fflow(_get_json(FFLOW_URL, params, {"Referer": "https://data.eastmoney.com/zjlx/dpzjlx.html"}, retries=2))
    if not rows:
        raise ValueError("fflow 无数据")
    return rows


def fetch_sse_etf_scale(d: date) -> dict[str, tuple[date | None, float]]:
    params = {"isPagination": "true", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1",
              "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
              "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "STAT_DATE": d.isoformat()}
    return parse_sse_etf_scale(_get_json(SSE_ETF_URL, params, {"Referer": "https://www.sse.com.cn/"}))


def fetch_sina_kline(symbol: str, datalen: int = FLOW_LMT) -> list[tuple[date, float]]:
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": datalen}
    return parse_sina_kline(_get_json(SINA_KLINE_URL, params, {"Referer": "https://finance.sina.com.cn/"}))


def fetch_margin_sh(start: date, end: date) -> list[tuple[date, float]]:
    import akshare as ak

    df = ak.stock_margin_sse(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    out = []
    for _, row in df.iterrows():
        d = _to_date(row.get("信用交易日期"))
        v = _num(row.get("融资余额"))
        if d and v is not None:
            out.append((d, v / YI))
    return sorted(out)


def fetch_margin_sz(dates: list[date]) -> list[tuple[date, float]]:
    import akshare as ak

    out = []
    for d in dates:
        try:
            df = ak.stock_margin_szse(date=d.strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001  某日无数据（未发布/休市）就跳过，不插补
            continue
        if df is None or df.empty:
            continue
        v = _num(df.iloc[0].get("融资余额"))
        if v is not None:
            out.append((d, v))   # 深交所接口单位已是亿元
    return sorted(out)


# ---------------------------------------------------------------- bundle
def _cal_from(*seqs: list[date]) -> list[date]:
    for s in seqs:
        if s and len(s) >= 2:
            return sorted(set(s))
    return []



def attach_etf_holders(bundle: dict, holders: dict | None) -> dict:
    """把机构ETF持仓挂到已有资金流 bundle 上；不改主力/ETF申赎/两融字段。"""
    from cn_index_etf_holders import HOLDER_NOTES, SRC as HSRC

    bundle = dict(bundle)
    indices = {k: dict(v) for k, v in (bundle.get("indices") or {}).items()}
    hidx = (holders or {}).get("indices") or {}
    reason = (holders or {}).get("reason") or "无持仓数据"
    for iid, row in indices.items():
        h = hidx.get(iid)
        row["holders"] = h if isinstance(h, dict) else {
            "status": "missing", "reason": reason,
            "source": HSRC["label"], "source_url": HSRC["url"],
        }
        indices[iid] = row
    bundle["indices"] = indices
    notes = list(bundle.get("notes") or [])
    for n in HOLDER_NOTES:
        if n not in notes:
            notes.append(n)
    bundle["notes"] = notes
    if holders:
        if holders.get("fetched_at"):
            bundle["holders_fetched_at"] = holders.get("fetched_at")
        bundle["holders_from_seed"] = bool(holders.get("from_seed"))
    return bundle


def ensure_etf_holders(bundle: dict | None, holders: dict | None = None) -> dict | None:
    """若卡片还没有 holders 字段，用传入数据或本地种子补上。只读文件，不联网。"""
    if not isinstance(bundle, dict) or not bundle.get("indices"):
        return bundle
    rows = [v for v in bundle["indices"].values() if isinstance(v, dict)]
    if rows and all("holders" in r for r in rows):
        return bundle
    if holders is None:
        from cn_index_etf_holders import load_holder_bundle
        holders = load_holder_bundle()
    return attach_etf_holders(bundle, holders)


def build_flow_bundle(raw: dict, errors: dict, today: date) -> dict:
    """纯函数：raw 来自 fetchers（或测试夹具），输出前端/快照用 bundle。"""
    indices: dict[str, dict] = {}
    cal: list[date] = raw.get("calendar") or []
    shares_by_date = raw.get("etf_shares") or {}
    prices = raw.get("etf_prices") or {}
    for meta in FLOW_INDICES:
        iid = meta["id"]
        rows = raw.get(f"main:{iid}")
        if rows:
            main = main_flow_summary(rows, today)
        else:
            main = _missing(errors.get(f"main:{iid}") or "接口无返回")
        main.update({"source": SRC["main"]["label"], "source_url": SRC["main"]["url"]})
        if meta["etfs"]:
            items = []
            for code, name in meta["etfs"]:
                if not shares_by_date:
                    items.append(_missing(errors.get("etf_shares") or "上交所份额接口无返回", code=code, name=name))
                    continue
                it = etf_flow(code, name, shares_by_date, cal, prices.get(code) or [])
                if it.get("status") == "ok" and _stale(_to_date(it.get("asof")), today):
                    it = _missing(f"数据过期（{it.get('asof')}）", code=code, name=name)
                items.append(it)
            ok = [i for i in items if i.get("status") == "ok"]
            etf = {"status": "ok" if ok else "missing", "items": items, "unit": "亿元",
                   "asof": ok[0]["asof"] if ok else None,
                   "net_1d": sum_etf(items, "net_1d"), "net_5d": sum_etf(items, "net_5d"),
                   "net_20d": sum_etf(items, "net_20d"),
                   "coverage": f"{len(ok)}/{len(items)} 只有数据",
                   "source": SRC["etf"]["label"], "source_url": SRC["etf"]["url"],
                   "method": "净申赎≈份额变化×区间平均收盘价（估算）；只统计上交所上市的主要跟踪ETF"}
            if not ok:
                etf["reason"] = items[0].get("reason") if items else "无数据"
        else:
            etf = _missing(meta.get("etf_note") or "无可用ETF份额数据",
                           source=SRC["etf"]["label"], source_url=SRC["etf"]["url"])
        indices[iid] = {"id": iid, "name": meta["name"], "tracked": meta["tracked"], "main": main, "etf": etf}

    mrows = raw.get("main:market")
    market_main = main_flow_summary(mrows, today) if mrows else _missing(errors.get("main:market") or "接口无返回")
    market_main.update({"source": SRC["market"]["label"], "source_url": SRC["market"]["url"]})

    sh = margin_summary(raw.get("margin_sh") or [], today, "margin_sh", cal)
    if not raw.get("margin_sh") and errors.get("margin_sh"):
        sh["reason"] = errors["margin_sh"]
    sz = margin_summary(raw.get("margin_sz") or [], today, "margin_sz", cal)
    if not raw.get("margin_sz") and errors.get("margin_sz"):
        sz["reason"] = errors["margin_sz"]
    total: dict[str, Any]
    if sh.get("status") == "ok" and sz.get("status") == "ok" and sh.get("asof") == sz.get("asof"):
        total = {"status": "ok", "asof": sh["asof"], "unit": "亿元", "balance": _r(sh["balance"] + sz["balance"], 1)}
        for n in WINDOWS:
            a, b = sh.get(f"chg_{n}d"), sz.get(f"chg_{n}d")
            total[f"chg_{n}d"] = _r(a + b, 1) if a is not None and b is not None else None
    else:
        total = _missing("沪深两市日期不一致或有一方缺失，不合计")

    northbound = _missing(NORTHBOUND_REASON, source=SRC["northbound"]["label"], source_url=SRC["northbound"]["url"])

    asofs = [v["main"].get("asof") for v in indices.values() if v["main"].get("asof")]
    bundle = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "asof": max(asofs) if asofs else market_main.get("asof"),
        "unit": "亿元",
        "indices": indices,
        "order": [m["id"] for m in FLOW_INDICES],
        "market": {"main": market_main, "margin_sh": sh, "margin_sz": sz, "margin_total": total,
                   "northbound": northbound},
        "errors": dict(errors),
        "in_score": False,
        "notes": [
            "资金流只做展示，不计入基本面/综合分（两融已在流动性因子中，避免重复计数；政策仍是最高权重）",
            "主力净流入为东方财富按单笔成交额划分的估算口径（超大单+大单），不是交易所官方申报数据",
            "ETF 净申赎=份额变化×区间平均收盘价，为估算；深交所上市ETF无历史份额，未纳入",
            NORTHBOUND_REASON,
        ],
    }
    if raw.get("etf_holders"):
        bundle = attach_etf_holders(bundle, raw.get("etf_holders"))
    return bundle


FFLOW_SPACING_SEC = 1.2   # 东方财富 push2 对并发/高频很敏感：串行 + 间隔


def fetch_main_all() -> dict:
    """串行拉取各指数 + 两市合计主力资金；遇到限流立刻停，剩余项记错误（显示无数据）。"""
    out: dict[str, Any] = {"errors": {}}
    jobs = [(f"main:{m['id']}", m["secid"], None) for m in FLOW_INDICES]
    jobs.append(("main:market", MARKET_SECIDS[0], MARKET_SECIDS[1]))
    for i, (key, sid, sid2) in enumerate(jobs):
        if i:
            time.sleep(FFLOW_SPACING_SEC)
        try:
            out[key] = fetch_fflow(sid, sid2)
        except SourceRateLimited as exc:
            for k, _, _ in jobs[i:]:
                out["errors"][k] = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            out["errors"][key] = f"{type(exc).__name__}: {str(exc)[:80]}"
    return out


def default_fetchers() -> dict[str, Callable[[], Any]]:
    f: dict[str, Callable[[], Any]] = {"main_all": fetch_main_all}
    f["cal"] = lambda: [d for d, _ in fetch_sina_kline("sh000300", FLOW_LMT)]
    codes = [c for m in FLOW_INDICES for c, _ in m["etfs"]]
    f["etf_prices"] = lambda: {c: _safe_kline(f"sh{c}") for c in codes}
    f["margin_sh"] = lambda: fetch_margin_sh(datetime.now(CST).date() - timedelta(days=45), datetime.now(CST).date())
    return f


def _safe_kline(sym: str) -> list[tuple[date, float]]:
    try:
        return fetch_sina_kline(sym, FLOW_LMT)
    except Exception:  # noqa: BLE001
        logger.debug("sina kline failed %s", sym, exc_info=True)
        return []


def _shares_for_calendar(cal: list[date]) -> tuple[list[date], dict[date, dict]]:
    """上交所份额：取 T、T-1、T-5、T-20 四个交易日；T 未发布则整体后移一天（只后移一次）。"""
    out: dict[date, dict] = {}
    for shift in (0, 1):
        c = cal[: len(cal) - shift] if shift else cal
        if len(c) < 2:
            break
        need = [c[-1]] + [c[-1 - n] for n in WINDOWS if len(c) > n]
        with ThreadPoolExecutor(max_workers=4) as pool:
            res = dict(zip(need, pool.map(lambda d: _safe_sse(d), need)))
        if res.get(c[-1]):
            out = {d: v for d, v in res.items() if v}
            return c, out
    return cal, out


def _safe_sse(d: date) -> dict:
    try:
        return fetch_sse_etf_scale(d)
    except Exception:  # noqa: BLE001
        logger.debug("sse etf scale failed %s", d, exc_info=True)
        return {}


class IndexFlowProvider:
    """Cached bundle. ``get_cached`` never touches the network; ``refresh`` is single-flight."""

    def __init__(self, ttl: float = FLOW_CACHE_TTL, fetchers: dict[str, Callable[[], Any]] | None = None):
        self.ttl = ttl
        self._fetchers = fetchers
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None

    def get_cached(self) -> dict | None:
        with self._lock:
            if self._cache and time.time() - self._cache[0] < self.ttl:
                return self._cache[1]
        return None

    def refresh(self, force: bool = False) -> dict:
        if not force:
            c = self.get_cached()
            if c is not None:
                return c
        with self._fetch_lock:
            if not force:
                c = self.get_cached()
                if c is not None:
                    return c
            return self._refresh_locked()

    def _refresh_locked(self) -> dict:
        t0 = time.time()
        raw: dict[str, Any] = {}
        errors: dict[str, str] = {}
        jobs = self._fetchers if self._fetchers is not None else default_fetchers()
        pool = ThreadPoolExecutor(max_workers=FLOW_WORKERS)
        futs = {pool.submit(fn): k for k, fn in jobs.items()}
        done, not_done = wait(list(futs), timeout=FLOW_BATCH_TIMEOUT_SEC)
        for fut in done:
            k = futs[fut]
            try:
                raw[k] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors[k] = f"{type(exc).__name__}: {str(exc)[:80]}"
        for fut in not_done:
            errors[futs[fut]] = f"超时（>{FLOW_BATCH_TIMEOUT_SEC:.0f}s）"
            fut.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        main_all = raw.pop("main_all", None)
        if isinstance(main_all, dict):
            errors.update(main_all.pop("errors", {}) or {})
            raw.update(main_all)
        elif "main_all" in errors:
            for m in FLOW_INDICES:
                errors.setdefault(f"main:{m['id']}", errors["main_all"])
            errors.setdefault("main:market", errors["main_all"])

        cal_main = [r["date"] for r in (raw.get("main:csi300") or [])]
        cal = _cal_from(raw.pop("cal", None) or [], cal_main)
        raw["calendar"] = cal
        if self._fetchers is None and cal:
            cal2, shares = _shares_for_calendar(cal)
            if shares:
                raw["calendar"] = cal2
                raw["etf_shares"] = shares
            else:
                errors["etf_shares"] = "上交所ETF份额接口无返回"
            sz_dates = [cal[-1 - n] for n in (0,) + WINDOWS if len(cal) > n]
            try:
                raw["margin_sz"] = fetch_margin_sz(sz_dates)
            except Exception as exc:  # noqa: BLE001
                errors["margin_sz"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        bundle = build_flow_bundle(raw, errors, datetime.now(CST).date())
        # 机构持仓单独取数：失败只回退种子，不得把主力/ETF申赎打成无数据。注入 fetchers 时不联网。
        try:
            if raw.get("etf_holders"):
                holders = raw["etf_holders"]
            elif self._fetchers is None:
                from cn_index_etf_holders import refresh_holder_bundle
                holders = refresh_holder_bundle(live=True)
            else:
                holders = None
            bundle = ensure_etf_holders(bundle, holders)
        except Exception:  # noqa: BLE001
            logger.warning("etf holders attach failed; keeping flow bundle", exc_info=True)
            try:
                bundle = ensure_etf_holders(bundle)
            except Exception:  # noqa: BLE001
                logger.debug("etf holders seed attach also failed", exc_info=True)
        bundle["fetch_seconds"] = round(time.time() - t0, 1)
        with self._lock:
            self._cache = (time.time(), bundle)
        logger.info("cn index flows: %d errors, %.1fs", len(errors), time.time() - t0)
        return bundle


DEFAULT_FLOW_PROVIDER = IndexFlowProvider()


def flow_bundle_age_hours(bundle: dict | None, now: datetime | None = None) -> float | None:
    if not isinstance(bundle, dict) or not bundle.get("fetched_at"):
        return None
    try:
        ts = datetime.fromisoformat(str(bundle["fetched_at"]))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=CST)
    now = now or datetime.now(CST)
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def pick_flow_bundle(*bundles: dict | None) -> dict | None:
    best, best_age = None, None
    for b in bundles:
        age = flow_bundle_age_hours(b)
        if age is None:
            continue
        if best_age is None or age < best_age:
            best, best_age = b, age
    return best


__all__ = ["IndexFlowProvider", "DEFAULT_FLOW_PROVIDER", "build_flow_bundle", "parse_fflow",
           "parse_sse_etf_scale", "parse_sina_kline", "etf_flow", "margin_summary", "main_flow_summary",
           "pick_flow_bundle", "flow_bundle_age_hours", "FLOW_CACHE_TTL", "NO_DATA",
           "attach_etf_holders", "ensure_etf_holders"]


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    b = DEFAULT_FLOW_PROVIDER.refresh(force=True)
    print(json.dumps(b, ensure_ascii=False, indent=1, default=str)[:6000])
