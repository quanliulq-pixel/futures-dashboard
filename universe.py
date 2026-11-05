# -*- coding: utf-8 -*-
"""
Commodity-only instrument universe (大宗商品):
- China: commodity continuous mains (exclude equity index / bonds / freight index)
- North America: energy, metals, ags, livestock, softs (exclude rates/FX/equity/crypto)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from deflation_tags import apply_deflation

logger = logging.getLogger(__name__)

# Only these sectors are kept (国债/外汇/股指/加密等一律排除)
COMMODITY_SECTORS = {
    "黑色",
    "有色",
    "贵金属",
    "能源",
    "化工",
    "农产品",
    "畜牧",
    "软商品",
    "林产品",
    "轻工",  # 纸浆等
}

# ---------- CN helpers ----------

# futures_zh_realtime name overrides (display name without 连续 often fails)
CN_REALTIME_NAME_OVERRIDE = {
    "MA0": "郑醇",
    "FU0": "燃油",
    "HC0": "热轧卷板",
    "IF0": "沪深300指数期货",
    "IH0": "上证50指数期货",
    "IC0": "中证500指数期货",
    "IM0": "中证1000指数期货",
    "TF0": "5年期国债",
    "TS0": "2年期国债",
    "T0": "10年期国债",
    "TL0": "30年期国债",
    "SC0": "原油",
    "NR0": "20号胶",
    "LU0": "低硫燃料油",
    "BC0": "国际铜",
    "EC0": "集运指数欧线",
    "SI0": "工业硅",
    "LC0": "碳酸锂",
    "PS0": "多晶硅",
    "PT0": "铂",
    "PD0": "钯",
}

# Root-letter → sector for CN continuous symbols
CN_SECTOR_BY_ROOT = {
    # 黑色（含铁合金）
    "RB": "黑色", "HC": "黑色", "I": "黑色", "J": "黑色", "JM": "黑色", "SS": "黑色",
    "SF": "黑色", "SM": "黑色",
    # 有色
    "CU": "有色", "AL": "有色", "ZN": "有色", "PB": "有色", "NI": "有色", "SN": "有色",
    "AO": "有色", "BC": "有色", "AD": "有色",
    # 贵金属
    "AU": "贵金属", "AG": "贵金属", "PT": "贵金属", "PD": "贵金属",
    # 能源化工
    "SC": "能源", "FU": "能源", "BU": "能源", "LU": "能源", "PG": "能源",
    "TA": "化工", "MA": "化工", "EG": "化工", "EB": "化工", "V": "化工", "L": "化工",
    "PP": "化工", "SA": "化工", "UR": "化工", "PF": "化工", "SH": "化工", "PX": "化工",
    "PR": "化工", "PL": "化工", "BZ": "化工", "RU": "化工", "NR": "化工", "BR": "化工",
    "FG": "化工", "SI": "化工", "LC": "化工", "PS": "化工",
    # 农产品（粮油果蔬）
    "A": "农产品", "B": "农产品", "M": "农产品", "Y": "农产品", "P": "农产品", "C": "农产品",
    "CS": "农产品", "RR": "农产品", "OI": "农产品", "RM": "农产品",
    "RS": "农产品", "WH": "农产品", "RI": "农产品", "LR": "农产品", "JR": "农产品",
    "AP": "农产品", "CJ": "农产品", "PK": "农产品",
    # 畜牧
    "LH": "畜牧", "JD": "畜牧",
    # 软商品
    "SR": "软商品", "CF": "软商品", "CY": "软商品",
    # 林产品 & 轻工
    "LG": "林产品", "FB": "林产品",
    "SP": "轻工", "OP": "轻工",
    # 股指/利率/其他
    "IF": "股指", "IH": "股指", "IC": "股指", "IM": "股指",
    "T": "利率", "TF": "利率", "TS": "利率", "TL": "利率",
    "EC": "航运",
}

EXCHANGE_LABEL = {
    "dce": "大商所",
    "czce": "郑商所",
    "shfe": "上期所",
    "ine": "上期能源",
    "cffex": "中金所",
    "gfex": "广期所",
}


def _cn_root(symbol: str) -> str:
    m = re.match(r"^([A-Za-z]+)", str(symbol))
    return m.group(1).upper() if m else str(symbol).upper()


def _cn_sector(symbol: str) -> str:
    root = _cn_root(symbol)
    if root in CN_SECTOR_BY_ROOT:
        return CN_SECTOR_BY_ROOT[root]
    # longest prefix match
    for k in sorted(CN_SECTOR_BY_ROOT.keys(), key=len, reverse=True):
        if root.startswith(k):
            return CN_SECTOR_BY_ROOT[k]
    return "其他"


def _cn_display_name(name: str) -> str:
    n = str(name).replace("连续", "").strip()
    # clean suffixes like 指数期货
    return n


def _cn_realtime_name(symbol: str, name: str) -> str:
    sym = str(symbol).upper()
    if sym in CN_REALTIME_NAME_OVERRIDE:
        return CN_REALTIME_NAME_OVERRIDE[sym]
    return _cn_display_name(name)


def _rows_to_cn_items(rows: list[tuple[str, str, str]] | list) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            symbol, exchange, name_raw = row[0], row[1], row[2]
        else:
            symbol = str(row["symbol"]).upper()
            name_raw = str(row.get("name", symbol))
            exchange = str(row.get("exchange", "")).lower()
        symbol = str(symbol).upper()
        exchange = str(exchange).lower()
        name = _cn_display_name(name_raw)
        items.append(
            {
                "id": f"CN:{symbol}",
                "symbol": symbol,
                "name": name,
                "name_cn": _cn_realtime_name(symbol, name_raw),
                "sector": _cn_sector(symbol),
                "market": "cn",
                "exchange": EXCHANGE_LABEL.get(exchange, exchange or "国内"),
            }
        )
    seen = set()
    out = []
    for it in items:
        if it["id"] in seen:
            continue
        if it.get("sector") not in COMMODITY_SECTORS:
            continue
        seen.add(it["id"])
        out.append(apply_deflation(it))
    return out


def fetch_cn_universe(prefer_static: bool = False) -> list[dict[str, Any]]:
    """All domestic continuous contracts: static full slate first (fast), optional live merge."""
    from cn_static import CN_STATIC_ROWS
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    static_items = _rows_to_cn_items(CN_STATIC_ROWS)
    if prefer_static:
        logger.info("CN universe static: %d symbols", len(static_items))
        return static_items

    def _live():
        import akshare as ak

        return ak.futures_display_main_sina()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_live)
            df = fut.result(timeout=12)
        if df is not None and len(df) > 0:
            rows = [
                (str(r["symbol"]).upper(), str(r.get("exchange", "")).lower(), str(r.get("name", "")))
                for _, r in df.iterrows()
            ]
            out = _rows_to_cn_items(rows)
            have = {x["id"] for x in out}
            for it in static_items:
                if it["id"] not in have:
                    out.append(it)
            logger.info("CN universe live+static: %d symbols", len(out))
            return out
    except FuturesTimeout:
        logger.warning("CN live list timed out (12s), using static fallback")
    except Exception as exc:
        logger.warning("CN live list failed (%s), using static fallback", exc)

    logger.info("CN universe static fallback: %d symbols", len(static_items))
    return static_items


# ---------- North America (Yahoo continuous) ----------
# Comprehensive liquid + standard CME Group / CFE / ICE products with Yahoo =F feeds.
# Grouped by sector for maintainability.

NA_FUTURES_SPEC: list[tuple[str, str, str, str]] = [
    # symbol, name, sector, exchange  —— 仅大宗商品（不含股指/国债/外汇/加密）
    # --- Energy (NYMEX / ICE) ---
    ("CL=F", "WTI原油", "能源", "NYMEX"),
    ("BZ=F", "布伦特原油", "能源", "ICE"),
    ("NG=F", "天然气NG", "能源", "NYMEX"),
    ("HO=F", "取暖油ULSD", "能源", "NYMEX"),
    ("RB=F", "RBOB汽油", "能源", "NYMEX"),
    # --- Metals (COMEX / NYMEX) ---
    ("GC=F", "COMEX黄金", "贵金属", "COMEX"),
    ("SI=F", "COMEX白银", "贵金属", "COMEX"),
    ("HG=F", "COMEX铜", "有色", "COMEX"),
    ("PL=F", "铂金", "贵金属", "NYMEX"),
    ("PA=F", "钯金", "贵金属", "NYMEX"),
    ("ALI=F", "COMEX铝", "有色", "COMEX"),
    # --- Grains & Oilseeds (CBOT) ---
    ("ZC=F", "玉米", "农产品", "CBOT"),
    ("ZW=F", "小麦", "农产品", "CBOT"),
    ("ZS=F", "大豆", "农产品", "CBOT"),
    ("ZM=F", "豆粕", "农产品", "CBOT"),
    ("ZL=F", "豆油", "农产品", "CBOT"),
    ("ZO=F", "燕麦", "农产品", "CBOT"),
    ("ZR=F", "糙米", "农产品", "CBOT"),
    ("KE=F", "硬红冬麦", "农产品", "CBOT"),
    # --- Livestock (CME) ---
    ("LE=F", "活牛", "畜牧", "CME"),
    ("HE=F", "瘦肉猪", "畜牧", "CME"),
    ("GF=F", "饲牛", "畜牧", "CME"),
    ("DC=F", "III类牛奶", "畜牧", "CME"),
    # --- Softs (ICE US) ---
    ("KC=F", "咖啡", "软商品", "ICE"),
    ("CT=F", "棉花", "软商品", "ICE"),
    ("SB=F", "原糖11号", "软商品", "ICE"),
    ("CC=F", "可可", "软商品", "ICE"),
    ("OJ=F", "橙汁", "软商品", "ICE"),
    ("LB=F", "木材", "林产品", "CME"),
]


def fetch_na_universe() -> list[dict[str, Any]]:
    """North American commodity futures with free continuous feeds (Yahoo)."""
    items = []
    seen = set()
    for symbol, name, sector, exchange in NA_FUTURES_SPEC:
        if symbol in seen:
            continue
        if sector not in COMMODITY_SECTORS:
            continue
        seen.add(symbol)
        items.append(
            apply_deflation(
                {
                    "id": f"US:{symbol}",
                    "symbol": symbol,
                    "name": name,
                    "sector": sector,
                    "market": "us",
                    "exchange": exchange,
                }
            )
        )
    logger.info("NA commodity universe: %d symbols", len(items))
    return items


# ---------- Cached combined universe ----------

_universe_cache: tuple[float, list[dict], dict[str, dict]] | None = None
UNIVERSE_TTL = 3600  # refresh CN list hourly


def get_universe(force: bool = False, live_cn: bool = False) -> tuple[list[dict], dict[str, dict]]:
    """
    Returns (list of instruments, id->meta map).

    Default uses full static CN slate (instant, complete).
    Set live_cn=True to attempt merging Sina live list (may be slow).
    """
    global _universe_cache
    now = time.time()
    if not force and _universe_cache and now - _universe_cache[0] < UNIVERSE_TTL:
        return _universe_cache[1], _universe_cache[2]

    # Prefer static for reliability/speed; full slate of listed domestic products
    cn = fetch_cn_universe(prefer_static=not live_cn)
    na = fetch_na_universe()
    all_items = cn + na
    by_id = {x["id"]: x for x in all_items}
    _universe_cache = (now, all_items, by_id)
    logger.info("Total universe: %d (CN=%d NA=%d)", len(all_items), len(cn), len(na))
    return all_items, by_id


def get_cn_instruments() -> list[dict]:
    items, _ = get_universe()
    return [x for x in items if x["market"] == "cn"]


def get_us_instruments() -> list[dict]:
    items, _ = get_universe()
    return [x for x in items if x["market"] == "us"]


def get_instrument(instrument_id: str) -> dict | None:
    _, by_id = get_universe()
    return by_id.get(instrument_id)
