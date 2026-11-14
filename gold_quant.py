# -*- coding: utf-8 -*-
"""黄金量化纸面跟踪（/api/gold/quant）。研究参考，不能下单；本模块没有任何下单接口。

两条规则（2026-11-13 研究定稿，参数固定，不在线调参）：
- C1：沪金主连 AU0 只做多/空仓，5票趋势分 T（21/63/126/252 日时序动量 + EMA50/EMA200）取正部分，
  × min(1.5, 20% / EWMA年化波动(半衰期20))，调仓阈值 0.10；>1 倍的部分按 FRED 中国3个月银行间利率
  （IR3TIB01CNM156N，月度，按月末+45天才视为可得）扣融资成本。单边成本 4bp。
- 稳健版：华安黄金ETF 518880 只做多/空仓，同样的趋势分，目标波动 12%，上限 1.0（不加杠杆），单边成本 3bp。
- 基准：同期买入持有。

时序：只用已完成的日线收盘 t 决策，t+1 开盘成交；t→t+1 开盘的隔夜跳空按旧仓位计；成本按 |Δ仓位|。
盘中/夜盘未完成的 K 线一律丢弃。抓取失败时回退「上次快照（日期）」，没有就「无数据」，绝不编造。
不使用东方财富实时推送接口。
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

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
SEED_PATHS = (ROOT / "data" / "gold_quant.json", ROOT / "docs" / "api" / "gold_quant.json")
RUNTIME_PATH = ROOT / "data" / "gold_quant_latest.json"
REMOTE_SEED_URL = "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/gold_quant.json"
SINA_FUT_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_AU0=/"
                "InnerFuturesNewService.getDailyKLine?symbol=AU0")
SINA_STOCK_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_CN_RATE = "IR3TIB01CNM156N"
FRED_UA = "futures-dashboard/1.0 (+https://github.com/quanliulq-pixel/futures-dashboard)"
DISCLAIMER = "研究参考，不能下单，不构成投资建议"
IS_END = "2020-12-31"
OOS_START = "2021-01-01"
PAPER_START = "2026-11-13"
REFRESH_HOURS = 6.0
MAX_SNAPSHOT_DAYS = 10
CLOSE_CUTOFF = dtime(15, 5)
LOOKBACKS = (21, 63, 126, 252)
AU_GRAMS_PER_LOT = 1000

STRATEGIES = {
    "c1": dict(name="C1 · 沪金AU0 趋势+20%目标波动（上限1.5倍）", short="C1 杠杆版", symbol="AU0", vehicle="沪金主连 AU0",
               target_vol=0.20, cap=1.5, band=0.10, cost_per_side=4e-4, financing=True, eval_warmup=504,
               execution="下一交易时段开盘成交（AU 夜盘 21:00 起；无夜盘时次日 9:00）"),
    "robust": dict(name="稳健版 · 518880 趋势+12%目标波动（不加杠杆）", short="稳健版", symbol="518880",
                   vehicle="华安黄金ETF 518880", target_vol=0.12, cap=1.0, band=0.10, cost_per_side=3e-4,
                   financing=False, eval_warmup=252, execution="下一交易日 9:30 开盘成交"),
}
CAVEATS = {
    "c1": [
        "依赖杠杆：仓位最高 1.5 倍名义价值；同样规则用在 COMEX 金（GC=F）上跑输买入持有。",
        "起点敏感：从 2014–2016 年起算年化低于买入持有；超额主要来自 2011/2013/2025/2026 少数年份。",
        "统计不显著：63日分块 bootstrap 年化差 P(>0)≈0.77；参数扰动下“回撤≤买入持有0.7倍”仅约 31% 组合成立。",
        "实盘颗粒度：AU 每手 1000 克，1 手名义约 {lot} 万元；0.1 倍仓位精度需约 {acct} 万元账户；单日 −13%（2026-02-02）在 1.5 倍下约 −20%。",
        "AU0 为未复权主力连续（换月跳空≈持有成本），>1 倍部分已扣中国3个月银行间利率作融资成本；未计保证金利息收入、税费与涨跌停无法成交。",
    ],
    "robust": [
        "不加杠杆（ETF 只做多，上限 1.0）：历史上主要价值是把最大回撤约减半，全期年化低于买入持有。",
        "单笔胜率低（约 2–4 成）、盈亏比高，典型趋势跟踪：震荡市连续小亏，牛市只拿到部分涨幅。",
    ],
}

_lock = threading.Lock()
_cache: dict | None = None


# ---------------------------------------------------------------- utils
def _now() -> datetime:
    return datetime.now(CST)


def _r(x: Any, n: int = 4) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def _http(url: str, *, params=None, headers=None, timeout=20):
    import gold  # reuse bounded retry/timeout helper

    return gold._http("GET", url, params=params, headers=headers, timeout=timeout)


# ---------------------------------------------------------------- parsing (pure)
def _ohlc_frame(rows: list[dict], keys: tuple[str, str, str, str, str]) -> pd.DataFrame:
    dk, ok, hk, lk, ck = keys
    recs = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            d = pd.Timestamp(str(r[dk])[:10])
            o, h, l, c = (float(r[k]) for k in (ok, hk, lk, ck))
        except (KeyError, TypeError, ValueError):
            continue
        if min(o, h, l, c) > 0 and all(math.isfinite(v) for v in (o, h, l, c)):
            recs.append((d, o, h, l, c))
    df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close"])
    if df.empty:
        return df.set_index("date")
    return df.drop_duplicates("date", keep="last").set_index("date").sort_index()


def parse_sina_futures_jsonp(text: str) -> pd.DataFrame:
    a, b = (text or "").find("(["), (text or "").rfind("])")
    if a < 0 or b < 0:
        return _ohlc_frame([], ("d", "o", "h", "l", "c"))
    try:
        rows = json.loads(text[a + 1:b + 1])
    except ValueError:
        rows = []
    return _ohlc_frame(rows, ("d", "o", "h", "l", "c"))


def parse_sina_stock_kline(js: Any) -> pd.DataFrame:
    return _ohlc_frame(js if isinstance(js, list) else [], ("day", "open", "high", "low", "close"))


def parse_fred_monthly(text: str) -> pd.Series:
    out = {}
    for line in (text or "").splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            out[pd.Timestamp(parts[0])] = float(parts[1])
        except ValueError:
            continue
    return pd.Series(out, dtype=float).sort_index()


def completed_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop bars that may still be forming: dated after today, or today before 15:05 CST."""
    now = (now or _now()).astimezone(CST)
    today = pd.Timestamp(now.date())
    keep = df.index < today
    if now.time() >= CLOSE_CUTOFF:
        keep = keep | (df.index == today)
    return df[keep]


