# -*- coding: utf-8 -*-
"""US stocks + ETFs with the largest weekly / monthly / yearly moves."""
from __future__ import annotations

import net_limits

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_TTL = 2 * 3600
UNI_TTL = 24 * 3600
DATA_DIR = Path(__file__).resolve().parent / "data" / "us_movers"
PX_PKL = DATA_DIR / "close.pkl"
UNI_JSON = DATA_DIR / "universe.json"
WIKI_JSON = DATA_DIR / "wiki_universe.json"

_px_cache: tuple[float, pd.DataFrame, dict[str, dict]] | None = None
_lock = threading.Lock()

PERIODS = {
    "W": {"days": 5, "label": "近一周"},
    "M": {"days": 21, "label": "近一月"},
    "Y": {"days": 252, "label": "近一年"},
}

# Liquid ETFs (equity, sector, bond, commodity, country)
ETF_TICKERS = {
    "SPY": "标普500ETF",
    "QQQ": "纳斯达克100ETF",
    "IWM": "罗素2000ETF",
    "DIA": "道琼斯ETF",
    "VOO": "先锋标普500",
    "VTI": "全美市场",
    "VEA": "发达市场(除美)",
    "VWO": "新兴市场",
    "EFA": "欧澳远东",
    "EEM": "新兴市场iShares",
    "IEMG": "核心新兴",
    "FXI": "中国大型股",
    "KWEB": "中概互联",
    "MCHI": "MSCI中国",
    "EWJ": "日本",
    "EWZ": "巴西",
    "INDA": "印度",
    "XLK": "科技板块",
    "XLF": "金融板块",
    "XLE": "能源板块",
    "XLV": "医疗板块",
    "XLI": "工业板块",
    "XLY": "可选消费",
    "XLP": "必选消费",
    "XLU": "公用事业",
    "XLB": "原材料",
    "XLRE": "房地产",
    "XLC": "通信板块",
    "SMH": "半导体",
    "SOXX": "半导体iShares",
    "XBI": "生物科技",
    "IBB": "生物科技iShares",
    "XOP": "油气开采",
    "GDX": "金矿",
    "GDXJ": "小型金矿",
    "GLD": "黄金ETF",
    "IAU": "黄金iShares",
    "SLV": "白银ETF",
    "USO": "原油ETF",
    "UNG": "天然气ETF",
    "DBC": "商品指数",
    "PDBC": "优化商品",
    "DBA": "农产品",
    "TLT": "20年美债",
    "IEF": "7-10年美债",
    "SHY": "1-3年美债",
    "TIP": "抗通胀债",
    "LQD": "投资级债",
    "HYG": "高收益债",
    "JNK": "垃圾债",
    "AGG": "综合债券",
    "BND": "全美债券",
    "VNQ": "房地产REIT",
    "SCHD": "高股息",
    "VIG": "股息增长",
    "VYM": "高股息先锋",
    "ARKK": "ARK创新",
    "ARKW": "ARK互联网",
    "IBIT": "比特币现货",
    "BITO": "比特币期货",
    "BOTZ": "机器人",
    "HACK": "网络安全",
    "TAN": "太阳能",
    "ICLN": "清洁能源",
    "QQQM": "纳指迷你",
    "VGT": "信息科技先锋",
}

