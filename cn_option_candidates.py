# -*- coding: utf-8 -*-
"""国内「已上市商品期货期权」标的 → 卖Put/卖Call 方向候选（连续合约研究用）。

设计约束
- 只纳入白名单内、公开资料可确认已有对应商品期货期权的品种；
  不把无期权合约、股指期货（IO/HO/MO 为指数期权）、国债期货误当可卖期权标的。
- 价格来自新浪主力连续合约日线（与看板 CN:* 一致），仅做 HV/RSI/涨跌方向评分。
- 不构造 IV、权利金、行权价、到期日或期权链；无链时明确「仅标的方向候选」。
- 币种为人民币；不适用美股 CSP / 备兑术语。
- 连续合约 ≠ 可交易期权标的月份，仅作方向研究标识。
- 行情 asof 取日线 date 列最后有效日；扫描时间单独标注，二者不得混用。
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

import fundamentals as _fund

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
# 扫描时间统一为 UTC+8（北京时间 = 新加坡时间）；标签由实际时间戳偏移推导。
CN_HV20_MIN = 18.0
FETCH_ATTEMPTS = 2
FETCH_RETRY_DELAY_SEC = 1.5

# 公开可追溯来源（交易所/券商规则页）。行级 source_urls 引用这些键。
SOURCE_CATALOG: dict[str, str] = {
    # 上期所 / 上期能源
    "shfe_intro": "https://www.shfe.com.cn/about/introduce/simintro/",
    "shfe_fee": "https://www.shfe.com.cn/reports/businessdata/feeandcharges/202501/t20250102_824218.html",
    "shfe_cu_notice": "https://www.shfe.com.cn/publicnotice/notice/201809/t20180906_793549.html",
    "shfe_au_contract": "https://www.shfe.com.cn/products/option/ferrousandpreciousmetal/au_o/standard_au_o/202401/t20240103_331332.html",
    "shfe_options_expand": "https://www.shfe.com.cn/index/othercontents/2026_Options/",
    # 大商所
    "dce_option_review_pdf": "http://www.dce.com.cn/dalianshangpin/resource/cms/article/489932/8501011/2022072517563330695.pdf",
    "dce_corn_option_pdf": "http://www.dce.com.cn/dalianshangpin/resource/cms/article/8536039/489773/240219%E7%8E%89%E7%B1%B3%E6%9C%9F%E8%B4%A7%E5%92%8C%E6%9C%9F%E6%9D%83%E5%AE%A3%E4%BC%A0%E9%A1%B5.pdf",
    # 郑商所
    "czce_option_manual_pdf": "https://www.czce.com.cn/cn/rootfiles/2020/01/10/1572882614555335-1572882614593442.pdf",
    "czce_ma_contract_pdf": "https://www.czce.com.cn/cn/rootfiles/2019/12/12/1572880104532909-1572880104553785.pdf",
    # 券商公开品种表（覆盖多所期权，含菜油等）
    "htfc_option_list": "https://webtrade.htfc.com/main/a/20260901/80122405.shtml",
    "orient_dce": "https://www.orientfutures.com.sg/zh/china-market-access/dalian-commodity-exchange-dce/",
    "orient_czce": "https://www.orientfutures.com.sg/china-market-access/zhengzhou-commodity-exchange-zce-cn/",
}

SOURCE_NOTES = [
    f"上期所简介（列明已上市商品期权品种，含铜/铝/锌/黄金/白银/螺纹/天胶/原油等）：{SOURCE_CATALOG['shfe_intro']}",
    f"上期所收费一览（期权手续费品种表）：{SOURCE_CATALOG['shfe_fee']}",
    f"上期所铜期权上市通知：{SOURCE_CATALOG['shfe_cu_notice']}",
    f"上期所黄金期货期权合约页：{SOURCE_CATALOG['shfe_au_contract']}",
    f"上期所期权扩容专题（热轧卷板/不锈钢/低硫燃料油等）：{SOURCE_CATALOG['shfe_options_expand']}",
    f"大商所期权市场综述 PDF（豆粕/玉米/棕榈油/铁矿石/LPG/PP/L/V 等）：{SOURCE_CATALOG['dce_option_review_pdf']}",
    f"大商所玉米期货与期权宣传页 PDF：{SOURCE_CATALOG['dce_corn_option_pdf']}",
    f"郑商所期权交易实务 PDF（白糖/棉花/甲醇/PTA/菜粕等合约表）：{SOURCE_CATALOG['czce_option_manual_pdf']}",
    f"郑商所甲醇期权合约 PDF：{SOURCE_CATALOG['czce_ma_contract_pdf']}",
    f"华泰期货公开期权/期货品种表：{SOURCE_CATALOG['htfc_option_list']}",
    f"东证期货 DCE 接入说明（豆粕/豆油/棕榈/铁矿等期权）：{SOURCE_CATALOG['orient_dce']}",
    f"东证期货 ZCE 接入说明（白糖/棉花/PTA/甲醇/菜油/菜粕等期权）：{SOURCE_CATALOG['orient_czce']}",
    "此前项目内新浪期权页实测：cu / au / m / sr（补充，非唯一依据）",
]


def _row(
    name: str,
    root: str,
    exchange: str,
    exchange_code: str,
    sector: str,
    *,
    source_keys: list[str],
    verify: str,
) -> dict[str, Any]:
    urls = [SOURCE_CATALOG[k] for k in source_keys if k in SOURCE_CATALOG]
    return {
        "name": name,
        "root": root,
        "exchange": exchange,
        "exchange_code": exchange_code,
        "sector": sector,
        "option_listed": True,
        "verify": verify,
        "source_keys": list(source_keys),
        "source_urls": urls,
    }


# 白名单：连续合约代码 → 元数据。每行带可点击 source_urls。
CN_OPTIONABLE_CONTINUOUS: dict[str, dict[str, Any]] = {
    # —— 核心：sina 实测 + 交易所公开 ——
    "CU0": _row("沪铜", "CU", "上期所", "shfe", "有色", source_keys=["shfe_cu_notice", "shfe_intro", "shfe_fee"], verify="shfe_official_notice+intro"),
    "AU0": _row("黄金", "AU", "上期所", "shfe", "贵金属", source_keys=["shfe_au_contract", "shfe_intro", "shfe_fee"], verify="shfe_official_contract+intro"),
    "M0": _row("豆粕", "M", "大商所", "dce", "农产品", source_keys=["dce_option_review_pdf", "orient_dce"], verify="dce_official_pdf+broker_list"),
    "SR0": _row("白糖", "SR", "郑商所", "czce", "软商品", source_keys=["czce_option_manual_pdf", "orient_czce"], verify="czce_official_pdf+broker_list"),
    # —— 上期所 / 能源 ——
    "AG0": _row("白银", "AG", "上期所", "shfe", "贵金属", source_keys=["shfe_intro", "shfe_fee"], verify="shfe_official_intro+fee"),
    "AL0": _row("铝", "AL", "上期所", "shfe", "有色", source_keys=["shfe_intro", "shfe_fee"], verify="shfe_official_intro+fee"),
    "ZN0": _row("沪锌", "ZN", "上期所", "shfe", "有色", source_keys=["shfe_intro", "shfe_fee"], verify="shfe_official_intro+fee"),
    "RB0": _row("螺纹钢", "RB", "上期所", "shfe", "黑色", source_keys=["shfe_intro", "shfe_fee"], verify="shfe_official_intro+fee"),
    "RU0": _row("天然橡胶", "RU", "上期所", "shfe", "化工", source_keys=["shfe_intro", "shfe_fee"], verify="shfe_official_intro+fee"),
    "SC0": _row("原油", "SC", "上期能源", "ine", "能源", source_keys=["shfe_intro", "htfc_option_list"], verify="shfe_ine_intro+broker_list"),
    # —— 大商所 ——
    "C0": _row("玉米", "C", "大商所", "dce", "农产品", source_keys=["dce_corn_option_pdf", "dce_option_review_pdf"], verify="dce_official_pdf"),
    "I0": _row("铁矿石", "I", "大商所", "dce", "黑色", source_keys=["dce_option_review_pdf", "orient_dce"], verify="dce_official_pdf+broker_list"),
    "P0": _row("棕榈油", "P", "大商所", "dce", "农产品", source_keys=["dce_option_review_pdf", "orient_dce"], verify="dce_official_pdf+broker_list"),
    "Y0": _row("豆油", "Y", "大商所", "dce", "农产品", source_keys=["orient_dce", "htfc_option_list"], verify="broker_access_list"),
    "L0": _row("塑料", "L", "大商所", "dce", "化工", source_keys=["dce_option_review_pdf", "htfc_option_list"], verify="dce_official_pdf+broker_list"),
    "V0": _row("PVC", "V", "大商所", "dce", "化工", source_keys=["dce_option_review_pdf", "htfc_option_list"], verify="dce_official_pdf+broker_list"),
    "PP0": _row("聚丙烯", "PP", "大商所", "dce", "化工", source_keys=["dce_option_review_pdf", "htfc_option_list"], verify="dce_official_pdf+broker_list"),
    "PG0": _row("液化石油气", "PG", "大商所", "dce", "能源", source_keys=["dce_option_review_pdf", "htfc_option_list"], verify="dce_official_pdf+broker_list"),
    # —— 郑商所 ——
    "CF0": _row("棉花", "CF", "郑商所", "czce", "软商品", source_keys=["czce_option_manual_pdf", "orient_czce"], verify="czce_official_pdf+broker_list"),
    "TA0": _row("PTA", "TA", "郑商所", "czce", "化工", source_keys=["czce_option_manual_pdf", "orient_czce"], verify="czce_official_pdf+broker_list"),
    "MA0": _row("甲醇", "MA", "郑商所", "czce", "化工", source_keys=["czce_ma_contract_pdf", "czce_option_manual_pdf"], verify="czce_official_contract_pdf"),
    "RM0": _row("菜粕", "RM", "郑商所", "czce", "农产品", source_keys=["czce_option_manual_pdf", "orient_czce"], verify="czce_official_pdf+broker_list"),
    "OI0": _row("菜油", "OI", "郑商所", "czce", "农产品", source_keys=["orient_czce", "htfc_option_list"], verify="broker_access_list"),
}

CN_EXCLUDED_CONTINUOUS = {
    "IF0", "IH0", "IC0", "IM0",
    "T0", "TF0", "TS0", "TL0",
    "EC0",
    "FB0", "RR0", "WH0", "JR0", "RI0", "LR0", "CY0", "RS0",
}

# 与 bias「观望等待」门槛对齐：score>=28 才进入方向候选栏；不改美股 CSP 规则。
CN_MIN_SCORE = 28.0

CN_METHOD_PUT = (
    "国内商品期货（白名单内已上市期权品种）仅提供连续合约标的方向候选："
    "复用与美股相同的 HV/RSI/涨跌评分框架中的方向部分；"
    f"仅 score≥{CN_MIN_SCORE:.0f}（观望等待及以上）列入候选栏，文案为研究/观望，不作下单推荐；"
    "不提供权利金/IV/行权价/到期日，未核验期权链与流动性，不能下单。"
    "基本面分（库存/仓单、基差、期限结构、季节性）与技术分按 0.6×技术+0.4×基本面 合成综合分，"
    "入选仍看技术分门槛，排序按综合分；缺数据的因子标「无数据」不插补。"
)
CN_METHOD_CALL = (
    "国内商品期货（白名单内已上市期权品种）仅提供连续合约标的方向候选："
    "复用卖Call 的 RSI/均线/涨跌方向评分；"
    f"仅 score≥{CN_MIN_SCORE:.0f}（观望等待及以上）列入候选栏，文案为研究/观望，不作下单推荐；"
    "不提供权利金/IV/行权价/到期日，未核验期权链与流动性，不能下单。"
    "基本面分（库存/仓单、基差、期限结构、季节性）与技术分按 0.6×技术+0.4×基本面 合成综合分，"
    "入选仍看技术分门槛，排序按综合分；缺数据的因子标「无数据」不插补。"
)
CN_DISCLAIMER = (
    "国内部分为期货期权方向研究：价格为人民币连续合约，"
    "卖方适用期货期权保证金与行权后期货持仓/交割风险，"
    "不适用美股现金担保卖Put或备兑卖Call术语。连续合约不可当作真实可交易期权标的月份。"
)

HistoryLoader = Callable[[str], Any]

# 基本面取数入口（运行时查找，测试可 monkeypatch 为离线桩）。None/False ⇒ 不取基本面。
def _default_fund_provider():
    from fundamentals import DEFAULT_CN_PROVIDER

    return DEFAULT_CN_PROVIDER


DEFAULT_CN_FUND_PROVIDER: Any = _default_fund_provider


def instrument_id(symbol: str) -> str:
    sym = str(symbol).upper()
    return sym if sym.startswith("CN:") else f"CN:{sym}"


def continuous_symbol(symbol: str) -> str:
    return str(symbol).upper().removeprefix("CN:")


def is_optionable_continuous(symbol: str) -> bool:
    return continuous_symbol(symbol) in CN_OPTIONABLE_CONTINUOUS


def is_explicitly_excluded(symbol: str) -> bool:
    return continuous_symbol(symbol) in CN_EXCLUDED_CONTINUOUS


def optionable_universe() -> list[dict[str, Any]]:
    rows = []
    for sym, meta in CN_OPTIONABLE_CONTINUOUS.items():
        rows.append(
            {
                "symbol": sym,
                "id": instrument_id(sym),
                "name": meta["name"],
                "root": meta["root"],
                "exchange": meta["exchange"],
                "exchange_code": meta["exchange_code"],
                "sector": meta["sector"],
                "market": "cn",
                "kind": "cn_fut",
                "currency": "CNY",
                "underlying_type": "cn_continuous_futures",
                "option_listed": True,
                "verify": meta.get("verify"),
                "source_urls": list(meta.get("source_urls") or []),
                "source_keys": list(meta.get("source_keys") or []),
            }
        )
    return rows


def _default_history_loader(instrument_id_: str):
    from data_sources import get_daily_history

    return get_daily_history(instrument_id_)


def _valid_close(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    return x


def _parse_asof(value: Any) -> str | None:
    """Return YYYY-MM-DD from Timestamp/date/str; None if unknown."""
    if value is None:
        return None
    try:
        import pandas as pd

        ts = pd.Timestamp(value)
        if ts is pd.NaT or ts != ts:
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:
        s = str(value).strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        return None


def prepare_close_frame(df) -> tuple[Any | None, str | None, str | None]:
    """Validate OHLCV frame → (close Series sorted by date, asof YYYY-MM-DD|None, reject_reason|None).

    - Rejects non-finite / <=0 closes (does not drop only the bad tail and pretend the prior bar is latest).
    - Sorts/dedupes by date only when a date column or DatetimeIndex is confirmed.
    """
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None, None, "empty_frame"

    close_col = None
    for c in ("close", "Close", "收盘价", "收盘"):
        if c in df.columns:
            close_col = c
            break
    if close_col is None:
        return None, None, "missing_close_column"

    work = df.copy()
    date_col = None
    for c in ("date", "Date", "datetime", "Datetime", "时间", "日期"):
        if c in work.columns:
            date_col = c
            break

    if date_col is not None:
        parsed = pd.to_datetime(work[date_col], errors="coerce")
        if parsed.notna().sum() == 0:
            return None, None, "date_column_unparseable"
        work = work.assign(_dt=parsed).dropna(subset=["_dt"])
        work = work.sort_values("_dt").drop_duplicates("_dt", keep="last")
        closes = [_valid_close(v) for v in work[close_col].tolist()]
        if any(v is None for v in closes):
            return None, None, "nonpositive_or_nonfinite_close"
        series = pd.Series(closes, index=pd.DatetimeIndex(work["_dt"]), dtype=float)
        asof = _parse_asof(series.index[-1]) if len(series) else None
        return series, asof, None

    if isinstance(work.index, pd.DatetimeIndex):
        work = work.sort_index()
        work = work[~work.index.duplicated(keep="last")]
        closes = [_valid_close(v) for v in work[close_col].tolist()]
        if any(v is None for v in closes):
            return None, None, "nonpositive_or_nonfinite_close"
        series = pd.Series(closes, index=work.index, dtype=float)
        asof = _parse_asof(series.index[-1]) if len(series) else None
        return series, asof, None

    # No confirmed date axis: do not invent sort; still reject bad closes.
    closes = [_valid_close(v) for v in work[close_col].tolist()]
    if any(v is None for v in closes):
        return None, None, "nonpositive_or_nonfinite_close"
    series = pd.Series(closes, dtype=float)
    return series, None, None


def metrics_gate(series) -> tuple[str | None, float | None]:
    """Return (reject_reason, hv20). reject_reason None means the series passes."""
    from short_put_screen import _hv

    if series is None or len(series) < 40:
        return "insufficient_bars", None
    hv20 = _hv(series, 20)
    if hv20 is None:
        return "hv20_unavailable", None
    if hv20 < CN_HV20_MIN:
        return "low_hv20", float(hv20)
    return None, float(hv20)


def _metrics_from_series(series) -> dict[str, Any] | None:
    from short_put_screen import _hv, _hv_percentile, _r, _rsi

    gate, _ = metrics_gate(series)
    if gate is not None:
        return None
    px = float(series.iloc[-1])
    hv20 = _hv(series, 20)
    hv60 = _hv(series, 60)
    ma20 = float(series.tail(20).mean()) if len(series) >= 20 else None
    ma60 = float(series.tail(60).mean()) if len(series) >= 60 else None
    return {
        "price": _r(px, 2),
        "hv20": _r(hv20, 1),
        "hv60": _r(hv60, 1),
        "hv_pctile": _r(_hv_percentile(series), 1),
        "rsi14": _r(_rsi(series), 1),
        "chg_1d": _r(float(series.pct_change().iloc[-1] * 100), 2),
        "chg_5d": _r(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 2) if len(series) >= 6 else None,
        "chg_21d": _r(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 2) if len(series) >= 22 else None,
        "vs_ma20_pct": _r((px / ma20 - 1) * 100, 2) if ma20 else None,
        "vs_ma60_pct": _r((px / ma60 - 1) * 100, 2) if ma60 else None,
    }


def _cn_risk(side: str) -> dict[str, Any]:
    return {
        "capital_per_contract": None,
        "max_contracts_hint": None,
        "budget_usd": None,
        "max_name_pct": None,
        "currency": "CNY",
        "margin_note": (
            "中国商品期货期权卖方按交易所及期货公司保证金规则；"
            "不适用美股现金担保（CSP）或股票备兑占用估算。"
            "行权/履约后可能持有对应期货合约，需考虑保证金追加、强平与交割风险。"
        ),
        "iv_tag": "未核验期权链",
        "iv_hv_spread": None,
        "earnings": None,
        "flags": ["连续合约", "期权链未核验", "仅方向研究", "人民币计价"],
        "level": "高",
        "side": side,
    }


def _cn_action(side: str) -> str:
    if side == "call":
        return (
            "标的方向研究/观望候选（连续合约）；未核验期权链与流动性，不能下单。"
            "国内卖Call 为期货期权卖方，保证金与行权后期货持仓风险需按期货公司规则单独核算，"
            "不适用美股备兑/裸卖Call术语。"
        )
    return (
        "标的方向研究/观望候选（连续合约）；未核验期权链与流动性，不能下单。"
        "国内卖Put 为期货期权卖方，不适用美股现金担保卖Put；"
        "行权后可能转为期货多头持仓，注意保证金与交割。"
    )


def _cn_bias(score: float | None, rsi: float | None, side: str) -> str:
    s = score or 0
    if s >= 55:
        return "优先关注（方向·研究）"
    if s >= 40:
        return "可考虑（方向·研究）"
    if s >= 28:
        return "观望等待（方向·研究）"
    if side == "put" and rsi is not None and rsi < 28:
        return "谨慎抄底（方向）"
    if side == "call" and rsi is not None and rsi < 28:
        return "急跌慎卖（方向）"
    return "暂不推荐（方向）"


def now_cst_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def tz_label_for(ts: str | None) -> str | None:
    """Label derived from the timestamp's own UTC offset (never hard-coded)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except Exception:
        return None
    off = dt.utcoffset()
    if off is None:
        return None
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    h, m = divmod(abs(mins), 60)
    utc = f"UTC{sign}{h}" + (f":{m:02d}" if m else "")
    if mins == 8 * 60:
        return f"北京时间/新加坡时间 {utc}"
    return utc


