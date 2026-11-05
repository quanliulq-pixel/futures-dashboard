# -*- coding: utf-8 -*-
"""
Tiered commodity indicators:
basis, calendar spread, industry margins, CFTC, FX, import parity,
hog leading series, inventory days-of-cover, light macro.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

BASIS_VARS = [
    "RB", "HC", "SA", "FG", "UR", "V", "I", "J", "JM", "M", "Y", "P", "C",
    "SR", "CU", "AL", "ZN", "RU", "TA", "MA", "PP", "L", "SI", "LC", "LH",
    "CF", "OI", "RM", "BU", "SC", "AU", "AG", "PX", "A", "B",
]

VAR_TO_ID = {
    "RB": "CN:RB0", "HC": "CN:HC0", "SA": "CN:SA0", "FG": "CN:FG0", "UR": "CN:UR0",
    "V": "CN:V0", "I": "CN:I0", "J": "CN:J0", "JM": "CN:JM0", "M": "CN:M0",
    "Y": "CN:Y0", "P": "CN:P0", "C": "CN:C0", "SR": "CN:SR0", "CU": "CN:CU0",
    "AL": "CN:AL0", "ZN": "CN:ZN0", "RU": "CN:RU0", "TA": "CN:TA0", "MA": "CN:MA0",
    "PP": "CN:PP0", "L": "CN:L0", "SI": "CN:SI0", "LC": "CN:LC0", "LH": "CN:LH0",
    "CF": "CN:CF0", "OI": "CN:OI0", "RM": "CN:RM0", "BU": "CN:BU0", "SC": "CN:SC0",
    "AU": "CN:AU0", "AG": "CN:AG0", "PX": "CN:PX0", "A": "CN:A0", "B": "CN:B0",
}

VAR_NAME = {
    "RB": "螺纹钢", "HC": "热卷", "SA": "纯碱", "FG": "玻璃", "UR": "尿素",
    "V": "PVC", "I": "铁矿石", "J": "焦炭", "JM": "焦煤", "M": "豆粕",
    "Y": "豆油", "P": "棕榈油", "C": "玉米", "SR": "白糖", "CU": "沪铜",
    "AL": "沪铝", "ZN": "沪锌", "RU": "橡胶", "TA": "PTA", "MA": "甲醇",
    "PP": "聚丙烯", "L": "塑料", "SI": "工业硅", "LC": "碳酸锂", "LH": "生猪",
    "CF": "棉花", "OI": "菜油", "RM": "菜粕", "BU": "沥青", "SC": "原油SC",
    "AU": "沪金", "AG": "沪银", "PX": "对二甲苯", "A": "豆一", "B": "豆二",
}

CFTC_MAP = {
    "US:CL=F": ("纽约原油-净仓位", "WTI原油"),
    "US:NG=F": ("纽约天然气-净仓位", "天然气NG"),
    "US:GC=F": ("黄金-净仓位", "COMEX金"),
    "US:SI=F": ("白银-净仓位", "COMEX银"),
    "US:SB=F": ("原糖-净仓位", "原糖"),
    "US:ZS=F": ("大豆-净仓位", "美大豆"),
    "US:ZM=F": ("豆粕-净仓位", "美豆粕"),
    "US:ZL=F": ("豆油-净仓位", "美豆油"),
    "US:ZC=F": ("玉米-净仓位", "美玉米"),
    "US:CT=F": ("棉花-净仓位", "美棉"),
    "US:PL=F": ("铂金-净仓位", "铂金"),
    "US:PA=F": ("钯金-净仓位", "钯金"),
}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _item(**kwargs) -> dict:
    kwargs.setdefault("wow", None)
    kwargs.setdefault("wow_pct", None)
    kwargs.setdefault("d4w", None)
    kwargs.setdefault("d4w_pct", None)
    kwargs.setdefault("extra", {})
    return kwargs


def _change_vs(series: pd.Series, days: int) -> float | None:
    """Change vs value ~`days` calendar days ago on a datetime-indexed series."""
    if series is None or series.empty:
        return None
    s = series.dropna()
    if len(s) < 2:
        return None
    last = float(s.iloc[-1])
    last_dt = s.index[-1]
    if hasattr(last_dt, "tz") and last_dt.tz is not None:
        s = s.copy()
        s.index = s.index.tz_localize(None)
        last_dt = s.index[-1]
    target = last_dt - pd.Timedelta(days=days)
    prior = s[s.index <= target]
    if prior.empty:
        step = max(1, round(days * 5 / 7))
        if len(s) > step:
            return last - float(s.iloc[-step - 1])
        return None
    return last - float(prior.iloc[-1])


def _recent_trade_dates(n: int = 8) -> list[str]:
    out = []
    d = datetime.now()
    for _ in range(n * 2):
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
        if len(out) >= n:
            break
    return out


def fetch_basis_and_spreads() -> list[dict]:
    import akshare as ak

    df = None
    used = None
    for d in _recent_trade_dates(8):
        try:
            cand = ak.futures_spot_price(date=d, vars_list=BASIS_VARS)
            if cand is not None and len(cand):
                df = cand
                used = d
                break
        except Exception as exc:
            logger.debug("basis %s: %s", d, exc)
    if df is None or df.empty:
        return []

    rows = []
    by_sym = {}
    for _, r in df.iterrows():
        sym = str(r.get("symbol", "")).upper()
        by_sym[sym] = r
        iid = VAR_TO_ID.get(sym)
        name = VAR_NAME.get(sym, sym)
        spot = _num(r.get("spot_price"))
        near_px = _num(r.get("near_contract_price"))
        dom_px = _num(r.get("dominant_contract_price"))
        near_b = _num(r.get("near_basis"))
        dom_b = _num(r.get("dom_basis"))
        near_br = _num(r.get("near_basis_rate"))
        cal = (near_px - dom_px) if near_px is not None and dom_px is not None else None

        if near_br is not None and near_br > 0.01:
            bsig = "现货升水偏紧"
        elif near_br is not None and near_br < -0.03:
            bsig = "现货深贴水偏松"
        elif near_br is not None and near_br < 0:
            bsig = "现货小贴水"
        else:
            bsig = "基差中性"

        if cal is not None and near_px and cal / near_px > 0.01:
            csig = "近月升水(back)"
        elif cal is not None and near_px and cal / near_px < -0.01:
            csig = "近月贴水(contango)"
        else:
            csig = "月差平坦"

        rows.append(
            _item(
                kind="basis",
                id=iid,
                name=name,
                metric="基差(现货-近月)",
                value=near_b,
                unit="元",
                asof=used,
                signal=bsig,
                source="生意社/100ppi",
                extra={
                    "spot": spot,
                    "near": r.get("near_contract"),
                    "near_px": near_px,
                    "dom": r.get("dominant_contract"),
                    "dom_px": dom_px,
                    "near_basis_rate": near_br,
                    "dom_basis": dom_b,
                },
            )
        )
        rows.append(
            _item(
                kind="spread",
                id=iid,
                name=name,
                metric="月差(近月-主力)",
                value=cal,
                unit="元",
                asof=used,
                signal=csig,
                source="生意社近月/主力",
                extra={"near": r.get("near_contract"), "dom": r.get("dominant_contract")},
            )
        )

    # industry margins from the same spot table
    rows.extend(_margins_from_spots(by_sym, used))
    return rows


def _margins_from_spots(by_sym: dict, asof: str | None) -> list[dict]:
    rows = []

    def spot(sym: str) -> float | None:
        r = by_sym.get(sym)
        if r is None:
            return None
        return _num(r.get("spot_price")) or _num(r.get("dominant_contract_price"))

    rb, iore, jc = spot("RB"), spot("I"), spot("J")
    if rb and iore and jc:
        # 粗估长流程：1吨螺纹约 1.6 吨矿 + 0.45 吨焦
        # 矿+焦后还要扣烧结/合金/人工等，用 1000 作其他成本残差
        other = 1000.0
        cost = 1.6 * iore + 0.45 * jc + other
        profit = rb - cost
        rows.append(
            _item(
                kind="margin",
                id="CN:RB0",
                name="螺纹钢",
                metric="长流程吨钢利润(估)",
                value=profit,
                unit="元/吨",
                asof=asof,
                signal="利润为负偏减产" if profit < 0 else ("利润偏厚" if profit > 250 else "利润中性"),
                source="现货: RB-1.6I-0.45J-1000其他",
                extra={"rb": rb, "ore": iore, "coke": jc, "cost": cost},
            )
        )

    hc = spot("HC")
    if hc and iore and jc:
        other = 1000.0
        cost = 1.6 * iore + 0.45 * jc + other
        profit = hc - cost
        rows.append(
            _item(
                kind="margin",
                id="CN:HC0",
                name="热卷",
                metric="长流程吨钢利润(估)",
                value=profit,
                unit="元/吨",
                asof=asof,
                signal="利润为负偏减产" if profit < 0 else ("利润偏厚" if profit > 250 else "利润中性"),
                source="现货: HC-1.6I-0.45J-1000其他",
            )
        )

    ta, px = spot("TA"), spot("PX")
    if ta and px:
        # 1吨 PTA 约 0.655 吨 PX，加工费≈ PTA - 0.655*PX
        fee = ta - 0.655 * px
        rows.append(
            _item(
                kind="margin",
                id="CN:TA0",
                name="PTA",
                metric="PTA加工费(估)",
                value=fee,
                unit="元/吨",
                asof=asof,
                signal="加工费偏低" if fee < 200 else ("加工费偏厚" if fee > 500 else "加工费中性"),
                source="TA-0.655*PX",
            )
        )

    m, y = spot("M"), spot("Y")
    b = spot("B")
    if b is None:
        try:
            from data_sources import get_cached_quote_price, get_daily_history

            b = get_cached_quote_price("CN:B0")
            if b is None:
                df_b = get_daily_history("CN:B0")
                if df_b is not None and not df_b.empty:
                    b = _num(df_b["close"].iloc[-1])
        except Exception:
            b = None
    if m and y and b:
        # 国内进口大豆压榨盘面利润粗估：1吨进口豆(豆二B)≈0.785吨豆粕+0.185吨豆油-150加工费
        crush = 0.785 * m + 0.185 * y - b - 150.0
        rows.append(
            _item(
                kind="margin",
                id="CN:M0",
                name="大豆压榨",
                metric="国内压榨利润(估)",
                value=crush,
                unit="元/吨豆",
                asof=asof,
                signal="压榨亏损" if crush < 0 else ("压榨偏厚" if crush > 200 else "压榨中性"),
                source="0.785粕+0.185油-豆二B-150加工费",
                extra={"meal": m, "oil": y, "soy_b": b},
            )
        )
    return rows


def fetch_us_margins_and_fx() -> tuple[list[dict], float | None]:
    import yfinance as yf

    rows: list[dict] = []
    fx = None
    try:
        h = yf.Ticker("USDCNY=X").history(period="2mo")
        if h is not None and not h.empty:
            s_fx = h["Close"].dropna().astype(float)
            if not s_fx.empty:
                fx = float(s_fx.iloc[-1])
                wow_fx = _change_vs(s_fx, 7)
                d4w_fx = _change_vs(s_fx, 28)
                rows.append(
                    _item(
                        kind="fx",
                        id=None,
                        name="美元人民币",
                        metric="USD/CNY",
                        value=fx,
                        unit="",
                        asof=str(s_fx.index[-1].date()),
                        wow=wow_fx,
                        d4w=d4w_fx,
                        signal="人民币贬值抬进口成本" if wow_fx and wow_fx > 0 else "汇率中性/升值",
                        source="Yahoo USDCNY=X",
                    )
                )
    except Exception as exc:
        logger.warning("fx fail: %s", exc)

    tickers = ["CL=F", "HO=F", "RB=F", "ZS=F", "ZM=F", "ZL=F"]
    series_map: dict[str, pd.Series] = {}
    try:
        raw = yf.download(tickers, period="2mo", group_by="ticker", auto_adjust=True, threads=True, progress=False)
        for t in tickers:
            try:
                if len(tickers) == 1:
                    close = raw["Close"].dropna()
                else:
                    if t not in raw.columns.get_level_values(0):
                        continue
                    close = raw[t]["Close"].dropna()
                if not close.empty:
                    series_map[t] = close.astype(float)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("us margin download fail: %s", exc)

    cl_s, ho_s, rb_s = series_map.get("CL=F"), series_map.get("HO=F"), series_map.get("RB=F")
    if cl_s is not None and ho_s is not None and rb_s is not None:
        # 3-2-1 crack: (2*RB*42 + HO*42 - 3*CL) / 3
        crack_s = ((2 * rb_s * 42 + ho_s * 42 - 3 * cl_s) / 3).dropna()
        if not crack_s.empty:
            crack = float(crack_s.iloc[-1])
            rows.append(
                _item(
                    kind="margin",
                    id="US:CL=F",
                    name="WTI裂解",
                    metric="3-2-1裂解价差",
                    value=crack,
                    unit="USD/桶",
                    asof=str(crack_s.index[-1].date()),
                    wow=_change_vs(crack_s, 7),
                    d4w=_change_vs(crack_s, 28),
                    signal="裂解偏弱" if crack < 15 else ("裂解偏厚" if crack > 30 else "裂解中性"),
                    source="2*RBOB*42+HO*42-3*CL",
                )
            )

    zs_s, zm_s, zl_s = series_map.get("ZS=F"), series_map.get("ZM=F"), series_map.get("ZL=F")
    if zs_s is not None and zm_s is not None and zl_s is not None:
        # USD/bu: meal $/st *0.022 + oil c/lb *0.11 - soy $/bu
        crush_s = (zm_s * 0.022 + zl_s * 0.11 - zs_s / 100.0).dropna()
        if not crush_s.empty:
            crush = float(crush_s.iloc[-1])
            rows.append(
                _item(
                    kind="margin",
                    id="US:ZS=F",
                    name="美豆压榨",
                    metric="CBOT压榨利润",
                    value=crush,
                    unit="USD/蒲式耳",
                    asof=str(crush_s.index[-1].date()),
                    wow=_change_vs(crush_s, 7),
                    d4w=_change_vs(crush_s, 28),
                    signal="压榨亏损" if crush < 0 else ("压榨偏厚" if crush > 1.5 else "压榨中性"),
                    source="0.022*粕+0.11*油-豆价",
                )
            )
    return rows, fx


def fetch_import_parity(fx: float | None) -> list[dict]:
    import yfinance as yf

    if not fx:
        return []
    rows = []
    # (us_id, cn_id, name, metric, unit, note)
    specs = [
        ("US:CL=F", "CN:SC0", "WTI原油", "WTI折SC保税平价", "元/桶", "(WTI+2.5运保)×汇率（SC为保税净价）"),
        ("US:BZ=F", "CN:SC0", "布伦特原油", "布伦特折SC保税平价", "元/桶", "(Brent+2.0运保)×汇率（SC为保税净价）"),
        ("US:HG=F", "CN:CU0", "COMEX铜", "COMEX铜进口完税平价", "元/吨", "(USD/磅×2204.62+60溢价)×汇率×1.13增值税"),
        ("US:ZM=F", "CN:M0", "美豆粕", "美豆粕进口完税平价", "元/吨", "(USD/短吨×1.1023+45运费)×汇率×1.09增值税"),
        ("US:SB=F", "CN:SR0", "原糖", "原糖配额外进口成本(估)", "元/吨", "[(美分/100×2204.62+35运费)×汇率×1.50关税×1.13增值税]+450加工"),
        ("US:ZC=F", "CN:C0", "美玉米", "美玉米配额内完税平价", "元/吨", "[(美分/100×39.368+55基差运费)×汇率×1.01关税×1.09增值税]+80港杂"),
    ]
    try:
        raw = yf.download(
            [s[0].split(":", 1)[1] for s in specs],
            period="5d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as exc:
        logger.warning("parity download fail: %s", exc)
        return []

    def last_px_and_dt(symbol: str) -> tuple[float | None, str]:
        try:
            if symbol not in raw.columns.get_level_values(0):
                return None, datetime.now().strftime("%Y-%m-%d")
            s = raw[symbol]["Close"].dropna()
            if s.empty:
                return None, datetime.now().strftime("%Y-%m-%d")
            dt = str(s.index[-1].date()) if hasattr(s.index[-1], "date") else str(s.index[-1])[:10]
            return float(s.iloc[-1]), dt
        except Exception:
            return None, datetime.now().strftime("%Y-%m-%d")

    for us_id, cn_id, name, metric, unit, note in specs:
        us_sym = us_id.split(":", 1)[1]
        px, asof_dt = last_px_and_dt(us_sym)
        if px is None:
            continue
        extra_info: dict[str, Any] = {"fx": fx, "us_price": px, "us_id": us_id, "link_id": cn_id}
        if us_sym == "CL=F":
            # SC0 is bonded (net of VAT/tariff); add ~$2.5/bbl freight & insurance
            parity = (px + 2.5) * fx
            extra_info["fob_cny"] = px * fx
        elif us_sym == "BZ=F":
            parity = (px + 2.0) * fx
            extra_info["fob_cny"] = px * fx
        elif us_sym == "HG=F":
            # CU0 includes 13% VAT; 0% import tariff on cathode; ~$60/t premium + freight
            fob_usd = px * 2204.6226
            parity = (fob_usd + 60.0) * fx * 1.13
            extra_info["fob_cny"] = fob_usd * fx
        elif us_sym == "ZM=F":
            # M0 includes 9% VAT; ~$45/t freight
            fob_usd = px * 1.10231
            parity = (fob_usd + 45.0) * fx * 1.09
            extra_info["fob_cny"] = fob_usd * fx
        elif us_sym == "SB=F":
            # SR0 marginal import anchor is out-of-quota (50% tariff, 13% VAT, +450 refining)
            # Also record in-quota (15% tariff) in extra
            cnf_usd = (px / 100.0) * 2204.6226 + 35.0
            in_quota = cnf_usd * fx * 1.15 * 1.13 + 450.0
            out_quota = cnf_usd * fx * 1.50 * 1.13 + 450.0
            parity = out_quota
            extra_info["in_quota_parity"] = round(in_quota, 1)
            extra_info["fob_cny"] = (px / 100.0) * 2204.6226 * fx
        elif us_sym == "ZC=F":
            # C0 in-quota (1% tariff, 9% VAT, ~$55 basis+freight, +80 port)
            cnf_usd = (px / 100.0) * 39.36825 + 55.0
            parity = cnf_usd * fx * 1.01 * 1.09 + 80.0
            extra_info["fob_cny"] = (px / 100.0) * 39.36825 * fx
        else:
            parity = px * fx
        rows.append(
            _item(
                kind="parity",
                id=cn_id,
                name=name,
                metric=metric,
                value=parity,
                unit=unit,
                asof=asof_dt,
                signal="仅平价、待对照内盘",
                source=note,
                extra=extra_info,
            )
        )
    return rows


def fetch_cftc() -> list[dict]:
    import akshare as ak

    try:
        df = ak.macro_usa_cftc_merchant_goods_holding()
    except Exception as exc:
        logger.warning("cftc fail: %s", exc)
        return []
    if df is None or df.empty:
        return []
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    asof = str(last.get("日期"))[:10]
    rows = []
    for iid, (col, name) in CFTC_MAP.items():
        if col not in df.columns:
            continue
        v = _num(last.get(col))
        pv = _num(prev.get(col)) if prev is not None else None
        if v is None:
            continue
        if v > 50000:
            sig = "商业净多偏多"
        elif v < -50000:
            sig = "商业净空(基金或偏多)"
        else:
            sig = "持仓中性"
        # merchant net: commercial hedging. Negative commercial often means specs long.
        rows.append(
            _item(
                kind="cot",
                id=iid,
                name=name,
                metric="CFTC商业净持仓",
                value=v,
                unit="手",
                asof=asof,
                wow=(v - pv) if pv is not None else None,
                signal=sig,
                source="CFTC商品商业持仓(金十)",
            )
        )
    return rows


def fetch_cn_positions() -> list[dict]:
    """Top-member long/short for a few liquid domestic commodities."""
    import akshare as ak

    want = ["RB", "SA", "FG", "I", "M", "CU", "LH", "V"]
    spot = None
    used = None
    for d in _recent_trade_dates(6):
        try:
            cand = ak.futures_spot_price(date=d, vars_list=want)
            if cand is not None and len(cand):
                spot = cand
                used = d
                break
        except Exception:
            continue
    if spot is None:
        return []

    rows = []
    for _, r in spot.iterrows():
        sym = str(r.get("symbol", "")).upper()
        contract = str(r.get("dominant_contract") or "").strip()
        if not contract:
            continue
        try:
            lg = ak.futures_hold_pos_sina(symbol="多单持仓", contract=contract, date=used)
            sh = ak.futures_hold_pos_sina(symbol="空单持仓", contract=contract, date=used)
        except Exception as exc:
            logger.debug("pos %s: %s", contract, exc)
            continue
        if lg is None or sh is None or lg.empty or sh.empty:
            continue
        # Exclude summary rows (e.g., "合计" / "总计") before taking top 5 seats
        lg_clean = lg[~lg.iloc[:, 0].astype(str).str.contains("合计|总计", na=False)]
        if "会员简称" in lg_clean.columns:
            lg_clean = lg_clean[~lg_clean["会员简称"].astype(str).str.contains("合计|总计", na=False)]
        sh_clean = sh[~sh.iloc[:, 0].astype(str).str.contains("合计|总计", na=False)]
        if "会员简称" in sh_clean.columns:
            sh_clean = sh_clean[~sh_clean["会员简称"].astype(str).str.contains("合计|总计", na=False)]

        long_top = float(pd.to_numeric(lg_clean["多单持仓"], errors="coerce").head(5).sum())
        short_top = float(pd.to_numeric(sh_clean["空单持仓"], errors="coerce").head(5).sum())
        net = long_top - short_top
        dlong = float(pd.to_numeric(lg_clean["比上交易增减"], errors="coerce").head(5).sum())
        dshort = float(pd.to_numeric(sh_clean["比上交易增减"], errors="coerce").head(5).sum())
        dnet = dlong - dshort
        if net > 0 and dnet > 0:
            sig = "席位净多且净多增加"
        elif net < 0 and dnet < 0:
            sig = "席位净空且净空扩大"
        elif net > 0:
            sig = "前5席位净多(净多收敛)"
        else:
            sig = "前5席位净空(净空收敛)"
        rows.append(
            _item(
                kind="seat",
                id=VAR_TO_ID.get(sym),
                name=VAR_NAME.get(sym, sym),
                metric="前5席位净持仓(日变动)",
                value=net,
                unit="手",
                asof=used,
                wow=dnet,
                signal=sig,
                source=f"新浪持仓 {contract}",
                extra={"contract": contract, "long5": long_top, "short5": short_top, "dlong5": dlong, "dshort5": dshort},
            )
        )
    return rows


def fetch_house_price() -> list[dict]:
    import akshare as ak

    try:
        df = ak.macro_china_new_house_price()
    except Exception as exc:
        logger.warning("house fail: %s", exc)
        return []
    if df is None or df.empty or "日期" not in df.columns:
        return []
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    latest = df["日期"].max()
    sub = df[df["日期"] == latest]
    col = "新建商品住宅价格指数-同比"
    if col not in sub.columns:
        return []
    avg = pd.to_numeric(sub[col], errors="coerce").mean()
    # 指数 100=持平，96.5 ≈ 同比 -3.5%
    yoy = avg - 100.0 if avg and avg > 50 else avg
    return [
        _item(
            kind="macro",
            id="CN:RB0",
            name="70城新房价格",
            metric="新建住宅均价同比(指数-100)",
            value=yoy,
            unit="个百分点",
            asof=str(latest.date()),
            signal="房价同比仍弱" if yoy is not None and yoy < 0 else "房价同比转正",
            source="70城新建商品住宅价格指数",
        )
    ]


def fetch_hog_leading() -> list[dict]:
    import akshare as ak

    rows = []
    try:
        df = ak.futures_hog_cost(symbol="仔猪价格")
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            s = pd.to_numeric(df.set_index("date")["value"], errors="coerce").dropna()
            if not s.empty:
                v = float(s.iloc[-1])
                wow = _change_vs(s, 7)
                d4w = _change_vs(s, 28)
                rows.append(
                    _item(
                        kind="hog",
                        id="CN:LH0",
                        name="仔猪价格",
                        metric="仔猪价格",
                        value=v,
                        unit="元/公斤",
                        asof=str(s.index[-1].date()),
                        wow=wow,
                        d4w=d4w,
                        signal="仔猪价下行偏去产能" if wow is not None and wow < 0 else "仔猪价平稳/回升",
                        source="玄田成本维度",
                    )
                )
    except Exception as exc:
        logger.warning("piglet fail: %s", exc)

    try:
        df = ak.index_hog_spot_price()
        if df is not None and not df.empty:
            df = df.copy()
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"]).sort_values("日期")
            last = df.iloc[-1]
            s_idx = pd.to_numeric(df.set_index("日期")["指数"], errors="coerce").dropna()
            v = _num(last.get("指数"))
            wow = _change_vs(s_idx, 7) if not s_idx.empty else None
            d4w = _change_vs(s_idx, 28) if not s_idx.empty else None
            rows.append(
                _item(
                    kind="hog",
                    id="CN:LH0",
                    name="生猪指数",
                    metric="行情宝生猪指数",
                    value=v,
                    unit="",
                    asof=str(last.get("日期"))[:10],
                    wow=wow,
                    d4w=d4w,
                    extra={"成交均价": _num(last.get("成交均价")), "成交均重": _num(last.get("成交均重"))},
                    signal="均重偏高增供" if _num(last.get("成交均重")) and _num(last.get("成交均重")) > 130 else "体重中性",
                    source="行情宝",
                )
            )
    except Exception as exc:
        logger.warning("hog index fail: %s", exc)
    return rows


def fetch_macro() -> list[dict]:
    import akshare as ak

    rows = []
    try:
        pmi = ak.macro_china_pmi_yearly()
        if pmi is not None and not pmi.empty:
            # typically date + value
            col = pmi.columns[-1]
            v = _num(pmi.iloc[-1][col])
            asof = None
            for c in pmi.columns:
                if "日期" in str(c) or "date" in str(c).lower() or "时间" in str(c):
                    asof = str(pmi.iloc[-1][c])[:10]
                    break
            if not asof:
                idx = pmi.index[-1]
                asof = str(idx)[:10] if not isinstance(idx, (int, float)) else None
            rows.append(
                _item(
                    kind="macro",
                    id=None,
                    name="官方制造业PMI",
                    metric="中国官方制造业PMI",
                    value=v,
                    unit="",
                    asof=asof or "最新一期",
                    signal="荣枯线下需求弱" if v is not None and v < 50 else "荣枯线上",
                    source="金十/官方PMI",
                )
            )
    except Exception as exc:
        logger.warning("pmi fail: %s", exc)
    return rows


def enrich_inventory_days(items: list[dict]) -> list[dict]:
    """Add warehouse-receipt depletion weeks proxy only when 4-week destocking is meaningful."""
    extra = []
    for it in items:
        if it.get("kind") != "inventory":
            continue
        inv = it.get("value")
        d4 = it.get("d4w")
        # Require meaningful 4-week destock (at least 3% of current warehouse receipts)
        # so near-flat receipts don't divide by ~0 and produce 100+ weeks "库存偏厚" false signals
        if not inv or d4 is None or d4 >= -max(abs(inv) * 0.03, 1.0):
            continue
        weekly = abs(d4) / 4.0
        if weekly <= 0:
            continue
        weeks = inv / weekly
        extra.append(
            _item(
                kind="cover",
                id=it.get("id"),
                name=it.get("name"),
                metric="仓单去化周数(按近4周速度)",
                value=weeks,
                unit="周",
                asof=it.get("asof"),
                signal="去库较快(≤6周)" if weeks <= 6 else ("去库温和" if weeks <= 16 else "去库偏慢"),
                source="仓单余量/近4周周均注销速度",
            )
        )
    return extra


def fetch_all_indicators() -> list[dict]:
    items: list[dict] = []
    for fn, label in (
        (fetch_basis_and_spreads, "basis"),
        (fetch_cftc, "cftc"),
        (fetch_hog_leading, "hog-lead"),
        (fetch_macro, "macro"),
        (fetch_cn_positions, "seats"),
        (fetch_house_price, "house"),
    ):
        try:
            items.extend(fn())
        except Exception:
            logger.exception("%s failed", label)

    fx = None
    try:
        us_rows, fx = fetch_us_margins_and_fx()
        items.extend(us_rows)
    except Exception:
        logger.exception("us margins/fx failed")

    try:
        items.extend(fetch_import_parity(fx))
    except Exception:
        logger.exception("parity failed")

    return items