# 2x / 3x and inverse — shown in a dedicated column, not mixed with spot ETFs
LEV_ETF_TICKERS = {
    "TQQQ": ("纳指3倍", 3),
    "SQQQ": ("纳指-3倍", -3),
    "QLD": ("纳指2倍", 2),
    "QID": ("纳指-2倍", -2),
    "UPRO": ("标普3倍", 3),
    "SPXU": ("标普-3倍", -3),
    "SPXL": ("标普3倍Direxion", 3),
    "SPXS": ("标普-3倍Direxion", -3),
    "SSO": ("标普2倍", 2),
    "SDS": ("标普-2倍", -2),
    "TNA": ("罗素2000 3倍", 3),
    "TZA": ("罗素2000 -3倍", -3),
    "UWM": ("罗素2000 2倍", 2),
    "TWM": ("罗素2000 -2倍", -2),
    "UDOW": ("道指3倍", 3),
    "SDOW": ("道指-3倍", -3),
    "DDM": ("道指2倍", 2),
    "DXD": ("道指-2倍", -2),
    "UMDD": ("中盘3倍", 3),
    "SMDD": ("中盘-3倍", -3),
    "SOXL": ("半导体3倍", 3),
    "SOXS": ("半导体-3倍", -3),
    "USD": ("半导体2倍", 2),
    "SSG": ("半导体-2倍", -2),
    "TECL": ("科技3倍", 3),
    "TECS": ("科技-3倍", -3),
    "ROM": ("科技2倍", 2),
    "REW": ("科技-2倍", -2),
    "FAS": ("金融3倍", 3),
    "FAZ": ("金融-3倍", -3),
    "UYG": ("金融2倍", 2),
    "SKF": ("金融-2倍", -2),
    "ERX": ("能源2倍", 2),
    "ERY": ("能源-2倍", -2),
    "GUSH": ("油气开采2倍", 2),
    "DRIP": ("油气开采-2倍", -2),
    "DIG": ("油气2倍", 2),
    "DUG": ("油气-2倍", -2),
    "LABU": ("生物3倍", 3),
    "LABD": ("生物-3倍", -3),
    "CURE": ("医疗3倍", 3),
    "PILL": ("制药3倍", 3),
    "DFEN": ("国防3倍", 3),
    "NAIL": ("地产建筑3倍", 3),
    "DRN": ("房地产3倍", 3),
    "DRV": ("房地产-3倍", -3),
    "RETL": ("零售3倍", 3),
    "WANT": ("可选消费3倍", 3),
    "WEBL": ("互联网3倍", 3),
    "WEBS": ("互联网-3倍", -3),
    "DUSL": ("工业3倍", 3),
    "TPOR": ("运输3倍", 3),
    "HIBL": ("高贝塔3倍", 3),
    "HIBS": ("高贝塔-3倍", -3),
    "CWEB": ("中概互联2倍", 2),
    "YINN": ("富时中国3倍", 3),
    "YANG": ("富时中国-3倍", -3),
    "KORU": ("韩国3倍", 3),
    "EURL": ("欧洲3倍", 3),
    "EDC": ("新兴3倍", 3),
    "EDZ": ("新兴-3倍", -3),
    "INDL": ("印度3倍", 3),
    "MEXX": ("墨西哥3倍", 3),
    "FNGU": ("FANG+ 3倍", 3),
    "FNGD": ("FANG+ -3倍", -3),
    "BULZ": ("FANG创新3倍", 3),
    "BERZ": ("FANG创新-3倍", -3),
    "UGL": ("黄金2倍", 2),
    "GLL": ("黄金-2倍", -2),
    "AGQ": ("白银2倍", 2),
    "ZSL": ("白银-2倍", -2),
    "UCO": ("原油2倍", 2),
    "SCO": ("原油-2倍", -2),
    "BOIL": ("天然气2倍", 2),
    "KOLD": ("天然气-2倍", -2),
    "NUGT": ("金矿2倍", 2),
    "DUST": ("金矿-2倍", -2),
    "JNUG": ("小型金矿2倍", 2),
    "JDST": ("小型金矿-2倍", -2),
    "TMF": ("20年美债3倍", 3),
    "TBT": ("20年美债-2倍", -2),
    "TTT": ("20年美债-3倍", -3),
    "UBT": ("20年美债2倍", 2),
    "TYD": ("中期美债3倍", 3),
    "TYO": ("中期美债-3倍", -3),
    "UST": ("中期美债2倍", 2),
    "BITX": ("比特币2倍", 2),
    "BITU": ("比特币2倍ProShares", 2),
    "SBIT": ("比特币-2倍", -2),
    "ETHU": ("以太坊2倍", 2),
    "ETHT": ("以太坊2倍ProShares", 2),
    "NVDL": ("英伟达2倍", 2),
    "NVDX": ("英伟达2倍T-Rex", 2),
    "NVDU": ("英伟达2倍Direxion", 2),
    "NVD": ("英伟达-1倍", -1),
    "TSLL": ("特斯拉2倍", 2),
    "TSLS": ("特斯拉-1倍", -1),
    "CONL": ("Coinbase2倍", 2),
    "MSTU": ("微策略2倍", 2),
    "MSTX": ("微策略2倍Defiance", 2),
    "MSTZ": ("微策略-2倍", -2),
    "AMDL": ("超威2倍", 2),
    "AMZU": ("亚马逊2倍", 2),
    "AAPU": ("苹果2倍", 2),
    "MSFU": ("微软2倍", 2),
    "GGLL": ("谷歌2倍", 2),
    "METU": ("Meta2倍", 2),
    "PTIR": ("Palantir2倍", 2),
    "PLTU": ("Palantir2倍Direxion", 2),
}

