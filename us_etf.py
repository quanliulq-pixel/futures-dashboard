# -*- coding: utf-8 -*-
"""美股主流 ETF 分析页（/api/us-etf）。研究参考，不构成投资建议。

架构与 黄金 / 美气NG 相同：请求只读内存/本地快照（秒回），过期时后台单飞刷新；某只 ETF 本轮没取到时
回退到 10 天内的上次快照并标注「上次快照（日期）」，没有就如实显示「无数据」。绝不编造数字，
不使用东方财富实时推送接口。资金流/AUM 暂无已验证的免费可靠来源，不展示、不估算。

数据：Yahoo Finance 日线（yfinance，auto_adjust=True：收益含分红再投资口径），^VIX / ^VIX3M 指数。
口径：1日/1周/1月/3月/1年 = 1/5/21/63/252 个交易日；YTD 以上一年最后一个交易日收盘为基准；
52周高点用近 252 日收盘最高；MA50/MA200 为收盘简单均线；RSI14 为 Wilder 平滑；20日年化波动 = 对数收益标准差×√252；
相对强弱 = ETF 收益 − SPY 同期收益（百分点）；量比 = 当日成交量 / 此前 20 日均量。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
ET = ZoneInfo("America/New_York")
SEED_PATHS = (ROOT / "data" / "us_etf.json", ROOT / "docs" / "api" / "us_etf.json")
RUNTIME_PATH = ROOT / "data" / "us_etf_latest.json"
REMOTE_SEED_URL = "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/us_etf.json"
DISCLAIMER = "研究参考，不构成投资建议"
REFRESH_HOURS = 2.0
MAX_SNAPSHOT_DAYS = 10
CHART_DAYS = 252
RRG_WEEKS = 8
BENCH = "SPY"

GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("broad", "宽基指数", [("SPY", "标普500"), ("QQQ", "纳斯达克100"), ("DIA", "道琼斯30"), ("IWM", "罗素2000小盘"), ("VTI", "全美市场")]),
    ("sector", "行业板块", [("XLK", "科技"), ("XLF", "金融"), ("XLE", "能源"), ("XLV", "医疗"), ("XLI", "工业"), ("XLY", "可选消费"),
                          ("XLP", "必选消费"), ("XLU", "公用事业"), ("XLB", "原材料"), ("XLRE", "房地产"), ("XLC", "通信服务"), ("SMH", "半导体")]),
    ("style", "风格因子", [("IWF", "罗素1000成长"), ("IWD", "罗素1000价值"), ("MTUM", "动量因子"), ("USMV", "低波动因子")]),
    ("bond", "债券", [("SHY", "1-3年美债"), ("IEF", "7-10年美债"), ("TLT", "20年+美债"), ("LQD", "投资级公司债"), ("HYG", "高收益债")]),
    ("commodity", "商品/美元", [("GLD", "黄金"), ("SLV", "白银"), ("USO", "原油"), ("UUP", "美元指数多头")]),
    ("intl", "海外市场", [("EFA", "发达市场(除美)"), ("EEM", "新兴市场"), ("FXI", "中国大盘"), ("KWEB", "中概互联网")]),
    ("crypto", "加密资产", [("IBIT", "比特币现货")]),
]
INDICES = [("^VIX", "VIX 30日隐含波动"), ("^VIX3M", "VIX 3个月")]
RRG_GROUPS = ("sector",)
RISK_PAIRS = [
    ("SPY", "TLT", "股债 SPY/TLT", "股票相对长债走强＝偏 Risk-on"),
    ("HYG", "LQD", "信用 HYG/LQD", "高收益债相对投资级走强＝信用偏好上升"),
    ("XLY", "XLP", "消费 XLY/XLP", "可选相对必选走强＝周期偏好上升"),
    ("SMH", "SPY", "半导体 SMH/SPY", "高贝塔成长领涨＝偏 Risk-on"),
    ("IWM", "SPY", "小盘 IWM/SPY", "小盘相对大盘走强＝市场宽度改善"),
]
HORIZONS = {"r1d": 1, "r1w": 5, "r1m": 21, "r3m": 63, "r1y": 252}

_lock = threading.Lock()
_cache: dict | None = None


def all_symbols() -> list[str]:
    return [s for _, _, items in GROUPS for s, _ in items] + [s for s, _ in INDICES]


def _now() -> datetime:
    return datetime.now(CST)


def _r(x: Any, n: int = 4) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


# ---------------------------------------------------------------- pure metrics
def rsi_wilder(close: pd.Series, n: int = 14) -> float | None:
    d = close.diff().dropna()
    if len(d) < n + 1:
        return None
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().iloc[-1]
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().iloc[-1]
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return float(100 - 100 / (1 + gain / loss))


def ret(close: pd.Series, n: int) -> float | None:
    c = close.dropna()
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None


def ytd(close: pd.Series) -> float | None:
    c = close.dropna()
    if c.empty:
        return None
    prior = c[c.index.year < c.index[-1].year]
    return float(c.iloc[-1] / prior.iloc[-1] - 1) if len(prior) else None


def trend_state(close: float, ma50: float | None, ma200: float | None) -> str:
    if ma50 is None or ma200 is None:
        return "数据不足"
    if close > ma50 > ma200:
        return "多头排列"
    if close < ma50 < ma200:
        return "空头排列"
    return "偏多" if close > ma200 else "偏空"


def etf_metrics(df: pd.DataFrame, bench: pd.Series | None) -> dict:
    """df: Open/High/Low/Close/Volume (adjusted), ascending dates."""
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    if len(df) < 30:
        raise ValueError("日线不足")
    c = df["Close"]
    last = float(c.iloc[-1])
    ma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None
    ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    hi = float(c.iloc[-252:].max())
    lo = float(c.iloc[-252:].min())
    lr = np.log(c).diff()
    hv20 = float(lr.iloc[-20:].std() * math.sqrt(252)) if len(lr.dropna()) >= 20 else None
    out = {"status": "ok", "asof": str(c.index[-1].date()), "close": _r(last, 3)}
    for k, n in HORIZONS.items():
        out[k] = _r(ret(c, n))
    out["ytd"] = _r(ytd(c))
    out.update({
        "from_high": _r(last / hi - 1), "high_52w": _r(hi, 3), "low_52w": _r(lo, 3),
        "pos_52w": _r((last - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "ma50": _r(ma50, 3), "ma200": _r(ma200, 3), "vs_ma200": _r(last / ma200 - 1) if ma200 else None,
        "trend": trend_state(last, ma50, ma200), "rsi14": _r(rsi_wilder(c), 1), "hv20": _r(hv20),
    })
    v = df["Volume"] if "Volume" in df else None
    if v is not None and len(v) > 21 and float(v.iloc[-21:-1].mean() or 0) > 0:
        out["vol_ratio"] = _r(float(v.iloc[-1]) / float(v.iloc[-21:-1].mean()), 2)
        out["volume"] = int(v.iloc[-1]) if math.isfinite(float(v.iloc[-1])) else None
    else:
        out["vol_ratio"] = None
    if bench is not None:
        b = bench.reindex(c.index).dropna()
        for k, n in (("rs_1m", 21), ("rs_3m", 63), ("rs_1y", 252)):
            e, s = ret(c.loc[b.index], n), ret(b, n)
            out[k] = _r(e - s) if e is not None and s is not None else None
    return out


def rrg_points(close: pd.Series, bench: pd.Series, weeks: int = RRG_WEEKS) -> list[dict]:
    """Transparent RRG-style coordinates: x = 63日相对收益(vs SPY)，y = 该相对强弱的 21 日变化。Weekly tail."""
    rs = (close / bench).dropna()
    if len(rs) < 63 + 21 + 5 * weeks:
        return []
    x = rs / rs.shift(63) - 1
    y = rs / rs.shift(21) - 1
    pts = pd.DataFrame({"x": x, "y": y}).dropna()
    tail = pts.groupby(pts.index.to_period("W")).tail(1).iloc[-weeks:]
    if tail.index[-1] != pts.index[-1]:
        tail = pd.concat([tail.iloc[1:], pts.iloc[[-1]]])
    return [{"d": str(d.date()), "x": _r(r.x), "y": _r(r.y)} for d, r in tail.iterrows()]


def quadrant(x: float | None, y: float | None) -> str | None:
    if x is None or y is None:
        return None
    if x >= 0:
        return "领先" if y >= 0 else "转弱"
    return "改善" if y >= 0 else "落后"


def risk_pair(num: pd.Series, den: pd.Series) -> dict:
    ratio = (num / den).dropna()
    if len(ratio) < 60:
        return {"status": "missing", "reason": "数据不足"}
    ma50 = ratio.rolling(50).mean().iloc[-1]
    ch21 = ratio.iloc[-1] / ratio.iloc[-22] - 1
    above = ratio.iloc[-1] > ma50
    score = 1 if (above and ch21 > 0) else -1 if (not above and ch21 < 0) else 0
    return {"status": "ok", "ratio": _r(ratio.iloc[-1], 4), "chg_21d": _r(ch21), "vs_ma50": _r(ratio.iloc[-1] / ma50 - 1), "score": score}


def vix_block(vix: pd.Series | None, vix3m: pd.Series | None) -> dict:
    if vix is None or len(vix.dropna()) < 30:
        return {"status": "missing", "reason": "VIX 无数据"}
    v = vix.dropna()
    out = {"status": "ok", "asof": str(v.index[-1].date()), "vix": _r(v.iloc[-1], 2), "vix_chg_5d": _r(v.iloc[-1] - v.iloc[-6], 2),
           "vix_rank_1y": _r((v.iloc[-252:] < v.iloc[-1]).mean() * 100, 0)}
    term = None
    if vix3m is not None and len(vix3m.dropna()):
        v3 = vix3m.dropna()
        if v3.index[-1] == v.index[-1]:
            term = float(v.iloc[-1] / v3.iloc[-1])
            out["vix3m"] = _r(v3.iloc[-1], 2)
    out["term_ratio"] = _r(term, 3)
    lvl = float(v.iloc[-1])
    if lvl > 25 or (term is not None and term > 1.0):
        out["score"] = -1
    elif lvl < 20 and (term is None or term < 0.95):
        out["score"] = 1
    else:
        out["score"] = 0
    return out


# ---------------------------------------------------------------- fetch
def _frame(raw: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    try:
        df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
    except KeyError:
        return None
    df = df.dropna(subset=["Close"]) if "Close" in df else None
    return df if df is not None and len(df) else None


def fetch_prices(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    import net_limits

    symbols = symbols or all_symbols()
    raw = net_limits.yf_download(symbols, period="2y", auto_adjust=True, group_by="ticker", threads=True, progress=False)
    if raw is None or raw.empty:
        raise ValueError("Yahoo 无数据")
    out = {}
    for s in symbols:
        f = _frame(raw, s)
        if f is not None:
            out[s] = f
    if BENCH not in out:
        raise ValueError("Yahoo SPY 无数据")
    return out


def default_fetchers() -> dict[str, Callable[[], Any]]:
    return {"yahoo": fetch_prices}


# ---------------------------------------------------------------- payload
def _is_intraday(asof: str, now: datetime) -> bool:
    n = now.astimezone(ET)
    return asof == str(n.date()) and n.weekday() < 5 and n.time() < dtime(16, 15)


def _prev_rows(prev: dict | None) -> dict[str, dict]:
    out = {}
    for g in (prev or {}).get("groups") or []:
        for r in g.get("items") or []:
            if r.get("symbol"):
                out[r["symbol"]] = r
    return out


def _fallback_row(prev_row: dict | None, base: dict, today: date, reason: str) -> dict:
    if prev_row and prev_row.get("status") == "ok" and prev_row.get("asof"):
        try:
            age = (today - date.fromisoformat(prev_row["asof"])).days
        except ValueError:
            age = 999
        if age <= MAX_SNAPSHOT_DAYS:
            r = copy.deepcopy(prev_row)
            r.update(snapshot=True, snapshot_date=prev_row["asof"], snapshot_reason=reason)
            return r
    return {**base, "status": "missing", "reason": reason}


def _norm_series(s: pd.Series, days: int = CHART_DAYS) -> list:
    s = s.dropna().iloc[-days:]
    if s.empty:
        return []
    return [_r(x / s.iloc[0] * 100, 2) for x in s]


def build_payload(prices: dict[str, pd.DataFrame], errors: dict | None = None, prev: dict | None = None,
                  now: datetime | None = None) -> dict:
    now = now or _now()
    errors = dict(errors or {})
    prevrows = _prev_rows(prev)
    closes = {s: f["Close"] for s, f in prices.items() if f is not None and len(f)}
    bench = closes.get(BENCH)
    groups, asofs = [], []
    for gid, gname, items in GROUPS:
        rows = []
        for sym, name in items:
            base = {"symbol": sym, "name": name, "group": gid}
            if sym in prices:
                try:
                    m = etf_metrics(prices[sym], bench if sym != BENCH else None)
                    if sym == BENCH:
                        m.update(rs_1m=0.0, rs_3m=0.0, rs_1y=0.0)
                    rows.append({**base, **m})
                    asofs.append(m["asof"])
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors[sym] = f"{type(exc).__name__}: {str(exc)[:120]}"
            rows.append(_fallback_row(prevrows.get(sym), base, now.date(), errors.get(sym) or "Yahoo 本轮无该 ETF 数据"))
        groups.append({"id": gid, "name": gname, "items": rows})
    ok_rows = [r for g in groups for r in g["items"] if r.get("status") == "ok"]
    live_rows = [r for r in ok_rows if not r.get("snapshot")]
    asof = max(asofs) if asofs else None

    # 行业轮动（RRG 风格）
    rrg = []
    if bench is not None:
        for gid, _, items in GROUPS:
            if gid not in RRG_GROUPS:
                continue
            for sym, name in items:
                if sym in closes:
                    pts = rrg_points(closes[sym], bench)
                    if pts:
                        rrg.append({"symbol": sym, "name": name, "tail": pts, "x": pts[-1]["x"], "y": pts[-1]["y"],
                                    "quadrant": quadrant(pts[-1]["x"], pts[-1]["y"])})
    rrg.sort(key=lambda r: (r["x"] or 0), reverse=True)

    # 风险偏好仪表盘
    pairs, chart = [], {"dates": [], "series": []}
    for a, b, label, meaning in RISK_PAIRS:
        if a in closes and b in closes:
            blk = risk_pair(closes[a], closes[b])
            ratio = (closes[a] / closes[b]).dropna()
            if not chart["dates"]:
                chart["dates"] = [str(d.date()) for d in ratio.index[-CHART_DAYS:]]
            chart["series"].append({"name": label, "data": _norm_series(ratio.reindex(pd.to_datetime(chart["dates"])))})
        else:
            blk = {"status": "missing", "reason": "Yahoo 无数据"}
        pairs.append({"key": f"{a}/{b}", "label": label, "meaning": meaning, **blk})
    vix = vix_block(closes.get("^VIX"), closes.get("^VIX3M"))
    scores = [p["score"] for p in pairs if p.get("status") == "ok"] + ([vix["score"]] if vix.get("status") == "ok" else [])
    total = sum(scores)
    if not scores:
        regime = "无数据"
    elif total >= 2:
        regime = "偏 Risk-on"
    elif total <= -2:
        regime = "偏 Risk-off"
    else:
        regime = "中性/分化"
    risk = {"status": "ok" if scores else "missing", "score": total, "max_score": len(scores), "regime": regime,
            "pairs": pairs, "vix": vix, "chart": chart,
            "rule": "每组比值：高于 50 日均线且 21 日上升记 +1，低于均线且下降记 −1，否则 0；VIX＜20 且 VIX/VIX3M＜0.95 记 +1，VIX＞25 或期限倒挂(＞1) 记 −1。合计 ≥+2 偏 Risk-on，≤−2 偏 Risk-off。"}

    summary = _summary(groups, rrg, risk)
    status = "ok" if live_rows or ok_rows else "no_data"
    return {
        "schema_version": 1, "status": status, "updated_at": now.isoformat(timespec="seconds"), "asof": asof,
        "intraday": bool(asof and _is_intraday(asof, now)), "title": "美股主流 ETF", "disclaimer": DISCLAIMER,
        "benchmark": BENCH, "groups": groups, "rotation": {"items": rrg, "method": "x＝63 日相对 SPY 收益；y＝相对强弱比值 21 日变化；尾迹为近 8 周周末点。四象限：领先(+,+)、转弱(+,−)、落后(−,−)、改善(−,+)。类 RRG 的透明近似，非 JdK 专有算法。"},
        "risk": risk, "summary": summary,
        "notes": [
            "价格为 Yahoo Finance 复权收盘（含分红再投资），可能与券商未复权报价略有差异；美股盘中刷新时最新一根为盘中价。",
            "周=5、月=21、3月=63、年=252 个交易日；YTD 以上年最后交易日为基准；相对强弱为相对 SPY 的超额收益（百分点）。",
            "资金流/AUM 暂无已验证的免费可靠接口，不展示、不估算。",
            "IBIT 于 2024-01 上市，1年以上指标以实际可得历史为准。",
        ],
        "sources": [{"label": "Yahoo Finance 日线（yfinance）", "url": "https://finance.yahoo.com/quote/SPY/history/"},
                    {"label": "Cboe VIX / VIX3M（经 Yahoo ^VIX、^VIX3M）", "url": "https://www.cboe.com/tradable_products/vix/"}],
        "errors": errors,
    }


def _summary(groups: list[dict], rrg: list[dict], risk: dict) -> dict:
    rows = [r for g in groups for r in g["items"] if r.get("status") == "ok"]
    sectors = [r for r in rows if r["group"] == "sector" and r.get("r1m") is not None]
    sectors.sort(key=lambda r: r["r1m"], reverse=True)
    with_ma = [r for r in rows if r.get("vs_ma200") is not None]
    above = sum(1 for r in with_ma if r["vs_ma200"] > 0)
    eq = [r for r in rows if r["group"] in ("broad", "sector", "style", "intl") and r.get("vs_ma200") is not None]
    eq_above = sum(1 for r in eq if r["vs_ma200"] > 0)
    lines = []
    if sectors:
        lines.append("近1月行业领涨：" + "、".join(f"{r['name']}{r['symbol']} {r['r1m'] * 100:+.1f}%" for r in sectors[:3]))
        lines.append("近1月行业落后：" + "、".join(f"{r['name']}{r['symbol']} {r['r1m'] * 100:+.1f}%" for r in sectors[-3:][::-1]))
    lead = [r["symbol"] for r in rrg if r["quadrant"] == "领先"]
    improving = [r["symbol"] for r in rrg if r["quadrant"] == "改善"]
    if rrg:
        lines.append(f"轮动：领先象限 {('、'.join(lead) or '无')}；改善象限 {('、'.join(improving) or '无')}。")
    if eq:
        lines.append(f"权益类 ETF 站上 MA200：{eq_above}/{len(eq)}；全部 ETF：{above}/{len(with_ma)}。")
    if risk.get("status") == "ok":
        lines.append(f"风险偏好：{risk['regime']}（合计 {risk['score']:+d} / ±{risk['max_score']}）。")
    return {"lines": lines, "breadth_equity": {"above": eq_above, "total": len(eq)}, "breadth_all": {"above": above, "total": len(with_ma)}}


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
        logger.info("us_etf runtime write failed", exc_info=True)


def fetch_remote_seed() -> dict | None:
    try:
        import requests

        js = requests.get(REMOTE_SEED_URL, timeout=10).json()
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
        prices: dict = {}
        for key, fn in (fetchers or default_fetchers()).items():
            try:
                prices.update(fn() or {})
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if not prices:
            if prev:
                out = copy.deepcopy(prev)
                out.update(errors=errors, note="本轮 Yahoo 抓取失败，显示上次快照", from_snapshot=True)
                return out
            return {**build_payload({}, errors, None, now=now), "status": "no_data"}
        payload = build_payload(prices, errors, prev, now=now)
        if payload.get("status") == "ok":
            _cache = payload
            _write_runtime(payload)
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
    return {"schema_version": 1, "status": "no_data", "error": "无数据", "updated_at": _now().isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER}


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
    print(json.dumps({"status": data.get("status"), "asof": data.get("asof"), "errors": data.get("errors"),
                      "regime": (data.get("risk") or {}).get("regime"), "summary": (data.get("summary") or {}).get("lines")},
                     ensure_ascii=False, indent=1))
    if "--write-seed" in sys.argv and data.get("status") == "ok":
        write_seed(data)