# ---------------------------------------------------------------- engine (pure, no I/O)
def trend_votes(close: pd.Series) -> pd.DataFrame:
    v = {f"mom{L}": np.sign(close / close.shift(L) - 1) for L in LOOKBACKS}
    v["ema50_200"] = np.sign(close.ewm(span=50).mean() - close.ewm(span=200).mean())
    out = pd.DataFrame(v)
    out[close.shift(252).isna()] = np.nan
    return out


def trend_score(close: pd.Series) -> pd.Series:
    return trend_votes(close).sum(axis=1, min_count=5) / 5.0


def ewma_vol(close: pd.Series, halflife: int = 20) -> pd.Series:
    r = np.log(close).diff()
    return np.sqrt((r ** 2).ewm(halflife=halflife, min_periods=60).mean() * 252)


def apply_band(w: pd.Series, band: float) -> pd.Series:
    out = np.zeros(len(w))
    cur = 0.0
    for i, x in enumerate(w.fillna(0).to_numpy()):
        if abs(x - cur) > band or (x == 0 and cur != 0) or (cur == 0 and x != 0 and abs(x) > band / 2):
            cur = x
        out[i] = cur
    return pd.Series(out, index=w.index)


def target_weights(close: pd.Series, target_vol: float, cap: float, band: float) -> pd.DataFrame:
    """Weight decided at close t (uses data <= t only)."""
    T = trend_score(close)
    vol = ewma_vol(close)
    valid = T.notna()
    raw = (T.clip(lower=0) * np.minimum(cap, target_vol / vol)).where(valid)
    w = apply_band(raw, band).where(valid).fillna(0.0)
    return pd.DataFrame({"T": T, "vol": vol, "raw": raw, "w": w})


