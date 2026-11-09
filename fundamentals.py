# -*- coding: utf-8 -*-
"""基本面打分：国内商品期货（库存/基差/期限结构/季节性）与美股（估值/质量/成长/分析师 + 财报窗口）。

原则
- 每个因子输出 signal ∈ [-1, 1]（正 = 基本面偏多/偏支撑），附数值、数据日期、来源；
- 卖Put 侧基本面分 = 50 + 50·加权signal；卖Call 侧 = 50 − 50·加权signal（偏空/高估利于卖Call）；
- 缺失因子标「无数据」，只在可用因子间重新归一权重；全部缺失时基本面分为 None，
  综合分 = 技术分并标「基本面无数据」。绝不插补或编造数值。
- 综合分 = TECH_WEIGHT·技术分(截断到0–100) + FUND_WEIGHT·基本面分。
- 所有网络取数失败都不致命：因子记为无数据 + 原因。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# —— 综合权重（具名常量） ——
TECH_WEIGHT = 0.6
FUND_WEIGHT = 0.4

CN_FACTOR_WEIGHTS: dict[str, float] = {
    "inventory": 0.30,
    "basis": 0.30,
    "term": 0.25,
    "seasonality": 0.15,
}
US_FACTOR_WEIGHTS: dict[str, float] = {
    "valuation": 0.25,
    "quality": 0.30,
    "growth": 0.25,
    "analyst": 0.20,
}

# 美股财报落在期权到期前（含）→ 基本面分扣分（卖Put/卖Call 都扣）。
EARNINGS_IN_WINDOW_PENALTY = 10.0
# 无期权合约时用于判断「财报窗口」的默认天数（与 14–45 DTE 选约上限一致）。
EARNINGS_DEFAULT_WINDOW_DAYS = 45

# 成本控制
US_FUND_TOP_N = 30
US_FUND_CACHE_TTL = 24 * 3600
US_FUND_WORKERS = 6
US_FUND_BATCH_TIMEOUT_SEC = 90.0
CN_FUND_CACHE_TTL = 6 * 3600
CN_FUND_WORKERS = 6
CN_FUND_BATCH_TIMEOUT_SEC = 120.0

# CN 因子标定
CN_INV_LOOKBACK_ROWS = 20          # 约 4 周（东方财富库存/仓单为逐日序列）
CN_INV_FULL_SCALE = 0.30           # 库存 4 周下降 30% → signal +1
CN_BASIS_FULL_SCALE = 0.05         # 现货较主力升水 5% → signal +1
CN_BASIS_SUSPECT = 0.10            # |基差率|>10% 时在文案中提示核对口径（不改分）
CN_TERM_FULL_SCALE = 0.01          # 近月较远月每月升水 1% → signal +1
CN_SEASON_YEARS = 10
CN_SEASON_MIN_YEARS = 5
CN_SEASON_HORIZON_DAYS = 30
CN_SEASON_RET_FULL_SCALE = 0.05

NO_DATA = "无数据"

# akshare 参数映射
CN_INVENTORY_CODE = {
    "CU0": "cu", "AU0": "au", "M0": "m", "SR0": "SR", "AG0": "ag", "AL0": "al", "ZN0": "zn",
    "RB0": "rb", "RU0": "ru", "C0": "c",  # SC0：东方财富无原油库存序列（akshare 映射为 None）
    "I0": "i", "P0": "p", "Y0": "y",
    "L0": "l", "V0": "v", "PP0": "pp", "PG0": "pg", "CF0": "CF", "TA0": "TA", "MA0": "MA",
    "RM0": "RM", "OI0": "OI",
}
CN_REALTIME_NAME = {
    "CU0": "沪铜", "AU0": "黄金", "M0": "豆粕", "SR0": "白糖", "AG0": "白银", "AL0": "沪铝",
    "ZN0": "沪锌", "RB0": "螺纹钢", "RU0": "橡胶", "SC0": "原油", "C0": "玉米", "I0": "铁矿石",
    "P0": "棕榈", "Y0": "豆油", "L0": "塑料", "V0": "PVC", "PP0": "PP", "PG0": "液化石油气",
    "CF0": "棉花", "TA0": "PTA", "MA0": "郑醇", "RM0": "菜粕", "OI0": "菜油",
}

SRC_CN_INVENTORY = {
    "function": "akshare.futures_inventory_em",
    "url": "https://data.eastmoney.com/ifdata/kcsj.html",
    "label": "东方财富 期货库存/仓单",
}
SRC_CN_BASIS = {
    "function": "akshare.futures_spot_price",
    "url": "https://www.100ppi.com/sf/",
    "label": "生意社 现期表（现货 vs 主力）",
}
SRC_CN_TERM = {
    "function": "akshare.futures_zh_realtime",
    "url": "https://finance.sina.com.cn/futuremarket/",
    "label": "新浪期货 各月合约前结算价",
}
SRC_CN_SEASON = {
    "function": "akshare.futures_main_sina（看板已有日线）",
    "url": "https://finance.sina.com.cn/futuremarket/",
    "label": "新浪主力连续合约历史日线",
}


def _src_us(symbol: str) -> dict[str, str]:
    return {
        "function": "yfinance.Ticker.info",
        "url": f"https://finance.yahoo.com/quote/{symbol}/key-statistics",
        "label": "Yahoo Finance 关键统计",
    }


# ---------------------------------------------------------------- helpers

def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _r(x: Any, n: int = 2) -> Any:
    v = _num(x)
    return None if v is None else round(v, n)


def today_cst() -> date:
    return datetime.now(CST).date()


def factor(
    key: str,
    name: str,
    *,
    value: Any = None,
    value_text: str | None = None,
    asof: str | None = None,
    source: dict | None = None,
    signal: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """One factor record. signal None ⇒ 无数据（never imputed）."""
    ok = signal is not None and _num(signal) is not None
    return {
        "key": key,
        "name": name,
        "value": value if ok else None,
        "value_text": value_text if ok else None,
        "asof": asof if ok else None,
        "source": (source or {}).get("label"),
        "source_function": (source or {}).get("function"),
        "source_url": (source or {}).get("url"),
        "signal": round(clip(float(signal)), 3) if ok else None,
        "signal_text": signal_text(signal) if ok else NO_DATA,
        "status": "ok" if ok else "missing",
        "reason": reason if not ok else None,
        "weight": None,
        "contribution": None,
    }


def signal_text(s: float | None) -> str:
    if s is None:
        return NO_DATA
    if s >= 0.5:
        return "明显偏多"
    if s >= 0.15:
        return "偏多"
    if s > -0.15:
        return "中性"
    if s > -0.5:
        return "偏空"
    return "明显偏空"


def score_factors(
    factors: list[dict[str, Any]],
    weights: dict[str, float],
    side: str,
    *,
    penalty: float = 0.0,
    penalty_reason: str | None = None,
) -> dict[str, Any]:
    """Weighted fundamental score for one side.

    Returns {fund_score, fund_status, fund_available, fund_missing, fund_factors}.
    Contributions are in score points relative to the neutral 50 and sum (with the
    penalty) to fund_score − 50.
    """
    side = "call" if side == "call" else "put"
    sign = -1.0 if side == "call" else 1.0
    out_factors = [dict(f) for f in factors]
    avail = [f for f in out_factors if f.get("status") == "ok" and f.get("key") in weights]
    wsum = sum(weights[f["key"]] for f in avail)
    missing = [f["name"] for f in out_factors if f.get("status") != "ok"]
    if not avail or wsum <= 0:
        for f in out_factors:
            f["weight"] = 0.0
            f["contribution"] = None
        return {
            "fund_score": None,
            "fund_status": "no_data",
            "fund_status_cn": "基本面无数据",
            "fund_available": [],
            "fund_missing": missing,
            "fund_factors": out_factors,
            "fund_penalty": None,
        }
    s = 0.0
    for f in out_factors:
        if f.get("status") == "ok" and f.get("key") in weights:
            w = weights[f["key"]] / wsum
            f["weight"] = round(w, 3)
            contrib = 50.0 * sign * w * float(f["signal"])
            f["contribution"] = round(contrib, 1)
            s += contrib
        else:
            f["weight"] = 0.0
            f["contribution"] = None
    score = 50.0 + s
    pen = None
    if penalty and penalty > 0:
        score -= penalty
        pen = {"points": -round(penalty, 1), "reason": penalty_reason}
    score = max(0.0, min(100.0, score))
    return {
        "fund_score": round(score, 1),
        "fund_status": "ok" if not missing else "partial",
        "fund_status_cn": "基本面完整" if not missing else f"部分无数据（{'、'.join(missing)}）",
        "fund_available": [f["name"] for f in avail],
        "fund_missing": missing,
        "fund_factors": out_factors,
        "fund_penalty": pen,
    }


def combine_scores(tech: Any, fund: Any) -> dict[str, Any]:
    """combined = TECH_WEIGHT·clip(tech,0,100) + FUND_WEIGHT·fund; fund None ⇒ combined = tech."""
    t = _num(tech)
    f = _num(fund)
    if t is None:
        return {"combined_score": None, "combined_basis": "no_tech"}
    tc = max(0.0, min(100.0, t))
    if f is None:
        return {"combined_score": round(tc, 1), "combined_basis": "tech_only"}
    return {"combined_score": round(TECH_WEIGHT * tc + FUND_WEIGHT * f, 1), "combined_basis": "tech+fund"}


def apply_fund_to_row(row: dict, fund: dict | None, *, not_computed_reason: str | None = None) -> dict:
    """Write tech/fund/combined fields onto a candidate row (score keeps tech semantics)."""
    tech = row.get("score")
    row["tech_score"] = tech
    if fund is None:
        row["fund_score"] = None
        row["fund_status"] = "not_computed"
        row["fund_status_cn"] = not_computed_reason or "基本面未计算"
        row["fund_factors"] = []
        row["fund_available"] = []
        row["fund_missing"] = []
        row["fund_penalty"] = None
    else:
        row.update(fund)
    comb = combine_scores(tech, row.get("fund_score"))
    row["combined_score"] = comb["combined_score"]
    row["combined_basis"] = comb["combined_basis"]
    return row


def rank_key(row: dict) -> float:
    v = _num(row.get("combined_score"))
    if v is None:
        v = _num(row.get("score"))
    return -1e9 if v is None else v


# ---------------------------------------------------------------- CN factor scoring (pure)

def cn_inventory_factor(df, *, lookback: int = CN_INV_LOOKBACK_ROWS) -> dict:
    """Falling inventory/receipts ⇒ supportive (+)."""
    name = "库存/仓单趋势"
    try:
        import pandas as pd

        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return factor("inventory", name, reason="无库存数据", source=SRC_CN_INVENTORY)
        dcol = "日期" if "日期" in df.columns else "date"
        vcol = "库存" if "库存" in df.columns else "inventory"
        w = df[[dcol, vcol]].copy()
        w[vcol] = pd.to_numeric(w[vcol], errors="coerce")
        w[dcol] = pd.to_datetime(w[dcol], errors="coerce")
        w = w.dropna().sort_values(dcol)
        if len(w) <= lookback:
            return factor("inventory", name, reason=f"库存序列不足{lookback + 1}期", source=SRC_CN_INVENTORY)
        last = float(w[vcol].iloc[-1])
        ref = float(w[vcol].iloc[-1 - lookback])
        if last < 0 or ref < 0:
            return factor("inventory", name, reason="库存为负值，数据异常", source=SRC_CN_INVENTORY)
        if ref == 0:
            if last == 0:
                chg = 0.0
            else:
                return factor("inventory", name, reason="基期库存为0，无法计算变化率", source=SRC_CN_INVENTORY)
        else:
            chg = (last - ref) / ref
        sig = clip(-chg / CN_INV_FULL_SCALE)
        asof = w[dcol].iloc[-1].strftime("%Y-%m-%d")
        ref_date = w[dcol].iloc[-1 - lookback].strftime("%Y-%m-%d")
        txt = (f"{asof} 库存 {last:,.0f}，较 {ref_date}（{lookback}期前）{chg * 100:+.1f}%"
               f"（{'去化偏多' if chg < 0 else '累库偏空' if chg > 0 else '持平'}）")
        return factor("inventory", name, value=round(chg * 100, 2), value_text=txt, asof=asof,
                      source=SRC_CN_INVENTORY, signal=sig)
    except Exception as exc:  # noqa: BLE001
        return factor("inventory", name, reason=f"库存解析失败 {type(exc).__name__}", source=SRC_CN_INVENTORY)


def cn_basis_factor(spot_row: dict | None) -> dict:
    """Spot premium over dominant futures (backwardated basis) ⇒ bullish (+)."""
    name = "基差（现货vs主力）"
    if not spot_row:
        return factor("basis", name, reason="现期表无该品种", source=SRC_CN_BASIS)
    spot = _num(spot_row.get("spot_price"))
    dom = _num(spot_row.get("dominant_contract_price"))
    if spot is None or dom is None or spot <= 0 or dom <= 0:
        return factor("basis", name, reason="现货或主力价格缺失/非正", source=SRC_CN_BASIS)
    prem = (spot - dom) / dom  # >0 现货升水
    sig = clip(prem / CN_BASIS_FULL_SCALE)
    d = str(spot_row.get("date") or "")
    asof = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else (d[:10] or None)
    txt = (f"{asof} 现货 {spot:,.2f} / 主力 {spot_row.get('dominant_contract') or ''} {dom:,.2f}，"
           f"现货{'升水' if prem > 0 else '贴水' if prem < 0 else '平水'} {prem * 100:+.2f}%")
    if abs(prem) > CN_BASIS_SUSPECT:
        txt += "（|基差|>10%，现货报价口径可能与交割品级不一致，请核对）"
    return factor("basis", name, value=round(prem * 100, 2), value_text=txt, asof=asof,
                  source=SRC_CN_BASIS, signal=sig)


def _contract_month_index(code: str) -> int | None:
    """'CU2612' → months since 2000; CZCE 3-digit 'MA701' not used by sina realtime (4-digit)."""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 4:
        return None
    yy, mm = int(digits[-4:-2]), int(digits[-2:])
    if not 1 <= mm <= 12:
        return None
    return yy * 12 + mm


def cn_term_factor(contracts: list[dict] | None, tradedate: str | None = None) -> dict:
    """Two most-held contracts' previous settlement: near > far (backwardation) ⇒ bullish (+)."""
    name = "期限结构（近月vs远月）"
    rows = []
    for c in contracts or []:
        idx = _contract_month_index(c.get("symbol") or "")
        ps = _num(c.get("presettlement"))
        pos = _num(c.get("position"))
        if idx is None or ps is None or ps <= 0 or pos is None:
            continue
        rows.append({"symbol": c.get("symbol"), "idx": idx, "ps": ps, "pos": pos})
    if len(rows) < 2:
        return factor("term", name, reason="有效合约不足2个", source=SRC_CN_TERM)
    top = sorted(rows, key=lambda r: r["pos"], reverse=True)[:2]
    near, far = sorted(top, key=lambda r: r["idx"])
    gap = far["idx"] - near["idx"]
    if gap <= 0:
        return factor("term", name, reason="合约月份无法区分", source=SRC_CN_TERM)
    spread = (near["ps"] - far["ps"]) / far["ps"]
    per_month = spread / gap
    sig = clip(per_month / CN_TERM_FULL_SCALE)
    shape = "近强远弱（Back）" if per_month > 0 else "近弱远强（Contango）" if per_month < 0 else "平坦"
    asof = None
    if tradedate:
        asof = f"前结算价（行情交易日 {str(tradedate)[:10]}）"
    txt = (f"持仓前二：{near['symbol']} 前结算 {near['ps']:,.2f} / {far['symbol']} {far['ps']:,.2f}，"
           f"相差{gap}个月，月均 {per_month * 100:+.2f}%（{shape}）")
    return factor("term", name, value=round(per_month * 100, 3), value_text=txt, asof=asof,
                  source=SRC_CN_TERM, signal=sig)