# Fallback mega/large caps if Wikipedia is unreachable
FALLBACK_STOCKS = {
    "AAPL": "苹果", "MSFT": "微软", "NVDA": "英伟达", "AMZN": "亚马逊", "GOOGL": "谷歌A",
    "GOOG": "谷歌C", "META": "Meta", "TSLA": "特斯拉", "BRK-B": "伯克希尔B", "AVGO": "博通",
    "JPM": "摩根大通", "V": "Visa", "UNH": "联合健康", "XOM": "埃克森美孚", "LLY": "礼来",
    "MA": "万事达", "JNJ": "强生", "WMT": "沃尔玛", "PG": "宝洁", "ORCL": "甲骨文",
    "HD": "家得宝", "COST": "开市客", "ABBV": "艾伯维", "BAC": "美国银行", "NFLX": "奈飞",
    "CVX": "雪佛龙", "KO": "可口可乐", "MRK": "默沙东", "PEP": "百事", "CRM": "Salesforce",
    "AMD": "超威", "TMO": "赛默飞", "CSCO": "思科", "LIN": "林德", "ACN": "埃森哲",
    "MCD": "麦当劳", "ABT": "雅培", "DHR": "丹纳赫", "WFC": "富国银行", "TXN": "德州仪器",
    "IBM": "IBM", "GE": "通用电气", "CAT": "卡特彼勒", "QCOM": "高通", "INTU": "直觉软件",
    "NOW": "ServiceNow", "VZ": "威瑞森", "DIS": "迪士尼", "AMGN": "安进", "ISRG": "直觉外科",
    "PM": "菲利普莫里斯", "NEE": "新纪元能源", "SPGI": "标普全球", "RTX": "雷神", "PFE": "辉瑞",
    "LOW": "劳氏", "UNP": "联合太平洋", "HON": "霍尼韦尔", "BKNG": "Booking", "AMAT": "应用材料",
    "GS": "高盛", "PLTR": "Palantir", "UBER": "优步", "INTC": "英特尔", "BA": "波音",
    "SBUX": "星巴克", "NKE": "耐克", "MDT": "美敦力", "ADI": "亚德诺", "LRCX": "拉姆研究",
    "MU": "美光", "PANW": "Palo Alto", "KLAC": "科磊", "SNPS": "新思", "CDNS": "铿腾",
    "CRWD": "CrowdStrike", "SHOP": "Shopify", "MELI": "美客多", "SE": "Sea", "BABA": "阿里巴巴",
    "PDD": "拼多多", "JD": "京东", "NIO": "蔚来", "XPEV": "小鹏", "LI": "理想",
    "COIN": "Coinbase", "MSTR": "微策略", "HOOD": "Robinhood", "SOFI": "SoFi",
    "SMCI": "超微电脑", "ARM": "安谋", "TSM": "台积电", "ASML": "阿斯麦",
    "C": "花旗", "MS": "摩根士丹利", "BLK": "贝莱德", "SCHW": "嘉信", "AXP": "运通",
    "DE": "迪尔", "MMM": "3M", "GEV": "GE Vernova", "ETN": "伊顿", "PH": "派克汉尼汾",
    "COP": "康菲", "SLB": "斯伦贝谢", "EOG": "EOG", "MPC": "马拉松石油", "PSX": "Phillips 66",
    "CVS": "CVS", "ELV": "Elevance", "CI": "信诺", "BMY": "施贵宝", "GILD": "吉利德",
    "VRTX": "福泰", "REGN": "再生元", "MRNA": "莫德纳", "T": "AT&T", "CMCSA": "康卡斯特",
    "TMUS": "T-Mobile", "ADBE": "Adobe", "PYPL": "PayPal", "XYZ": "Block", "ABNB": "Airbnb",
    "SNOW": "Snowflake", "NET": "Cloudflare", "DDOG": "Datadog", "ZS": "Zscaler",
    "APP": "AppLovin", "DASH": "DoorDash", "RBLX": "Roblox", "U": "Unity",
    "F": "福特", "GM": "通用汽车", "RIVN": "Rivian", "LCID": "Lucid",
}


def _read_html(url: str) -> list[pd.DataFrame]:
    import io
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        html = resp.read()
    return pd.read_html(io.BytesIO(html), header=0)