def financing_rate(index: pd.DatetimeIndex, monthly: pd.Series | None) -> pd.Series:
    """Annual decimal rate known at each session: month value usable from month-end + 45 days, then 2 sessions lag."""
    if monthly is None or monthly.empty:
        return pd.Series(np.nan, index=index)
    s = monthly.copy()
    s.index = s.index + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(index.union(s.index)).ffill().reindex(index).shift(2) / 100.0


def simulate(ohlc: pd.DataFrame, w: pd.Series, cost: float, rate: pd.Series | None = None) -> pd.DataFrame:
    """w[t] decided at close t -> executed at open t+1. Row t holds that day's P&L."""
    w = w.reindex(ohlc.index).fillna(0.0)
    w_exec = w.shift(1).fillna(0.0)
    w_on = w.shift(2).fillna(0.0)
    gap = ohlc["open"] / ohlc["close"].shift(1) - 1
    intraday = ohlc["close"] / ohlc["open"] - 1
    gross = (1 + w_on * gap) * (1 + w_exec * intraday) - 1
    tc = cost * (w_exec - w_on).abs()
    fin = pd.Series(0.0, index=ohlc.index)
    if rate is not None:
        fin = (w_exec.abs() - 1).clip(lower=0) * rate.reindex(ohlc.index).fillna(0.0) / 252
    net = gross - tc - fin
    return pd.DataFrame({"net": net, "gross": gross, "tc": tc, "fin": fin, "w": w_exec, "w_on": w_on}).iloc[1:]


def metrics(df: pd.DataFrame) -> dict:
    r = df["net"].dropna()
    if len(r) < 20:
        return {"status": "missing", "reason": "样本不足"}
    nav = (1 + r).cumprod()
    yrs = len(r) / 252
    sd = r.std()
    dd = (nav / nav.cummax() - 1).min()
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    downside = math.sqrt((np.minimum(r, 0) ** 2).mean())
    w = df["w"].loc[r.index]
    sgn = np.sign(w.round(6))
    episodes = (sgn != sgn.shift()).cumsum()
    trades = np.array([(1 + g).prod() - 1 for _, g in r.groupby(episodes) if sgn.loc[g.index[0]] != 0])
    pos, neg = trades[trades > 0].sum(), -trades[trades < 0].sum()
    return {
        "status": "ok", "start": str(r.index[0].date()), "end": str(r.index[-1].date()), "sessions": int(len(r)),
        "total_return": _r(nav.iloc[-1] - 1), "cagr": _r(cagr), "vol": _r(sd * math.sqrt(252)),
        "sharpe": _r(r.mean() / sd * math.sqrt(252), 3) if sd > 0 else None,
        "sortino": _r(r.mean() / downside * math.sqrt(252), 3) if downside > 0 else None,
        "max_drawdown": _r(dd), "calmar": _r(cagr / abs(dd), 3) if dd < 0 else None,
        "trades": int(len(trades)), "win_rate": _r((trades > 0).mean(), 3) if len(trades) else None,
        "profit_factor": _r(pos / neg, 2) if neg > 0 else None,
        "turnover": _r(w.diff().abs().fillna(w.abs()).sum() / yrs, 2), "exposure": _r((w.abs() > 1e-6).mean(), 3),
        "avg_weight": _r(w.abs().mean(), 3), "cost_drag": _r(df["tc"].loc[r.index].sum() / yrs),
        "financing_drag": _r(df["fin"].loc[r.index].sum() / yrs) if "fin" in df else 0.0,
    }


def _periods(df: pd.DataFrame) -> dict:
    return {"full": metrics(df), "is": metrics(df.loc[:IS_END]), "oos": metrics(df.loc[OOS_START:])}


def _yearly(s: pd.DataFrame, b: pd.DataFrame) -> list[dict]:
    out = []
    for y in sorted(set(s.index.year)):
        rs, rb = s["net"][s.index.year == y], b["net"][b.index.year == y]
        nav_s, nav_b = (1 + rs).cumprod(), (1 + rb).cumprod()
        out.append({"year": int(y), "strategy": _r(nav_s.iloc[-1] - 1), "buy_hold": _r(nav_b.iloc[-1] - 1),
                    "strategy_dd": _r((nav_s / nav_s.cummax() - 1).min()), "buy_hold_dd": _r((nav_b / nav_b.cummax() - 1).min()),
                    "avg_weight": _r(s["w"][s.index.year == y].mean(), 3), "partial": bool(y == s.index[-1].year or y == s.index[0].year)})
    return out