_REASON_CN = {
    "fetch_error": "取数失败",
    "empty_frame": "无行情数据",
    "missing_close_column": "缺收盘价列",
    "date_column_unparseable": "日期列无法解析",
    "nonpositive_or_nonfinite_close": "收盘价含非正/非有限值",
    "insufficient_bars": "日线不足40根",
    "hv20_unavailable": "HV20无法计算",
    "low_hv20": f"HV20<{CN_HV20_MIN:.0f}%（波动不足，未入评分）",
}


def _load_with_retry(loader: HistoryLoader, iid: str):
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return loader(iid)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < FETCH_ATTEMPTS and FETCH_RETRY_DELAY_SEC > 0:
                time.sleep(FETCH_RETRY_DELAY_SEC)
    assert last is not None
    raise last


def build_cn_direction_candidates(
    side: str = "put",
    *,
    history_loader: HistoryLoader | None = None,
    top_n: int = 12,
    min_score: float | None = None,
    fund_provider: Any = None,
) -> dict[str, Any]:
    """Build direction-only CN candidates for put or call tab.

    Inclusion gate is unchanged (technical ``score`` ≥ min_score); ordering is by
    ``combined_score`` = 0.6·tech + 0.4·fundamental (tech only when fundamentals missing).
    """
    side = "call" if side == "call" else "put"
    min_score = CN_MIN_SCORE if min_score is None else float(min_score)
    loader = history_loader or _default_history_loader
    if side == "call":
        from short_call_screen import _r, _score_row
    else:
        from short_put_screen import _r, _score_row

    scanned_at = now_cst_iso()
    scanned_tz = tz_label_for(scanned_at)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    reject_reasons: dict[str, str] = {}
    failed: list[dict[str, Any]] = []      # 取数/数据校验失败（未取到可用行情）
    filtered: list[dict[str, Any]] = []    # 取到行情但未过 HV/样本门槛
    fetched_ok = 0
    series_by_sym: dict[str, Any] = {}
    for meta in optionable_universe():
        sym = meta["symbol"]
        iid = meta["id"]
        try:
            try:
                df = _load_with_retry(loader, iid)
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("cn candidate %s fetch failed: %s", sym, detail)
                failed.append({"symbol": sym, "name": meta["name"], "reason": "fetch_error",
                               "reason_cn": _REASON_CN["fetch_error"], "detail": detail})
                reject_reasons[sym] = "fetch_error"
                errors.append(f"{sym}:{type(exc).__name__}")
                continue
            series, asof, reject = prepare_close_frame(df)
            if reject:
                logger.warning("cn candidate %s rejected: %s", sym, reject)
                failed.append({"symbol": sym, "name": meta["name"], "reason": reject,
                               "reason_cn": _REASON_CN.get(reject, reject)})
                reject_reasons[sym] = reject
                errors.append(f"{sym}:{reject}")
                continue
            fetched_ok += 1
            gate, hv20_val = metrics_gate(series)
            metrics = None if gate else _metrics_from_series(series)
            if metrics is None:
                gate = gate or "hv20_unavailable"
                logger.info("cn candidate %s filtered: %s hv20=%s", sym, gate, hv20_val)
                filtered.append({"symbol": sym, "name": meta["name"], "reason": gate,
                                 "reason_cn": _REASON_CN.get(gate, gate),
                                 "hv20": None if hv20_val is None else round(hv20_val, 1),
                                 "asof": asof or "未知"})
                reject_reasons[sym] = gate
                continue
            row = {
                **metrics,
                "symbol": sym,
                "id": iid,
                "name": meta["name"],
                "kind": "cn_fut",
                "sector": meta["sector"],
                "market": "cn",
                "exchange": meta["exchange"],
                "currency": "CNY",
                "underlying_type": "cn_continuous_futures",
                "candidate_mode": "underlying_direction_only",
                "option_chain_status": "unverified",
                "option_listed": True,
                "verify": meta.get("verify"),
                "source_urls": list(meta.get("source_urls") or []),
                "put": None,
                "call": None,
                "earnings": None,
                "edge": None,
                "checklist": None,
                "price_note": "新浪主力连续合约收盘价（人民币）；非具体可交易期权标的合约",
                "asof": asof or "未知",
                "asof_status": "ok" if asof else "unknown",
                "asof_tz": "交易所交易日（日线日期）" if asof else None,
                "price_source": "sina_continuous_daily",
                "scanned_at": scanned_at,
                "scanned_at_tz": scanned_tz,
            }
            row["score"] = _r(_score_row(row), 1)
            series_by_sym[sym] = series
            row["bias_cn"] = _cn_bias(row.get("score"), row.get("rsi14"), side)
            row["action"] = _cn_action(side)
            row["risk"] = _cn_risk(side)
            rows.append(row)
        except Exception as exc:  # noqa: BLE001 — scoring bug etc.; never silent
            detail = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("cn candidate %s failed: %s", sym, detail)
            failed.append({"symbol": sym, "name": meta["name"], "reason": "processing_error",
                           "reason_cn": "处理异常", "detail": detail})
            errors.append(f"{sym}:{type(exc).__name__}")
            reject_reasons[sym] = type(exc).__name__

    fund_meta = _attach_cn_fundamentals(rows, series_by_sym, side, fund_provider)
    ranked = sorted(rows, key=_fund.rank_key, reverse=True)
    # Gate on the technical score (unchanged threshold); order by combined score.
    picks = [r for r in ranked if (r.get("tech_score") if r.get("tech_score") is not None else (r.get("score") or 0)) >= min_score][:top_n]
    status = "ok" if picks else ("empty" if not ranked else "below_threshold")
    note = None
    if failed:
        logger.warning("cn candidates %s: %d/%d symbols without usable data: %s", side, len(failed),
                       len(CN_OPTIONABLE_CONTINUOUS), ", ".join(f["symbol"] for f in failed))
    if not ranked:
        note = "白名单品种暂无足够连续合约行情，或波动未达筛选阈值；未伪补国内信号。"
    elif not picks:
        note = f"已扫描到方向样本，但均低于研究/观望门槛 score≥{min_score:.0f}；候选栏保持空，不作低分推荐。"
    return {
        "side": side,
        "status": status,
        "updated_at": scanned_at,
        "scanned_at": scanned_at,
        "scanned_at_tz": scanned_tz,
        "universe_size": len(CN_OPTIONABLE_CONTINUOUS),
        "scanned": len(ranked),
        "fetched": fetched_ok,
        "failed": failed,
        "filtered": filtered,
        "hv20_min": CN_HV20_MIN,
        "candidates": picks,
        "all": ranked,
        "errors": errors[:20],
        "reject_reasons": reject_reasons,
        "source_notes": SOURCE_NOTES,
        "source_catalog": SOURCE_CATALOG,
        "min_score": min_score,
        "note": note,
        "disclaimer": CN_DISCLAIMER,
        "fund_meta": fund_meta,
    }


