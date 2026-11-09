# -*- coding: utf-8 -*-
"""A股指数基本面：政策 / 估值 / 流动性 / 宏观 四组因子（数据驱动、可核验）。

原则（与 fundamentals.py 的卖Put/卖Call 基本面一致）
- 每个因子 signal ∈ [-1, 1]，正 = 对指数偏支撑/偏多（利于卖Put），负 = 偏空/高估（利于卖Call）；
  每个因子带数值、数据日期 asof、来源函数与 URL；没有数据或数据过期 ⇒「无数据」，绝不插补。
- 基本面分（多空刻度）= 50 + 50·Σ w·signal，两级权重：
  组权重 GROUP_WEIGHTS（政策最大）；组内权重 FACTOR_WEIGHTS。组内缺失只在组内重新归一，
  整组缺失时其余组按组权重重新归一，所以只要政策组有任一因子，政策组仍占 35%（相对可用组）。
- 政策因子只用公开、可回溯的数值：LPR、存款准备金率、银行间7天回购利率（DR007，备选 Shibor 1W）、
  10年期国债收益率。不使用新闻/主观判断/模型生成文本。
- 政策事件窗口：只收录有官方规则可核验的固定日程（LPR 每月20日，遇节假日顺延），
  落在近月期权到期前（含）时给出提醒，不改分。
- 估值因子按指数计算；政策/流动性/宏观为全市场因子（scope=market，三只指数相同）。
- 所有取数失败都不致命：因子记为无数据 + 原因；整体只在后台任务里取数并缓存。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from typing import Any, Callable

from fundamentals import CST, NO_DATA, _num, _r, clip, combine_scores, factor, signal_text

logger = logging.getLogger(__name__)

# —— 权重（具名常量） ——
# 政策 0.35：A股对货币/财政政策与监管导向高度敏感（降准降息、利率走廊变化常直接驱动指数），
# 用户要求加大政策权重；其次估值 0.25（决定安全边际）、流动性 0.25（两融/货币活化度反映增量资金），
# 宏观 0.15（PMI/价格/信贷为月频、滞后，且大部分已反映在政策与估值里）。
GROUP_WEIGHTS: dict[str, float] = {
    "policy": 0.35,
    "valuation": 0.25,
    "liquidity": 0.25,
    "macro": 0.15,
}
GROUP_ORDER = ("policy", "valuation", "liquidity", "macro")
GROUP_NAMES = {"policy": "政策", "valuation": "估值", "liquidity": "流动性", "macro": "宏观"}
GROUP_SCOPE = {"policy": "market", "valuation": "index", "liquidity": "market", "macro": "market"}

# 组内权重（每组和为 1）
FACTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "policy": {"lpr": 0.35, "rrr": 0.20, "money_rate": 0.25, "cgb10y": 0.20},
    "valuation": {"pe_pctile": 0.40, "pb_pctile": 0.20, "erp": 0.40},
    "liquidity": {"margin": 0.60, "m1m2": 0.40},
    "macro": {"pmi": 0.40, "prices": 0.25, "credit": 0.35},
}
FACTOR_GROUP = {k: g for g, ws in FACTOR_WEIGHTS.items() for k in ws}

# —— 标定 ——
LPR_LOOKBACK_DAYS = 180
LPR_FULL_SCALE_BP = 20.0          # 半年内 1年期LPR 下调 20bp → +1
RRR_LOOKBACK_DAYS = 365
RRR_FULL_SCALE_PT = 0.5           # 一年内累计降准 0.5 个百分点 → +1
MONEY_SHORT_N = 20
MONEY_LONG_N = 60
MONEY_FULL_SCALE_PCT = 0.15       # 20日均值较60日均值低 15bp → +1
CGB_LOOKBACK_N = 60
CGB_FULL_SCALE_BP = 30.0          # 10年国债 60 个交易日下行 30bp → +1
VAL_YEARS = 10
VAL_MIN_POINTS = 36               # 分位至少 36 个月度样本
VAL_PCT_FULL = 40.0               # 分位 10% → +1；90% → −1
MARGIN_LOOKBACK_N = 20
MARGIN_FULL_SCALE_PCT = 5.0       # 融资余额 20 个交易日 +5% → +1
M1M2_LOOKBACK_M = 3
M1M2_FULL_SCALE_PT = 2.0          # M1−M2 剪刀差 3 个月收窄 2pt → +1
PMI_LEVEL_FULL = 1.5
PMI_TREND_FULL = 1.0
PRICES_LOOKBACK_M = 3
PRICES_FULL_SCALE_PT = 2.0        # PPI 同比 3 个月上行 2pt → +1（再通胀）
CREDIT_FULL_SCALE = 0.30          # 近3个月新增人民币贷款同比 +30% → +1

# 数据新鲜度（超过则视为无数据，不用旧值冒充）
MAX_AGE_DAILY_DAYS = 15
MAX_AGE_MONTHLY_DAYS = 100        # 月度数据以月末为日期；发布滞后约 2–6 周

# 成本控制
FUND_CACHE_TTL = 12 * 3600
FUND_BATCH_TIMEOUT_SEC = 150.0
FUND_WORKERS = 6
EVENT_DEFAULT_WINDOW_DAYS = 30

# 期权偏向调整阈值（仅基本面层；技术立场阈值不变）
FUND_PUT_VETO = 40.0       # 技术=偏卖Put 且 基本面分 ≤40 → 改为 铁秃鹰/价差
FUND_CALL_VETO = 60.0      # 技术=偏卖Call 且 基本面分 ≥60 → 改为 铁秃鹰/价差
UPGRADE_PUT = 60.0         # 技术=铁秃鹰/价差 且 综合≥60 且 基本面≥60 → 偏卖Put
UPGRADE_CALL = 40.0        # 技术=铁秃鹰/价差 且 综合≤40 且 基本面≤40 → 偏卖Call

INDEX_LG_NAME = {"csi300": "沪深300", "sse50": "上证50", "csi1000": "中证1000"}
INDEX_LG_PAGE = {
    "csi300": "https://legulegu.com/stockdata/hs300-ttm-lyr",
    "sse50": "https://legulegu.com/stockdata/sz50-ttm-lyr",
    "csi1000": "https://legulegu.com/stockdata/zz1000-ttm-lyr",
}
INDEX_LG_PB_PAGE = {
    "csi300": "https://legulegu.com/stockdata/hs300-pb",
    "sse50": "https://legulegu.com/stockdata/sz50-pb",
    "csi1000": "https://legulegu.com/stockdata/zz1000-pb",
}

SRC = {
    "lpr": {"function": "akshare.macro_china_lpr", "url": "https://data.eastmoney.com/cjsj/globalRateLPR.html",
            "label": "东方财富 LPR（人民银行授权公布）"},
    "rrr": {"function": "akshare.macro_china_reserve_requirement_ratio", "url": "https://data.eastmoney.com/cjsj/ckzbj.html",
            "label": "东方财富 存款准备金率调整"},
    "dr007": {"function": "akshare.repo_rate_hist（FDR007=DR007）", "url": "https://www.chinamoney.com.cn/chinese/bkfrr/",
              "label": "中国货币网 银行间回购定盘利率"},
    "shibor": {"function": "akshare.rate_interbank（Shibor 1周）",
               "url": "https://data.eastmoney.com/shibor/shibor.aspx?m=sg&t=88&d=99333&cu=sgd&type=009065&p=79",
               "label": "东方财富 Shibor 1周"},
    "cgb": {"function": "akshare.bond_zh_us_rate", "url": "https://data.eastmoney.com/cjsj/zmgzsyl.html",
            "label": "东方财富 中国10年期国债收益率"},
    "margin": {"function": "akshare.stock_margin_account_info", "url": "https://data.eastmoney.com/rzrq/zhtjday.html",
               "label": "东方财富 两融账户统计（沪深合计融资余额）"},
    "money": {"function": "akshare.macro_china_money_supply", "url": "https://data.eastmoney.com/cjsj/hbgyl.html",
              "label": "东方财富 货币供应量 M1/M2"},
    "pmi": {"function": "akshare.macro_china_pmi", "url": "https://data.eastmoney.com/cjsj/pmi.html",
            "label": "东方财富 官方制造业PMI"},
    "ppi": {"function": "akshare.macro_china_ppi + macro_china_cpi", "url": "https://data.eastmoney.com/cjsj/ppi.html",
            "label": "东方财富 PPI/CPI 同比"},
    "credit": {"function": "akshare.macro_china_new_financial_credit", "url": "https://data.eastmoney.com/cjsj/xzxd.html",
               "label": "东方财富 新增人民币贷款"},
}


def _src_pe(index_id: str) -> dict:
    return {"function": f"akshare.stock_index_pe_lg('{INDEX_LG_NAME.get(index_id, index_id)}')",
            "url": INDEX_LG_PAGE.get(index_id, "https://legulegu.com/stockdata/hs300-ttm-lyr"),
            "label": "乐咕乐股 指数滚动市盈率（市值加权）"}


def _src_pb(index_id: str) -> dict:
    return {"function": f"akshare.stock_index_pb_lg('{INDEX_LG_NAME.get(index_id, index_id)}')",
            "url": INDEX_LG_PB_PAGE.get(index_id, "https://legulegu.com/stockdata/hs300-pb"),
            "label": "乐咕乐股 指数市净率（市值加权）"}


def _src_erp(index_id: str) -> dict:
    return {"function": f"akshare.stock_index_pe_lg('{INDEX_LG_NAME.get(index_id, index_id)}') + akshare.bond_zh_us_rate",
            "url": INDEX_LG_PAGE.get(index_id, "https://legulegu.com/stockdata/hs300-ttm-lyr"),
            "label": "股权风险溢价 = 1/PE(TTM) − 10年国债（乐咕乐股 + 东方财富）"}


# 政策事件：只收录有官方规则可核验的固定日程。
LPR_EVENT_SOURCES = [
    {"label": "人民银行公告〔2019〕第15号（每月20日，遇节假日顺延）",
     "url": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2025092212550514786/index.html"},
    {"label": "人民银行 LPR 公告列表",
     "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/index.html"},
]


# ---------------------------------------------------------------- helpers

def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    import re

    m = re.match(r"^(\d{4})年(\d{1,2})月(?:(\d{1,2})日|份)?", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            return date(y, mo, int(m.group(3)))
        return _month_end(y, mo)
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})(\d{2})$", s)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)))
    return None


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m // 12), (m % 12) + 1, 1)
    return nxt - timedelta(days=1)


def series_from(rows: Any, date_key: str, value_key: str) -> list[tuple[date, float]]:
    """DataFrame / list[dict] → sorted [(date, value)] with finite values only."""
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        try:
            rows = rows.to_dict("records")
        except Exception:  # noqa: BLE001
            return []
    out: dict[date, float] = {}
    for rec in rows or []:
        try:
            d = _to_date(rec.get(date_key))
            v = _num(rec.get(value_key))
        except Exception:  # noqa: BLE001
            continue
        if d is not None and v is not None:
            out[d] = v
    return sorted(out.items())


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _stale(asof: date | None, today: date, max_days: int) -> bool:
    return asof is None or (today - asof).days > max_days


def _f(key: str, name: str, src: dict, **kw) -> dict:
    rec = factor(key, name, source=src, **kw)
    rec["group"] = FACTOR_GROUP.get(key)
    rec["scope"] = GROUP_SCOPE.get(rec["group"] or "", "market")
    return rec


def _missing(key: str, name: str, src: dict, reason: str) -> dict:
    return _f(key, name, src, signal=None, reason=reason)


def _stale_missing(key: str, name: str, src: dict, asof: date | None) -> dict:
    return _missing(key, name, src, f"数据过期（最新 {_iso(asof) or '未知'}），不使用旧值")


def percentile_rank(values: list[float], x: float) -> float:
    """Share of values ≤ x, in percent (0–100)."""
    if not values:
        return float("nan")
    return 100.0 * sum(1 for v in values if v <= x) / len(values)


# ---------------------------------------------------------------- policy factors (pure)

def lpr_factor(lpr: list[tuple[date, float]], today: date, *, lookback_days: int = LPR_LOOKBACK_DAYS) -> dict:
    """1年期 LPR 近半年变动：下调 = 宽松 = 偏支撑。"""
    key, name, src = "lpr", "LPR（1年期）近半年变动", SRC["lpr"]
    if not lpr:
        return _missing(key, name, src, "无 LPR 序列")
    d_last, v_last = lpr[-1]
    if _stale(d_last, today, 45):
        return _stale_missing(key, name, src, d_last)
    start = today - timedelta(days=lookback_days)
    base = [v for d, v in lpr if d <= start]
    v0 = base[-1] if base else lpr[0][1]
    chg_bp = (v_last - v0) * 100.0
    last_move = None
    for (d1, a), (d2, b) in zip(lpr, lpr[1:]):
        if abs(b - a) > 1e-9:
            last_move = (d2, (b - a) * 100.0)
    lm = f"；最近一次调整 {last_move[0].isoformat()} {last_move[1]:+.0f}bp" if last_move else ""
    sig = clip(-chg_bp / LPR_FULL_SCALE_BP)
    return _f(key, name, src, value=_r(chg_bp, 1), asof=_iso(d_last), signal=sig,
              value_text=f"1年期LPR {v_last:.2f}%，近{lookback_days}天 {chg_bp:+.0f}bp{lm}")


def rrr_factor(rrr: list[tuple[date, float]], today: date, *, lookback_days: int = RRR_LOOKBACK_DAYS,
               checked: date | None = None) -> dict:
    """rrr: [(生效日, 大型机构调整幅度 pt)]；一年内累计降准 = 宽松。"""
    key, name, src = "rrr", "存款准备金率 近一年调整", SRC["rrr"]
    if not rrr:
        return _missing(key, name, src, "无准备金率调整记录")
    start = today - timedelta(days=lookback_days)
    recent = [(d, v) for d, v in rrr if start < d <= today]
    total = sum(v for _, v in recent)
    last_d, last_v = rrr[-1]
    sig = clip(-total / RRR_FULL_SCALE_PT)
    txt = (f"近{lookback_days}天累计 {total:+.2f}pt（{len(recent)} 次）" if recent
           else f"近{lookback_days}天无调整（最近 {last_d.isoformat()} {last_v:+.2f}pt）")
    return _f(key, name, src, value=_r(total, 2), asof=_iso(checked or today), signal=sig,
              value_text=txt + "；大型金融机构口径")


def money_rate_factor(series: list[tuple[date, float]], today: date, *, label: str = "DR007",
                      src: dict | None = None) -> dict:
    """银行间7天资金利率 20日均 vs 60日均：走低 = 宽松。"""
    key, name = "money_rate", f"资金利率（{label}）趋势"
    src = src or SRC["dr007"]
    if len(series) < MONEY_LONG_N:
        return _missing(key, name, src, f"样本不足（{len(series)}<{MONEY_LONG_N}）")
    d_last, v_last = series[-1]
    if _stale(d_last, today, MAX_AGE_DAILY_DAYS):
        return _stale_missing(key, name, src, d_last)
    vals = [v for _, v in series]
    a20 = sum(vals[-MONEY_SHORT_N:]) / MONEY_SHORT_N
    a60 = sum(vals[-MONEY_LONG_N:]) / MONEY_LONG_N
    diff = a20 - a60
    sig = clip(-diff / MONEY_FULL_SCALE_PCT)
    return _f(key, name, src, value=_r(diff * 100, 1), asof=_iso(d_last), signal=sig,
              value_text=f"{label} 最新 {v_last:.3f}%，20日均 {a20:.3f}% vs 60日均 {a60:.3f}%（{diff * 100:+.1f}bp）")


def cgb10y_factor(series: list[tuple[date, float]], today: date, *, lookback: int = CGB_LOOKBACK_N) -> dict:
    """10年期国债收益率 60 个交易日变动：下行 = 货币环境偏松。"""
    key, name, src = "cgb10y", "10年期国债收益率 趋势", SRC["cgb"]
    if len(series) <= lookback:
        return _missing(key, name, src, f"样本不足（{len(series)}≤{lookback}）")
    d_last, v_last = series[-1]
    if _stale(d_last, today, MAX_AGE_DAILY_DAYS):
        return _stale_missing(key, name, src, d_last)
    d0, v0 = series[-1 - lookback]
    chg_bp = (v_last - v0) * 100.0
    sig = clip(-chg_bp / CGB_FULL_SCALE_BP)
    return _f(key, name, src, value=_r(chg_bp, 1), asof=_iso(d_last), signal=sig,
              value_text=f"10Y {v_last:.3f}%，较 {d0.isoformat()}（{lookback}个交易日前）{chg_bp:+.1f}bp")


# ---------------------------------------------------------------- valuation factors (pure, per index)

def _hist_window(series: list[tuple[date, float]], years: int) -> list[tuple[date, float]]:
    if not series:
        return []
    cut = series[-1][0] - timedelta(days=int(365.25 * years))
    return [(d, v) for d, v in series if d >= cut]


def pctile_factor(key: str, name: str, series: list[tuple[date, float]], today: date, src: dict,
                  *, metric: str, years: int = VAL_YEARS) -> dict:
    """估值分位：分位低 = 便宜 = 偏支撑。"""
    clean = [(d, v) for d, v in series if v > 0]
    if not clean:
        return _missing(key, name, src, "无估值序列")
    d_last, v_last = clean[-1]
    if _stale(d_last, today, MAX_AGE_DAILY_DAYS):
        return _stale_missing(key, name, src, d_last)
    win = _hist_window(clean, years)
    if len(win) < VAL_MIN_POINTS:
        return _missing(key, name, src, f"历史样本不足（{len(win)}<{VAL_MIN_POINTS}）")
    pct = percentile_rank([v for _, v in win], v_last)
    sig = clip((50.0 - pct) / VAL_PCT_FULL)
    return _f(key, name, src, value=_r(pct, 1), asof=_iso(d_last), signal=sig,
              value_text=f"{metric} {v_last:.2f}，近{years}年分位 {pct:.0f}%（{win[0][0].isoformat()} 起 {len(win)} 个样本）")


def erp_factor(pe: list[tuple[date, float]], cgb: list[tuple[date, float]], today: date, src: dict,
               *, years: int = VAL_YEARS) -> dict:
    """股权风险溢价 ERP = 100/PE − 10Y；ERP 分位高 = 相对债券便宜 = 偏支撑。"""
    key, name = "erp", "股权风险溢价（ERP）分位"
    pe = [(d, v) for d, v in pe if v > 0]
    if not pe or not cgb:
        return _missing(key, name, src, "缺 PE 或 10年国债序列")
    d_last, pe_last = pe[-1]
    if _stale(d_last, today, MAX_AGE_DAILY_DAYS):
        return _stale_missing(key, name, src, d_last)
    cgb_sorted = sorted(cgb)
    cdates = [d for d, _ in cgb_sorted]
    import bisect

    def y_at(d: date) -> float | None:
        i = bisect.bisect_right(cdates, d) - 1
        if i < 0 or (d - cdates[i]).days > 10:
            return None
        return cgb_sorted[i][1]

    hist = []
    for d, v in _hist_window(pe, years):
        y = y_at(d)
        if y is not None:
            hist.append((d, 100.0 / v - y))
    if len(hist) < VAL_MIN_POINTS:
        return _missing(key, name, src, f"ERP 历史样本不足（{len(hist)}<{VAL_MIN_POINTS}）")
    y_last = y_at(d_last)
    if y_last is None:
        return _missing(key, name, src, f"{d_last.isoformat()} 附近无 10年国债收益率")
    erp = 100.0 / pe_last - y_last
    pct = percentile_rank([v for _, v in hist], erp)
    sig = clip((pct - 50.0) / VAL_PCT_FULL)
    return _f(key, name, src, value=_r(erp, 2), asof=_iso(d_last), signal=sig,
              value_text=f"ERP {erp:.2f}%（盈利收益率 {100.0 / pe_last:.2f}% − 10Y {y_last:.3f}%），近{years}年分位 {pct:.0f}%")


# ---------------------------------------------------------------- liquidity factors (pure)

def margin_factor(series: list[tuple[date, float]], today: date, *, lookback: int = MARGIN_LOOKBACK_N) -> dict:
    """沪深两融融资余额 20 个交易日变动：上升 = 杠杆资金流入。"""
    key, name, src = "margin", "两融融资余额 趋势", SRC["margin"]
    if len(series) <= lookback:
        return _missing(key, name, src, f"样本不足（{len(series)}≤{lookback}）")
    d_last, v_last = series[-1]
    if _stale(d_last, today, MAX_AGE_DAILY_DAYS):
        return _stale_missing(key, name, src, d_last)
    d0, v0 = series[-1 - lookback]
    if v0 <= 0:
        return _missing(key, name, src, "基期余额无效")
    pct = (v_last / v0 - 1.0) * 100.0
    sig = clip(pct / MARGIN_FULL_SCALE_PCT)
    return _f(key, name, src, value=_r(pct, 2), asof=_iso(d_last), signal=sig,
              value_text=f"融资余额 {v_last:,.0f} 亿元，较 {d0.isoformat()}（{lookback}个交易日前）{pct:+.2f}%")


def m1m2_factor(m1: list[tuple[date, float]], m2: list[tuple[date, float]], today: date,
                *, lookback: int = M1M2_LOOKBACK_M) -> dict:
    """M1 同比 − M2 同比（剪刀差）近 3 个月变化：收窄/转正 = 资金活化 = 偏支撑。"""
    key, name, src = "m1m2", "M1−M2 剪刀差 变化", SRC["money"]
    m2d = dict(m2)
    sc = [(d, a - m2d[d]) for d, a in m1 if d in m2d]
    if len(sc) <= lookback:
        return _missing(key, name, src, "M1/M2 样本不足")
    d_last, v_last = sc[-1]
    if _stale(d_last, today, MAX_AGE_MONTHLY_DAYS):
        return _stale_missing(key, name, src, d_last)
    d0, v0 = sc[-1 - lookback]
    chg = v_last - v0
    sig = clip(chg / M1M2_FULL_SCALE_PT)
    return _f(key, name, src, value=_r(v_last, 2), asof=_iso(d_last), signal=sig,
              value_text=f"剪刀差 {v_last:+.1f}pt（M1 {dict(m1)[d_last]:.1f}% / M2 {m2d[d_last]:.1f}%），较{lookback}个月前 {chg:+.1f}pt")


# ---------------------------------------------------------------- macro factors (pure)

def pmi_factor(pmi: list[tuple[date, float]], today: date) -> dict:
    key, name, src = "pmi", "制造业PMI", SRC["pmi"]
    if len(pmi) < 4:
        return _missing(key, name, src, "PMI 样本不足")
    d_last, v = pmi[-1]
    if _stale(d_last, today, MAX_AGE_MONTHLY_DAYS):
        return _stale_missing(key, name, src, d_last)
    prev3 = sum(x for _, x in pmi[-4:-1]) / 3.0
    sig = clip(0.6 * (v - 50.0) / PMI_LEVEL_FULL + 0.4 * (v - prev3) / PMI_TREND_FULL)
    return _f(key, name, src, value=_r(v, 1), asof=_iso(d_last), signal=sig,
              value_text=f"{d_last.strftime('%Y-%m')} PMI {v:.1f}（前3月均 {prev3:.2f}，荣枯线 50）")


def prices_factor(ppi: list[tuple[date, float]], cpi: list[tuple[date, float]], today: date,
                  *, lookback: int = PRICES_LOOKBACK_M) -> dict:
    """PPI 同比近 3 个月变化：上行 = 再通胀/盈利修复 = 偏支撑。CPI 仅展示。"""
    key, name, src = "prices", "价格（PPI 同比趋势）", SRC["ppi"]
    if len(ppi) <= lookback:
        return _missing(key, name, src, "PPI 样本不足")
    d_last, v = ppi[-1]
    if _stale(d_last, today, MAX_AGE_MONTHLY_DAYS):
        return _stale_missing(key, name, src, d_last)
    v0 = ppi[-1 - lookback][1]
    chg = v - v0
    sig = clip(chg / PRICES_FULL_SCALE_PT)
    cpi_txt = ""
    if cpi:
        cd, cv = cpi[-1]
        cpi_txt = f"；CPI 同比 {cv:+.1f}%（{cd.strftime('%Y-%m')}）"
    return _f(key, name, src, value=_r(v, 2), asof=_iso(d_last), signal=sig,
              value_text=f"{d_last.strftime('%Y-%m')} PPI 同比 {v:+.1f}%，较{lookback}个月前 {chg:+.1f}pt{cpi_txt}")


def credit_factor(loans: list[tuple[date, float]], today: date) -> dict:
    """近 3 个月新增人民币贷款合计 vs 去年同期：多增 = 信用扩张 = 偏支撑。"""
    key, name, src = "credit", "新增人民币贷款（近3月同比）", SRC["credit"]
    if len(loans) < 15:
        return _missing(key, name, src, "信贷样本不足")
    d_last, _ = loans[-1]
    if _stale(d_last, today, MAX_AGE_MONTHLY_DAYS):
        return _stale_missing(key, name, src, d_last)
    by = {(d.year, d.month): v for d, v in loans}
    cur, prev = [], []
    y, m = d_last.year, d_last.month
    for _ in range(3):
        cur.append(by.get((y, m)))
        prev.append(by.get((y - 1, m)))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    if any(x is None for x in cur + prev):
        return _missing(key, name, src, "近3月或去年同期缺数")
    s, s0 = sum(cur), sum(prev)
    if abs(s0) < 1e-9:
        return _missing(key, name, src, "去年同期为 0")
    ratio = (s - s0) / abs(s0)
    sig = clip(ratio / CREDIT_FULL_SCALE)
    return _f(key, name, src, value=_r(ratio * 100, 1), asof=_iso(d_last), signal=sig,
              value_text=f"近3月新增贷款 {s:,.0f} 亿元 vs 去年同期 {s0:,.0f} 亿元（{ratio * 100:+.1f}%）")


# ---------------------------------------------------------------- scoring

def score_index_factors(factors: list[dict]) -> dict:
    """Two-level weighting on the bullish scale (high = favors 卖Put, low = favors 卖Call)."""
    out = [dict(f) for f in factors]
    avail_groups: dict[str, list[dict]] = {}
    for f in out:
        g = f.get("group")
        if f.get("status") == "ok" and g in FACTOR_WEIGHTS and f.get("key") in FACTOR_WEIGHTS[g]:
            avail_groups.setdefault(g, []).append(f)
    gsum = sum(GROUP_WEIGHTS[g] for g in avail_groups)
    missing = [f["name"] for f in out if f.get("status") != "ok"]
    groups = []
    total = 0.0
    for g in GROUP_ORDER:
        fs = avail_groups.get(g, [])
        g_eff = (GROUP_WEIGHTS[g] / gsum) if (fs and gsum > 0) else 0.0
        inner = sum(FACTOR_WEIGHTS[g][f["key"]] for f in fs)
        g_contrib = 0.0
        for f in fs:
            w = g_eff * FACTOR_WEIGHTS[g][f["key"]] / inner
            f["weight"] = round(w, 3)
            c = 50.0 * w * float(f["signal"])
            f["contribution"] = round(c, 1)
            g_contrib += c
        total += g_contrib
        n_all = sum(1 for f in out if f.get("group") == g)
        groups.append({
            "key": g, "name": GROUP_NAMES[g], "scope": GROUP_SCOPE[g],
            "weight_nominal": GROUP_WEIGHTS[g], "weight": round(g_eff, 3),
            "available": len(fs), "total": n_all,
            "contribution": round(g_contrib, 1) if fs else None,
            "emphasis": g == "policy",
        })
    for f in out:
        if f.get("status") != "ok":
            f["weight"] = 0.0
            f["contribution"] = None
    out.sort(key=lambda f: (GROUP_ORDER.index(f["group"]) if f.get("group") in GROUP_ORDER else 9))
    if not avail_groups:
        return {"fund_score": None, "fund_status": "no_data", "fund_status_cn": "基本面无数据",
                "fund_available": [], "fund_missing": missing, "fund_factors": out, "fund_groups": groups,
                "fund_penalty": None}
    score = max(0.0, min(100.0, 50.0 + total))
    return {
        "fund_score": round(score, 1),
        "fund_status": "ok" if not missing else "partial",
        "fund_status_cn": "基本面完整" if not missing else f"部分无数据（{'、'.join(missing)}）",
        "fund_available": [f["name"] for f in out if f.get("status") == "ok"],
        "fund_missing": missing,
        "fund_factors": out,
        "fund_groups": groups,
        "fund_penalty": None,
    }


def tech_score_from_advice(score: Any) -> float | None:
    """Existing signed advice score (≈−57..+57, clamp −100..100) → 0–100 bullish scale."""
    s = _num(score)
    if s is None:
        return None
    return round(max(0.0, min(100.0, 50.0 + s / 2.0)), 1)


# ---------------------------------------------------------------- policy-event window

def lpr_dates(start: date, end: date) -> list[date]:
    """LPR 公布日：每月20日，遇周末顺延到周一（法定节假日/调休未计，以人民银行公告为准）。"""
    out = []
    y, m = start.year, start.month
    while True:
        d = date(y, m, 20)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if d > end:
            break
        if d >= start:
            out.append(d)
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def policy_events(today: date, horizon_days: int = 60) -> list[dict]:
    return [{
        "date": d.isoformat(),
        "name": "LPR 报价公布（9:00）",
        "kind": "lpr",
        "date_basis": "按规则推算：每月20日，遇节假日顺延（此处仅按周末顺延，调休/法定假日以人民银行公告为准）",
        "sources": LPR_EVENT_SOURCES,
    } for d in lpr_dates(today, today + timedelta(days=horizon_days))]


def event_window(events: list[dict] | None, today: date, expiry: date | None,
                 *, default_days: int = EVENT_DEFAULT_WINDOW_DAYS) -> dict:
    end = expiry or (today + timedelta(days=default_days))
    hits = []
    for e in events or []:
        d = _to_date(e.get("date"))
        if d is not None and today <= d <= end:
            hits.append(e)
    basis = f"近月期权到期 {end.isoformat()}" if expiry else f"未取到期日，按 {default_days} 天"
    if not hits:
        return {"in_window": False, "events": [], "window_end": end.isoformat(), "basis": basis, "text": None}
    names = "、".join(f"{e['date']} {e['name']}" for e in hits)
    return {"in_window": True, "events": hits, "window_end": end.isoformat(), "basis": basis,
            "text": f"政策事件落在{basis}之前：{names}；事件前后波动可能放大，不因此改分"}


# ---------------------------------------------------------------- option-bias adjustment

def adjust_option_bias(tech_bias: str, stance: str, fund_score: Any, combined: Any,
                       *, event_in_window: bool = False) -> tuple[str, str | None]:
    """Return (option_bias_cn, reason). Technical stance/thresholds are never changed here."""
    f = _num(fund_score)
    c = _num(combined)
    if f is None:
        return tech_bias, None
    if tech_bias == "偏卖Put" and f <= FUND_PUT_VETO:
        return "铁秃鹰/价差", f"基本面分 {f:.1f}≤{FUND_PUT_VETO:.0f}（偏空），不裸卖Put，改为价差"
    if tech_bias == "偏卖Call" and f >= FUND_CALL_VETO:
        return "铁秃鹰/价差", f"基本面分 {f:.1f}≥{FUND_CALL_VETO:.0f}（偏多），不裸卖Call，改为价差"
    if tech_bias == "铁秃鹰/价差" and stance != "观望高波" and c is not None:
        if c >= UPGRADE_PUT and f >= UPGRADE_PUT:
            if event_in_window:
                return tech_bias, "综合/基本面偏多，但政策事件窗口内不升级为单边卖Put"
            return "偏卖Put", f"综合分 {c:.1f}、基本面分 {f:.1f} 均≥{UPGRADE_PUT:.0f}，价差改为偏卖Put"
        if c <= UPGRADE_CALL and f <= UPGRADE_CALL:
            if event_in_window:
                return tech_bias, "综合/基本面偏空，但政策事件窗口内不升级为单边卖Call"
            return "偏卖Call", f"综合分 {c:.1f}、基本面分 {f:.1f} 均≤{UPGRADE_CALL:.0f}，价差改为偏卖Call"
    return tech_bias, None


def fund_bundle_age_hours(bundle: dict | None, now: datetime | None = None) -> float | None:
    if not bundle or not bundle.get("fetched_at"):
        return None
    try:
        t = datetime.fromisoformat(str(bundle["fetched_at"]))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=CST)
    now = now or datetime.now(CST)
    return (now - t).total_seconds() / 3600.0


FUND_BUNDLE_MAX_AGE_HOURS = 48.0


def apply_to_advice(advice: dict, index_id: str, bundle: dict | None, *, expiry: date | None = None,
                    today: date | None = None) -> dict:
    """Add tech/fund/combined + fund_factors/groups + event window to one advice dict (in place)."""
    today = today or datetime.now(CST).date()
    tech = tech_score_from_advice(advice.get("score"))
    advice["tech_score"] = tech
    advice["option_bias_tech_cn"] = advice.get("option_bias_cn")
    age = fund_bundle_age_hours(bundle)
    if not bundle or (age is not None and age > FUND_BUNDLE_MAX_AGE_HOURS):
        why = "基本面未计算" if not bundle else f"基本面数据已超过 {FUND_BUNDLE_MAX_AGE_HOURS:.0f} 小时，未使用"
        advice.update({"fund_score": None, "fund_status": "not_computed", "fund_status_cn": why,
                       "fund_factors": [], "fund_groups": [], "fund_available": [], "fund_missing": [],
                       "fund_penalty": None, "fund_adjust_cn": None, "fund_asof": None})
        comb = combine_scores(tech, None)
        advice["combined_score"] = comb["combined_score"]
        advice["combined_basis"] = comb["combined_basis"]
        advice["policy_event_window"] = event_window(policy_events(today), today, expiry)
        return advice
    facs = list(bundle.get("market_factors") or []) + list((bundle.get("index_factors") or {}).get(index_id) or [])
    if not (bundle.get("index_factors") or {}).get(index_id):
        for key, nm, src in (("pe_pctile", "PE(TTM) 分位", _src_pe(index_id)),
                             ("pb_pctile", "PB 分位", _src_pb(index_id)),
                             ("erp", "股权风险溢价（ERP）分位", _src_erp(index_id))):
            facs.append(_missing(key, nm, src, "该指数估值未取到"))
    sc = score_index_factors(facs)
    advice.update(sc)
    comb = combine_scores(tech, sc.get("fund_score"))
    advice["combined_score"] = comb["combined_score"]
    advice["combined_basis"] = comb["combined_basis"]
    advice["fund_asof"] = bundle.get("fetched_at")
    events = bundle.get("events") or policy_events(today)
    ew = event_window(events, today, expiry)
    advice["policy_event_window"] = ew
    new_bias, why = adjust_option_bias(advice.get("option_bias_cn") or "", advice.get("stance_cn") or "",
                                       sc.get("fund_score"), comb["combined_score"],
                                       event_in_window=bool(ew.get("in_window")))
    advice["option_bias_cn"] = new_bias
    advice["fund_adjust_cn"] = why
    return advice


# ---------------------------------------------------------------- fetching (network; background only)

def _ak():
    import akshare as ak

    return ak


def _fetch_lpr():
    return series_from(_ak().macro_china_lpr(), "TRADE_DATE", "LPR1Y")


def _fetch_rrr():
    df = _ak().macro_china_reserve_requirement_ratio()
    return series_from(df, "生效时间", "大型金融机构-调整幅度")


def _fetch_money_rate():
    today = datetime.now(CST).date()
    try:
        ak = _ak()
        frames = []
        # chinamoney 单次查询区间有限：按 ~2 个月切片取近 6 个月
        end = today
        for _ in range(3):
            start = end - timedelta(days=60)
            frames.extend(series_from(ak.repo_rate_hist(start_date=start.strftime("%Y%m%d"),
                                                        end_date=end.strftime("%Y%m%d")), "date", "FDR007"))
            end = start - timedelta(days=1)
        s = sorted(dict(frames).items())
        if len(s) >= MONEY_LONG_N:
            return {"series": s, "label": "DR007", "src": "dr007"}
        err = f"DR007 样本不足 {len(s)}"
    except Exception as exc:  # noqa: BLE001
        err = f"DR007 {type(exc).__name__}: {str(exc)[:60]}"
    df = _ak().rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1周")
    s = series_from(df, "报告日", "利率")
    return {"series": s[-400:], "label": "Shibor 1W", "src": "shibor", "fallback_reason": err}


def _fetch_cgb():
    start = (datetime.now(CST).date() - timedelta(days=int(365.25 * (VAL_YEARS + 1)))).strftime("%Y%m%d")
    return series_from(_ak().bond_zh_us_rate(start_date=start), "日期", "中国国债收益率10年")


def _fetch_margin():
    return series_from(_ak().stock_margin_account_info(), "日期", "融资余额")


def _fetch_money_supply():
    df = _ak().macro_china_money_supply()
    return {"m1": series_from(df, "月份", "货币(M1)-同比增长"), "m2": series_from(df, "月份", "货币和准货币(M2)-同比增长")}


def _fetch_pmi():
    return series_from(_ak().macro_china_pmi(), "月份", "制造业-指数")


def _fetch_prices():
    ak = _ak()
    ppi = series_from(ak.macro_china_ppi(), "月份", "当月同比增长")
    try:
        cpi = series_from(ak.macro_china_cpi(), "月份", "全国-同比增长")
    except Exception:  # noqa: BLE001
        cpi = []
    return {"ppi": ppi, "cpi": cpi}


def _fetch_credit():
    return series_from(_ak().macro_china_new_financial_credit(), "月份", "当月")


# 乐咕乐股接口在 akshare 里用 py_mini_racer(V8) 生成 token；多线程并发调用 V8 会段错误（本机实测 SIGSEGV），
# 所以所有 legulegu 请求串行执行并加进程级锁。
_LG_LOCK = threading.Lock()


def _fetch_pe(index_id: str):
    with _LG_LOCK:
        return series_from(_ak().stock_index_pe_lg(symbol=INDEX_LG_NAME[index_id]), "日期", "滚动市盈率")


def _fetch_pb(index_id: str):
    with _LG_LOCK:
        return series_from(_ak().stock_index_pb_lg(symbol=INDEX_LG_NAME[index_id]), "日期", "市净率")


def _fetch_valuation(index_ids: list[str]) -> dict:
    """One job, sequential: {"pe:<id>": series, "pb:<id>": series, "errors": {...}}."""
    out: dict[str, Any] = {"errors": {}}
    for iid in index_ids:
        for kind, fn in (("pe", _fetch_pe), ("pb", _fetch_pb)):
            try:
                out[f"{kind}:{iid}"] = fn(iid)
            except Exception as exc:  # noqa: BLE001
                out["errors"][f"{kind}:{iid}"] = f"{type(exc).__name__}: {str(exc)[:80]}"
    return out


def build_bundle(raw: dict, errors: dict, today: date, index_ids: list[str]) -> dict:
    """Pure: raw fetched series → serializable factor bundle."""
    def err(k):
        e = errors.get(k)
        return f"取数失败：{e}" if e else None

    mf: list[dict] = []

    def add(rec, ek):
        if rec["status"] != "ok" and err(ek):
            rec["reason"] = err(ek)
        mf.append(rec)

    add(lpr_factor(raw.get("lpr") or [], today), "lpr")
    add(rrr_factor(raw.get("rrr") or [], today), "rrr")
    mr = raw.get("money_rate") or {}
    rec = money_rate_factor(mr.get("series") or [], today, label=mr.get("label") or "DR007",
                            src=SRC.get(mr.get("src") or "dr007"))
    if mr.get("fallback_reason") and rec["status"] == "ok":
        rec["value_text"] += f"（DR007 不可用，改用 Shibor 1W：{mr['fallback_reason']}）"
    add(rec, "money_rate")
    add(cgb10y_factor(raw.get("cgb") or [], today), "cgb")
    add(margin_factor(raw.get("margin") or [], today), "margin")
    ms = raw.get("money_supply") or {}
    add(m1m2_factor(ms.get("m1") or [], ms.get("m2") or [], today), "money_supply")
    add(pmi_factor(raw.get("pmi") or [], today), "pmi")
    pr = raw.get("prices") or {}
    add(prices_factor(pr.get("ppi") or [], pr.get("cpi") or [], today), "prices")
    add(credit_factor(raw.get("credit") or [], today), "credit")

    idx: dict[str, list[dict]] = {}
    for iid in index_ids:
        pe = raw.get(f"pe:{iid}") or []
        pb = raw.get(f"pb:{iid}") or []
        facs = []
        r1 = pctile_factor("pe_pctile", "PE(TTM) 分位", pe, today, _src_pe(iid), metric="PE(TTM)")
        if r1["status"] != "ok" and err(f"pe:{iid}"):
            r1["reason"] = err(f"pe:{iid}")
        r2 = pctile_factor("pb_pctile", "PB 分位", pb, today, _src_pb(iid), metric="PB")
        if r2["status"] != "ok" and err(f"pb:{iid}"):
            r2["reason"] = err(f"pb:{iid}")
        r3 = erp_factor(pe, raw.get("cgb") or [], today, _src_erp(iid))
        if r3["status"] != "ok" and (err(f"pe:{iid}") or err("cgb")):
            r3["reason"] = err(f"pe:{iid}") or err("cgb")
        idx[iid] = [r1, r2, r3]
    return {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "asof_date": today.isoformat(),
        "market_factors": mf,
        "index_factors": idx,
        "events": policy_events(today),
        "errors": {k: v for k, v in errors.items()},
        "weights": {"groups": GROUP_WEIGHTS, "factors": FACTOR_WEIGHTS},
        "notes": [
            "北向资金每日净买额自 2024-08-16 起停止披露（东方财富序列此后为空），未纳入流动性因子",
            "社融增量序列（macro_china_shrzgm）更新滞后，改用新增人民币贷款",
        ],
    }


class IndexFundProvider:
    """Shared, cached A股指数 fundamentals. ``get_cached`` never touches the network."""

    def __init__(self, ttl: float = FUND_CACHE_TTL, fetchers: dict[str, Callable[[], Any]] | None = None,
                 index_ids: list[str] | None = None):
        self.ttl = ttl
        self.index_ids = index_ids or list(INDEX_LG_NAME)
        self._fetchers = fetchers
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()   # single-flight: concurrent callers wait, then reuse
        self._cache: tuple[float, dict] | None = None

    def fetchers(self) -> dict[str, Callable[[], Any]]:
        if self._fetchers is not None:
            return self._fetchers
        f: dict[str, Callable[[], Any]] = {
            "lpr": _fetch_lpr, "rrr": _fetch_rrr, "money_rate": _fetch_money_rate, "cgb": _fetch_cgb,
            "margin": _fetch_margin, "money_supply": _fetch_money_supply, "pmi": _fetch_pmi,
            "prices": _fetch_prices, "credit": _fetch_credit,
        }
        f["valuation"] = lambda: _fetch_valuation(self.index_ids)
        return f

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
        jobs = self.fetchers()
        pool = ThreadPoolExecutor(max_workers=FUND_WORKERS)
        futs = {pool.submit(fn): k for k, fn in jobs.items()}
        done, not_done = wait(list(futs), timeout=FUND_BATCH_TIMEOUT_SEC)
        for fut in done:
            k = futs[fut]
            try:
                raw[k] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors[k] = f"{type(exc).__name__}: {str(exc)[:80]}"
        for fut in not_done:
            errors[futs[fut]] = f"超时（>{FUND_BATCH_TIMEOUT_SEC:.0f}s）"
            fut.cancel()
        val = raw.pop("valuation", None)
        if isinstance(val, dict):
            errors.update(val.pop("errors", {}) or {})
            raw.update(val)
        elif "valuation" in errors:
            for iid in self.index_ids:
                errors.setdefault(f"pe:{iid}", errors["valuation"])
                errors.setdefault(f"pb:{iid}", errors["valuation"])
        pool.shutdown(wait=False, cancel_futures=True)
        bundle = build_bundle(raw, errors, datetime.now(CST).date(), self.index_ids)
        bundle["fetch_seconds"] = round(time.time() - t0, 1)
        with self._lock:
            self._cache = (time.time(), bundle)
        logger.info("cn index fundamentals: %s ok, %d errors, %.1fs",
                    sum(1 for f in bundle["market_factors"] if f["status"] == "ok"), len(errors), time.time() - t0)
        return bundle


DEFAULT_PROVIDER = IndexFundProvider()


def pick_bundle(*bundles: dict | None) -> dict | None:
    """Freshest bundle by fetched_at (None-safe)."""
    best, best_age = None, None
    for b in bundles:
        age = fund_bundle_age_hours(b)
        if age is None:
            continue
        if best_age is None or age < best_age:
            best, best_age = b, age
    return best


METHOD_TEXT = (
    "综合分 = 0.6×技术分 + 0.4×基本面分（0–100，50 中性；越高越偏多、利于卖Put，越低利于卖Call）。"
    "技术分 = 50 + 原建议评分/2。基本面四组：政策 35%（LPR、准备金率、DR007、10年国债，权重最高）、"
    "估值 25%（PE/PB 近10年分位、股权风险溢价，按指数）、流动性 25%（两融融资余额、M1−M2）、"
    "宏观 15%（PMI、PPI、新增贷款）。缺数据标「无数据」并在组内/组间重新归一，不插补。"
    f"基本面只调整期权偏向：偏卖Put 且基本面≤{FUND_PUT_VETO:.0f} 或 偏卖Call 且基本面≥{FUND_CALL_VETO:.0f} → 改价差；"
    f"价差 且 综合与基本面均≥{UPGRADE_PUT:.0f} → 偏卖Put，均≤{UPGRADE_CALL:.0f} → 偏卖Call（观望高波/不做裸卖不变，政策事件窗口内不升级）。"
    "立场、置信度与技术阈值不变。"
)

__all__ = [
    "GROUP_WEIGHTS", "FACTOR_WEIGHTS", "IndexFundProvider", "DEFAULT_PROVIDER", "apply_to_advice",
    "score_index_factors", "adjust_option_bias", "event_window", "policy_events", "build_bundle",
    "pick_bundle", "METHOD_TEXT", "NO_DATA", "signal_text",
]