def _curves(s: pd.DataFrame, b: pd.DataFrame) -> dict:
    nav_s, nav_b = (1 + s["net"]).cumprod(), (1 + b["net"]).cumprod()
    dd_s, dd_b = nav_s / nav_s.cummax() - 1, nav_b / nav_b.cummax() - 1
    frame = pd.DataFrame({"s": nav_s, "b": nav_b, "ds": dd_s, "db": dd_b, "w": s["w"]})
    weekly = frame.groupby(frame.index.to_period("W")).tail(1)
    if weekly.index[-1] != frame.index[-1]:
        weekly = pd.concat([weekly, frame.iloc[[-1]]])
    return {"dates": [str(d.date()) for d in weekly.index], "nav": [_r(x) for x in weekly.s], "nav_bh": [_r(x) for x in weekly.b],
            "dd": [_r(x) for x in weekly.ds], "dd_bh": [_r(x) for x in weekly.db], "weight": [_r(x, 3) for x in weekly.w]}


def run_strategy(key: str, ohlc: pd.DataFrame, rates: pd.Series | None = None) -> dict:
    cfg = STRATEGIES[key]
    if len(ohlc) < cfg["eval_warmup"] + 60:
        raise ValueError("日线不足")
    tw = target_weights(ohlc["close"], cfg["target_vol"], cfg["cap"], cfg["band"])
    rate = financing_rate(ohlc.index, rates) if cfg["financing"] else None
    if cfg["financing"] and (rate is None or rate.dropna().empty):
        raise ValueError("融资利率无数据")
    sim = simulate(ohlc, tw["w"], cfg["cost_per_side"], rate)
    bh_w = pd.Series(1.0, index=ohlc.index).where(tw["T"].notna()).fillna(0.0)
    bh = simulate(ohlc, bh_w, cfg["cost_per_side"])
    start = ohlc.index[cfg["eval_warmup"]]
    s, b = sim.loc[start:], bh.loc[start:]
    return {"tw": tw, "sim": sim, "bh": bh, "s": s, "b": b}


def signal_block(key: str, ohlc: pd.DataFrame, res: dict) -> dict:
    cfg = STRATEGIES[key]
    c = ohlc["close"]
    tw = res["tw"]
    votes = trend_votes(c).iloc[-1]
    names = {"mom21": "21日动量", "mom63": "63日动量", "mom126": "126日动量", "mom252": "252日动量", "ema50_200": "EMA50 vs EMA200"}
    detail = []
    for L in LOOKBACKS:
        detail.append({"key": f"mom{L}", "name": names[f"mom{L}"], "vote": _r(votes[f"mom{L}"], 0),
                       "value": _r(c.iloc[-1] / c.iloc[-1 - L] - 1), "unit": "pct"})
    e50, e200 = c.ewm(span=50).mean().iloc[-1], c.ewm(span=200).mean().iloc[-1]
    detail.append({"key": "ema50_200", "name": names["ema50_200"], "vote": _r(votes["ema50_200"], 0),
                   "value": _r(e50 / e200 - 1), "ema50": _r(e50, 3), "ema200": _r(e200, 3), "unit": "pct"})
    target = float(tw["w"].iloc[-1])
    held = float(tw["w"].iloc[-2]) if len(tw) > 1 else 0.0
    last_price = float(c.iloc[-1])
    out = {"status": "ok", "asof": str(c.index[-1].date()), "close": _r(last_price, 3), "trend_score": _r(tw["T"].iloc[-1], 2),
           "votes": detail, "ewma_vol": _r(tw["vol"].iloc[-1]), "raw_weight": _r(tw["raw"].iloc[-1], 3),
           "target_weight": _r(target, 3), "held_weight": _r(held, 3), "action": _action(held, target),
           "execution": cfg["execution"]}
    if key == "c1":
        lot = last_price * AU_GRAMS_PER_LOT
        out["lot_notional_wan"] = _r(lot / 1e4, 1)
        out["account_for_0p1_wan"] = _r(lot / 0.1 / 1e4, 0)
        out["target_lots_per_million"] = _r(target * 1e6 / lot, 2) if lot > 0 else None
    return out