def _attach_cn_fundamentals(rows: list[dict], series_by_sym: dict, side: str, fund_provider: Any) -> dict:
    """Add tech/fund/combined fields to every scored row. Never fatal."""
    t0 = time.time()
    provider = fund_provider
    if provider is None:
        factory = DEFAULT_CN_FUND_PROVIDER
        try:
            provider = factory() if callable(factory) and not hasattr(factory, "snapshot") else factory
        except Exception:  # noqa: BLE001
            provider = None
    snap: dict | None = None
    status = "disabled" if not provider else "ok"
    err = None
    if provider and rows:
        try:
            snap = provider.snapshot([r["symbol"] for r in rows])
        except Exception as exc:  # noqa: BLE001
            logger.warning("cn fundamentals fetch failed: %s", exc)
            err = f"{type(exc).__name__}: {str(exc)[:120]}"
            status = "error"
            snap = None
    for r in rows:
        if not provider:
            _fund.apply_fund_to_row(r, None, not_computed_reason="基本面未计算（未启用取数）")
            continue
        try:
            facs = _fund.cn_factors_for(r["symbol"], series_by_sym.get(r["symbol"]), snap)
            if snap is None:
                for f in facs:
                    if f["key"] != "seasonality" and f["status"] != "ok":
                        f["reason"] = f"取数失败：{err}" if err else f.get("reason")
            fund = _fund.score_factors(facs, _fund.CN_FACTOR_WEIGHTS, side)
            _fund.apply_fund_to_row(r, fund)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cn fundamentals %s failed: %s", r.get("symbol"), exc)
            _fund.apply_fund_to_row(r, None, not_computed_reason=f"基本面计算异常 {type(exc).__name__}")
    return {
        "status": status,
        "error": err,
        "seconds": round(time.time() - t0, 1),
        "fetch_seconds": (snap or {}).get("fetch_seconds"),
        "fetched_at": (snap or {}).get("fetched_at"),
        "fetch_errors": dict(list(((snap or {}).get("errors") or {}).items())[:30]),
        "weights": dict(_fund.CN_FACTOR_WEIGHTS),
        "tech_weight": _fund.TECH_WEIGHT,
        "fund_weight": _fund.FUND_WEIGHT,
        "method": _fund.method_text("cn", side),
    }


