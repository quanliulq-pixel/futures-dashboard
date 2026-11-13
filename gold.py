# -*- coding: utf-8 -*-
"""黄金研究与盯盘页（/api/gold）。

架构与 美气NG / A股指数 相同：请求只读内存/本地快照（秒回），过期时后台单飞刷新；
任何上游失败只影响对应模块，模块回退到 10 天（月度/周度数据更长）内的上次有效快照并标注
「上次快照（日期）」，没有就如实显示「无数据」。绝不编造数字，也不使用东方财富实时推送接口。

已在本机实测可用的公开数据源（无需 API key）：
- 新浪实时 hq.sinajs.cn：hf_GC（COMEX金）、hf_XAU（伦敦金现货）、hf_SI、nf_AU0（沪金连续）、
  fx_susdcny（在岸人民币）、sh518880（华安黄金ETF）→ 同一时刻内外盘比价。
- 新浪日线：InnerFuturesNewService.getDailyKLine?symbol=AU0（沪金主连），
  GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU/GC/SI（伦敦金/COMEX金/银，Yahoo 失败时兜底）。
- Yahoo Finance 日线：GC=F、SI=F、USDCNY=X、^TNX、DX-Y.NYB、^VIX。
- 上海黄金交易所 www.sge.com.cn/graph/Dailyhq：Au99.99 日线。
- FRED fredgraph.csv：DFII10（10Y TIPS 实际利率）、DGS10（10Y 名义）。
- CFTC Socrata publicreporting.cftc.gov 6dca-aqww：COMEX 黄金（088691）非商业净多。
- 金十数据 datacenter-api.jin10.com（category=etf, attr_id=1）：SPDR Gold Trust 持仓吨数。
- 东方财富数据中心 datacenter-web RPT_ECONOMY_GOLD_CURRENCY：中国黄金储备（亿美元，非吨数）。
- 上交所 ETF 规模 query.sse.com.cn + 新浪 ETF 日线：518880/518800/518660 份额与估算净申赎。
- 天天基金 F10 cyrjg：518880 半年报机构持有比例。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
SEED_PATHS = (ROOT / "data" / "gold.json", ROOT / "docs" / "api" / "gold.json")
RUNTIME_PATH = ROOT / "data" / "gold_latest.json"
REMOTE_SEED_URL = "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/gold.json"
OZ_G = 31.1034768  # 1 金衡盎司 = 31.1034768 克
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HTTP_TIMEOUT = 15
FRED_UA = "futures-dashboard/1.0 (+https://github.com/quanliulq-pixel/futures-dashboard)"
REFRESH_HOURS = 3.0
CHART_DAYS = 500
CN_GOLD_ETFS = [("518880", "华安黄金ETF"), ("518800", "国泰黄金ETF"), ("518660", "工银黄金ETF")]
YAHOO_SYMBOLS = ["GC=F", "SI=F", "USDCNY=X", "^TNX", "DX-Y.NYB", "^VIX"]
DISCLAIMER = "研究参考，不能下单，不构成投资建议"

# 每个模块回退到上次快照时允许的最大日历天数（按数据本身的 asof 计算）。
MAX_SNAPSHOT_AGE = {
    "realtime": 10, "prices": 10, "premium": 10, "ratio": 10, "technicals": 10, "macro": 10,
    "spdr": 10, "cftc": 21, "cn_etfs": 10, "inst": 400, "pboc": 75,
}

SOURCES = [
    {"key": "sina_rt", "label": "新浪实时：COMEX金 hf_GC / 伦敦金 hf_XAU / 白银 hf_SI / 沪金 nf_AU0 / 在岸人民币 fx_susdcny / 518880",
     "url": "https://finance.sina.com.cn/futuremarket/"},
    {"key": "sina_daily", "label": "新浪日线：沪金主连 AU0、伦敦金 XAU（Yahoo 失败时 COMEX GC/SI 兜底）",
     "url": "https://finance.sina.com.cn/futures/quotes/AU0.shtml"},
    {"key": "yahoo", "label": "Yahoo Finance 日线：GC=F、SI=F、USDCNY=X、^TNX、DX-Y.NYB、^VIX",
     "url": "https://finance.yahoo.com/quote/GC%3DF/history/"},
    {"key": "sge", "label": "上海黄金交易所 Au99.99 日线", "url": "https://www.sge.com.cn/sjzx/mrhq"},
    {"key": "fred", "label": "FRED：DFII10 10年期TIPS实际利率、DGS10 10年期名义利率",
     "url": "https://fred.stlouisfed.org/series/DFII10"},
    {"key": "cftc", "label": "CFTC COT（Legacy 期货）：COMEX 黄金 088691 非商业持仓",
     "url": "https://publicreporting.cftc.gov/resource/6dca-aqww"},
    {"key": "spdr", "label": "SPDR Gold Trust（GLD）持仓吨数，金十数据整理",
     "url": "https://datacenter.jin10.com/reportType/dc_etf_gold"},
    {"key": "pboc", "label": "中国官方黄金储备（亿美元，国家外汇管理局口径，东方财富数据中心）",
     "url": "https://data.eastmoney.com/cjsj/hjwh.html"},
    {"key": "sse_etf", "label": "上交所 ETF 规模（份额）+ 新浪 ETF 日线：518880 / 518800 / 518660",
     "url": "https://www.sse.com.cn/assortment/fund/etf/list/scale/"},
    {"key": "cyrjg", "label": "天天基金 F10 持有人结构（半年报）：518880 机构持有比例",
     "url": "https://fundf10.eastmoney.com/cyrjg_518880.html"},
]

_cache: dict | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------- small helpers (pure)
def _num(v: Any) -> float | None:
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(x: float | None, n: int = 2) -> float | None:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return round(float(x), n)


def _to_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _now() -> datetime:
    return datetime.now(CST)


def _clean(series: Any) -> list[tuple[date, float]]:
    """[(date, value)] 升序、去重、去掉非正/非数值；不做任何填充。"""
    out: dict[date, float] = {}
    for row in series or []:
        try:
            d, v = row[0], row[1]
        except (TypeError, IndexError, KeyError):
            continue
        d, v = _to_date(d), _num(v)
        if d is not None and v is not None:
            out[d] = v
    return sorted(out.items())


def pct_change(series: list[tuple[date, float]], n: int) -> float | None:
    if len(series) <= n or not series[-1 - n][1]:
        return None
    return (series[-1][1] / series[-1 - n][1] - 1.0) * 100.0


def diff_change(series: list[tuple[date, float]], n: int) -> float | None:
    if len(series) <= n:
        return None
    return series[-1][1] - series[-1 - n][1]


def sma(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def rsi(vals: list[float], n: int = 14) -> float | None:
    """Wilder RSI。"""
    if len(vals) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = vals[i] - vals[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(vals)):
        ch = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(ch, 0.0)) / n
        al = (al * (n - 1) + max(-ch, 0.0)) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def hv_series(vals: list[float], n: int = 20) -> list[float | None]:
    """年化历史波动率（对数收益，252 日）。"""
    rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0 and vals[i] > 0]
    out: list[float | None] = [None]
    for i in range(1, len(vals)):
        if i < n:
            out.append(None)
            continue
        w = rets[i - n:i]
        if len(w) < n:
            out.append(None)
            continue
        m = sum(w) / n
        var = sum((x - m) ** 2 for x in w) / (n - 1)
        out.append(math.sqrt(var) * math.sqrt(252) * 100.0)
    return out


def pct_rank(window: list[float], x: float | None) -> float | None:
    """x 在窗口中的分位（0-100，含等于）。"""
    vals = [v for v in window if v is not None]
    if x is None or len(vals) < 20:
        return None
    return sum(1 for v in vals if v <= x) / len(vals) * 100.0


def align_prior(cn: list[tuple[date, float]], intl: list[tuple[date, float]]) -> list[tuple[date, float, date]]:
    """国内 d 日收盘对齐「严格早于 d」的最近一个海外收盘（无前视）。返回 (d, intl_value, intl_date)。"""
    out = []
    j = -1
    for d, _ in cn:
        while j + 1 < len(intl) and intl[j + 1][0] < d:
            j += 1
        if j >= 0 and (d - intl[j][0]).days <= 5:
            out.append((d, intl[j][1], intl[j][0]))
    return out


def intl_rmb_per_gram(usd_per_oz: float | None, usdcny: float | None) -> float | None:
    if usd_per_oz is None or usdcny is None:
        return None
    return usd_per_oz * usdcny / OZ_G


def _missing(reason: str, **kw) -> dict:
    out = {"status": "missing", "reason": reason}
    out.update(kw)
    return out


# ---------------------------------------------------------------- parsers (pure)
def parse_sina_hq(text: str) -> dict[str, list[str]]:
    out = {}
    for m in re.finditer(r'var hq_str_([A-Za-z0-9_]+)="([^"]*)";', text or ""):
        if m.group(2).strip():
            out[m.group(1)] = m.group(2).split(",")
    return out


def realtime_from_hq(hq: dict[str, list[str]]) -> dict:
    """新浪 hq 字段 → 实时报价。hf_*：0最新 7昨结 12日期 6时间；nf_AU0：8最新 10昨结 17日期 1时间；
    fx_susdcny：8最新 3昨收；sh518880：3最新 2昨收 30日期 31时间。"""
    def hf(key):
        f = hq.get(key)
        if not f or len(f) < 13:
            return None
        last, prev = _num(f[0]), _num(f[7])
        if not last:
            return None
        return {"last": last, "prev": prev, "chg_pct": _r((last / prev - 1) * 100, 2) if prev else None,
                "time": f"{f[12]} {f[6]}".strip()}
    q: dict[str, Any] = {"gc": hf("hf_GC"), "xau": hf("hf_XAU"), "si": hf("hf_SI")}
    f = hq.get("nf_AU0")
    if f and len(f) > 17 and _num(f[8]):
        last, prev = _num(f[8]), _num(f[10])
        t = f[1].zfill(6)
        q["au0"] = {"last": last, "prev": prev, "chg_pct": _r((last / prev - 1) * 100, 2) if prev else None,
                    "time": f"{f[17]} {t[:2]}:{t[2:4]}:{t[4:6]}"}
    else:
        q["au0"] = None
    f = hq.get("fx_susdcny")
    if f and len(f) > 8 and (_num(f[8]) or _num(f[1])):
        last = _num(f[8]) or _num(f[1])
        prev = _num(f[3])
        q["usdcny"] = {"last": last, "prev": prev, "chg_pct": _r((last / prev - 1) * 100, 3) if prev else None,
                       "time": f"{f[-1]} {f[0]}".strip()}
    else:
        q["usdcny"] = None
    f = hq.get("sh518880")
    if f and len(f) > 31 and _num(f[3]):
        last, prev = _num(f[3]), _num(f[2])
        q["etf518880"] = {"last": last, "prev": prev, "chg_pct": _r((last / prev - 1) * 100, 2) if prev else None,
                          "time": f"{f[30]} {f[31]}"}
    else:
        q["etf518880"] = None
    return q


def parse_sina_jsonp_kline(text: str, date_key: str, close_key: str) -> list[tuple[date, float]]:
    a, b = (text or "").find("(["), (text or "").rfind("])")
    if a < 0 or b < 0:
        return []
    try:
        rows = json.loads(text[a + 1:b + 1])
    except ValueError:
        return []
    return _clean((r.get(date_key), r.get(close_key)) for r in rows if isinstance(r, dict))


def parse_sge_daily(js: Any) -> list[tuple[date, float]]:
    rows = (js or {}).get("time") if isinstance(js, dict) else None
    return _clean((r[0], r[2]) for r in rows or [] if isinstance(r, (list, tuple)) and len(r) >= 3)


def parse_fred_csv(text: str) -> list[tuple[date, float]]:
    out = []
    for line in (text or "").splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))  # "." = 缺失，_clean 会丢弃
    return _clean(out)


def parse_cftc(rows: Any) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = _to_date(r.get("report_date_as_yyyy_mm_dd"))
        lg, sh, oi = (_num(r.get(k)) for k in ("noncomm_positions_long_all", "noncomm_positions_short_all", "open_interest_all"))
        if d and lg is not None and sh is not None and oi:
            out.append({"date": d, "long": lg, "short": sh, "oi": oi, "net": lg - sh})
    out.sort(key=lambda x: x["date"])
    return out


def parse_jin10_etf(js: Any) -> list[tuple[date, float]]:
    vals = (((js or {}).get("data") or {}).get("values")) if isinstance(js, dict) else None
    return _clean((v[0], v[1]) for v in vals or [] if isinstance(v, (list, tuple)) and len(v) >= 2)


def parse_pboc(js: Any) -> list[dict]:
    data = (((js or {}).get("result") or {}).get("data")) if isinstance(js, dict) else None
    out = []
    for r in data or []:
        d = _to_date(r.get("REPORT_DATE"))
        v = _num(r.get("GOLD_RESERVES"))
        if d and v is not None:
            out.append({"month": d.strftime("%Y-%m"), "date": d, "value": v,
                        "mom_pct": _num(r.get("GOLD_RESERVES_SEQUENTIAL")), "yoy_pct": _num(r.get("GOLD_RESERVES_SAME"))})
    out.sort(key=lambda x: x["date"])
    return out


# ---------------------------------------------------------------- block builders (pure)
def _price_row(name: str, series: list[tuple[date, float]], unit: str, source: str) -> dict:
    if not series:
        return _missing("无数据", name=name, unit=unit, source=source)
    return {"status": "ok", "name": name, "unit": unit, "source": source, "asof": _iso(series[-1][0]),
            "last": _r(series[-1][1], 4), "chg_1d_pct": _r(pct_change(series, 1)),
            "chg_5d_pct": _r(pct_change(series, 5)), "chg_20d_pct": _r(pct_change(series, 20)),
            "chg_250d_pct": _r(pct_change(series, 250))}


def build_realtime(q: dict | None) -> dict:
    if not q or not (q.get("au0") or q.get("gc") or q.get("xau")):
        return _missing("新浪实时无数据")
    out = {"status": "ok", "quotes": q, "formula": "海外金价折人民币（元/克）= 美元/盎司 × USD/CNY ÷ 31.1034768"}
    fx = (q.get("usdcny") or {}).get("last")
    au = (q.get("au0") or {}).get("last")
    for key in ("xau", "gc"):
        usd = (q.get(key) or {}).get("last")
        rmb = intl_rmb_per_gram(usd, fx)
        out[f"{key}_rmb_g"] = _r(rmb, 2)
        if rmb and au:
            out[f"premium_vs_{key}"] = _r(au - rmb, 2)
            out[f"premium_vs_{key}_pct"] = _r((au / rmb - 1) * 100, 2)
    times = [v.get("time") for v in q.values() if isinstance(v, dict) and v.get("time")]
    out["asof"] = max(times)[:10] if times else None
    out["asof_time"] = max(times) if times else None
    return out


def build_premium(au: list[tuple[date, float]], intl: list[tuple[date, float]], fx: list[tuple[date, float]],
                  intl_name: str) -> dict:
    if not au or not intl or not fx:
        return _missing("沪金或海外金价/汇率无数据")
    fx_map = dict(fx)
    aligned = align_prior(au, intl)
    au_map = dict(au)
    fx_sorted = sorted(fx_map)
    rows = []
    k = -1
    for d, usd, intl_d in aligned:
        while k + 1 < len(fx_sorted) and fx_sorted[k + 1] <= intl_d:
            k += 1
        if k < 0 or (intl_d - fx_sorted[k]).days > 5:
            continue
        rmb = intl_rmb_per_gram(usd, fx_map[fx_sorted[k]])
        a = au_map[d]
        rows.append({"d": _iso(d), "au": _r(a, 2), "intl_usd": _r(usd, 2), "intl_rmb": _r(rmb, 2),
                     "prem": _r(a - rmb, 2), "prem_pct": _r((a / rmb - 1) * 100, 3)})
    if not rows:
        return _missing("沪金与海外金价无可对齐交易日")
    pcts = [r["prem_pct"] for r in rows[-250:]]
    last = rows[-1]
    return {"status": "ok", "asof": last["d"], "intl": intl_name, "unit": "元/克",
            "latest": last["prem"], "latest_pct": last["prem_pct"],
            "mean_1y_pct": _r(sum(pcts) / len(pcts), 3), "max_1y_pct": max(pcts), "min_1y_pct": min(pcts),
            "rank_1y": _r(pct_rank(pcts, last["prem_pct"]), 1),
            "chg_5d_pct": _r(last["prem_pct"] - rows[-6]["prem_pct"], 3) if len(rows) > 5 else None,
            "series": rows[-CHART_DAYS:],
            "method": f"沪金主连 AU0 当日收盘（北京15:00） vs 前一海外交易日 {intl_name} 收盘 × 同日 USD/CNY ÷ 31.1034768；"
                      "日线时间不同步，仅用于看溢价趋势，最新同步比价以实时卡片为准。"}


def build_ratio(gold: list[tuple[date, float]], silver: list[tuple[date, float]]) -> dict:
    sm = dict(silver)
    rows = [(d, g / sm[d]) for d, g in gold if sm.get(d)]
    if len(rows) < 30:
        return _missing("金银价无数据")
    vals = [v for _, v in rows]
    w = vals[-750:]
    return {"status": "ok", "asof": _iso(rows[-1][0]), "latest": _r(vals[-1], 2),
            "rank_3y": _r(pct_rank(w, vals[-1]), 1), "max_3y": _r(max(w), 2), "min_3y": _r(min(w), 2),
            "chg_20d": _r(vals[-1] - vals[-21], 2) if len(vals) > 20 else None,
            "series": [{"d": _iso(d), "v": _r(v, 2)} for d, v in rows[-CHART_DAYS:]]}


def build_technicals(name: str, series: list[tuple[date, float]], unit: str) -> dict:
    if len(series) < 80:
        return _missing("日线不足", name=name, unit=unit)
    vals = [v for _, v in series]
    ma20, ma60, ma120 = sma(vals, 20), sma(vals, 60), sma(vals, 120)
    hv = hv_series(vals, 20)
    last = vals[-1]
    w52 = vals[-252:]
    hi, lo = max(w52), min(w52)
    hv_last = hv[-1]
    trend = "多头排列" if ma20[-1] and ma60[-1] and last > ma20[-1] > ma60[-1] else (
        "空头排列" if ma20[-1] and ma60[-1] and last < ma20[-1] < ma60[-1] else "震荡")
    return {"status": "ok", "name": name, "unit": unit, "asof": _iso(series[-1][0]), "last": _r(last, 2),
            "ma20": _r(ma20[-1], 2), "ma60": _r(ma60[-1], 2), "ma120": _r(ma120[-1], 2),
            "vs_ma60_pct": _r((last / ma60[-1] - 1) * 100, 2) if ma60[-1] else None,
            "rsi14": _r(rsi(vals, 14), 1), "hv20": _r(hv_last, 1),
            "hv_rank_1y": _r(pct_rank(hv[-252:], hv_last), 1),
            "high_52w": _r(hi, 2), "low_52w": _r(lo, 2),
            "pos_52w": _r((last - lo) / (hi - lo) * 100, 1) if hi > lo else None,
            "from_high_pct": _r((last / hi - 1) * 100, 2), "trend": trend,
            "chg_20d_pct": _r(pct_change(series, 20), 2)}


def _rolling_corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 30 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def build_macro(real: list[tuple[date, float]], nominal: list[tuple[date, float]], tnx: list[tuple[date, float]],
                dxy: list[tuple[date, float]], vix: list[tuple[date, float]], gold: list[tuple[date, float]]) -> dict:
    out: dict[str, Any] = {"status": "ok" if (real or dxy) else "missing"}
    if real:
        out["real10y"] = {"status": "ok", "asof": _iso(real[-1][0]), "last": _r(real[-1][1], 2), "unit": "%",
                          "chg_5d_bp": _r((diff_change(real, 5) or 0) * 100, 0) if len(real) > 5 else None,
                          "chg_20d_bp": _r(diff_change(real, 20) * 100, 0) if len(real) > 20 else None,
                          "rank_3y": _r(pct_rank([v for _, v in real[-750:]], real[-1][1]), 1)}
    else:
        out["real10y"] = _missing("FRED DFII10 无数据")
    if nominal:
        out["nominal10y"] = {"status": "ok", "asof": _iso(nominal[-1][0]), "last": _r(nominal[-1][1], 2), "unit": "%",
                             "chg_20d_bp": _r(diff_change(nominal, 20) * 100, 0) if len(nominal) > 20 else None}
        rm = dict(real)
        be = [(d, v - rm[d]) for d, v in nominal if d in rm]
        out["breakeven10y"] = ({"status": "ok", "asof": _iso(be[-1][0]), "last": _r(be[-1][1], 2), "unit": "%",
                                "chg_20d_bp": _r(diff_change(be, 20) * 100, 0) if len(be) > 20 else None}
                               if be else _missing("无数据"))
    else:
        out["nominal10y"] = _missing("FRED DGS10 无数据")
        out["breakeven10y"] = _missing("无数据")
    out["tnx"] = ({"status": "ok", "asof": _iso(tnx[-1][0]), "last": _r(tnx[-1][1], 3), "unit": "%",
                   "chg_20d_bp": _r(diff_change(tnx, 20) * 100, 0) if len(tnx) > 20 else None}
                  if tnx else _missing("Yahoo ^TNX 无数据"))
    out["dxy"] = (dict(_price_row("美元指数 DXY", dxy, "点", "Yahoo DX-Y.NYB"), rank_1y=_r(pct_rank([v for _, v in dxy[-252:]], dxy[-1][1]), 1))
                  if dxy else _missing("Yahoo DX-Y.NYB 无数据"))
    out["vix"] = _price_row("VIX", vix, "点", "Yahoo ^VIX") if vix else _missing("Yahoo ^VIX 无数据")
    # 120 日：金价日收益 vs 实际利率日变化 的相关系数（负相关越强 = 实际利率驱动越明显）。
    if real and gold:
        gm, rm = dict(gold), dict(real)
        common = sorted(set(gm) & set(rm))[-121:]
        if len(common) >= 31:
            gx = [math.log(gm[common[i]] / gm[common[i - 1]]) for i in range(1, len(common))]
            ry = [rm[common[i]] - rm[common[i - 1]] for i in range(1, len(common))]
            out["corr_gold_real_120d"] = _r(_rolling_corr(gx, ry), 2)
    asofs = [v.get("asof") for v in out.values() if isinstance(v, dict) and v.get("asof")]
    out["asof"] = max(asofs) if asofs else None
    return out


def build_spdr(series: list[tuple[date, float]]) -> dict:
    if not series:
        return _missing("SPDR 持仓无数据")
    return {"status": "ok", "asof": _iso(series[-1][0]), "tonnes": _r(series[-1][1], 2), "unit": "吨",
            "chg_1d": _r(diff_change(series, 1), 2), "chg_5d": _r(diff_change(series, 5), 2),
            "chg_20d": _r(diff_change(series, 20), 2),
            "series": [{"d": _iso(d), "v": _r(v, 2)} for d, v in series[-CHART_DAYS:]]}


def build_cftc(rows: list[dict]) -> dict:
    if not rows:
        return _missing("CFTC 无数据")
    last = rows[-1]
    nets = [r["net"] for r in rows[-156:]]
    def chg(n):
        return _r(last["net"] - rows[-1 - n]["net"], 0) if len(rows) > n else None
    return {"status": "ok", "asof": _iso(last["date"]), "net": _r(last["net"], 0), "long": _r(last["long"], 0),
            "short": _r(last["short"], 0), "oi": _r(last["oi"], 0),
            "net_pct_oi": _r(last["net"] / last["oi"] * 100, 1), "chg_1w": chg(1), "chg_4w": chg(4),
            "rank_3y": _r(pct_rank(nets, last["net"]), 1), "unit": "手（100盎司/手）",
            "series": [{"d": _iso(r["date"]), "net": _r(r["net"], 0)} for r in rows[-156:]]}


def build_pboc(rows: list[dict], gold_month_end: dict[str, float] | None = None) -> dict:
    if not rows:
        return _missing("黄金储备无数据")
    last = rows[-1]
    out = {"status": "ok", "asof": _iso(last["date"]), "month": last["month"], "value": _r(last["value"], 2),
           "unit": "亿美元", "mom_pct": _r(last["mom_pct"], 2), "yoy_pct": _r(last["yoy_pct"], 2),
           "series": [{"m": r["month"], "v": _r(r["value"], 2)} for r in rows[-24:]],
           "note": "官方口径为储备价值（亿美元），随金价波动；吨数暂无已验证的公开免 key 接口，不做估算。"}
    if gold_month_end:
        g1 = gold_month_end.get(last["month"])
        prev_m = rows[-2]["month"] if len(rows) > 1 else None
        g0 = gold_month_end.get(prev_m) if prev_m else None
        if g1 and g0:
            out["gold_mom_pct"] = _r((g1 / g0 - 1) * 100, 2)
    return out


def month_end_closes(series: list[tuple[date, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d, v in series:
        out[d.strftime("%Y-%m")] = v
    return out


def build_cn_etfs(items: list[dict] | None) -> dict:
    ok = [i for i in items or [] if i.get("status") == "ok"]
    if not ok:
        return _missing("上交所黄金ETF份额无数据", items=items or [])
    def tot(k):
        vals = [i.get(k) for i in ok if i.get(k) is not None]
        return _r(sum(vals), 2) if vals else None
    return {"status": "ok", "asof": max(i["asof"] for i in ok), "items": items, "unit": "亿元",
            "shares_yi": tot("shares_yi"), "net_1d": tot("net_1d"), "net_5d": tot("net_5d"), "net_20d": tot("net_20d"),
            "method": "估算净申赎 = 区间份额变化 × 区间平均收盘价；上交所 T 日份额未发布时整体后移一个交易日。"
                      "深交所 159934 等不在上交所口径内，未纳入。"}


# ---------------------------------------------------------------- signal (pure, research only)
def _clamp(x: int, lo: int = -2, hi: int = 2) -> int:
    return max(lo, min(hi, x))


def build_signal(p: dict) -> dict:
    """四因子研究打分（每项 -2…+2）。缺数据的因子记 0 并注明，不猜。"""
    factors = []
    # 1) 趋势
    t = (p.get("technicals") or {}).get("GC") or {}
    s, why = 0, []
    if t.get("status") == "ok":
        last, ma20, ma60 = t.get("last"), t.get("ma20"), t.get("ma60")
        if last and ma60:
            s += 1 if last > ma60 else -1
            why.append(f"COMEX金 {'站上' if last > ma60 else '跌破'} MA60（{t.get('vs_ma60_pct')}%）")
        if ma20 and ma60:
            s += 1 if ma20 > ma60 else -1
            why.append("MA20 在 MA60 之上" if ma20 > ma60 else "MA20 在 MA60 之下")
        r = t.get("rsi14")
        if r is not None and r >= 75:
            s -= 1
            why.append(f"RSI14={r} 超买，追高性价比低")
        elif r is not None and r <= 25:
            s += 1
            why.append(f"RSI14={r} 超卖")
    else:
        why.append("金价日线无数据")
    factors.append({"key": "trend", "name": "趋势", "score": _clamp(s), "reasons": why})
    # 2) 宏观：实际利率 + 美元
    m = p.get("macro") or {}
    s, why = 0, []
    rr = m.get("real10y") or {}
    if rr.get("status") == "ok" and rr.get("chg_20d_bp") is not None:
        c = rr["chg_20d_bp"]
        if c <= -10:
            s += 1
        elif c >= 10:
            s -= 1
        why.append(f"10Y实际利率 {rr.get('last')}%，20日 {c:+.0f}bp")
    else:
        why.append("实际利率无数据")
    dx = m.get("dxy") or {}
    if dx.get("status") == "ok" and dx.get("chg_20d_pct") is not None:
        c = dx["chg_20d_pct"]
        if c <= -1:
            s += 1
        elif c >= 1:
            s -= 1
        why.append(f"美元指数 {dx.get('last')}，20日 {c:+.2f}%")
    else:
        why.append("美元指数无数据")
    factors.append({"key": "macro", "name": "实际利率/美元", "score": _clamp(s), "reasons": why})
    # 3) 资金/持仓
    pos = p.get("positioning") or {}
    s, why = 0, []
    sp = pos.get("spdr") or {}
    if sp.get("status") == "ok" and sp.get("chg_20d") is not None:
        c = sp["chg_20d"]
        if c >= 5:
            s += 1
        elif c <= -5:
            s -= 1
        why.append(f"SPDR 持仓 {sp.get('tonnes')} 吨，20日 {c:+.2f} 吨")
    cf = pos.get("cftc") or {}
    if cf.get("status") == "ok" and cf.get("rank_3y") is not None:
        rk = cf["rank_3y"]
        if rk >= 85:
            s -= 1
            why.append(f"CFTC 投机净多 3年分位 {rk:.0f}%，拥挤")
        elif rk <= 20:
            s += 1
            why.append(f"CFTC 投机净多 3年分位 {rk:.0f}%，偏低")
        else:
            why.append(f"CFTC 投机净多 3年分位 {rk:.0f}%")
    ce = pos.get("cn_etfs") or {}
    if ce.get("status") == "ok" and ce.get("net_20d") is not None:
        c = ce["net_20d"]
        if c >= 10:
            s += 1
        elif c <= -10:
            s -= 1
        why.append(f"上交所黄金ETF 20日估算净申赎 {c:+.2f} 亿元")
    if not why:
        why.append("持仓/资金无数据")
    factors.append({"key": "flows", "name": "持仓/资金", "score": _clamp(s), "reasons": why})
    # 4) 国内溢价 / 金银比（情绪）
    s, why = 0, []
    pr = p.get("premium") or {}
    if pr.get("status") == "ok" and pr.get("rank_1y") is not None:
        rk = pr["rank_1y"]
        if rk >= 90:
            s -= 1
            why.append(f"沪金溢价处1年 {rk:.0f}% 分位，注意溢价回落")
        elif rk <= 10:
            s += 1
            why.append(f"沪金溢价处1年 {rk:.0f}% 分位（偏低/折价）")
        else:
            why.append(f"沪金溢价1年分位 {rk:.0f}%")
    else:
        why.append("内外盘溢价无数据")
    gs = p.get("ratio") or {}
    if gs.get("status") == "ok" and gs.get("rank_3y") is not None:
        why.append(f"金银比 {gs.get('latest')}（3年分位 {gs['rank_3y']:.0f}%）")
    factors.append({"key": "premium", "name": "溢价/情绪", "score": _clamp(s), "reasons": why})

    total = sum(f["score"] for f in factors)
    regime = "偏多" if total >= 3 else ("偏空" if total <= -3 else "震荡")
    hv_rank = t.get("hv_rank_1y") if t.get("status") == "ok" else None
    vol_hi = hv_rank is not None and hv_rank >= 70
    if regime == "偏多":
        hint = "趋势与驱动偏多：回调分批思路；波动率偏高时可研究卖虚值Put（溢价高位时注意回落风险）。" if vol_hi else \
               "趋势与驱动偏多：回调分批思路，跌破 MA60 需重新评估。"
    elif regime == "偏空":
        hint = "驱动偏空：控制仓位，反弹不追；持仓可研究保护性Put。"
    else:
        hint = "多空因子分歧：区间思路为主；波动率高位时可研究卖宽跨，低位时等待方向确认。" if vol_hi else \
               "多空因子分歧：区间思路为主，等待实际利率/美元给出方向。"
    return {"score": total, "max_score": 8, "regime": regime, "factors": factors, "hint": hint,
            "hv_rank_1y": hv_rank, "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------- payload assembly (pure)
def _block_date(block: Any) -> date | None:
    return _to_date(block.get("asof")) if isinstance(block, dict) else None


def _with_fallback(new: dict, prev: dict | None, key: str, today: date) -> dict:
    """新模块失败时用上次快照中同名模块（不超过 MAX_SNAPSHOT_AGE 天）；否则保留「无数据」。"""
    if isinstance(new, dict) and new.get("status") == "ok":
        return new
    if isinstance(prev, dict) and prev.get("status") == "ok":
        d = _block_date(prev)
        if d and (today - d).days <= MAX_SNAPSHOT_AGE.get(key, 10):
            out = copy.deepcopy(prev)
            out["snapshot"] = True
            out["snapshot_date"] = prev.get("snapshot_date") or _iso(d)
            out["snapshot_reason"] = (new or {}).get("reason") or "本轮抓取失败"
            return out
    return new


def build_payload(raw: dict, errors: dict | None = None, prev: dict | None = None,
                  now: datetime | None = None) -> dict:
    now = now or _now()
    today = now.date()
    errors = dict(errors or {})
    prev = prev if isinstance(prev, dict) and prev.get("status") == "ok" else None
    pv = lambda *path: _dig(prev, *path)  # noqa: E731

    y = {k: _clean(v) for k, v in (raw.get("yahoo") or {}).items()}
    gc = y.get("GC=F") or _clean(raw.get("sina_gc"))
    gc_src = "Yahoo GC=F" if y.get("GC=F") else ("新浪 GC 日线" if raw.get("sina_gc") else None)
    si = y.get("SI=F") or _clean(raw.get("sina_si"))
    fx = y.get("USDCNY=X") or []
    xau = _clean(raw.get("sina_xau"))
    au0 = _clean(raw.get("au0"))
    sge = _clean(raw.get("sge"))
    fred = raw.get("fred") or {}
    real, nominal = _clean(fred.get("DFII10")), _clean(fred.get("DGS10"))

    prices = {
        "GC": _price_row("COMEX黄金（连续）", gc, "美元/盎司", gc_src or "无数据"),
        "XAU": _price_row("伦敦金现货", xau, "美元/盎司", "新浪 XAU 日线"),
        "AU0": _price_row("沪金主连 AU0", au0, "元/克", "新浪 AU0 日线"),
        "SGE": _price_row("上金所 Au99.99", sge, "元/克", "上海黄金交易所"),
        "SI": _price_row("COMEX白银（连续）", si, "美元/盎司", "Yahoo SI=F" if y.get("SI=F") else "新浪 SI 日线"),
        "USDCNY": _price_row("美元/人民币", fx, "", "Yahoo USDCNY=X"),
    }
    prices_ok = sum(1 for v in prices.values() if v.get("status") == "ok")
    prices_block = {"status": "ok" if prices_ok else "missing", "rows": prices,
                    "asof": max((v["asof"] for v in prices.values() if v.get("asof")), default=None)}
    if not prices_ok:
        prices_block["reason"] = "金价日线全部无数据"

    intl_series, intl_name = (xau, "伦敦金现货 XAU") if xau else (gc, "COMEX GC")
    premium = build_premium(au0, intl_series, fx, intl_name)
    sge_premium = build_premium(sge, intl_series, fx, intl_name)
    if sge_premium.get("status") == "ok":
        premium.setdefault("sge", {k: sge_premium.get(k) for k in ("asof", "latest", "latest_pct", "rank_1y", "mean_1y_pct")})

    technicals = {"GC": build_technicals("COMEX黄金", gc, "美元/盎司"),
                  "AU0": build_technicals("沪金主连 AU0", au0, "元/克")}
    tech_block = {"status": "ok" if any(v.get("status") == "ok" for v in technicals.values()) else "missing",
                  **technicals}
    tech_block["asof"] = max((v.get("asof") for v in technicals.values() if v.get("asof")), default=None)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "updated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "realtime": _with_fallback(build_realtime(raw.get("realtime")), pv("realtime"), "realtime", today),
        "prices": _with_fallback(prices_block, pv("prices"), "prices", today),
        "premium": _with_fallback(premium, pv("premium"), "premium", today),
        "ratio": _with_fallback(build_ratio(gc, si), pv("ratio"), "ratio", today),
        "macro": _with_fallback(build_macro(real, nominal, y.get("^TNX") or [], y.get("DX-Y.NYB") or [],
                                            y.get("^VIX") or [], gc), pv("macro"), "macro", today),
        "positioning": {
            "spdr": _with_fallback(build_spdr(_clean(raw.get("spdr"))), pv("positioning", "spdr"), "spdr", today),
            "cftc": _with_fallback(build_cftc(raw.get("cftc") or []), pv("positioning", "cftc"), "cftc", today),
            "cn_etfs": _with_fallback(build_cn_etfs(raw.get("cn_etfs")), pv("positioning", "cn_etfs"), "cn_etfs", today),
            "inst": _with_fallback(raw.get("inst") or _missing("天天基金机构持有比例无数据"),
                                   pv("positioning", "inst"), "inst", today),
            "pboc": _with_fallback(build_pboc(raw.get("pboc") or [], month_end_closes(gc)),
                                   pv("positioning", "pboc"), "pboc", today),
        },
    }
    # 技术面同样允许快照回退（逐品种）
    tb = {}
    for k, v in technicals.items():
        tb[k] = _with_fallback(v, pv("technicals", k), "technicals", today)
    tb["status"] = "ok" if any(isinstance(v, dict) and v.get("status") == "ok" for v in tb.values()) else "missing"
    tb["asof"] = max((v.get("asof") for v in tb.values() if isinstance(v, dict) and v.get("asof")), default=None)
    payload["technicals"] = tb
    payload["chart"] = build_chart(gc, real, au0, payload.get("premium"), y.get("DX-Y.NYB") or [])
    if not payload["chart"].get("dates") and isinstance(pv("chart"), dict) and pv("chart").get("dates"):
        payload["chart"] = dict(pv("chart"), snapshot=True)
    payload["signal"] = build_signal(payload)
    core = [payload["prices"], payload["premium"], payload["technicals"]]
    payload["status"] = "ok" if any(b.get("status") == "ok" for b in core) else "no_data"
    if payload["status"] != "ok":
        payload["error"] = "无数据"
    snaps = {}
    for key, blk in _iter_blocks(payload):
        if blk.get("snapshot"):
            snaps[key] = blk.get("snapshot_date")
    payload["snapshot_blocks"] = snaps
    payload["errors"] = errors
    payload["sources"] = SOURCES
    payload["disclaimer"] = DISCLAIMER
    payload["notes"] = [
        "红色=上涨/流入/增加，绿色=下跌/流出/减少。",
        "内外盘溢价 = 沪金(元/克) − 海外金价×USD/CNY÷31.1034768；含资金成本、进口配额、交割与时差因素，非无风险套利。",
        "10Y实际利率（TIPS）与美元指数是金价的核心宏观驱动；央行购金与ETF持仓反映中长期资金。",
        "CFTC 每周五公布截至周二的持仓；SPDR 持仓为日度；中国黄金储备为月度、以美元价值计。",
        "所有信号仅为方向研究，不能下单，不构成投资建议。",
    ]
    return payload


def _dig(obj: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(p)
    return obj


def _iter_blocks(payload: dict):
    for k in ("realtime", "prices", "premium", "ratio", "macro"):
        if isinstance(payload.get(k), dict):
            yield k, payload[k]
    for k, v in (payload.get("positioning") or {}).items():
        if isinstance(v, dict):
            yield f"positioning.{k}", v
    for k, v in (payload.get("technicals") or {}).items():
        if isinstance(v, dict):
            yield f"technicals.{k}", v


def build_chart(gc, real, au0, premium, dxy) -> dict:
    """ECharts 用的对齐序列（按 COMEX 交易日），缺值为 null，不插值。"""
    if not gc:
        return {"dates": []}
    gcs = gc[-CHART_DAYS:]
    start = gcs[0][0]
    rm, dm = dict(real), dict(dxy)
    vals = [v for _, v in gc]
    ma60 = sma(vals, 60)[-len(gcs):]
    out = {"dates": [_iso(d) for d, _ in gcs], "gc": [_r(v, 2) for _, v in gcs],
           "gc_ma60": [_r(v, 2) for v in ma60],
           "real10y": [_r(rm.get(d), 2) for d, _ in gcs], "dxy": [_r(dm.get(d), 2) for d, _ in gcs]}
    au = [(d, v) for d, v in au0 if d >= start]
    if au:
        av = [v for _, v in au0]
        m20, m60 = sma(av, 20)[-len(au):], sma(av, 60)[-len(au):]
        pm = {r["d"]: r["prem_pct"] for r in (premium or {}).get("series") or []} if (premium or {}).get("status") == "ok" else {}
        out["au_dates"] = [_iso(d) for d, _ in au]
        out["au0"] = [_r(v, 2) for _, v in au]
        out["au_ma20"] = [_r(v, 2) for v in m20]
        out["au_ma60"] = [_r(v, 2) for v in m60]
        out["prem_pct"] = [pm.get(_iso(d)) for d, _ in au]
    return out


# ---------------------------------------------------------------- network fetchers (background only)
def _http(method: str, url: str, *, params=None, data=None, headers=None, timeout=HTTP_TIMEOUT, retries=2):
    import requests

    h = {"User-Agent": UA}
    h.update(headers or {})
    last: Exception | None = None
    for i in range(retries):
        try:
            r = requests.request(method, url, params=params, data=data, headers=h, timeout=timeout)
            r.raise_for_status()
            if not r.content:
                raise ValueError("empty body")
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * (i + 1))
    raise last or RuntimeError("request failed")


def fetch_realtime() -> dict:
    r = _http("GET", "https://hq.sinajs.cn/list=hf_GC,hf_XAU,hf_SI,nf_AU0,fx_susdcny,sh518880",
              headers={"Referer": "https://finance.sina.com.cn/"})
    q = realtime_from_hq(parse_sina_hq(r.content.decode("gbk", errors="replace")))
    if not any(q.values()):
        raise ValueError("新浪实时为空")
    return q


def fetch_sina_inner_daily(symbol: str = "AU0") -> list[tuple[date, float]]:
    url = (f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}=/"
           f"InnerFuturesNewService.getDailyKLine?symbol={symbol}")
    rows = parse_sina_jsonp_kline(_http("GET", url).text, "d", "c")
    if len(rows) < 60:
        raise ValueError(f"新浪 {symbol} 日线不足")
    return rows


def fetch_sina_global_daily(symbol: str) -> list[tuple[date, float]]:
    url = (f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}=/"
           f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}")
    rows = parse_sina_jsonp_kline(_http("GET", url).text, "date", "close")
    if len(rows) < 60:
        raise ValueError(f"新浪 {symbol} 日线不足")
    return rows


def fetch_sge(symbol: str = "Au99.99") -> list[tuple[date, float]]:
    r = _http("POST", "https://www.sge.com.cn/graph/Dailyhq", data={"instid": symbol},
              headers={"Referer": "https://www.sge.com.cn/sjzx/mrhq", "Origin": "https://www.sge.com.cn",
                       "X-Requested-With": "XMLHttpRequest"})
    rows = parse_sge_daily(r.json())
    if len(rows) < 60:
        raise ValueError("上金所日线不足")
    return rows


def fetch_yahoo() -> dict[str, list[tuple[date, float]]]:
    import net_limits

    d = net_limits.yf_download(YAHOO_SYMBOLS, period="3y", auto_adjust=False, progress=False, threads=False)
    if d is None or d.empty:
        raise ValueError("Yahoo 无数据")
    close = d["Close"]
    out = {}
    for s in YAHOO_SYMBOLS:
        if s in close:
            col = close[s].dropna()
            out[s] = [(idx.date(), float(v)) for idx, v in col.items() if float(v) > 0]
    if not out.get("GC=F"):
        raise ValueError("Yahoo GC=F 无数据")
    return out


def fetch_fred(series_id: str, years: int = 4) -> list[tuple[date, float]]:
    cosd = (date.today() - timedelta(days=365 * years)).isoformat()
    # FRED 的 CDN 会直接断开浏览器 UA 的脚本请求；用明确的非浏览器 UA（实测 curl / requests 默认 UA 均 200）。
    r = _http("GET", "https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id, "cosd": cosd},
              headers={"User-Agent": FRED_UA})
    rows = parse_fred_csv(r.text)
    if len(rows) < 30:
        raise ValueError(f"FRED {series_id} 无数据")
    return rows


def fetch_cftc(weeks: int = 170) -> list[dict]:
    params = {"$where": "cftc_contract_market_code='088691'", "$order": "report_date_as_yyyy_mm_dd DESC",
              "$limit": str(weeks),
              "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all"}
    rows = parse_cftc(_http("GET", "https://publicreporting.cftc.gov/resource/6dca-aqww.json", params=params,
                            timeout=30).json())
    if not rows:
        raise ValueError("CFTC 无数据")
    return rows


def fetch_spdr(pages: int = 13) -> list[tuple[date, float]]:
    """金十每页 20 个交易日，按 max_date 向前翻页；默认约 1 年。"""
    headers = {"x-app-id": "rU6QIu7JHe2gOUeR", "x-csrf-token": "x-csrf-token", "x-version": "1.0.0",
               "Referer": "https://datacenter.jin10.com/"}
    out: dict[date, float] = {}
    max_date = ""
    for _ in range(pages):
        js = _http("GET", "https://datacenter-api.jin10.com/reports/list_v2",
                   params={"category": "etf", "attr_id": "1", "max_date": max_date}, headers=headers).json()
        rows = parse_jin10_etf(js)
        if not rows:
            break
        out.update(rows)
        max_date = (rows[0][0] - timedelta(days=1)).isoformat()
        time.sleep(0.3)
    if not out:
        raise ValueError("SPDR 持仓无数据")
    return sorted(out.items())


def fetch_pboc() -> list[dict]:
    params = {"reportName": "RPT_ECONOMY_GOLD_CURRENCY",
              "columns": "REPORT_DATE,TIME,GOLD_RESERVES,GOLD_RESERVES_SAME,GOLD_RESERVES_SEQUENTIAL",
              "pageNumber": "1", "pageSize": "36", "sortColumns": "REPORT_DATE", "sortTypes": "-1",
              "source": "WEB", "client": "WEB"}
    rows = parse_pboc(_http("GET", "https://datacenter-web.eastmoney.com/api/data/v1/get", params=params).json())
    if not rows:
        raise ValueError("黄金储备无数据")
    return rows


def fetch_cn_etfs() -> list[dict]:
    import cn_index_flow as f

    prices = {code: f.fetch_sina_kline("sh" + code, 60) for code, _ in CN_GOLD_ETFS}
    cal = [d for d, _ in prices[CN_GOLD_ETFS[0][0]]]
    today = date.today()
    # 新浪日线可能含今天（盘中/收盘后），上交所份额通常 T+1 才有
    cal = [d for d in cal if d <= today]
    cal2, shares = f._shares_for_calendar(cal)
    items = [f.etf_flow(code, name, shares, cal2, prices.get(code) or []) for code, name in CN_GOLD_ETFS]
    if not any(i.get("status") == "ok" for i in items):
        raise ValueError("上交所黄金ETF份额无数据")
    return items


def fetch_inst(code: str = "518880") -> dict:
    import cn_index_etf_holders as h

    rows = h.complete_rows(h.parse_cyrjg(h.fetch_cyrjg(code)))
    if not rows:
        raise ValueError("cyrjg 无数据")
    last = rows[-1]
    hist = [{"d": _iso(r["date"]), "inst_pct": r["inst_pct"]} for r in rows[-8:]]
    return {"status": "ok", "code": code, "name": "华安黄金ETF", "asof": _iso(last["date"]),
            "inst_pct": _r(last["inst_pct"], 2), "indiv_pct": _r(last.get("indiv_pct"), 2),
            "total_shares_yi": _r(last["total_shares"], 2), "history": hist,
            "note": "定期报告口径（半年/年报），非实时。"}


def default_fetchers() -> dict[str, Callable[[], Any]]:
    return {
        "realtime": fetch_realtime,
        "yahoo": fetch_yahoo,
        "au0": lambda: fetch_sina_inner_daily("AU0"),
        "sina_xau": lambda: fetch_sina_global_daily("XAU"),
        "sge": fetch_sge,
        "fred_real": lambda: fetch_fred("DFII10"),
        "fred_nominal": lambda: fetch_fred("DGS10"),
        "cftc": fetch_cftc,
        "spdr": fetch_spdr,
        "pboc": fetch_pboc,
        "cn_etfs": fetch_cn_etfs,
        "inst": fetch_inst,
    }


def fetch_all(fetchers: dict[str, Callable[[], Any]] | None = None, max_workers: int = 6) -> tuple[dict, dict]:
    from concurrent.futures import ThreadPoolExecutor

    fetchers = fetchers or default_fetchers()
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def run(item):
        k, fn = item
        try:
            return k, fn(), None
        except Exception as exc:  # noqa: BLE001
            logger.info("gold fetch %s failed: %s", k, exc)
            return k, None, f"{type(exc).__name__}: {str(exc)[:160]}"

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for k, val, err in pool.map(run, list(fetchers.items())):
            if err:
                errors[k] = err
            else:
                raw[k] = val
    raw["fred"] = {"DFII10": raw.pop("fred_real", None) or [], "DGS10": raw.pop("fred_nominal", None) or []}
    # Yahoo 失败时：COMEX 金/银用新浪日线兜底
    if not (raw.get("yahoo") or {}).get("GC=F"):
        for key, sym in (("sina_gc", "GC"), ("sina_si", "SI")):
            try:
                raw[key] = fetch_sina_global_daily(sym)
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return raw, errors


# ---------------------------------------------------------------- cache / snapshot
def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _newest(cands: list[dict | None]) -> dict | None:
    ok = [c for c in cands if isinstance(c, dict) and c.get("status") == "ok"]
    if not ok:
        return None
    return max(ok, key=lambda c: str(c.get("updated_at") or ""))


def load_best() -> dict | None:
    return _newest([_cache, _load_json(RUNTIME_PATH)] + [_load_json(p) for p in SEED_PATHS])


def _write_runtime(payload: dict) -> None:
    try:
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUNTIME_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(RUNTIME_PATH)
    except Exception:  # noqa: BLE001
        logger.info("gold runtime write failed", exc_info=True)


def fetch_remote_seed() -> dict | None:
    try:
        js = _http("GET", REMOTE_SEED_URL, timeout=10, retries=1).json()
        return js if isinstance(js, dict) and js.get("status") == "ok" else None
    except Exception:  # noqa: BLE001
        return None


def refresh(fetchers: dict[str, Callable[[], Any]] | None = None, *, use_remote: bool = True) -> dict:
    """全量抓取→组装→写运行时缓存。整轮核心数据失败时尝试 GitHub 上最新 Pages 种子。"""
    global _cache
    with _lock:
        prev = load_best()
        if use_remote:
            remote = fetch_remote_seed()
            prev = _newest([prev, remote]) or prev
        raw, errors = fetch_all(fetchers)
        payload = build_payload(raw, errors, prev)
        if not has_live_core(payload) and prev:
            # 本轮核心行情（价格/溢价/技术面）一个实时值都没有：不刷新 updated_at，整体沿用上次快照。
            payload = copy.deepcopy(prev)
            payload["errors"] = errors
            payload["note"] = "本轮实时抓取失败，显示上次快照"
            payload["from_snapshot"] = True
        if payload.get("status") == "ok" and not payload.get("from_snapshot"):
            _cache = payload
            _write_runtime(payload)
        return payload


def has_live_core(payload: dict) -> bool:
    blocks = [payload.get("prices"), payload.get("premium")] + [
        v for k, v in (payload.get("technicals") or {}).items() if isinstance(v, dict)]
    return any(isinstance(b, dict) and b.get("status") == "ok" and not b.get("snapshot") for b in blocks)


def snapshot() -> dict:
    best = load_best()
    if best:
        out = copy.deepcopy(best)
        try:
            age = (_now() - datetime.fromisoformat(out["updated_at"])).total_seconds() / 3600.0
        except (KeyError, TypeError, ValueError):
            age = None
        out["age_hours"] = _r(age, 1)
        return out
    return {"schema_version": 1, "status": "no_data", "error": "无数据", "updated_at": _now().isoformat(timespec="seconds"),
            "sources": SOURCES, "disclaimer": DISCLAIMER}


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
    print(json.dumps({"status": data.get("status"), "errors": data.get("errors"),
                      "signal": (data.get("signal") or {}).get("regime")}, ensure_ascii=False))
    if "--write-seed" in sys.argv and data.get("status") == "ok":
        write_seed(data)
