# -*- coding: utf-8 -*-
"""沪金期权卖Put研究（/api/gold/put）。模型定价回测，非真实期权成交；研究参考，不能下单。

标的与黄金页量化纸面跟踪一致：上期所黄金期货期权（au），用沪金主连 AU0 日线做趋势/波动，
实盘候选用新浪 OptionService 黄金期权链（au_o / shfe）+ 对应月份期货行情。不用东财 push2。
历史期权成交与官方 IV 无可靠免费序列时，回测用 Black-76 + HV 近似，页面明确标注。
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import gold_quant as gq

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
SEED_PATHS = (ROOT / "data" / "gold_put.json", ROOT / "docs" / "api" / "gold_put.json")
RUNTIME_PATH = ROOT / "data" / "gold_put_latest.json"
PRICES_PATH = ROOT / "data" / "gold_put_prices.json"
CHAIN_PATH = ROOT / "data" / "gold_put_chain_snapshot.json"
LISTED_ON = date(2019, 12, 20)  # 上期所黄金期权上市日
IS_START, IS_END = "2020-01-02", "2023-12-29"
OOS_START = "2024-01-02"
CLOSE_CUTOFF = dtime(15, 5)
MONTHS_URL = "https://stock.finance.sina.com.cn/futures/view/optionsDP.php/au_o/shfe"
CHAIN_URL = "https://stock.finance.sina.com.cn/futures/api/openapi.php/OptionService.getOptionData"
HQ_URL = "https://hq.sinajs.cn/list="
DISCLAIMER = "研究参考，不能下单，不构成投资建议"
PARAMS = dict(
    dte=30, min_dte=21, max_dte=45, otm_move=1.25,
    iv_hv_assumption=1.20, iv_hv_live_min=1.10,
    hv_cap=0.40, vol_jump_limit=1.50,
    take_profit=0.50, stop_premium_multiple=2.50, exit_dte=7,
    collateral_total=0.20, planned_risk=0.005,
    rate=0.03, half_spread_premium=0.05, fee_per_gram=0.002,
    multiplier=1000, fut_margin_rate=0.10,
    min_oi=20, max_spread=0.25, delta_lo=0.20, delta_hi=0.30,
)

_cache = None
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(CST)


def _r(x, n=6):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def _http(url, *, params=None, headers=None, timeout=20):
    import gold
    return gold._http("GET", url, params=params, headers=headers, timeout=timeout)


def _referer(url=MONTHS_URL):
    return {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------- pricing (pure)
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_fut_put(fut, strike, years, vol, rate=0.03):
    """Black-76 put on a futures (equivalent to BS with q=r). Returns (price, delta dP/dF)."""
    if fut <= 0 or strike <= 0 or not all(math.isfinite(x) for x in (fut, strike, years, vol, rate)):
        raise ValueError("invalid Black-76 inputs")
    disc = math.exp(-rate * max(years, 0))
    if years <= 0:
        return max(strike - fut, 0.0) * disc, (-disc if fut < strike else 0.0)
    if vol <= 0:
        intrinsic = disc * (strike - fut)
        return max(intrinsic, 0.0), (-disc if intrinsic > 0 else 0.0)
    d1 = (math.log(fut / strike) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    price = disc * (strike * norm_cdf(-d2) - fut * norm_cdf(-d1))
    delta = -disc * norm_cdf(-d1)
    return max(price, 0.0), delta


def implied_put_vol(fut, strike, years, mid, rate=0.03, tol=0.02):
    """Invert Black-76 from mid price. Returns None if mid is unusable (never invent a vol)."""
    if mid is None or mid <= 0 or years <= 0 or fut <= 0 or strike <= 0:
        return None
    disc = math.exp(-rate * years)
    floor = max(disc * (strike - fut), 0.0)
    if mid + 1e-12 < floor:
        return None
    lo, hi = 1e-4, 3.0
    plo, _ = bs_fut_put(fut, strike, years, lo, rate)
    phi, _ = bs_fut_put(fut, strike, years, hi, rate)
    if mid < plo - 1e-9 or mid > phi + 1e-6:
        return None
    for _ in range(60):
        mid_v = (lo + hi) / 2
        px, _ = bs_fut_put(fut, strike, years, mid_v, rate)
        if px > mid:
            hi = mid_v
        else:
            lo = mid_v
    vol = (lo + hi) / 2
    px, _ = bs_fut_put(fut, strike, years, vol, rate)
    if px <= 0 or abs(px - mid) / mid > tol:
        return None
    return vol


def strike_model(spot, hv):
    """Synthetic continuous strike, not an actual listed option contract."""
    return spot * math.exp(-PARAMS["otm_move"] * hv * math.sqrt(PARAMS["dte"] / 365))


def unit_fee(premium):
    return PARAMS["half_spread_premium"] * premium + PARAMS["fee_per_gram"]


def seller_margin(spot, strike, premium, fut_rate=None):
    """上期所期权卖方保证金估算（官方公式，期货保证金率默认 10% 为估算）。"""
    rate = PARAMS["fut_margin_rate"] if fut_rate is None else fut_rate
    m = PARAMS["multiplier"]
    fut = max(spot, 0) * m * rate
    otm = max(spot - strike, 0) * m
    prem = max(premium, 0) * m
    return max(prem + fut - 0.5 * otm, prem + 0.5 * fut)


def collateral_weight(spot, strike, premium):
    margin = seller_margin(spot, strike, premium)
    if margin <= 0:
        return 0.0
    risk = ((PARAMS["stop_premium_multiple"] - 1) * premium + 2 * unit_fee(premium)) * PARAMS["multiplier"] / margin
    return min(PARAMS["collateral_total"], PARAMS["planned_risk"] / risk) if risk > 0 else 0.0


def option_expiry(month_code: str, calendar=None) -> date | None:
    """期权最后交易日：标的期货交割月前一个月的倒数第 5 个交易日（未逐日核验假期调整）。"""
    m = re.fullmatch(r"au(\d{2})(\d{2})", month_code, re.I)
    if not m:
        return None
    year, month = 2000 + int(m.group(1)), int(m.group(2))
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    start, end = date(year, month, 1), date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)
    sessions = []
    if calendar is not None and len(calendar):
        idx = pd.DatetimeIndex(calendar)
        sessions = [d.date() if hasattr(d, "date") else d for d in idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]]
    if len(sessions) < 5:
        sessions = [d.date() for d in pd.bdate_range(start, end)]
    if len(sessions) < 5:
        return None
    return sessions[-5]


# ---------------------------------------------------------------- features / rules (pure)
def features(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy().sort_index()
    c = p["close"]
    p["ma20"] = c.rolling(20).mean()
    p["ma60"] = c.rolling(60).mean()
    p["mom"] = c.pct_change(10, fill_method=None)
    log = np.log(c / c.shift())
    p["hv"] = log.rolling(20).std() * math.sqrt(252)
    p["hv60"] = log.rolling(60).std() * math.sqrt(252)
    p["T"] = gq.trend_score(c)
    return p


def eligible(row) -> tuple[bool, str]:
    keys = ("close", "ma20", "ma60", "mom", "hv", "hv60", "T")
    if not all(math.isfinite(float(row[k])) for k in keys):
        return False, "指标无数据"
    if not (row["close"] > row["ma20"] > row["ma60"] and row["mom"] > 0):
        return False, "AU0 非一致上升趋势（收盘>MA20>MA60 且10日动量为正），不卖黄金Put"
    if not (row["T"] > 0):
        return False, "黄金量化趋势分 T 未转正（21/63/126/252日动量与EMA50/200），不卖Put"
    if not (0 < row["hv"] <= PARAMS["hv_cap"]):
        return False, "AU0 HV20 超限或无效"
    if row["hv"] > PARAMS["vol_jump_limit"] * row["hv60"]:
        return False, "HV20 超过 HV60 的 1.5 倍，波动突升"
    return True, "AU0 上升趋势、量化趋势分 T>0、HV 及波动突升过滤通过"


def exit_reason(mark, credit, spot, strike, dte, regime_ok):
    if dte <= 0:
        return "expiry"
    if dte <= PARAMS["exit_dte"]:
        return "expiry_guard"
    if spot <= strike:
        return "strike_breach"
    if mark >= PARAMS["stop_premium_multiple"] * credit:
        return "premium_stop"
    if mark <= PARAMS["take_profit"] * credit:
        return "take_profit"
    if not regime_ok:
        return "regime_exit"
    return None


def metrics(values, trades):
    v = np.array(values, dtype=float)
    if len(v) < 2:
        raise ValueError("回测无数据")
    r = v / np.r_[1.0, v[:-1]] - 1
    dd = v / np.maximum.accumulate(np.r_[1.0, v])[1:] - 1
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return dict(
        total_return=float(v[-1] - 1),
        cagr=float(v[-1] ** (252 / len(v)) - 1),
        max_drawdown=float(dd.min()),
        sharpe=float(r.mean() / sd * np.sqrt(252)) if sd else None,
        win_rate=sum(t["return"] > 0 for t in trades) / len(trades) if trades else None,
        trade_count=len(trades),
        avg_holding_days=float(np.mean([t["days"] for t in trades])) if trades else None,
    )


def backtest(p: pd.DataFrame, start: str, end: str) -> dict:
    dates = p.index[(p.index >= pd.Timestamp(start)) & (p.index <= pd.Timestamp(end))]
    if len(dates) < 2:
        raise ValueError("回测无数据")
    cash, pos, trades, curve, assignments = 1.0, None, [], [], 0
    for dt in dates:
        i = p.index.get_loc(dt)
        if i < 252:
            raise ValueError("预热不足")
        prev, row = p.iloc[i - 1], p.loc[dt]
        exited = False
        if pos is not None:
            spot = float(row["open"])
            dte = (pos["expiry"] - dt).days
            vol = float(prev["hv"]) * PARAMS["iv_hv_assumption"]
            mark, _ = bs_fut_put(spot, pos["strike"], max(dte, 0) / 365, vol, PARAMS["rate"])
            okay, _ = eligible(prev)
            why = exit_reason(mark, pos["credit"], spot, pos["strike"], dte, okay)
            if why:
                debit = pos["units"] * (mark + unit_fee(mark))
                cash -= debit
                pnl = pos["net_credit"] - debit
                assigned = why == "expiry" and spot < pos["strike"]
                assignments += int(assigned)
                trades.append(dict(
                    symbol="AU0", entry=str(pos["entry"].date()), exit=str(dt.date()),
                    reason=why, days=i - pos["entry_i"], pnl=pnl,
                    collateral_return=pnl / pos["collateral"], assigned=bool(assigned),
                ))
                pos, exited = None, True
        if pos is None and not exited:
            okay, _ = eligible(prev)
            if okay:
                spot = float(row["open"])
                hv = float(prev["hv"])
                strike = strike_model(float(prev["close"]), hv)
                if spot > strike:
                    expiry = dt + pd.Timedelta(days=PARAMS["dte"])
                    credit, _ = bs_fut_put(spot, strike, PARAMS["dte"] / 365, hv * PARAMS["iv_hv_assumption"], PARAMS["rate"])
                    if credit > unit_fee(credit):
                        margin_per = seller_margin(spot, strike, credit)
                        equity = cash  # flat before new entry
                        collateral = min(equity * collateral_weight(spot, strike, credit), equity)
                        if collateral > 0 and margin_per > 0:
                            units = collateral / margin_per * PARAMS["multiplier"]
                            # units here is grams-equivalent of option (multiplier grams per lot)
                            # PnL is in premium * units (yuan/gram * grams = yuan), scale so collateral is cash units.
                            # Simpler: treat collateral as cash reserved; units = collateral / (margin_per)
                            # and PnL in account units = units * (credit - mark - fees) * multiplier / 1
                            # If start cash=1, collateral is a fraction of equity.
                            # profit in yuan = (grams) * (credit-fee); grams = (collateral_yuan) / margin_per * multiplier
                            # We work entirely in account fraction: 1 unit of cash.
                            # net_credit (account) = collateral / margin_per * (credit - fee) * multiplier
                            lots_cash = collateral / margin_per  # number of lots if collateral were yuan; here collateral is account units
                            net_credit = lots_cash * (credit - unit_fee(credit)) * PARAMS["multiplier"]
                            cash += net_credit
                            pos = dict(
                                strike=strike, credit=credit, lots_cash=lots_cash,
                                units=lots_cash * PARAMS["multiplier"],
                                collateral=collateral, net_credit=net_credit,
                                entry=dt, entry_i=i, expiry=expiry,
                            )
        liability = 0.0
        if pos is not None:
            spot = float(row["close"])
            dte = max((pos["expiry"] - dt).days, 0)
            mark, _ = bs_fut_put(spot, pos["strike"], dte / 365, float(prev["hv"]) * PARAMS["iv_hv_assumption"], PARAMS["rate"])
            if dt == dates[-1]:
                debit = pos["units"] * (mark + unit_fee(mark))
                cash -= debit
                pnl = pos["net_credit"] - debit
                trades.append(dict(
                    symbol="AU0", entry=str(pos["entry"].date()), exit=str(dt.date()),
                    reason="period_end", days=i - pos["entry_i"] + 1, pnl=pnl,
                    collateral_return=pnl / pos["collateral"], assigned=False,
                ))
                pos = None
            else:
                liability = pos["units"] * mark
        curve.append(dict(date=str(dt.date()), strategy=float(cash - liability)))
    # AU0 buy-and-hold, same open-to-close, 单边 4bp like C1 (entry+exit 8bp total, 4bp/side)
    units_bh = 1 / (float(p.loc[dates[0], "open"]) * (1 + 4e-4))
    vals = [units_bh * float(p.loc[d, "close"]) for d in dates]
    vals[-1] *= (1 - 4e-4)
    for point, v in zip(curve, vals):
        point["AU0"] = float(v)
    converted = [{"return": t["pnl"], "days": t["days"]} for t in trades]
    mt = metrics([r["strategy"] for r in curve], converted)
    mt["worst_trade"] = min((t["collateral_return"] for t in trades), default=None)
    mt["assignment_count"] = assignments
    bh = metrics(vals, [{"return": vals[-1] - 1, "days": len(dates)}])
    bh["worst_trade"] = None
    bh["assignment_count"] = None
    return dict(
        start=str(dates[0].date()), end=str(dates[-1].date()),
        metrics={"strategy": mt, "AU0": bh},
        curve=curve, trades=trades,
    )


# ---------------------------------------------------------------- live chain
def _f(x):
    try:
        if x is None or x == "" or x == "-":
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _i(x):
    v = _f(x)
    return int(v) if v is not None else None


def parse_put_row(row, strike_hint=None):
    """Sina down row: bid_vol, bid, last, ask, ask_vol, oi, change, code (no strike)."""
    if not row or len(row) < 8:
        return None
    code = str(row[-1])
    strike = strike_hint
    m = re.search(r"P(\d+(?:\.\d+)?)$", code, re.I)
    if m:
        strike = _f(m.group(1)) or strike
    bid, last, ask = _f(row[1]), _f(row[2]), _f(row[3])
    mid = None
    if bid is not None and ask is not None and ask > 0:
        mid = (bid + ask) / 2
    elif last is not None:
        mid = last
    return dict(
        contract=code, strike=strike, bid=bid, ask=ask, last=last, mid=mid,
        bid_vol=_i(row[0]), ask_vol=_i(row[4]), open_interest=_i(row[5]), change=_f(row[6]),
    )


def parse_call_row(row):
    if not row or len(row) < 9:
        return None
    return _f(row[7]), str(row[8])


def list_months(html: str) -> list[str]:
    found = sorted(set(re.findall(r"\bau\d{4}\b", html or "", re.I)), key=lambda s: s.lower())
    return found


def parse_nf_last(text: str) -> dict:
    """nf_AUYYMM：字段 8 最新、10 昨结、17 日期（与 gold.py 沪金解析一致）。"""
    out = {}
    for m in re.finditer(r'hq_str_nf_(AU\d{4})="([^"]*)"', text or "", re.I):
        parts = m.group(2).split(",")
        if len(parts) < 18:
            continue
        last, prev, asof = _f(parts[8]), _f(parts[10]), parts[17]
        if last and last > 0:
            out[m.group(1).upper()] = dict(last=last, prev=prev, asof=asof)
    return out


def select_contract(puts, spot, hv, expiry, today):
    """Use real bid/ask only. Never replace a missing chain with model quotes."""
    if not expiry:
        return None
    dte = (expiry - today).days if isinstance(expiry, date) else (date.fromisoformat(str(expiry)[:10]) - today).days
    if not PARAMS["min_dte"] <= dte <= PARAMS["max_dte"]:
        return None
    years = dte / 365
    candidates = []
    for r in puts:
        try:
            k, bid, ask, oi, mid = r.get("strike"), r.get("bid"), r.get("ask"), r.get("open_interest"), r.get("mid")
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (k, bid, ask, mid)):
                continue
            if oi is None or oi < PARAMS["min_oi"]:
                continue
            if not (0 < k < spot and 0 < bid <= ask and mid > 0):
                continue
            if (ask - bid) / mid > PARAMS["max_spread"]:
                continue
            iv = implied_put_vol(spot, k, years, mid, PARAMS["rate"])
            if iv is None or iv <= 0:
                continue
            _, delta = bs_fut_put(spot, k, years, iv, PARAMS["rate"])
            if not PARAMS["delta_lo"] <= abs(delta) <= PARAMS["delta_hi"]:
                continue
            if iv < hv * PARAMS["iv_hv_live_min"]:
                continue
            margin = seller_margin(spot, k, bid)
            premium_yield = (bid * PARAMS["multiplier"] / margin) if margin else None
            ann = premium_yield * 365 / dte if premium_yield is not None else None
            candidates.append(dict(
                contract=r.get("contract") or "",
                strike=k, expiry=str(expiry), dte=dte,
                bid=bid, ask=ask, mid=mid, iv=iv, delta=delta,
                open_interest=int(oi),
                premium_yield=premium_yield, annualized_yield=ann,
                breakeven=k - bid,
                margin_per_lot=margin,
                otm_pct=(spot - k) / spot,
                last_trade_at=None,
                collateral_weight=collateral_weight(spot, k, bid),
                iv_source="由买卖中间价反推的 Black-76 近似，不是交易所官方 IV",
            ))
        except (ValueError, TypeError, KeyError):
            continue
    return min(candidates, key=lambda x: abs(abs(x["delta"]) - 0.25)) if candidates else None


def fetch_live(panel, now=None, http_get=None, calendar=None):
    now = now or _now()
    today = now.date()
    f = features(panel)
    last = f.iloc[-1]
    okay, reason = eligible(last)
    item = dict(
        symbol="au", name="上期所黄金期权", underlying="AU0 / 对应月份沪金期货",
        action="不卖", regime_ok=okay, reason=reason, quote=None,
        chain_status="无数据", chain_sample=[],
        spot=float(last["close"]), hv=float(last["hv"]), trend_T=float(last["T"]),
        asof=str(last.name.date()) if hasattr(last.name, "date") else str(last.name)[:10],
        event_calendar="无数据：需人工核验 FOMC / 非农 / 央行与上期所假期调整",
        updated_at=now.isoformat(timespec="seconds"),
    )
    get = http_get or (lambda url, **kw: _http(url, **kw))
    archives = []
    try:
        html = get(MONTHS_URL, headers=_referer()).text
        months = list_months(html)
        if not months:
            item["chain_status"] = "无数据：新浪黄金期权页未解析到合约月份"
            return [item]
        cal = calendar if calendar is not None else panel.index
        # pick month whose estimated expiry is closest to target DTE
        dated = []
        for code in months:
            exp = option_expiry(code, cal)
            if exp is None:
                continue
            dte = (exp - today).days
            dated.append((code, exp, dte))
        window = [(c, e, d) for c, e, d in dated if PARAMS["min_dte"] <= d <= PARAMS["max_dte"]]
        if not window:
            item["chain_status"] = "无数据：21–45 天窗口内无黄金期权合约月份（到期日按规则估算）"
            item["available_months"] = [dict(month=c, expiry=str(e), dte=d) for c, e, d in dated]
            return [item]
        month, expiry, dte = min(window, key=lambda x: abs(x[2] - PARAMS["dte"]))
        raw = get(
            CHAIN_URL,
            params={"type": "futures", "product": "au_o", "exchange": "shfe", "pinzhong": month},
            headers=_referer(MONTHS_URL),
        ).json()
        data = ((raw or {}).get("result") or {}).get("data") or {}
        up, down = data.get("up") or [], data.get("down") or []
        # align strikes from calls
        strikes = []
        for row in up:
            parsed = parse_call_row(row)
            if parsed and parsed[0]:
                strikes.append(parsed[0])
        puts = []
        for i, row in enumerate(down):
            hint = strikes[i] if i < len(strikes) else None
            rec = parse_put_row(row, hint)
            if rec:
                puts.append(rec)
        archives.append(dict(
            month=month, expiry=str(expiry), retrieved_at=now.isoformat(timespec="seconds"),
            rows=puts,
        ))
        # underlying futures last
        hq_code = "AU" + month[2:]
        hq_text = get(HQ_URL + "nf_" + hq_code, headers=_referer()).content.decode("gb18030", "replace")
        hq = parse_nf_last(hq_text)
        fut = hq.get(hq_code)
        spot = float(fut["last"]) if fut and fut.get("last") else item["spot"]
        item["futures_month"] = month
        item["futures_spot"] = fut
        item["spot_used"] = spot
        item["spot_source"] = f"新浪 nf_{hq_code} 最新价" if fut else "AU0 最近完整日线收盘（对应月份期货行情无数据）"
        item["expiry_estimated"] = str(expiry)
        item["dte_estimated"] = dte
        item["chain_rows"] = len(puts)
        item["selected_expiry"] = str(expiry)
        samples = []
        years = dte / 365
        for r in puts:
            try:
                k, bid, ask = r.get("strike"), r.get("bid"), r.get("ask")
                if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (k, bid, ask)):
                    continue
                if not (0 < k < spot and 0 < bid <= ask):
                    continue
                mid = (bid + ask) / 2
                iv = implied_put_vol(spot, k, years, mid, PARAMS["rate"])
                if iv is None:
                    continue
                _, delta = bs_fut_put(spot, k, years, iv, PARAMS["rate"])
                samples.append(dict(strike=k, bid=bid, ask=ask, iv=iv, delta=delta, expiry=str(expiry), contract=r.get("contract")))
            except (ValueError, TypeError, KeyError):
                pass
        item["chain_sample"] = sorted(samples, key=lambda r: abs(abs(r["delta"]) - 0.25))[:2]
        item["chain_status"] = "已获取新浪黄金期权链（延时转播）；不等于实时可成交报价。到期日按规则估算"
        item["quote"] = select_contract(puts, spot, item["hv"], expiry, today)
        if item["quote"] is None:
            item["chain_status"] = "所选 30 天附近到期链无满足全部 delta / IV·HV / 价差 / 持仓量规则的合约，候选无数据"
        item["reason"] = reason + "；宏观事件与假期日历未核验，研究候选不作为下单建议"
    except Exception as exc:  # noqa: BLE001
        item["chain_status"] = "无数据：新浪黄金期权链获取失败（" + type(exc).__name__ + "）"
    if archives:
        CHAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHAIN_PATH.write_text(json.dumps(archives, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8")
    return [item]


# ---------------------------------------------------------------- panel / payload
def _load_prices() -> pd.DataFrame | None:
    if not PRICES_PATH.is_file():
        return None
    try:
        df = pd.read_json(PRICES_PATH, orient="table")
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df.sort_index()
    except (OSError, ValueError):
        return None


def _save_prices(df: pd.DataFrame) -> None:
    PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.write_text(df.to_json(orient="table", date_format="iso"), encoding="utf-8")


def fetch_panel(fetch_au0=None, now=None) -> pd.DataFrame:
    fn = fetch_au0 or gq.fetch_au0
    raw = fn()
    if raw is None or raw.empty:
        raise ValueError("新浪 AU0 日线无数据")
    df = gq.completed_bars(raw, now=now or _now())
    prev = _load_prices()
    if prev is not None and not prev.empty:
        df = pd.concat([prev, df]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
    need = ("open", "high", "low", "close")
    if any(c not in df.columns for c in need):
        raise ValueError("AU0 日线缺 OHLC")
    df = df[list(need)].dropna()
    df = df[(df[list(need)] > 0).all(axis=1)]
    if len(df) < 400:
        raise ValueError("AU0 日线不足")
    quality = [
        "标的日线：新浪沪金主连 AU0（未复权主力连续，换月跳空近似持有成本）；不是期权对应月份期货的连续合约。",
        f"上期所黄金期权上市日 {LISTED_ON.isoformat()}，回测不早于 2020-01-02。",
    ]
    df.attrs["data_quality"] = quality
    return df


def _round_metrics(m: dict) -> dict:
    out = {}
    for k, v in m.items():
        if isinstance(v, float):
            out[k] = None if not math.isfinite(v) else round(v, 6)
        else:
            out[k] = v
    return out


def _slim_curve(curve: list[dict]) -> list[dict]:
    # keep daily actual points (no interpolation), rounded
    out = []
    for p in curve:
        rec = {"date": p["date"], "strategy": _r(p["strategy"], 6), "AU0": _r(p["AU0"], 6)}
        out.append(rec)
    return out



def _round_live(live):
    out = []
    keys = ("spot", "hv", "trend_T", "spot_used")
    qkeys = ("strike", "bid", "ask", "mid", "iv", "delta", "premium_yield", "annualized_yield",
             "breakeven", "margin_per_lot", "otm_pct", "collateral_weight")
    for item in live or []:
        rec = dict(item)
        for k in keys:
            if k in rec:
                rec[k] = _r(rec[k], 4 if k != "margin_per_lot" else 2)
        if rec.get("quote"):
            q = dict(rec["quote"])
            for k in qkeys:
                if k in q and q[k] is not None:
                    q[k] = _r(q[k], 0 if k == "margin_per_lot" else 4)
            rec["quote"] = q
        samples = []
        for s in rec.get("chain_sample") or []:
            ss = dict(s)
            for k in ("strike", "bid", "ask", "iv", "delta"):
                if k in ss and ss[k] is not None:
                    ss[k] = _r(ss[k], 4)
            samples.append(ss)
        rec["chain_sample"] = samples
        out.append(rec)
    return out


def build_payload(panel, live=None, now=None) -> dict:
    now = now or _now()
    p = features(panel)
    end = str(p.index[-1].date())
    in_s = backtest(p, IS_START, IS_END)
    oos = backtest(p, OOS_START, end)
    for block in (in_s, oos):
        block["metrics"] = {k: _round_metrics(v) for k, v in block["metrics"].items()}
        block["curve"] = _slim_curve(block["curve"])
        # drop bulky trade list from public payload; keep counts in metrics
        block.pop("trades", None)
    return dict(
        schema_version=1, status="ok",
        updated_at=now.isoformat(timespec="seconds"), timezone="Asia/Shanghai",
        asof_session=str(p.index[-1].date()),
        params=PARAMS,
        listed_on=str(LISTED_ON),
        label="模型定价回测，非真实期权成交",
        disclaimer=DISCLAIMER,
        live=_round_live(live or []),
        data_quality=list(panel.attrs.get("data_quality") or []),
        in_sample=in_s,
        out_of_sample=oos,
        sources=[
            {"label": "新浪期货日线 AU0（沪金主连）", "url": "https://finance.sina.com.cn/futures/quotes/AU0.shtml"},
            {"label": "新浪 OptionService.getOptionData 黄金期权链 au_o / shfe", "url": MONTHS_URL},
            {"label": "新浪 nf_AUyyMM 对应月份沪金期货行情", "url": "https://finance.sina.com.cn/futures/quotes/AU0.shtml"},
            {"label": "上期所黄金期权合约规则（欧式、最后交易日、卖方保证金公式）",
             "url": "https://www.shfe.com.cn/content/2019_gold_option/index.html"},
        ],
        limitations=[
            "无免费可验证的历史期权成交序列。本页全部期权回测为 Black-76（期货期权，q=r）欧式模型，IV=前日 HV20×1.20 是固定假设，不是真实历史 IV 或已观察到的波动率溢价。",
            "连续合成行权价=前日收盘×exp(-1.25×HV20×sqrt(30/365))，30 个日历日合成到期；使用沪金主连 AU0（未复权、含换月跳空），不是历史挂牌行权价、对应月份期货或 1 手整数合约。",
            "黄金期权 2019-12-20 才上市；2020 年前的模型路径未纳入。回测样本内 2020–2023、2024 起事后留出，固定规则未调参，非真实前瞻验证。",
            f"固定利率 3%；买卖各扣 5% 期权模型价作为半价差，另扣约 {PARAMS['fee_per_gram']} 元/克（约 2 元/手）手续费估算。保证金按上期所卖方公式、期货保证金率按 10% 估算，与会员实际收取可能不同。",
            "入场条件结合黄金页已有信号：AU0 收盘>MA20>MA60、10 日动量为正，且量化趋势分 T>0（与 C1/稳健版同一套 5 票趋势）。HV20≤40% 且不超过 1.5×HV60。决策只用前日收盘，次日开盘成交/检查退出。",
            "止盈 50% 权利金；期权模型价达初始 2.5 倍、标的跌到行权价、趋势失效或余期≤7 天退出。不是盘中可保证执行的止损。",
            "到期价内次数仅表示模型到期按内在价值结算。虽为欧式，仍可能行权得到期货持仓；未模拟交割、强平和会员追加保证金。",
            "入场时保证金占用≤账户 20%；0.5% 计划止损预算不是最大亏损承诺，真实最大亏损可接近全部保证金并因行权转为期货。未使用现金担保美股模式（100×行权价）。",
            "真实链候选只展示新浪转播的买/卖价；IV 与 Δ 由中间价反推 Black-76，是近似，不是交易所官方隐含波动率。到期日按「交割月前一月倒数第 5 个交易日」估算，未逐日核验假期调整。事件日历无数据时默认不卖。",
            "100% 持有 AU0 与 ≤20% 保证金卖 Put 风险敞口不同。研究参考，不能下单。",
        ],
    )


def _dump_runtime(value: dict) -> None:
    RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(RUNTIME_PATH)


def refresh(*, use_remote: bool = True, fetch_au0=None, fetch_live_fn=None, now=None) -> dict:
    global _cache
    with _lock:
        now = now or _now()
        if use_remote:
            p = fetch_panel(fetch_au0=fetch_au0, now=now)
            _save_prices(p)
            live = (fetch_live_fn or fetch_live)(p, now=now)
        else:
            p = _load_prices()
            if p is None or p.empty:
                raise ValueError("无本地 AU0 归档，无法离线复算")
            p.attrs["data_quality"] = p.attrs.get("data_quality") or [
                "离线复算：使用已归档的 AU0 日线，未联网。",
            ]
            live = None
            # reuse last seed live block if present
            for path in SEED_PATHS:
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    live = old.get("live") or []
                    break
                except (OSError, ValueError):
                    pass
        value = build_payload(p, live, now)
        _dump_runtime(value)
        _cache = value
        return value


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None



def stored_payload():
    """Committed/runtime payload without request-time fields (stale, refresh_status)."""
    for value in [_cache, _load_json(RUNTIME_PATH)] + [_load_json(p) for p in SEED_PATHS]:
        if value and value.get("status") == "ok":
            value = copy.deepcopy(value)
            value.pop("stale", None)
            value.pop("refresh_status", None)
            value.pop("note", None)
            return value
    return None


def snapshot() -> dict:
    values = [_cache, _load_json(RUNTIME_PATH)] + [_load_json(p) for p in SEED_PATHS]
    now = _now()
    for value in values:
        if value and value.get("status") == "ok":
            value = copy.deepcopy(value)
            try:
                age = (now - datetime.fromisoformat(value["updated_at"])).total_seconds()
            except (TypeError, ValueError):
                age = 10**9
            value["stale"] = age > 24 * 3600
            if value["stale"]:
                for item in value.get("live") or []:
                    item.update(action="不卖", quote=None, chain_sample=[], chain_status="无数据：期权链快照超过24小时")
            return value
    return dict(schema_version=1, status="no_data", error="无数据", label="模型定价回测，非真实期权成交",
                disclaimer=DISCLAIMER, live=[])


def needs_refresh(value: dict | None) -> bool:
    if not value or value.get("status") != "ok" or not value.get("updated_at"):
        return True
    try:
        return _now() - datetime.fromisoformat(value["updated_at"]) > timedelta(hours=6)
    except ValueError:
        return True