def _action(held: float, target: float) -> str:
    if abs(target - held) < 1e-9:
        return "维持空仓" if target == 0 else f"维持 {target:.2f} 倍"
    if target == 0:
        return "清仓转空仓"
    if held == 0:
        return f"开仓至 {target:.2f} 倍"
    return f"{'加' if target > held else '减'}仓至 {target:.2f} 倍"


def update_ledger(prev: list[dict] | None, res: dict, ohlc: pd.DataFrame, cost: float, rate: pd.Series | None = None,
                  start: str = PAPER_START) -> list[dict]:
    """Append-only paper ledger. Recorded rows are never rewritten; new rows use the ledger's own recorded weights
    (overnight gap at the previous held weight, open fill at the previous close's target)."""
    rows = [dict(r) for r in (prev or []) if isinstance(r, dict) and r.get("date")]
    rows.sort(key=lambda r: r["date"])
    tw = res["tw"]
    t0 = pd.Timestamp(start)
    if not rows:
        if t0 not in tw.index:
            return []
        rows = [{"date": start, "close": _r(ohlc["close"].loc[t0], 3), "held_weight": 0.0,
                 "target_weight": _r(tw["w"].loc[t0], 3), "trend_score": _r(tw["T"].loc[t0], 2),
                 "daily_return": 0.0, "nav": 1.0, "note": f"起始：{start} 收盘，初始仓位 0"}]
    last = pd.Timestamp(rows[-1]["date"])
    for d in tw.index[tw.index > last]:
        i = ohlc.index.get_loc(d)
        prev_row = rows[-1]
        prev_close = float(ohlc["close"].iloc[i - 1])
        o, c = float(ohlc["open"].loc[d]), float(ohlc["close"].loc[d])
        w_on, w_exec = float(prev_row["held_weight"] or 0.0), float(prev_row["target_weight"] or 0.0)
        net = (1 + w_on * (o / prev_close - 1)) * (1 + w_exec * (c / o - 1)) - 1 - cost * abs(w_exec - w_on)
        if rate is not None and abs(w_exec) > 1:
            rv = rate.reindex([d]).iloc[0]
            net -= (abs(w_exec) - 1) * (0.0 if pd.isna(rv) else float(rv)) / 252
        nav = float(prev_row["nav"]) * (1 + net)
        rows.append({"date": str(d.date()), "close": _r(c, 3), "held_weight": _r(w_exec, 3),
                     "target_weight": _r(tw["w"].loc[d], 3), "trend_score": _r(tw["T"].loc[d], 2),
                     "daily_return": _r(net, 6), "nav": _r(nav, 6)})
    return rows


def strategy_block(key: str, ohlc: pd.DataFrame, rates: pd.Series | None, prev_ledger: list | None) -> dict:
    res = run_strategy(key, ohlc, rates)
    cfg = STRATEGIES[key]
    lot_wan = ohlc["close"].iloc[-1] * AU_GRAMS_PER_LOT / 1e4
    caveats = [c.format(lot=f"{lot_wan:.0f}", acct=f"{lot_wan * 10:.0f}") for c in CAVEATS[key]]
    rate = financing_rate(ohlc.index, rates) if cfg["financing"] else None
    ledger = update_ledger(prev_ledger, res, ohlc, cfg["cost_per_side"], rate)
    s, b = res["s"], res["b"]
    stressed = run_cost_stress(key, ohlc, rates)
    return {
        "status": "ok", "key": key, "name": cfg["name"], "short": cfg["short"], "vehicle": cfg["vehicle"],
        "asof": str(ohlc.index[-1].date()), "data_start": str(ohlc.index[0].date()),
        "params": {k: cfg[k] for k in ("target_vol", "cap", "band", "cost_per_side", "financing", "eval_warmup")},
        "signal": signal_block(key, ohlc, res),
        "backtest": {"strategy": _periods(s), "buy_hold": _periods(b), "cost_2x": stressed},
        "yearly": _yearly(s, b), "curves": _curves(s, b),
        "paper": {"start": PAPER_START, "ledger": ledger, "nav": ledger[-1]["nav"] if ledger else None,
                  "days": max(0, len(ledger) - 1)},
        "caveats": caveats,
    }