def _sp500_from_wiki() -> dict[str, str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = _read_html(url)
    df = tables[0]
    sym_col = "Symbol" if "Symbol" in df.columns else df.columns[0]
    name_col = "Security" if "Security" in df.columns else df.columns[1]
    out = {}
    for _, r in df.iterrows():
        sym = str(r[sym_col]).strip().replace(".", "-")
        name = str(r[name_col]).strip()
        if sym and sym != "nan":
            out[sym] = name
    return out


def _nasdaq100_from_wiki() -> dict[str, str]:
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    tables = _read_html(url)
    # usually the constituents table has Ticker / Company
    best = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("ticker" in c for c in cols) and len(t) > 50:
            best = t
            break
    if best is None:
        return {}
    tcol = [c for c in best.columns if "ticker" in str(c).lower()][0]
    ncol = [c for c in best.columns if "company" in str(c).lower() or "name" in str(c).lower()]
    ncol = ncol[0] if ncol else best.columns[0]
    out = {}
    for _, r in best.iterrows():
        sym = str(r[tcol]).strip().replace(".", "-")
        name = str(r[ncol]).strip()
        if sym and len(sym) <= 6:
            out[sym] = name
    return out


def _wiki_cached() -> dict[str, str]:
    now = time.time()
    if WIKI_JSON.exists() and now - WIKI_JSON.stat().st_mtime < UNI_TTL:
        try:
            return json.loads(WIKI_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    stocks = dict(FALLBACK_STOCKS)
    wiki_ok = False
    try:
        stocks.update(_sp500_from_wiki())
        wiki_ok = True
        logger.info("S&P 500 list loaded from Wikipedia (%d)", len(stocks))
    except Exception as exc:
        logger.warning("S&P 500 wiki fail, use fallback: %s", exc)
    try:
        extra = _nasdaq100_from_wiki()
        if extra:
            stocks.update(extra)
            wiki_ok = True
            logger.info("Nasdaq-100 extras loaded (%d tickers)", len(extra))
    except Exception as exc:
        logger.warning("Nasdaq-100 wiki fail: %s", exc)
    if wiki_ok:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            WIKI_JSON.write_text(json.dumps(stocks, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.debug("wiki cache write skipped", exc_info=True)
    return stocks


def load_universe() -> dict[str, dict]:
    """symbol -> {name, kind: stock|etf|lev}"""
    stocks = _wiki_cached()
    uni: dict[str, dict] = {}
    for s, n in stocks.items():
        if s in ETF_TICKERS or s in LEV_ETF_TICKERS:
            continue
        uni[s] = {"name": n, "kind": "stock"}
    for s, n in ETF_TICKERS.items():
        uni[s] = {"name": n, "kind": "etf"}
    for s, (n, lev) in LEV_ETF_TICKERS.items():
        uni[s] = {"name": n, "kind": "lev", "leverage": lev}
    return uni


def _extract_close(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    # yfinance MultiIndex columns (ticker, field) or single field
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        lvl1 = raw.columns.get_level_values(1)
        if "Close" in set(lvl1):
            close = raw.xs("Close", axis=1, level=1)
        elif "Close" in set(lvl0):
            close = raw["Close"]
        else:
            close = raw
    else:
        close = raw["Close"] if "Close" in raw.columns else raw
    if isinstance(close, pd.Series):
        name = symbols[0] if symbols else (close.name or "Close")
        close = close.to_frame(name=name)
    elif len(symbols) == 1 and "Close" in close.columns:
        close = close[["Close"]].copy()
        close.columns = [symbols[0]]
    close = close.copy()
    close.columns = [str(c).strip() for c in close.columns]
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    close.index = idx
    return close


def _load_disk() -> tuple[pd.DataFrame, dict[str, dict]] | None:
    if not PX_PKL.exists() or not UNI_JSON.exists():
        return None
    age = time.time() - PX_PKL.stat().st_mtime
    if age >= CACHE_TTL:
        return None
    try:
        close = pd.read_pickle(PX_PKL)
        uni = json.loads(UNI_JSON.read_text(encoding="utf-8"))
        if close is None or close.empty or not uni:
            return None
        return close, uni
    except Exception:
        logger.debug("US movers disk cache unreadable", exc_info=True)
        return None


def _save_disk(close: pd.DataFrame, uni: dict[str, dict]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        close.to_pickle(PX_PKL)
        UNI_JSON.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.debug("US movers disk cache write skipped", exc_info=True)


def _download_panel(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf

    frames: list[pd.DataFrame] = []
    chunk = 80
    for i in range(0, len(symbols), chunk):
        batch = symbols[i : i + chunk]
        logger.info("Yahoo US batch %d–%d / %d", i + 1, i + len(batch), len(symbols))
        try:
            raw = net_limits.yf_download(
                batch,
                period="15mo",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
            part = _extract_close(raw, batch)
            if part is not None and not part.empty:
                frames.append(part)
        except Exception:
            logger.exception("Yahoo batch failed (%s…)", batch[0])
    if not frames:
        return pd.DataFrame()
    close = pd.concat(frames, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    return close


def _finalize_panel(close: pd.DataFrame, full_uni: dict[str, dict]) -> tuple[pd.DataFrame, dict[str, dict]]:
    if close is None or close.empty:
        return pd.DataFrame(), {}
    keep = [c for c in close.columns if close[c].notna().sum() > 20]
    close = close[keep] if keep else close
    close = close.loc[:, ~close.columns.duplicated()]
    uni = {k: v for k, v in full_uni.items() if k in close.columns}
    return close, uni


def get_price_panel(force: bool = False) -> tuple[pd.DataFrame, dict[str, dict]]:
    global _px_cache
    full_uni = load_universe()
    now = time.time()
    if not force and _px_cache and now - _px_cache[0] < CACHE_TTL:
        cached = _px_cache[1]
        missing = [s for s in full_uni if s not in cached.columns]
        if not missing:
            close, uni = _finalize_panel(cached, full_uni)
            return close.copy(), uni

    with _lock:
        now = time.time()
        close = None
        if not force and _px_cache and now - _px_cache[0] < CACHE_TTL:
            close = _px_cache[1].copy()
        elif not force:
            disk = _load_disk()
            if disk is not None:
                close, _old = disk
                logger.info("US price panel from disk: %d series", close.shape[1])

        if close is None or force:
            logger.info("Downloading %d US symbols from Yahoo...", len(full_uni))
            close = _download_panel(list(full_uni.keys()))
        else:
            missing = [s for s in full_uni if s not in close.columns]
            if missing:
                logger.info("Downloading %d new US symbols (leveraged ETFs)…", len(missing))
                extra = _download_panel(missing)
                if extra is not None and not extra.empty:
                    close = pd.concat([close, extra], axis=1)

        close, uni = _finalize_panel(close, full_uni)
        _px_cache = (time.time(), close, uni)
        _save_disk(close, uni)
        logger.info("US price panel ready: %d series", close.shape[1])
        return close.copy(), dict(uni)


def _ret(series: pd.Series, days: int, kind: str = "stock") -> float | None:
    s = series.dropna()
    # do not shrink the window — new listings / spin-offs would look like 1000%+ yearly moves
    if len(s) < days + 1:
        return None
    win = s.iloc[-(days + 1) :]
    daily = win.pct_change().dropna()
    if daily.empty:
        return None
    # 3x funds can print ±40% days; only drop split-like prints
    if kind == "lev":
        lo, hi = -0.72, 1.25
    else:
        lo, hi = -0.45, 0.90
    if float(daily.min()) < lo or float(daily.max()) > hi:
        return None
    a = float(win.iloc[-1])
    b = float(win.iloc[0])
    if b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _top_bottom(rows: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    rows = sorted(rows, key=lambda r: r["change_pct"], reverse=True)
    gainers = rows[:limit]
    losers = list(reversed(rows[-limit:])) if rows else []
    return gainers, losers


def compute_movers(
    period: str = "W", kind: str = "all", limit: int = 20, force: bool = False
) -> dict[str, Any]:
    period = (period or "W").upper()
    if period not in PERIODS:
        period = "W"
    kind = (kind or "all").lower()
    days = PERIODS[period]["days"]
    limit = max(5, min(int(limit or 20), 50))

    close, uni = get_price_panel(force=force)
    main: list[dict] = []
    lev_rows: list[dict] = []
    last_dt = close.index[-1] if len(close) else None
    for sym, meta in uni.items():
        if sym not in close.columns:
            continue
        s = close[sym]
        last = s.dropna()
        if last.empty:
            continue
        px = float(last.iloc[-1])
        chg = _ret(s, days, kind=meta["kind"])
        if chg is None:
            continue
        row = {
            "symbol": sym,
            "name": meta["name"],
            "kind": meta["kind"],
            "leverage": meta.get("leverage"),
            "price": round(px, 4 if abs(px) < 1 else 2),
            "change_pct": round(chg, 2),
            "asof": str(last.index[-1].date()),
        }
        if meta["kind"] == "lev":
            lev_rows.append(row)
        if kind == "all" and meta["kind"] == "lev":
            continue
        if kind in {"stock", "etf", "lev"} and meta["kind"] != kind:
            continue
        main.append(row)

    gainers, losers = _top_bottom(main, limit)
    lev_gainers, lev_losers = _top_bottom(lev_rows, 12)
    return {
        "period": period,
        "period_label": PERIODS[period]["label"],
        "kind": kind,
        "asof": str(last_dt.date()) if last_dt is not None else None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": len(main),
        "gainers": gainers,
        "losers": losers,
        "lev_universe": len(lev_rows),
        "lev_gainers": lev_gainers,
        "lev_losers": lev_losers,
        "note": "周=5日、月=21日、年=252日。多倍ETF（2x/3x及反向）单独一列，不与现货ETF混排。",
    }
