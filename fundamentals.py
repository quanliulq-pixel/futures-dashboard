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

class UsInfoProvider:
    """yfinance Ticker.info with 24h in-memory cache; failures are cached briefly as None."""

    FAIL_TTL = 30 * 60

    def __init__(self, ttl: float = US_FUND_CACHE_TTL):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict | None, str | None, str | None]] = {}

    def _get_one(self, sym: str) -> tuple[dict | None, str | None, str | None]:
        now = time.time()
        with self._lock:
            hit = self._cache.get(sym)
        if hit:
            ts, info, fetched, err = hit
            if now - ts < (self.ttl if info is not None else self.FAIL_TTL):
                return info, fetched, err
        info, err = None, None
        try:
            import yfinance as yf

            raw = yf.Ticker(sym).info or {}
            if isinstance(raw, dict) and raw:
                info = raw
            else:
                err = "info为空"
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:80]}"
        fetched = today_cst().isoformat()
        with self._lock:
            self._cache[sym] = (time.time(), info, fetched, err)
        return info, fetched, err

    def batch(self, symbols: list[str]) -> dict[str, tuple[dict | None, str | None, str | None]]:
        out: dict[str, tuple[dict | None, str | None, str | None]] = {}
        if not symbols:
            return out
        pool = ThreadPoolExecutor(max_workers=US_FUND_WORKERS)
        futs = {pool.submit(self._get_one, s): s for s in symbols}
        done, not_done = wait(list(futs), timeout=US_FUND_BATCH_TIMEOUT_SEC)
        for f in done:
            try:
                out[futs[f]] = f.result()
            except Exception as exc:  # noqa: BLE001
                out[futs[f]] = (None, None, type(exc).__name__)
        for f in not_done:
            out[futs[f]] = (None, None, "超时")
        pool.shutdown(wait=False, cancel_futures=True)
        return out


DEFAULT_US_PROVIDER = UsInfoProvider()


def apply_us_fundamentals(
    rows: list[dict],
    side: str,
    *,
    option_key: str,
    top_n: int = US_FUND_TOP_N,
    provider: Any = None,
) -> dict[str, Any]:
    """Score fundamentals for the top-N technical rows that have an option idea.

    Mutates rows: adds tech_score/fund_score/combined_score/fund_factors/earnings_window.
    Returns meta for the payload.
    """
    t0 = time.time()
    side = "call" if side == "call" else "put"
    provider = provider or DEFAULT_US_PROVIDER
    by_tech = sorted(rows, key=lambda r: (_num(r.get("score")) if _num(r.get("score")) is not None else -1e9), reverse=True)
    # 名额只给有期权合约的非ETF标的（ETF 不取个股基本面）。
    stocks = [r for r in by_tech if r.get(option_key) and (r.get("kind") or "stock") != "etf"][:top_n]
    targets = stocks
    target_ids = {id(r) for r in targets}
    fetched = provider.batch([r["symbol"] for r in stocks]) if stocks else {}
    n_ok = n_fail = 0
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
        info, fdate, err = fetched.get(r["symbol"], (None, None, "未取数"))
        if info is not None and str(info.get("quoteType") or "").upper() == "ETF":
            fund = score_factors([], US_FACTOR_WEIGHTS, side)
            fund["fund_status_cn"] = "基本面无数据（ETF 不适用个股基本面）"
            apply_fund_to_row(r, fund)
            continue
        if info is None:
            n_fail += 1
            facs = us_factors_from_info({}, r["symbol"], None)
            for f in facs:
                f["reason"] = f"取数失败：{err}" if err else f.get("reason")
        else:
            n_ok += 1
            facs = us_factors_from_info(info, r["symbol"], fdate, r.get("price"))
        pen = EARNINGS_IN_WINDOW_PENALTY if (ew and ew["in_window"]) else 0.0
        fund = score_factors(facs, US_FACTOR_WEIGHTS, side, penalty=pen,
                             penalty_reason=(ew or {}).get("text") if pen else None)
        apply_fund_to_row(r, fund)
    return {
        "top_n": top_n,
        "targets": len(targets),
        "stocks_fetched": len(stocks),
        "info_ok": n_ok,
        "info_failed": n_fail,
        "seconds": round(time.time() - t0, 1),
        "cache_ttl_hours": US_FUND_CACHE_TTL / 3600,
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
                f"财报落在期权到期前扣{EARNINGS_IN_WINDOW_PENALTY:.0f}分；仅对技术分前{US_FUND_TOP_N}名计算，缓存24小时")
    return (f"{side_cn}基本面分0–100（50=中性，{pol}）：{body}。缺失因子标「无数据」并在可用因子间重新归一；"
            f"全部缺失时综合分=技术分。综合分={TECH_WEIGHT:.0%}×技术分(截断0–100)+{FUND_WEIGHT:.0%}×基本面分；"
            f"入选门槛仍按原技术分，排序按综合分。")