def run_cost_stress(key: str, ohlc: pd.DataFrame, rates: pd.Series | None) -> dict:
    cfg = STRATEGIES[key]
    tw = target_weights(ohlc["close"], cfg["target_vol"], cfg["cap"], cfg["band"])
    rate = financing_rate(ohlc.index, rates) if cfg["financing"] else None
    sim = simulate(ohlc, tw["w"], cfg["cost_per_side"] * 2, rate).loc[ohlc.index[cfg["eval_warmup"]]:]
    m = metrics(sim)
    return {k: m.get(k) for k in ("cagr", "sharpe", "max_drawdown")}


# ---------------------------------------------------------------- fetchers
def fetch_au0() -> pd.DataFrame:
    df = parse_sina_futures_jsonp(_http(SINA_FUT_URL, headers={"Referer": "https://finance.sina.com.cn/"}).text)
    if len(df) < 600:
        raise ValueError("新浪 AU0 日线不足")
    return df


def fetch_518880() -> pd.DataFrame:
    r = _http(SINA_STOCK_URL, params={"symbol": "sh518880", "scale": 240, "ma": "no", "datalen": 10000},
              headers={"Referer": "https://finance.sina.com.cn/"})
    df = parse_sina_stock_kline(r.json())
    if len(df) < 600:
        raise ValueError("新浪 518880 日线不足")
    return df


def fetch_cn_rate() -> pd.Series:
    r = _http(FRED_URL, params={"id": FRED_CN_RATE}, headers={"User-Agent": FRED_UA}, timeout=30)
    s = parse_fred_monthly(r.text)
    if len(s) < 24:
        raise ValueError("FRED 中国银行间利率无数据")
    return s


def default_fetchers() -> dict[str, Callable[[], Any]]:
    return {"au0": fetch_au0, "etf518880": fetch_518880, "cn_rate": fetch_cn_rate}


def fetch_all(fetchers: dict | None = None) -> tuple[dict, dict]:
    raw, errors = {}, {}
    for k, fn in (fetchers or default_fetchers()).items():
        try:
            raw[k] = fn()
        except Exception as exc:  # noqa: BLE001
            errors[k] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return raw, errors


# ---------------------------------------------------------------- payload
def _rates_from_prev(prev: dict | None) -> pd.Series | None:
    rows = ((prev or {}).get("financing") or {}).get("monthly") or []
    if not rows:
        return None
    return pd.Series({pd.Timestamp(r["d"]): float(r["v"]) for r in rows if r.get("v") is not None}).sort_index()


def _fallback(prev_block: dict | None, today: date, reason: str) -> dict:
    if isinstance(prev_block, dict) and prev_block.get("status") == "ok" and prev_block.get("asof"):
        try:
            age = (today - date.fromisoformat(prev_block["asof"])).days
        except ValueError:
            age = 999
        if age <= MAX_SNAPSHOT_DAYS:
            out = copy.deepcopy(prev_block)
            out["snapshot"] = True
            out["snapshot_date"] = prev_block["asof"]
            out["snapshot_reason"] = reason
            return out
    return {"status": "missing", "reason": reason}