def ensure_cn_candidate_fields(payload: dict | None, side: str = "put") -> dict:
    """Snapshot/API compat: never invent CN signals; ensure keys exist for old seeds."""
    out = dict(payload or {})
    side = "call" if side == "call" else "put"
    if "cn_candidates" not in out or out.get("cn_candidates") is None:
        out["cn_candidates"] = []
        out["cn_candidate_meta"] = {
            "side": side,
            "status": "absent_in_snapshot",
            "note": "当前快照未包含国内期货方向候选；重新扫描或静态导出后才会生成。未伪补信号。",
            "universe_size": len(CN_OPTIONABLE_CONTINUOUS),
            "scanned": 0,
            "min_score": CN_MIN_SCORE,
        }
    else:
        meta = dict(out.get("cn_candidate_meta") or {})
        meta.setdefault("side", side)
        meta.setdefault("status", "ok" if out["cn_candidates"] else "empty")
        meta.setdefault("universe_size", len(CN_OPTIONABLE_CONTINUOUS))
        meta.setdefault("min_score", CN_MIN_SCORE)
        # Re-derive tz labels from the timestamps (fixes old "Asia/Tokyo (UTC+9)" labels).
        if meta.get("scanned_at"):
            meta["scanned_at_tz"] = tz_label_for(meta["scanned_at"]) or meta.get("scanned_at_tz")
        fixed_rows = []
        for r in out["cn_candidates"]:
            if isinstance(r, dict) and r.get("scanned_at"):
                r = dict(r)
                r["scanned_at_tz"] = tz_label_for(r["scanned_at"]) or r.get("scanned_at_tz")
                if r.get("asof_tz") == "Asia/Tokyo-calendar-date":
                    r["asof_tz"] = "交易所交易日（日线日期）"
            fixed_rows.append(r)
        out["cn_candidates"] = fixed_rows
        out["cn_candidate_meta"] = meta
    # Strip any CN rows that may have leaked into US lists (old buggy attach).
    for key in ("picks", "watchlist", "all"):
        if isinstance(out.get(key), list):
            out[key] = [r for r in out[key] if not (isinstance(r, dict) and (r.get("kind") == "cn_fut" or r.get("market") == "cn"))]
    method = dict(out.get("method") or {})
    if "cn_futures" not in method:
        method["cn_futures"] = CN_METHOD_CALL if side == "call" else CN_METHOD_PUT
        out["method"] = method
    disc = out.get("disclaimer") or ""
    if "国内部分为期货期权方向研究" not in disc:
        out["disclaimer"] = (disc + " " + CN_DISCLAIMER).strip()
    return out


