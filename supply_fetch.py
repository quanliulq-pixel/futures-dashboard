# -*- coding: utf-8 -*-
"""Weekly fetch of warehouse receipts, hog capacity, and EIA gas storage."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data" / "supply"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"

# Eastmoney inventory symbols (仓单/库存, 周频可用)
INVENTORY_WATCH = [
    {"id": "CN:RB0", "em": "rb", "name": "螺纹钢", "unit": "手/吨口径"},
    {"id": "CN:HC0", "em": "hc", "name": "热卷", "unit": "手/吨口径"},
    {"id": "CN:SA0", "em": "SA", "name": "纯碱", "unit": "手/吨口径"},
    {"id": "CN:FG0", "em": "FG", "name": "玻璃", "unit": "手/吨口径"},
    {"id": "CN:UR0", "em": "UR", "name": "尿素", "unit": "手/吨口径"},
    {"id": "CN:V0", "em": "v", "name": "PVC", "unit": "手/吨口径"},
    {"id": "CN:SI0", "em": "si", "name": "工业硅", "unit": "手/吨口径"},
    {"id": "CN:LC0", "em": "lc", "name": "碳酸锂", "unit": "手/吨口径"},
    {"id": "CN:I0", "em": "i", "name": "铁矿石", "unit": "手/吨口径"},
    {"id": "CN:J0", "em": "j", "name": "焦炭", "unit": "手/吨口径"},
    {"id": "CN:JM0", "em": "jm", "name": "焦煤", "unit": "手/吨口径"},
    {"id": "CN:M0", "em": "m", "name": "豆粕", "unit": "手/吨口径"},
    {"id": "CN:C0", "em": "c", "name": "玉米", "unit": "手/吨口径"},
    {"id": "CN:SR0", "em": "SR", "name": "白糖", "unit": "手/吨口径"},
    {"id": "CN:LH0", "em": "lh", "name": "生猪仓单", "unit": "手/吨口径"},
    {"id": "CN:CU0", "em": "cu", "name": "沪铜", "unit": "手/吨口径"},
    {"id": "CN:AL0", "em": "al", "name": "沪铝", "unit": "手/吨口径"},
    {"id": "CN:PP0", "em": "pp", "name": "聚丙烯", "unit": "手/吨口径"},
]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_str(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v)[:10]
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else str(v)


def _change_vs(series: pd.Series, days: int) -> float | None:
    if series is None or series.empty:
        return None
    last = float(series.iloc[-1])
    last_dt = series.index[-1]
    target = last_dt - pd.Timedelta(days=days)
    prior = series[series.index <= target]
    if prior.empty:
        if len(series) > 4:
            prev = float(series.iloc[-5])
            return last - prev
        return None
    return last - float(prior.iloc[-1])


def _pct(delta: float | None, base: float | None) -> float | None:
    if delta is None or base in (None, 0):
        return None
    return delta / base * 100


def fetch_inventory() -> list[dict]:
    import akshare as ak

    rows = []
    for item in INVENTORY_WATCH:
        try:
            df = ak.futures_inventory_em(symbol=item["em"])
            if df is None or df.empty:
                continue
            df = df.copy()
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期")
            s = df.set_index("日期")["库存"].astype(float)
            last = float(s.iloc[-1])
            d1 = _change_vs(s, 7)
            d4 = _change_vs(s, 28)
            # 4-week direction for cut/de-stock reading
            if d4 is not None and d4 < -max(abs(last) * 0.03, 1):
                signal = "去库"
            elif d4 is not None and d4 > max(abs(last) * 0.03, 1):
                signal = "累库"
            else:
                signal = "持平"
            rows.append(
                {
                    "kind": "inventory",
                    "id": item["id"],
                    "name": item["name"],
                    "metric": "期货库存/仓单",
                    "value": last,
                    "unit": item["unit"],
                    "asof": _date_str(s.index[-1]),
                    "wow": d1,
                    "wow_pct": _pct(d1, last - (d1 or 0)),
                    "d4w": d4,
                    "d4w_pct": _pct(d4, last - (d4 or 0)),
                    "signal": signal,
                    "source": "东方财富期货库存",
                }
            )
        except Exception as exc:
            logger.warning("inventory fail %s: %s", item["em"], exc)
    return rows


def fetch_hog() -> list[dict]:
    import akshare as ak

    rows: list[dict] = []

    try:
        df = ak.futures_hog_supply(symbol="生猪产能")
        if df is not None and not df.empty:
            last = df.iloc[-1]
            sow = _num(last.get("能繁母猪存栏"))
            # previous non-null sow
            prev_sow = None
            for i in range(len(df) - 2, -1, -1):
                v = _num(df.iloc[i].get("能繁母猪存栏"))
                if v:
                    prev_sow = v
                    break
            delta = (sow - prev_sow) if sow and prev_sow else None
            if delta is not None and delta < -10:
                signal = "能繁去化"
            elif delta is not None and delta > 10:
                signal = "能繁回升"
            else:
                signal = "能繁持平"
            rows.append(
                {
                    "kind": "hog",
                    "id": "CN:LH0",
                    "name": "能繁母猪",
                    "metric": "能繁母猪存栏",
                    "value": sow,
                    "unit": "万头",
                    "asof": str(last.get("周期")),
                    "wow": None,
                    "wow_pct": None,
                    "d4w": delta,
                    "d4w_pct": _pct(delta, prev_sow),
                    "signal": signal,
                    "source": "玄田/统计局口径(akshare 生猪产能)",
                    "extra": {
                        "猪肉产量": _num(last.get("猪肉产量")),
                        "生猪存栏": _num(last.get("生猪存栏")),
                        "生猪出栏": _num(last.get("生猪出栏")),
                        "prev_sow": prev_sow,
                        "period": str(last.get("周期")),
                    },
                }
            )
    except Exception as exc:
        logger.warning("hog capacity fail: %s", exc)

    try:
        df = ak.futures_hog_supply(symbol="猪粮比价")
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            s = pd.to_numeric(df.set_index("date")["value"], errors="coerce").dropna()
            if not s.empty:
                last = float(s.iloc[-1])
                wow = _change_vs(s, 7)
                d4w = _change_vs(s, 28)
                # 传统盈亏线约 6；低于 5 深度亏损
                if last < 5:
                    signal = "深度亏损"
                elif last < 6:
                    signal = "亏损"
                else:
                    signal = "覆盖成本"
                rows.append(
                    {
                        "kind": "hog",
                        "id": "CN:LH0",
                        "name": "猪粮比",
                        "metric": "猪粮比价",
                        "value": last,
                        "unit": "倍",
                        "asof": _date_str(s.index[-1]),
                        "wow": wow,
                        "wow_pct": _pct(wow, last - (wow or 0)),
                        "d4w": d4w,
                        "d4w_pct": _pct(d4w, last - (d4w or 0)),
                        "signal": signal,
                        "source": "玄田猪粮比",
                    }
                )
    except Exception as exc:
        logger.warning("hog ratio fail: %s", exc)

    try:
        df = ak.futures_hog_core(symbol="外三元")
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            s = pd.to_numeric(df.set_index("date")["value"], errors="coerce").dropna()
            if not s.empty:
                last = float(s.iloc[-1])
                wow = _change_vs(s, 7)
                d4w = _change_vs(s, 28)
                # 对照行业完全成本约 12.5 元/kg、龙头 11.5
                if last < 11.5:
                    signal = "低于龙头完全成本"
                elif last < 12.5:
                    signal = "低于行业完全成本"
                else:
                    signal = "覆盖行业成本"
                rows.append(
                    {
                        "kind": "hog",
                        "id": "CN:LH0",
                        "name": "外三元猪价",
                        "metric": "外三元现货",
                        "value": last,
                        "unit": "元/公斤",
                        "asof": _date_str(s.index[-1]),
                        "wow": wow,
                        "wow_pct": _pct(wow, last - (wow or 0)),
                        "d4w": d4w,
                        "d4w_pct": _pct(d4w, last - (d4w or 0)),
                        "signal": signal,
                        "source": "玄田核心数据",
                    }
                )
    except Exception as exc:
        logger.warning("hog spot fail: %s", exc)

    try:
        df = ak.futures_hog_cost(symbol="二元母猪价格")
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
                    {
                        "kind": "hog",
                        "id": "CN:LH0",
                        "name": "二元母猪价",
                        "metric": "二元母猪价格",
                        "value": v,
                        "unit": "元/公斤",
                        "asof": _date_str(s.index[-1]),
                        "wow": wow,
                        "wow_pct": _pct(wow, v - (wow or 0)),
                        "d4w": d4w,
                        "d4w_pct": _pct(d4w, v - (d4w or 0)),
                        "signal": "母猪价下行偏去化" if wow is not None and wow < 0 else "母猪价平稳/回升",
                        "source": "玄田成本维度",
                    }
                )
    except Exception as exc:
        logger.warning("sow price fail: %s", exc)

    return rows


def fetch_ng_storage() -> list[dict]:
    import requests

    try:
        r = requests.get("https://ir.eia.gov/ngs/wngsr.txt", timeout=20)
        r.raise_for_status()
        text = r.content.decode("utf-8", errors="ignore")
        # strip BOM
        text = text.lstrip("\ufeff")
        total = re.search(r"Total \((\d{2}/\d{2}/\d{2})\):\s*([\d,]+)\s*Bcf", text)
        net = re.search(r"Net change:\s*([-\d,]+)\s*Bcf", text)
        yoy = re.search(r"% change from year ago:\s*([-\d.]+)", text)
        avg5 = re.search(r"% change from 5-year avg:\s*([-\d.]+)", text)
        if not total:
            return []
        asof_raw, val = total.group(1), float(total.group(2).replace(",", ""))
        # mm/dd/yy
        mm, dd, yy = asof_raw.split("/")
        year = 2000 + int(yy)
        asof = f"{year:04d}-{int(mm):02d}-{int(dd):02d}"
        wow = float(net.group(1).replace(",", "")) if net else None
        vs5 = float(avg5.group(1)) if avg5 else None
        if vs5 is not None and vs5 > 5:
            signal = "库存偏多"
        elif vs5 is not None and vs5 < -5:
            signal = "库存偏紧"
        else:
            signal = "库存中性"
        return [
            {
                "kind": "ng",
                "id": "US:NG=F",
                "name": "天然气库存",
                "metric": "EIA 美国地下库存",
                "value": val,
                "unit": "Bcf",
                "asof": asof,
                "wow": wow,
                "wow_pct": None,
                "d4w": None,
                "d4w_pct": None,
                "signal": signal,
                "source": "EIA Weekly NG Storage",
                "extra": {
                    "yoy_pct": float(yoy.group(1)) if yoy else None,
                    "vs_5y_pct": vs5,
                },
            }
        ]
    except Exception as exc:
        logger.warning("EIA NG storage fail: %s", exc)
        return []


def overlay_parity_vs_quotes(items: list[dict], allow_fetch: bool = True) -> None:
    """Compare import parity items against domestic contract prices and update signal.

    allow_fetch=False skips the per-item history download (used in request
    handlers on Render so no upstream I/O happens in the request thread)."""
    if not items:
        return
    try:
        from data_sources import get_daily_history, get_quotes

        quotes = {q["id"]: q.get("price") for q in get_quotes() if q.get("price") is not None}
        for it in items:
            if it.get("kind") != "parity":
                continue
            iid = it.get("id")
            if not iid:
                continue
            px = quotes.get(iid)
            if px is None and allow_fetch:
                try:
                    df = get_daily_history(iid)
                    if df is not None and not df.empty:
                        px = _num(df["close"].iloc[-1])
                except Exception:
                    px = None
            par = it.get("value")
            if px and par:
                gap = (px / par - 1.0) * 100.0
                it["extra"] = {**(it.get("extra") or {}), "cn_price": px, "vs_parity_pct": round(gap, 2)}
                if gap > 5:
                    it["signal"] = f"内盘高于平价{gap:.1f}%（进口有窗口）"
                elif gap < -5:
                    it["signal"] = f"内盘低于平价{gap:.1f}%（进口倒挂）"
                else:
                    it["signal"] = f"贴近进口平价({gap:+.1f}%)"
    except Exception:
        logger.debug("parity overlay skipped", exc_info=True)


def fetch_all() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    errors: list[str] = []
    for fn, label in (
        (fetch_inventory, "inventory"),
        (fetch_hog, "hog"),
        (fetch_ng_storage, "ng"),
    ):
        try:
            items.extend(fn())
        except Exception as exc:
            logger.exception("%s fetch crashed", label)
            errors.append(f"{label}: {exc}")

    try:
        from indicators import enrich_inventory_days, fetch_all_indicators

        items.extend(fetch_all_indicators())
        items.extend(enrich_inventory_days(items))
    except Exception as exc:
        logger.exception("indicators fetch crashed")
        errors.append(f"indicators: {exc}")

    overlay_parity_vs_quotes(items)

    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "week": date.today().isocalendar()[0] * 100 + date.today().isocalendar()[1],
        "items": items,
        "errors": errors,
        "note": "含基差/月差/利润/COT/进口平价/汇率/仓单/能繁。开工率无稳定免费源。利润与平价为估算。",
    }
    LATEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    logger.info("Supply snapshot saved: %d items", len(items))
    return payload


def load_latest() -> dict | None:
    if not LATEST_PATH.exists():
        return None
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_stale(hours: int = 168) -> bool:
    data = load_latest()
    if not data or not data.get("fetched_at"):
        return True
    try:
        ts = datetime.fromisoformat(data["fetched_at"])
    except ValueError:
        return True
    return datetime.now() - ts > timedelta(hours=hours)


def maybe_weekly_fetch(force: bool = False, force_if_stale_hours: int = 168) -> dict | None:
    """
    Fetch if forced, stale, or a new ISO week and weekday is Tue–Sun
    (Mon data often incomplete).
    """
    if force:
        return fetch_all()
    if is_stale(force_if_stale_hours):
        return fetch_all()

    data = load_latest()
    today = date.today()
    iso = today.isocalendar()
    this_week = iso[0] * 100 + iso[1]
    if data and data.get("week") != this_week and iso[2] >= 2:
        return fetch_all()
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = fetch_all()
    print("items", len(out["items"]), "errors", out["errors"])
    for it in out["items"]:
        print(it["name"], it["metric"], it.get("value"), it.get("signal"), it.get("asof"))