def build_payload(raw: dict, errors: dict | None = None, prev: dict | None = None, now: datetime | None = None) -> dict:
    now = now or _now()
    errors = dict(errors or {})
    prev = prev or {}
    rates = raw.get("cn_rate")
    rates_from_snapshot = False
    if rates is None or len(rates) == 0:
        rates = _rates_from_prev(prev)
        rates_from_snapshot = rates is not None
    blocks = {}
    for key, raw_key in (("c1", "au0"), ("robust", "etf518880")):
        prev_block = (prev.get("strategies") or {}).get(key)
        prev_ledger = ((prev_block or {}).get("paper") or {}).get("ledger")
        df = raw.get(raw_key)
        if df is None or len(df) == 0:
            blocks[key] = _fallback(prev_block, now.date(), errors.get(raw_key) or "日线无数据")
            continue
        try:
            blocks[key] = strategy_block(key, completed_bars(df, now), rates, prev_ledger)
        except Exception as exc:  # noqa: BLE001
            logger.info("gold_quant %s failed", key, exc_info=True)
            errors[key] = f"{type(exc).__name__}: {str(exc)[:160]}"
            blocks[key] = _fallback(prev_block, now.date(), errors[key])
    status = "ok" if any(b.get("status") == "ok" for b in blocks.values()) else "no_data"
    fin_rows = [{"d": str(d.date()), "v": _r(v, 4)} for d, v in (rates.items() if rates is not None else [])]
    return {
        "schema_version": 1, "status": status, "updated_at": now.isoformat(timespec="seconds"),
        "title": "黄金量化纸面跟踪", "disclaimer": DISCLAIMER,
        "research_only": True, "order_interface": False,
        "rules": {
            "timing": "收盘 t 决策（仅用已完成日线），t+1 开盘成交；隔夜跳空按旧仓位计；未完成的盘中/夜盘K线丢弃。",
            "signal": "趋势分 T = 5 票等权：sign(21/63/126/252 日收益) 与 sign(EMA50−EMA200)；只取正部分（只做多/空仓）。",
            "sizing": "仓位 = max(T,0) × min(上限, 目标波动 / EWMA年化波动(半衰期20日))；与当前仓位差 ≤0.10 不调。",
            "costs": "单边成本：ETF 3bp，期货 4bp（按 |Δ仓位|）；C1 超过 1 倍的部分按中国3个月银行间利率扣融资成本。",
            "periods": f"样本内 ~{IS_END}，样本外 {OOS_START}~；夏普无风险利率取 0，空仓现金收益取 0。",
            "paper": f"纸面跟踪自 {PAPER_START} 收盘起，初始仓位 0，账本只追加不改写。",
        },
        "strategies": blocks,
        "financing": {"series": FRED_CN_RATE, "label": "FRED 中国3个月银行间利率（月度，月末+45天可得）",
                      "monthly": fin_rows[-240:], "snapshot": rates_from_snapshot},
        "sources": [
            {"label": "新浪期货日线 AU0（沪金主连）", "url": "https://finance.sina.com.cn/futures/quotes/AU0.shtml"},
            {"label": "新浪日线 sh518880（华安黄金ETF）", "url": "https://finance.sina.com.cn/fund/quotes/518880/bc.shtml"},
            {"label": f"FRED {FRED_CN_RATE}", "url": f"https://fred.stlouisfed.org/series/{FRED_CN_RATE}"},
        ],
        "errors": errors,
    }


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
        logger.info("gold_quant runtime write failed", exc_info=True)


def fetch_remote_seed() -> dict | None:
    try:
        js = _http(REMOTE_SEED_URL, timeout=10).json()
        return js if isinstance(js, dict) and js.get("status") == "ok" else None
    except Exception:  # noqa: BLE001
        return None


def _longest_ledger_first(cands: list) -> dict | None:
    """Prefer the snapshot whose paper ledgers go furthest (ledger continuity), then newest."""
    ok = [c for c in cands if isinstance(c, dict) and c.get("status") == "ok"]
    if not ok:
        return None

    def key(c):
        last = [((b or {}).get("paper") or {}).get("ledger") or [] for b in (c.get("strategies") or {}).values()]
        return (max((l[-1]["date"] for l in last if l), default=""), str(c.get("updated_at") or ""))

    return max(ok, key=key)


def refresh(fetchers: dict | None = None, *, use_remote: bool = True, now: datetime | None = None) -> dict:
    global _cache
    with _lock:
        prev = load_best()
        if use_remote:
            prev = _longest_ledger_first([prev, fetch_remote_seed()]) or prev
        raw, errors = fetch_all(fetchers)
        payload = build_payload(raw, errors, prev, now=now)
        live = any(b.get("status") == "ok" and not b.get("snapshot") for b in payload["strategies"].values())
        if not live and prev:
            payload = copy.deepcopy(prev)
            payload["errors"] = errors
            payload["note"] = "本轮抓取失败，显示上次快照"
            payload["from_snapshot"] = True
        if payload.get("status") == "ok" and not payload.get("from_snapshot"):
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
            "disclaimer": DISCLAIMER, "research_only": True, "order_interface": False}


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
    brief = {k: (v.get("signal") or {}).get("target_weight") for k, v in (data.get("strategies") or {}).items()}
    print(json.dumps({"status": data.get("status"), "errors": data.get("errors"), "target_weights": brief}, ensure_ascii=False))
    if "--write-seed" in sys.argv and data.get("status") == "ok":
        write_seed(data)