def attach_cn_candidates(
    payload: dict,
    side: str = "put",
    *,
    populate: bool = True,
    history_loader: HistoryLoader | None = None,
    top_n: int = 12,
    fund_provider: Any = None,
) -> dict:
    """Attach CN direction candidates. Never merge CN into US picks/all tables."""
    side = "call" if side == "call" else "put"
    out = ensure_cn_candidate_fields(payload, side=side)
    if not populate:
        return out
    try:
        built = build_cn_direction_candidates(side, history_loader=history_loader, top_n=top_n,
                                              fund_provider=fund_provider)
        out["cn_candidates"] = built["candidates"]
        out["cn_candidate_meta"] = {
            "side": side,
            "status": built["status"],
            "note": built.get("note"),
            "universe_size": built["universe_size"],
            "scanned": built["scanned"],
            "fetched": built.get("fetched"),
            "failed": built.get("failed") or [],
            "filtered": built.get("filtered") or [],
            "hv20_min": built.get("hv20_min"),
            "errors": built.get("errors") or [],
            "reject_reasons": built.get("reject_reasons") or {},
            "source_notes": built.get("source_notes") or [],
            "source_catalog": built.get("source_catalog") or {},
            "updated_at": built.get("updated_at"),
            "scanned_at": built.get("scanned_at"),
            "scanned_at_tz": built.get("scanned_at_tz"),
            "min_score": built.get("min_score"),
            "disclaimer": built.get("disclaimer"),
            "fund_meta": built.get("fund_meta"),
        }
        # US lists stay US-only; CN lives only in cn_candidates.
        for key in ("picks", "watchlist", "all"):
            if isinstance(out.get(key), list):
                out[key] = [
                    r
                    for r in out[key]
                    if not (isinstance(r, dict) and (r.get("kind") == "cn_fut" or r.get("market") == "cn"))
                ]
    except Exception as exc:
        logger.exception("attach_cn_candidates failed")
        out["cn_candidates"] = []
        out["cn_candidate_meta"] = {
            "side": side,
            "status": "error",
            "note": f"国内候选生成失败：{type(exc).__name__}（未伪补信号）",
            "universe_size": len(CN_OPTIONABLE_CONTINUOUS),
            "scanned": 0,
            "min_score": CN_MIN_SCORE,
        }
    return out
