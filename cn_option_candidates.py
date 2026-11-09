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
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

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
)
CN_METHOD_CALL = (
    "国内商品期货（白名单内已上市期权品种）仅提供连续合约标的方向候选："
    "复用卖Call 的 RSI/均线/涨跌方向评分；"
    f"仅 score≥{CN_MIN_SCORE:.0f}（观望等待及以上）列入候选栏，文案为研究/观望，不作下单推荐；"
    "不提供权利金/IV/行权价/到期日，未核验期权链与流动性，不能下单。"
)
CN_DISCLAIMER = (
    "国内部分为期货期权方向研究：价格为人民币连续合约，"
    "卖方适用期货期权保证金与行权后期货持仓/交割风险，"
    "不适用美股现金担保卖Put或备兑卖Call术语。连续合约不可当作真实可交易期权标的月份。"
)

HistoryLoader = Callable[[str], Any]


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


def _metrics_from_series(series) -> dict[str, Any] | None:
    from short_put_screen import _hv, _hv_percentile, _r, _rsi

    if series is None or len(series) < 40:
        return None
    px = float(series.iloc[-1])
    hv20 = _hv(series, 20)
    if hv20 is None or hv20 < 18:
        return None
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


def build_cn_direction_candidates(
    side: str = "put",
    *,
    history_loader: HistoryLoader | None = None,
    top_n: int = 12,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Build direction-only CN candidates for put or call tab."""
    side = "call" if side == "call" else "put"
    min_score = CN_MIN_SCORE if min_score is None else float(min_score)
    loader = history_loader or _default_history_loader
    if side == "call":
        from short_call_screen import _r, _score_row
    else:
        from short_put_screen import _r, _score_row

    scanned_at = now_cst_iso()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    reject_reasons: dict[str, str] = {}
    for meta in optionable_universe():
        sym = meta["symbol"]
        iid = meta["id"]
        try:
            df = loader(iid)
            series, asof, reject = prepare_close_frame(df)
            if reject:
                reject_reasons[sym] = reject
                errors.append(f"{sym}:{reject}")
                continue
            metrics = _metrics_from_series(series)
            if metrics is None:
                reject_reasons[sym] = "insufficient_bars_or_low_hv"
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
                "asof_tz": "Asia/Tokyo-calendar-date" if asof else None,
                "price_source": "sina_continuous_daily",
                "scanned_at": scanned_at,
                "scanned_at_tz": "Asia/Tokyo (UTC+9)",
            }
            row["score"] = _r(_score_row(row), 1)
            row["bias_cn"] = _cn_bias(row.get("score"), row.get("rsi14"), side)
            row["action"] = _cn_action(side)
            row["risk"] = _cn_risk(side)
            rows.append(row)
        except Exception as exc:
            logger.debug("cn candidate %s failed: %s", sym, exc)
            errors.append(f"{sym}:{type(exc).__name__}")
            reject_reasons[sym] = type(exc).__name__

    ranked = sorted(rows, key=lambda r: r.get("score") or -1, reverse=True)
    # Keep low-score rows out of the candidate rail (bias 暂不推荐).
    picks = [r for r in ranked if (r.get("score") or 0) >= min_score][:top_n]
    status = "ok" if picks else ("empty" if not ranked else "below_threshold")
    note = None
    if not ranked:
        note = "白名单品种暂无足够连续合约行情，或波动未达筛选阈值；未伪补国内信号。"
    elif not picks:
        note = f"已扫描到方向样本，但均低于研究/观望门槛 score≥{min_score:.0f}；候选栏保持空，不作低分推荐。"
    return {
        "side": side,
        "status": status,
        "updated_at": scanned_at,
        "scanned_at": scanned_at,
        "scanned_at_tz": "Asia/Tokyo (UTC+9)",
        "universe_size": len(CN_OPTIONABLE_CONTINUOUS),
        "scanned": len(ranked),
        "candidates": picks,
        "all": ranked,
        "errors": errors[:20],
        "reject_reasons": reject_reasons,
        "source_notes": SOURCE_NOTES,
        "source_catalog": SOURCE_CATALOG,
        "min_score": min_score,
        "note": note,
        "disclaimer": CN_DISCLAIMER,
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
) -> dict:
    """Attach CN direction candidates. Never merge CN into US picks/all tables."""
    side = "call" if side == "call" else "put"
    out = ensure_cn_candidate_fields(payload, side=side)
    if not populate:
        return out
    try:
        built = build_cn_direction_candidates(side, history_loader=history_loader, top_n=top_n)
        out["cn_candidates"] = built["candidates"]
        out["cn_candidate_meta"] = {
            "side": side,
            "status": built["status"],
            "note": built.get("note"),
            "universe_size": built["universe_size"],
            "scanned": built["scanned"],
            "errors": built.get("errors") or [],
            "reject_reasons": built.get("reject_reasons") or {},
            "source_notes": built.get("source_notes") or [],
            "source_catalog": built.get("source_catalog") or {},
            "updated_at": built.get("updated_at"),
            "scanned_at": built.get("scanned_at"),
            "scanned_at_tz": built.get("scanned_at_tz"),
            "min_score": built.get("min_score"),
            "disclaimer": built.get("disclaimer"),
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