def cn_seasonality_factor(series, *, years: int = CN_SEASON_YEARS, horizon_days: int = CN_SEASON_HORIZON_DAYS,
                          min_years: int = CN_SEASON_MIN_YEARS) -> dict:
    """Same calendar window over past years on the main continuous series."""
    name = f"季节性（近{years}年同期{horizon_days}天）"
    try:
        import pandas as pd

        if series is None or len(series) < 260 or not isinstance(series.index, pd.DatetimeIndex):
            return factor("seasonality", name, reason="历史日线不足或无日期轴", source=SRC_CN_SEASON)
        s = series.sort_index()
        anchor = s.index[-1]
        rets = []
        for y in range(1, years + 1):
            try:
                start = anchor - pd.DateOffset(years=y)
            except Exception:
                continue
            end = start + pd.Timedelta(days=horizon_days)
            a = s[s.index >= start]
            b = s[s.index >= end]
            if a.empty or b.empty:
                continue
            # bars must be close to target dates (no multi-week gaps)
            if (a.index[0] - start).days > 7 or (b.index[0] - end).days > 7:
                continue
            if b.index[0] >= anchor - pd.Timedelta(days=1):
                continue
            rets.append(float(b.iloc[0] / a.iloc[0] - 1.0))
        if len(rets) < min_years:
            return factor("seasonality", name, reason=f"可用年份{len(rets)}<{min_years}", source=SRC_CN_SEASON)
        n = len(rets)
        ups = sum(1 for r in rets if r > 0)
        hit = ups / n
        rets_sorted = sorted(rets)
        med = rets_sorted[n // 2] if n % 2 else (rets_sorted[n // 2 - 1] + rets_sorted[n // 2]) / 2
        sig = 0.5 * (2 * hit - 1) + 0.5 * clip(med / CN_SEASON_RET_FULL_SCALE)
        asof = anchor.strftime("%Y-%m-%d")
        txt = f"近{n}年自 {anchor.strftime('%m-%d')} 起{horizon_days}天：上涨{ups}/{n}年，中位数 {med * 100:+.1f}%（主力连续，含换月跳空）"
        return factor("seasonality", name, value=round(med * 100, 2), value_text=txt, asof=asof,
                      source=SRC_CN_SEASON, signal=clip(sig))
    except Exception as exc:  # noqa: BLE001
        return factor("seasonality", name, reason=f"季节性计算失败 {type(exc).__name__}", source=SRC_CN_SEASON)


# ---------------------------------------------------------------- CN network provider

class CnFundamentalsProvider:
    """Fetch shared CN fundamentals once per TTL (both put & call scans reuse it)."""

    def __init__(self, ttl: float = CN_FUND_CACHE_TTL):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None

    def snapshot(self, symbols: Iterable[str]) -> dict:
        symbols = list(symbols)
        with self._lock:
            if self._cache and time.time() - self._cache[0] < self.ttl:
                cached = self._cache[1]
                if all(s in cached.get("inventory", {}) for s in symbols):
                    return cached
            snap = self._fetch(symbols)
            self._cache = (time.time(), snap)
            return snap

    def _fetch(self, symbols: list[str]) -> dict:
        t0 = time.time()
        snap: dict[str, Any] = {"inventory": {}, "term": {}, "spot": {}, "errors": {}, "fetched_at": None}
        snap["spot"], spot_err = _fetch_cn_spot_table()
        if spot_err:
            snap["errors"]["spot"] = spot_err

        def inv(sym):
            code = CN_INVENTORY_CODE.get(sym)
            if not code:
                return sym, None, "数据源无该品种库存序列"
            import akshare as ak

            try:
                return sym, ak.futures_inventory_em(symbol=code), None
            except Exception as exc:  # noqa: BLE001
                return sym, None, f"{type(exc).__name__}: {str(exc)[:80]}"

        def term(sym):
            nm = CN_REALTIME_NAME.get(sym)
            if not nm:
                return sym, None, "无合约名映射"
            import akshare as ak

            last = None
            for _ in range(2):
                try:
                    df = ak.futures_zh_realtime(symbol=nm)
                    df = df[~df["name"].astype(str).str.contains("连续")]
                    recs = df[["symbol", "presettlement", "position", "tradedate"]].to_dict("records")
                    return sym, recs, None
                except Exception as exc:  # noqa: BLE001
                    last = f"{type(exc).__name__}: {str(exc)[:80]}"
            return sym, None, last

        pool = ThreadPoolExecutor(max_workers=CN_FUND_WORKERS)
        kinds = {pool.submit(inv, s): "inventory" for s in symbols}
        kinds.update({pool.submit(term, s): "term" for s in symbols})
        done, not_done = wait(list(kinds), timeout=CN_FUND_BATCH_TIMEOUT_SEC)
        for fut in done:
            bucket = kinds[fut]
            try:
                sym, data, err = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if err:
                snap["errors"][f"{bucket}:{sym}"] = err
            snap[bucket][sym] = data
        for fut in not_done:
            fut.cancel()
        if not_done:
            snap["errors"]["timeout"] = f"{len(not_done)} 个请求超时（>{CN_FUND_BATCH_TIMEOUT_SEC:.0f}s）"
        pool.shutdown(wait=False, cancel_futures=True)
        for s in symbols:
            snap["inventory"].setdefault(s, None)
            snap["term"].setdefault(s, None)
        snap["fetched_at"] = datetime.now(CST).isoformat(timespec="seconds")
        snap["fetch_seconds"] = round(time.time() - t0, 1)
        return snap


def _fetch_cn_spot_table(max_back_days: int = 7) -> tuple[dict, str | None]:
    """Latest available 现期表 (walk back over weekends/holidays)."""
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        return {}, f"akshare 不可用 {type(exc).__name__}"
    d = today_cst()
    last_err = None
    for i in range(max_back_days):
        ds = (d - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.futures_spot_price(date=ds)
            if df is None or df.empty:
                last_err = f"{ds} 无数据"
                continue
            out = {}
            for rec in df.to_dict("records"):
                sym = str(rec.get("symbol") or "").upper()
                if sym:
                    out[sym] = rec
            return out, None
        except Exception as exc:  # noqa: BLE001
            last_err = f"{ds} {type(exc).__name__}: {str(exc)[:60]}"
    return {}, last_err


DEFAULT_CN_PROVIDER = CnFundamentalsProvider()


def cn_factors_for(symbol: str, series, snap: dict | None) -> list[dict]:
    snap = snap or {}
    root = symbol.upper().removesuffix("0")
    inv_df = (snap.get("inventory") or {}).get(symbol)
    term_recs = (snap.get("term") or {}).get(symbol)
    tradedate = None
    if term_recs:
        tradedate = term_recs[0].get("tradedate")
    errs = snap.get("errors") or {}
    f_inv = cn_inventory_factor(inv_df)
    if f_inv["status"] != "ok" and errs.get(f"inventory:{symbol}"):
        f_inv["reason"] = f"取数失败：{errs[f'inventory:{symbol}']}"
    f_basis = cn_basis_factor((snap.get("spot") or {}).get(root))
    if f_basis["status"] != "ok" and errs.get("spot"):
        f_basis["reason"] = f"现期表取数失败：{errs['spot']}"
    f_term = cn_term_factor(term_recs, tradedate)
    if f_term["status"] != "ok" and errs.get(f"term:{symbol}"):
        f_term["reason"] = f"取数失败：{errs[f'term:{symbol}']}"
    f_season = cn_seasonality_factor(series)
    return [f_inv, f_basis, f_term, f_season]


# ---------------------------------------------------------------- US factor scoring (pure)

def _band(x: float, bands: list[tuple[float, float]], else_sig: float) -> float:
    """bands: [(upper_exclusive, signal), ...] ascending."""
    for ub, sig in bands:
        if x < ub:
            return sig
    return else_sig


def _date_from_epoch(v: Any) -> str | None:
    x = _num(v)
    if x is None or x <= 0:
        return None
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def us_valuation_factor(info: dict, symbol: str, fetched: str | None) -> dict:
    name = "估值（PE）"
    src = _src_us(symbol)
    fpe = _num(info.get("forwardPE"))
    tpe = _num(info.get("trailingPE"))
    pe, which = None, None
    if fpe is not None and 0 < fpe < 1000:
        pe, which = fpe, "远期PE"
    elif tpe is not None and 0 < tpe < 1000:
        pe, which = tpe, "滚动PE"
    if pe is None:
        pm = _num(info.get("profitMargins"))
        if (fpe is not None and fpe < 0) or (pm is not None and pm < 0 and tpe is None):
            return factor("valuation", name, value=None, value_text="亏损（PE为负/无），估值无支撑",
                          asof=fetched, source=src, signal=-0.6)
        return factor("valuation", name, reason="无PE数据", source=src)
    sig = _band(pe, [(12, 1.0), (20, 0.5), (30, 0.0), (50, -0.5)], -1.0)
    return factor("valuation", name, value=round(pe, 1), value_text=f"{which} {pe:.1f}（绝对区间评估，非行业相对）",
                  asof=fetched, source=src, signal=sig)


def us_quality_factor(info: dict, symbol: str, fetched: str | None) -> dict:
    name = "盈利质量（利润率/ROE/FCF/负债）"
    src = _src_us(symbol)
    subs, parts = [], []
    pm = _num(info.get("profitMargins"))
    if pm is not None:
        subs.append(_band(pm, [(0, -1.0), (0.10, 0.0), (0.20, 0.5)], 1.0))
        parts.append(f"净利率 {pm * 100:.1f}%")
    roe = _num(info.get("returnOnEquity"))
    if roe is not None:
        subs.append(_band(roe, [(0, -1.0), (0.10, 0.0), (0.20, 0.5)], 1.0))
        parts.append(f"ROE {roe * 100:.1f}%")
    fcf = _num(info.get("freeCashflow"))
    if fcf is not None:
        subs.append(1.0 if fcf > 0 else -1.0 if fcf < 0 else 0.0)
        parts.append(f"FCF {'正' if fcf > 0 else '负' if fcf < 0 else '0'}（{fcf / 1e9:+.2f}B）")
    de = _num(info.get("debtToEquity"))
    if de is not None and de >= 0:
        subs.append(_band(de, [(50, 1.0), (100, 0.5), (200, 0.0)], -1.0))
        parts.append(f"负债/权益 {de / 100:.2f}x")
    if not subs:
        return factor("quality", name, reason="无利润率/ROE/FCF/负债数据", source=src)
    asof = _date_from_epoch(info.get("mostRecentQuarter"))
    return factor("quality", name, value=round(sum(subs) / len(subs), 3), value_text="；".join(parts),
                  asof=(f"最近财季 {asof}" if asof else fetched), source=src, signal=sum(subs) / len(subs))


def us_growth_factor(info: dict, symbol: str, fetched: str | None) -> dict:
    name = "成长（营收/盈利同比）"
    src = _src_us(symbol)
    subs, parts = [], []
    for k, lab in (("revenueGrowth", "营收"), ("earningsGrowth", "盈利")):
        g = _num(info.get(k))
        if g is None:
            continue
        subs.append(_band(g, [(-0.05, -1.0), (0.05, 0.0), (0.20, 0.5)], 1.0))
        parts.append(f"{lab}同比 {g * 100:+.1f}%")
    if not subs:
        return factor("growth", name, reason="无营收/盈利增速数据", source=src)
    asof = _date_from_epoch(info.get("mostRecentQuarter"))
    return factor("growth", name, value=round(sum(subs) / len(subs), 3), value_text="；".join(parts),
                  asof=(f"最近财季 {asof}" if asof else fetched), source=src, signal=sum(subs) / len(subs))


def us_analyst_factor(info: dict, symbol: str, fetched: str | None, price: Any = None) -> dict:
    name = "分析师（目标价/评级）"
    src = _src_us(symbol)
    n = _num(info.get("numberOfAnalystOpinions"))
    if n is None or n < 3:
        return factor("analyst", name, reason="覆盖分析师<3", source=src)
    subs, parts = [], [f"{int(n)}位分析师"]
    tgt = _num(info.get("targetMeanPrice"))
    px = _num(price) or _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if tgt is not None and px is not None and px > 0 and tgt > 0:
        up = tgt / px - 1
        subs.append(_band(up, [(-0.05, -1.0), (0.05, 0.0), (0.20, 0.5)], 1.0))
        parts.append(f"目标均价 {tgt:.2f}（较现价 {up * 100:+.1f}%）")
    rec = _num(info.get("recommendationMean"))
    if rec is not None and 1 <= rec <= 5:
        subs.append(0.5 if rec <= 2 else -0.5 if rec >= 3 else 0.0)
        parts.append(f"评级均值 {rec:.2f}（1强买–5卖）")
    if not subs:
        return factor("analyst", name, reason="无目标价/评级", source=src)
    return factor("analyst", name, value=round(sum(subs) / len(subs), 3), value_text="；".join(parts),
                  asof=fetched, source=src, signal=sum(subs) / len(subs))


def us_factors_from_info(info: dict | None, symbol: str, fetched: str | None, price: Any = None) -> list[dict]:
    info = info or {}
    return [
        us_valuation_factor(info, symbol, fetched),
        us_quality_factor(info, symbol, fetched),
        us_growth_factor(info, symbol, fetched),
        us_analyst_factor(info, symbol, fetched, price),
    ]


def earnings_window(earnings: dict | None, option: dict | None) -> dict | None:
    """Flag when the next earnings date falls on/before the option expiry (or default window)."""
    if not earnings or earnings.get("days") is None:
        return None
    days = _num(earnings.get("days"))
    if days is None:
        return None
    dte = _num((option or {}).get("dte"))
    expiry = (option or {}).get("expiry")
    window = dte if dte is not None else float(EARNINGS_DEFAULT_WINDOW_DAYS)
    in_window = days <= window
    basis = f"期权到期 {expiry}（{int(window)}天）" if dte is not None else f"默认{EARNINGS_DEFAULT_WINDOW_DAYS}天窗口（无期权合约）"
    text = (f"财报 {earnings.get('date')}（{int(days)}天后）落在{basis}之内，事件跳空风险"
            if in_window else f"财报 {earnings.get('date')}（{int(days)}天后）在{basis}之后")
    return {"in_window": bool(in_window), "date": earnings.get("date"), "days": int(days),
            "expiry": expiry, "window_days": int(window), "text": text}


# ---------------------------------------------------------------- US network provider

# Ticker.info 里真正被打分用到的键。Yahoo 在拿不到 crumb 时（quoteSummary / v7 quote
# 返回 401 "Invalid Crumb"），yfinance 默认吞掉异常，info 只剩无 crumb 也能取到的
# {"trailingPegRatio": …}——字典非空但打分键全无。这种情况必须算作「info为空」而非成功。
US_INFO_NEEDED_KEYS: tuple[str, ...] = (
    "forwardPE", "trailingPE", "profitMargins", "returnOnEquity", "freeCashflow", "debtToEquity",
    "revenueGrowth", "earningsGrowth", "numberOfAnalystOpinions", "targetMeanPrice", "recommendationMean",
)
US_INFO_NEG_TTL = 15 * 60            # info 为空/失败：只缓存 15 分钟，之后重试
US_SNAPSHOT_MAX_AGE_HOURS = 48.0     # 快照基本面最多复用 48 小时
US_SNAPSHOT_REMOTE_TTL = 30 * 60
US_SNAPSHOT_REMOTE_URLS: tuple[str, ...] = (
    "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/short_put.json",
    "https://raw.githubusercontent.com/quanliulq-pixel/futures-dashboard/main/docs/api/short_call.json",
)
US_FACTOR_KEYS: tuple[str, ...] = tuple(US_FACTOR_WEIGHTS)


def _src_us_statements(symbol: str) -> dict[str, str]:
    return {
        "function": "yfinance Ticker.get_valuation_measures/ttm_income_stmt/quarterly_income_stmt/"
                    "quarterly_balance_sheet/ttm_cashflow（fundamentals-timeseries）",
        "url": f"https://finance.yahoo.com/quote/{symbol}/financials",
        "label": "Yahoo Finance 财报/估值时间序列（按报表计算）",
    }


def _src_us_analyst(symbol: str) -> dict[str, str]:
    return {
        "function": "yfinance Ticker.analyst_price_targets/recommendations_summary",
        "url": f"https://finance.yahoo.com/quote/{symbol}/analysis",
        "label": "Yahoo Finance 分析师预期",
    }


def info_needed_status(info: Any) -> tuple[str, list[str], list[str]]:
    """('ok'|'empty', present, missing) by the keys the US factors actually need."""
    if not isinstance(info, dict):
        return "empty", [], list(US_INFO_NEEDED_KEYS)
    present = [k for k in US_INFO_NEEDED_KEYS if _num(info.get(k)) is not None]
    missing = [k for k in US_INFO_NEEDED_KEYS if k not in present]
    return ("ok" if present else "empty"), present, missing


# —— 报表回退（纯函数，可离线测试） ——

def _df_row(df, names: Iterable[str]):
    """First matching row of a statement DataFrame as a Series (columns = period dates, newest first)."""
    try:
        if df is None or getattr(df, "empty", True):
            return None
        for n in names:
            if n in df.index:
                s = df.loc[n]
                if hasattr(s, "ndim") and s.ndim > 1:
                    s = s.iloc[0]
                return s
    except Exception:  # noqa: BLE001
        return None
    return None


def _latest(series) -> tuple[float | None, Any]:
    if series is None:
        return None, None
    try:
        pairs = sorted(((c, _num(v)) for c, v in series.items()), key=lambda cv: str(cv[0]), reverse=True)
    except Exception:  # noqa: BLE001
        return None, None
    for c, v in pairs:
        if v is not None:
            return v, c
    return None, None


def _to_date(c: Any) -> date | None:
    try:
        if hasattr(c, "to_pydatetime"):
            return c.to_pydatetime().date()
        if isinstance(c, datetime):
            return c.date()
        if isinstance(c, date):
            return c
        return datetime.strptime(str(c)[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def _yoy(series) -> tuple[float | None, str | None]:
    """Latest quarter vs the quarter ~1 year earlier (330–400 days). Prior must be > 0."""
    if series is None:
        return None, None
    pts = []
    try:
        for c, v in series.items():
            d, x = _to_date(c), _num(v)
            if d is not None and x is not None:
                pts.append((d, x))
    except Exception:  # noqa: BLE001
        return None, None
    if len(pts) < 2:
        return None, None
    pts.sort(reverse=True)
    d0, v0 = pts[0]
    for d, v in pts[1:]:
        if 330 <= (d0 - d).days <= 400:
            if v <= 0:
                return None, None
            return v0 / v - 1.0, d0.isoformat()
    return None, None


def _epoch(d: date | None) -> int | None:
    if d is None:
        return None
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def info_from_statements(
    *,
    valuation=None,
    ttm_income=None,
    q_income=None,
    q_balance=None,
    ttm_cashflow=None,
    price: Any = None,
    price_targets: dict | None = None,
    rec_summary=None,
) -> tuple[dict, dict[str, str]]:
    """Rebuild the info keys the factors use from Yahoo timeseries statements (no crumb needed).

    Returns (info_like, how) — how[key] says which input produced it. Only computes from
    real reported numbers; anything not derivable is simply absent (never imputed).
    """
    out: dict[str, Any] = {}
    how: dict[str, str] = {}
    # PE：仅当 TTM 稀释EPS>0 时给出。滚动PE = 现价/TTM EPS（自算）；远期PE 取 Yahoo 估值表 Current 列。
    # 估值表 Current 列对亏损公司不可靠（实测 RIOT：TTM EPS −4.12 时表中 Trailing P/E 仍为 12.9），
    # 因此 EPS≤0 或 EPS 未知时不采用估值表 PE；亏损由净利率<0 体现。
    eps, _ = _latest(_df_row(ttm_income, ("Diluted EPS", "Basic EPS")))
    px = _num(price)
    if eps is not None and eps > 0:
        if px is not None and px > 0:
            out["trailingPE"] = px / eps
            how["trailingPE"] = "现价/TTM稀释EPS"
        cur = None
        try:
            if valuation is not None and not valuation.empty and "Current" in valuation.columns:
                cur = valuation["Current"]
        except Exception:  # noqa: BLE001
            cur = None
        fpe = _num(cur.get("Forward P/E")) if cur is not None and hasattr(cur, "get") else None
        if fpe is not None and fpe > 0:
            out["forwardPE"] = fpe
            how["forwardPE"] = "Yahoo估值表远期PE"
    ni, _ = _latest(_df_row(ttm_income, ("Net Income", "Net Income Common Stockholders")))
    rev, _ = _latest(_df_row(ttm_income, ("Total Revenue", "Operating Revenue")))
    if ni is not None and rev is not None and rev > 0:
        out["profitMargins"] = ni / rev
        how["profitMargins"] = "TTM净利润/TTM营收"
    eq, eq_col = _latest(_df_row(q_balance, ("Stockholders Equity", "Common Stock Equity")))
    if ni is not None and eq is not None and eq > 0:
        out["returnOnEquity"] = ni / eq
        how["returnOnEquity"] = "TTM净利润/最新股东权益"
    debt, _ = _latest(_df_row(q_balance, ("Total Debt",)))
    if debt is not None and eq is not None and eq > 0 and debt >= 0:
        out["debtToEquity"] = debt / eq * 100.0  # 与 info 同口径（百分比）
        how["debtToEquity"] = "总负债/股东权益"
    fcf, _ = _latest(_df_row(ttm_cashflow, ("Free Cash Flow",)))
    if fcf is not None:
        out["freeCashflow"] = fcf
        how["freeCashflow"] = "TTM自由现金流"
    rg, rq = _yoy(_df_row(q_income, ("Total Revenue", "Operating Revenue")))
    if rg is not None:
        out["revenueGrowth"] = rg
        how["revenueGrowth"] = "单季营收同比"
    eg, eq_q = _yoy(_df_row(q_income, ("Diluted EPS",)))
    if eg is None:
        eg, eq_q = _yoy(_df_row(q_income, ("Net Income",)))
    if eg is not None:
        out["earningsGrowth"] = eg
        how["earningsGrowth"] = "单季EPS/净利润同比"
    mrq = rq or eq_q or (_to_date(eq_col).isoformat() if _to_date(eq_col) else None)
    if mrq:
        out["mostRecentQuarter"] = _epoch(_to_date(mrq))
    # 分析师：analyst_price_targets / recommendations_summary（需 crumb；失败即缺失）
    pt = price_targets if isinstance(price_targets, dict) else {}
    tgt = _num(pt.get("mean"))
    if tgt is not None and tgt > 0:
        out["targetMeanPrice"] = tgt
        how["targetMeanPrice"] = "analyst_price_targets"
        cp = _num(pt.get("current"))
        if cp is not None and cp > 0:
            out["currentPrice"] = cp
    try:
        if rec_summary is not None and not rec_summary.empty:
            row = rec_summary.iloc[0]
            if "period" in rec_summary.columns:
                cur_rows = rec_summary[rec_summary["period"] == "0m"]
                if not cur_rows.empty:
                    row = cur_rows.iloc[0]
            counts = [(1, "strongBuy"), (2, "buy"), (3, "hold"), (4, "sell"), (5, "strongSell")]
            vals = [(w, _num(row.get(k))) for w, k in counts]
            if all(v is not None for _, v in vals):
                n = sum(v for _, v in vals)
                if n > 0:
                    out["numberOfAnalystOpinions"] = n
                    out["recommendationMean"] = sum(w * v for w, v in vals) / n
                    how["numberOfAnalystOpinions"] = how["recommendationMean"] = "recommendations_summary"
    except Exception:  # noqa: BLE001
        pass
    return out, how


_SANITIZE_RE = None


def _sanitize(msg: str) -> str:
    import re

    global _SANITIZE_RE
    if _SANITIZE_RE is None:
        _SANITIZE_RE = re.compile(r"(crumb|cookie|token)[=:]\s*[^\s&,'\"}]+", re.I)
    return _SANITIZE_RE.sub(r"\1=<redacted>", str(msg))[:160]


class _YfLogCapture(logging.Handler):
    """Collect yfinance WARNING+ messages (e.g. 'HTTP Error 401 … Invalid Crumb') for the probe."""

    def __init__(self, limit: int = 6):
        super().__init__(level=logging.WARNING)
        self.limit = limit
        self.msgs: list[str] = []

    def emit(self, record):  # noqa: D401
        try:
            m = _sanitize(record.getMessage())
        except Exception:  # noqa: BLE001
            return
        if m not in self.msgs and len(self.msgs) < self.limit:
            self.msgs.append(m)


def _yf_version() -> str | None:
    try:
        import yfinance as yf

        return getattr(yf, "__version__", None)
    except Exception:  # noqa: BLE001
        return None


class UsInfoProvider:
    """Per-symbol US fundamentals record.

    1) yfinance Ticker.info（quoteSummary + v7 quote，需 crumb）——缺少打分键即算「info为空」；
    2) info 为空/失败时回退到 fundamentals-timeseries 报表接口（无需 crumb）+ 分析师接口；
    3) info 正常的记录缓存 24h；info 为空/失败的记录只缓存 15 分钟（回退结果同样 15 分钟后重取）。
    Record: {info, info_status ok|empty|failed, fetched, err, present, missing, fallback, fallback_how,
             fallback_err, cached}.
    """

    FAIL_TTL = US_INFO_NEG_TTL

    def __init__(self, ttl: float = US_FUND_CACHE_TTL, neg_ttl: float = US_INFO_NEG_TTL,
                 ticker_factory: Callable[[str], Any] | None = None, clock: Callable[[], float] | None = None):
        self.ttl = ttl
        self.neg_ttl = neg_ttl
        self._ticker_factory = ticker_factory
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self.last_probe: dict | None = None

    def _ticker(self, sym: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(sym)
        import yfinance as yf

        return yf.Ticker(sym)

    def _fallback(self, t: Any, sym: str) -> tuple[dict, dict, dict]:
        """Statements/analyst fallback; returns (info_like, how, endpoint_status)."""
        ep: dict[str, str] = {}

        def grab(name: str, fn: Callable[[], Any]):
            try:
                v = fn()
            except Exception as exc:  # noqa: BLE001
                ep[name] = f"error {type(exc).__name__}"
                return None
            empty = v is None or (hasattr(v, "empty") and v.empty) or (isinstance(v, dict) and not v)
            ep[name] = "empty" if empty else "ok"
            return None if empty else v

        valuation = grab("valuation_measures", lambda: t.get_valuation_measures()
                         if hasattr(t, "get_valuation_measures") else None)
        ttm_income = grab("ttm_income_stmt", lambda: t.ttm_income_stmt)
        q_income = grab("quarterly_income_stmt", lambda: t.quarterly_income_stmt)
        q_balance = grab("quarterly_balance_sheet", lambda: t.quarterly_balance_sheet)
        ttm_cf = grab("ttm_cashflow", lambda: t.ttm_cashflow)
        price = None
        try:
            price = _num(t.fast_info["lastPrice"])
            ep["fast_info"] = "ok" if price is not None else "empty"
        except Exception as exc:  # noqa: BLE001
            ep["fast_info"] = f"error {type(exc).__name__}"
        targets = grab("analyst_price_targets", lambda: t.analyst_price_targets)
        recs = grab("recommendations_summary", lambda: t.recommendations_summary)
        info_like, how = info_from_statements(
            valuation=valuation, ttm_income=ttm_income, q_income=q_income, q_balance=q_balance,
            ttm_cashflow=ttm_cf, price=price, price_targets=targets, rec_summary=recs)
        if price is not None:
            info_like.setdefault("currentPrice", price)
        return info_like, how, ep

    def _get_one(self, sym: str, *, probe: bool = False) -> dict:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(sym)
        if hit:
            ts, rec = hit
            ttl = self.ttl if rec.get("info_status") == "ok" else self.neg_ttl
            if now - ts < ttl:
                out = dict(rec)
                out["cached"] = True
                return out
        cap = _YfLogCapture() if probe else None
        yf_logger = logging.getLogger("yfinance")
        if cap is not None:
            yf_logger.addHandler(cap)
        rec: dict[str, Any] = {"info": None, "info_status": "failed", "err": None, "present": [],
                               "missing": list(US_INFO_NEEDED_KEYS), "fallback": None, "fallback_how": {},
                               "fallback_err": None, "endpoints": {}, "info_key_count": 0, "cached": False}
        try:
            t = None
            try:
                t = self._ticker(sym)
                raw = t.info or {}
                if not isinstance(raw, dict):
                    raw = {}
                status, present, missing = info_needed_status(raw)
                rec.update(info_key_count=len(raw), present=present, missing=missing)
                if status == "ok":
                    rec.update(info=raw, info_status="ok")
                else:
                    rec.update(info_status="empty", err="info缺少打分字段",
                               quote_type=str(raw.get("quoteType") or "") or None)
                    logger.warning("US fundamentals %s: Ticker.info lacks scoring keys (got %d keys: %s); "
                                   "missing %s — likely Yahoo crumb/quoteSummary blocked; using statement fallback",
                                   sym, len(raw), ",".join(sorted(raw)[:6]) or "-", ",".join(missing))
            except Exception as exc:  # noqa: BLE001
                rec.update(info_status="failed", err=f"{type(exc).__name__}: {_sanitize(exc)[:80]}")
                logger.warning("US fundamentals %s: Ticker.info failed: %s", sym, rec["err"])
            if rec["info_status"] != "ok" and t is not None:
                try:
                    fb, how, ep = self._fallback(t, sym)
                    rec["endpoints"] = ep
                    if fb and any(k in fb for k in US_INFO_NEEDED_KEYS):
                        rec["fallback"] = fb
                        rec["fallback_how"] = how
                    else:
                        rec["fallback_err"] = "报表接口也无可用字段"
                except Exception as exc:  # noqa: BLE001
                    rec["fallback_err"] = f"{type(exc).__name__}: {_sanitize(exc)[:80]}"
        finally:
            if cap is not None:
                yf_logger.removeHandler(cap)
                rec["yf_log"] = cap.msgs
        rec["fetched"] = today_cst().isoformat()
        with self._lock:
            self._cache[sym] = (self._clock(), rec)
        return dict(rec)

    def batch(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not symbols:
            return out
        pool = ThreadPoolExecutor(max_workers=US_FUND_WORKERS)
        futs = {pool.submit(self._get_one, s, probe=(i == 0)): s for i, s in enumerate(symbols)}
        done, not_done = wait(list(futs), timeout=US_FUND_BATCH_TIMEOUT_SEC)
        for f in done:
            try:
                out[futs[f]] = f.result()
            except Exception as exc:  # noqa: BLE001
                out[futs[f]] = {"info": None, "info_status": "failed", "err": type(exc).__name__}
        for f in not_done:
            out[futs[f]] = {"info": None, "info_status": "failed", "err": "超时"}
        pool.shutdown(wait=False, cancel_futures=True)
        return out


DEFAULT_US_PROVIDER = UsInfoProvider()


def _normalize_record(rec: Any) -> dict:
    """Accept the legacy (info, fetched, err) tuple or the provider dict record."""
    if isinstance(rec, dict) and "info_status" in rec:
        return rec
    info, fetched, err = (tuple(rec) + (None, None, None))[:3] if isinstance(rec, (tuple, list)) else (None, None, "未取数")
    if info is None:
        return {"info": None, "info_status": "failed", "fetched": fetched, "err": err, "fallback": None}
    status, present, missing = info_needed_status(info)
    # legacy providers: an ETF quoteType dict is still a valid (ETF) answer
    is_etf = str(info.get("quoteType") or "").upper() == "ETF"
    if status == "ok" or is_etf:
        return {"info": info, "info_status": "ok", "fetched": fetched, "err": err, "present": present,
                "missing": missing, "fallback": None}
    return {"info": None, "info_status": "empty", "fetched": fetched, "err": err or "info缺少打分字段",
            "present": present, "missing": missing, "fallback": None, "info_key_count": len(info)}


# —— 快照复用（GitHub Actions 生成的 docs/api 快照里的 fund_factors） ——

def _parse_snapshot_time(v: Any) -> datetime | None:
    """docs/api 的 updated_at 为无时区 ISO（导出环境 TZ=Asia/Shanghai），按 UTC+8 解释。"""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt


class SnapshotFundIndex:
    """symbol → {factor_key: factor, ...} from snapshot payloads, newest ok factor wins."""

    def __init__(self, payloads: Iterable[tuple[str, dict]] = (), *, max_age_hours: float = US_SNAPSHOT_MAX_AGE_HOURS,
                 now: Callable[[], datetime] | None = None):
        self.max_age_hours = max_age_hours
        self._now = now or (lambda: datetime.now(CST))
        self._by_sym: dict[str, dict[str, tuple[datetime, str, dict]]] = {}
        self.sources: list[dict] = []
        for label, payload in payloads:
            self.add(label, payload)

    def add(self, label: str, payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        snap_at = _parse_snapshot_time(payload.get("updated_at"))
        self.sources.append({"source": label, "updated_at": payload.get("updated_at")})
        if snap_at is None:
            return
        rows = []
        for bucket in ("all", "picks", "watchlist"):
            rows.extend(payload.get(bucket) or [])
        for th in payload.get("themes") or []:
            rows.extend((th or {}).get("rows") or [])
        for r in rows:
            if not isinstance(r, dict) or not r.get("symbol") or (r.get("kind") or "stock") == "etf":
                continue
            for f in r.get("fund_factors") or []:
                if not isinstance(f, dict) or f.get("status") != "ok" or f.get("key") not in US_FACTOR_KEYS:
                    continue
                if _num(f.get("signal")) is None:
                    continue
                # 已经是复用来的因子：沿用其原始快照时间，避免一份旧值被反复「续命」
                orig = _parse_snapshot_time(f.get("snapshot_at")) if f.get("from_snapshot") else snap_at
                if orig is None:
                    continue
                cur = self._by_sym.setdefault(r["symbol"], {}).get(f["key"])
                if cur is None or orig > cur[0]:
                    self._by_sym[r["symbol"]][f["key"]] = (orig, label, f)

    def lookup(self, symbol: str, key: str) -> dict | None:
        hit = (self._by_sym.get(symbol) or {}).get(key)
        if not hit:
            return None
        orig, label, f = hit
        age_h = (self._now() - orig).total_seconds() / 3600.0
        if age_h < 0 or age_h > self.max_age_hours:
            return None
        g = dict(f)
        stamp = orig.astimezone(CST).strftime("%Y-%m-%d %H:%M")
        base_text = f.get("value_text") or ""
        if f.get("from_snapshot"):
            base_text = base_text.split("（来自快照")[0]
        base_asof = f.get("snapshot_orig_asof") if f.get("from_snapshot") else f.get("asof")
        g.update({
            "from_snapshot": True,
            "data_route": "snapshot",
            "snapshot_at": orig.isoformat(timespec="seconds"),
            "snapshot_age_hours": round(age_h, 1),
            "snapshot_source": label,
            "snapshot_orig_asof": base_asof,
            "value_text": f"{base_text}（来自快照 {stamp} 北京时间）",
            "asof": f"{base_asof or '日期未知'}·来自快照",
            "source": f"{f.get('source') or 'Yahoo Finance'} · 来自快照",
            "reason": None,
            "weight": None,
            "contribution": None,
        })
        return g

    def __len__(self) -> int:
        return len(self._by_sym)


_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_REMOTE_CACHE: tuple[float, list[tuple[str, dict]]] | None = None


def _remote_snapshots(timeout: float = 8.0) -> list[tuple[str, dict]]:
    """Latest docs/api snapshots from GitHub raw (public). Disabled with FUND_SNAPSHOT_REMOTE=0."""
    import os

    global _SNAPSHOT_REMOTE_CACHE
    if os.environ.get("FUND_SNAPSHOT_REMOTE", "1") == "0":
        return []
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT_REMOTE_CACHE and time.time() - _SNAPSHOT_REMOTE_CACHE[0] < US_SNAPSHOT_REMOTE_TTL:
            return _SNAPSHOT_REMOTE_CACHE[1]
    out: list[tuple[str, dict]] = []
    try:
        import requests

        for url in US_SNAPSHOT_REMOTE_URLS:
            try:
                r = requests.get(url, timeout=timeout)
                if r.ok:
                    out.append((url, r.json()))
            except Exception as exc:  # noqa: BLE001
                logger.debug("remote snapshot %s: %s", url, exc)
    except Exception:  # noqa: BLE001
        pass
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_REMOTE_CACHE = (time.time(), out)
    return out


def default_snapshot_index(*, remote: bool = True) -> SnapshotFundIndex:
    """Bundled docs/api/short_put.json + short_call.json (+ newest copies on GitHub main)."""
    import json
    from pathlib import Path

    payloads: list[tuple[str, dict]] = []
    root = Path(__file__).resolve().parent
    for rel in ("docs/api/short_put.json", "docs/api/short_call.json"):
        p = root / rel
        try:
            if p.exists():
                payloads.append((rel, json.loads(p.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            logger.debug("snapshot load failed %s", rel, exc_info=True)
    if remote:
        payloads.extend(_remote_snapshots())
    return SnapshotFundIndex(payloads)


_FACTOR_INFO_KEYS: dict[str, tuple[str, ...]] = {
    "valuation": ("forwardPE", "trailingPE"),
    "quality": ("profitMargins", "returnOnEquity", "freeCashflow", "debtToEquity"),
    "growth": ("revenueGrowth", "earningsGrowth"),
    "analyst": ("numberOfAnalystOpinions", "targetMeanPrice", "recommendationMean"),
}


def _label_route(facs: list[dict], route: str, symbol: str, how: dict[str, str] | None = None) -> None:
    for f in facs:
        if f.get("status") != "ok":
            continue
        f["data_route"] = route
        if route == "statements":
            src = _src_us_analyst(symbol) if f["key"] == "analyst" else _src_us_statements(symbol)
            f["source"], f["source_function"], f["source_url"] = src["label"], src["function"], src["url"]
            if how:
                keys = _FACTOR_INFO_KEYS.get(f["key"], ())
                if f["key"] == "valuation":
                    keys = ("forwardPE",) if "远期PE" in (f.get("value_text") or "") else ("trailingPE",)
                hows = list(dict.fromkeys(how[k] for k in keys if k in how))
                f["value_text"] = f"{f.get('value_text') or ''}（{'、'.join(hows) if hows else '按财报'}）"


def _probe_from(sym: str | None, rec: dict | None) -> dict | None:
    if not sym or not rec:
        return None
    return {
        "symbol": sym,
        "yfinance": _yf_version(),
        "info_status": rec.get("info_status"),
        "info_key_count": rec.get("info_key_count"),
        "needed_present": rec.get("present") or [],
        "needed_missing": rec.get("missing") or [],
        "fallback_keys": sorted((rec.get("fallback") or {}).keys()),
        "endpoints": rec.get("endpoints") or {},
        "yf_log": rec.get("yf_log") or [],
        "cached": bool(rec.get("cached")),
        "error": rec.get("err"),
    }


def apply_us_fundamentals(
    rows: list[dict],
    side: str,
    *,
    option_key: str,
    top_n: int = US_FUND_TOP_N,
    provider: Any = None,
    snapshot: "SnapshotFundIndex | Callable[[], SnapshotFundIndex] | None" = None,
) -> dict[str, Any]:
    """Score fundamentals for the top-N technical rows that have an option idea.

    Per factor the data route is: Ticker.info → 报表回退 → 快照（≤48h，标「来自快照」）→ 无数据.
    Mutates rows: adds tech_score/fund_score/combined_score/fund_factors/earnings_window.
    Returns meta for the payload (honest counts, per-factor routes and a 1-symbol probe).
    """
    t0 = time.time()
    side = "call" if side == "call" else "put"
    provider = provider or DEFAULT_US_PROVIDER
    by_tech = sorted(rows, key=lambda r: (_num(r.get("score")) if _num(r.get("score")) is not None else -1e9), reverse=True)
    # 名额只给有期权合约的非ETF标的（ETF 不取个股基本面）。
    stocks = [r for r in by_tech if r.get(option_key) and (r.get("kind") or "stock") != "etf"][:top_n]
    targets = stocks
    target_ids = {id(r) for r in targets}
    raw = provider.batch([r["symbol"] for r in stocks]) if stocks else {}
    fetched = {s: _normalize_record(v) for s, v in raw.items()}
    snap_state: dict[str, Any] = {"index": snapshot if isinstance(snapshot, SnapshotFundIndex) else None,
                                  "factory": snapshot if (snapshot is not None and not isinstance(snapshot, SnapshotFundIndex)) else None,
                                  "error": None}

    def _snap() -> SnapshotFundIndex | None:
        # 懒加载：只有确实缺因子时才读快照（含 GitHub raw）
        if snap_state["index"] is None and snap_state["factory"] is not None:
            fac, snap_state["factory"] = snap_state["factory"], None
            try:
                snap_state["index"] = fac()
            except Exception as exc:  # noqa: BLE001
                snap_state["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        return snap_state["index"]

    n_ok = n_empty = n_fail = 0
    n_fb_any = n_snap_any = n_scored = 0
    routes = {k: {"info": 0, "statements": 0, "snapshot": 0, "missing": 0} for k in US_FACTOR_KEYS}
    for r in rows:
        opt = r.get(option_key)
        ew = earnings_window(r.get("earnings"), opt)
        r["earnings_window"] = ew
        if (r.get("kind") or "stock") == "etf":
            # ETF 无个股基本面：不取数、不扣分，综合分=技术分（与是否进入前N无关）。
            fund = score_factors([], US_FACTOR_WEIGHTS, side)
            fund["fund_status_cn"] = "基本面无数据（ETF 不适用个股基本面）"
            apply_fund_to_row(r, fund)
            continue
        if id(r) not in target_ids:
            apply_fund_to_row(r, None, not_computed_reason=f"基本面未计算（仅对技术分前{top_n}名且有期权合约的标的计算）")
            continue
        sym = r["symbol"]
        rec = fetched.get(sym) or {"info": None, "info_status": "failed", "err": "未取数"}
        info = rec.get("info")
        qt = str((info or {}).get("quoteType") or rec.get("quote_type") or "").upper()
        if qt == "ETF":
            fund = score_factors([], US_FACTOR_WEIGHTS, side)
            fund["fund_status_cn"] = "基本面无数据（ETF 不适用个股基本面）"
            apply_fund_to_row(r, fund)
            continue
        fdate = rec.get("fetched")
        st = rec.get("info_status")
        if st == "ok":
            n_ok += 1
            facs = us_factors_from_info(info, sym, fdate, r.get("price"))
            _label_route(facs, "info", sym)
        else:
            if st == "empty":
                n_empty += 1
            else:
                n_fail += 1
            fb = rec.get("fallback")
            if fb:
                facs = us_factors_from_info(fb, sym, fdate, r.get("price"))
                _label_route(facs, "statements", sym, rec.get("fallback_how"))
            else:
                facs = us_factors_from_info({}, sym, None)
            why = "Ticker.info 缺少打分字段" if st == "empty" else f"取数失败：{rec.get('err')}"
            for f in facs:
                if f.get("status") != "ok":
                    f["reason"] = f"{why}；报表回退无此项" if fb else (
                        f"{why}" + (f"；报表回退失败：{rec.get('fallback_err')}" if rec.get("fallback_err") else ""))
        # 快照复用（仅对仍缺失的因子）
        if snapshot is not None and any(f.get("status") != "ok" for f in facs) and _snap() is not None:
            idx = _snap()
            for i, f in enumerate(facs):
                if f.get("status") == "ok":
                    continue
                g = idx.lookup(sym, f["key"])
                if g is not None:
                    facs[i] = g
                else:
                    f["reason"] = (f.get("reason") or "") + f"；无{US_SNAPSHOT_MAX_AGE_HOURS:.0f}小时内快照"
        used_fb = used_snap = False
        for f in facs:
            route = f.get("data_route") if f.get("status") == "ok" else "missing"
            routes[f["key"]][route or "missing"] += 1
            used_fb |= route == "statements"
            used_snap |= route == "snapshot"
        n_fb_any += used_fb
        n_snap_any += used_snap
        pen = EARNINGS_IN_WINDOW_PENALTY if (ew and ew["in_window"]) else 0.0
        fund = score_factors(facs, US_FACTOR_WEIGHTS, side, penalty=pen,
                             penalty_reason=(ew or {}).get("text") if pen else None)
        if fund.get("fund_score") is not None:
            n_scored += 1
        apply_fund_to_row(r, fund)
    probe_sym = stocks[0]["symbol"] if stocks else None
    degraded = (n_empty + n_fail) > 0
    return {
        "top_n": top_n,
        "targets": len(targets),
        "stocks_fetched": len(stocks),
        "info_ok": n_ok,
        "info_empty": n_empty,
        "info_failed": n_fail,
        "fallback_used": n_fb_any,
        "snapshot_used": n_snap_any,
        "fund_scored": n_scored,
        "factor_routes": routes,
        "data_status": "degraded" if degraded else "live",
        "snapshot_max_age_hours": US_SNAPSHOT_MAX_AGE_HOURS if snapshot is not None else None,
        "snapshot_loaded": snap_state["index"] is not None,
        "snapshot_sources": (snap_state["index"].sources if snap_state["index"] is not None else []),
        "snapshot_error": snap_state["error"],
        "probe": _probe_from(probe_sym, fetched.get(probe_sym) if probe_sym else None),
        "seconds": round(time.time() - t0, 1),
        "cache_ttl_hours": US_FUND_CACHE_TTL / 3600,
        "neg_cache_minutes": US_INFO_NEG_TTL / 60,
    }


def method_text(market: str, side: str) -> str:
    side_cn = "卖Call" if side == "call" else "卖Put"
    pol = "基本面偏空/高估得分高" if side == "call" else "基本面偏多/有支撑得分高"
    if market == "cn":
        w = CN_FACTOR_WEIGHTS
        body = (f"库存/仓单4周变化（去化偏多）{w['inventory']:.0%}、基差（现货升水偏多）{w['basis']:.0%}、"
                f"期限结构（近强远弱偏多）{w['term']:.0%}、季节性（近10年同期30天）{w['seasonality']:.0%}")
    else:
        w = US_FACTOR_WEIGHTS
        body = (f"估值{w['valuation']:.0%}、盈利质量{w['quality']:.0%}、成长{w['growth']:.0%}、分析师{w['analyst']:.0%}；"
                f"财报落在期权到期前扣{EARNINGS_IN_WINDOW_PENALTY:.0f}分；仅对技术分前{US_FUND_TOP_N}名计算，缓存24小时；"
                f"Yahoo 关键统计取不到时改用财报时间序列计算，仍缺失则复用{US_SNAPSHOT_MAX_AGE_HOURS:.0f}小时内快照（标「来自快照」）")
    return (f"{side_cn}基本面分0–100（50=中性，{pol}）：{body}。缺失因子标「无数据」并在可用因子间重新归一；"
            f"全部缺失时综合分=技术分。综合分={TECH_WEIGHT:.0%}×技术分(截断0–100)+{FUND_WEIGHT:.0%}×基本面分；"
            f"入选门槛仍按原技术分，排序按综合分。")
