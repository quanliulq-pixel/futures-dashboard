# -*- coding: utf-8 -*-
"""CFFEX index options crawler (IO/HO/MO).

Official www.cffex.com.cn is unreachable from many overseas hosts (TLS/500/timeout).
Primary live source: Sina finance CFFEX option T-quotes (延时转播).
Writes docs/api/cn_index_options.json for seed-first serving on Render.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / "docs" / "api" / "cn_index_options.json"

TIMEOUT = 10
RETRIES = 1
FRONT_MONTHS = 2
ATM_BAND = 5  # ± strikes around ATM kept in summary

UA = (
    "Mozilla/5.0 (compatible; futures-dashboard/1.0; +https://github.com/quanliulq-pixel/futures-dashboard)"
)

PRODUCTS = {
    "io": {
        "product": "io",
        "name": "沪深300股指期权",
        "short": "IO",
        "index_id": "csi300",
        "index_name": "沪深300",
        "months_url": "https://stock.finance.sina.com.cn/futures/view/optionsCffexDP.php",
        "relate_fallback": "sh000300",
    },
    "ho": {
        "product": "ho",
        "name": "上证50股指期权",
        "short": "HO",
        "index_id": "sse50",
        "index_name": "上证50",
        "months_url": "https://stock.finance.sina.com.cn/futures/view/optionsCffexDP.php/ho/cffex",
        "relate_fallback": "sh000016",
    },
    "mo": {
        "product": "mo",
        "name": "中证1000股指期权",
        "short": "MO",
        "index_id": "csi1000",
        "index_name": "中证1000",
        "months_url": "https://stock.finance.sina.com.cn/futures/view/optionsCffexDP.php/mo/cffex",
        "relate_fallback": "sh000852",
    },
}

# Documented official endpoints we try first (expected to fail from US/Render).
CFFEX_PROBE_URLS = [
    "https://www.cffex.com.cn/",
    "https://www.cffex.com.cn/hs300gzqq/",
    "https://www.cffex.com.cn/sj/quotesxs/index_300.txt",
    "http://www.cffex.com.cn/sj/quotesxs/index_300.txt",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Referer": "https://stock.finance.sina.com.cn/",
            "Accept": "*/*",
        }
    )
    return s


def _get(sess: requests.Session, url: str, *, timeout: float = TIMEOUT) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            r = sess.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as exc:
            last_exc = exc
            if attempt < RETRIES:
                time.sleep(0.4)
    assert last_exc is not None
    raise last_exc


_official_probe_cache: dict | None = None


def try_official_cffex(sess: requests.Session) -> dict:
    """Probe official CFFEX once per process; never block the pipeline on success."""
    global _official_probe_cache
    if _official_probe_cache is not None:
        return _official_probe_cache
    errors: list[str] = []
    # One short probe is enough — overseas hosts almost always time out.
    for url in CFFEX_PROBE_URLS[:1]:
        try:
            r = sess.get(url, timeout=3, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 200:
                _official_probe_cache = {"ok": True, "url": url, "bytes": len(r.content), "errors": errors}
                return _official_probe_cache
            errors.append(f"{url} -> HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    _official_probe_cache = {"ok": False, "url": None, "bytes": 0, "errors": errors}
    return _official_probe_cache


def list_months_sina(sess: requests.Session, product: str) -> list[str]:
    meta = PRODUCTS[product]
    html = _get(sess, meta["months_url"]).text
    pat = re.compile(rf"\b({re.escape(product)}\d{{4}})\b", re.I)
    found = sorted(set(m.group(1).lower() for m in pat.finditer(html)))
    # Prefer nearer months: YYMM ascending works for 26xx before 27xx
    return found


def fetch_chain_sina(sess: requests.Session, product: str, month_code: str) -> dict:
    qs = urlencode(
        {
            "type": "futures",
            "product": product,
            "exchange": "cffex",
            "pinzhong": month_code,
        }
    )
    url = f"https://stock.finance.sina.com.cn/futures/api/openapi.php/OptionService.getOptionData?{qs}"
    raw = _get(sess, url).json()
    data = ((raw or {}).get("result") or {}).get("data") or {}
    info = data.get("info") or {}
    up = data.get("up") or []
    down = data.get("down") or []
    return {"info": info, "up": up, "down": down, "url": url}


def fetch_spot_sina(sess: requests.Session, relate: str) -> float | None:
    """Sina hq.sinajs.cn — field[3] is last for index quotes."""
    if not relate:
        return None
    url = f"https://hq.sinajs.cn/list={relate}"
    try:
        r = sess.get(
            url,
            timeout=TIMEOUT,
            headers={**sess.headers, "Referer": "https://finance.sina.com.cn"},
        )
        r.raise_for_status()
        # encoding often gb18030
        text = r.content.decode("gb18030", errors="ignore")
        m = re.search(r'="([^"]*)"', text)
        if not m:
            return None
        parts = m.group(1).split(",")
        if len(parts) < 4:
            return None
        # index: name, open, prev_close, last, ...
        last = float(parts[3])
        return last if last > 0 else None
    except Exception:
        logger.debug("spot fetch failed for %s", relate, exc_info=True)
        return None


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _i(x: Any) -> int | None:
    try:
        if x is None or x == "":
            return None
        return int(float(x))
    except Exception:
        return None


def _parse_call_row(row: list) -> dict | None:
    # ['bid_vol','bid','last','ask','ask_vol','oi',change,'strike','code']
    if not row or len(row) < 9:
        return None
    strike = _f(row[7])
    code = str(row[8])
    if strike is None:
        m = re.search(r"[CP](\d+)$", code, re.I)
        strike = _f(m.group(1)) if m else None
    bid, last, ask = _f(row[1]), _f(row[2]), _f(row[3])
    mid = None
    if bid is not None and ask is not None and ask > 0:
        mid = round((bid + ask) / 2.0, 3)
    elif last is not None:
        mid = last
    return {
        "code": code,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": mid,
        "bid_vol": _i(row[0]),
        "ask_vol": _i(row[4]),
        "oi": _i(row[5]),
        "change": _f(row[6]),
    }


def _parse_put_row(row: list, strike_hint: float | None = None) -> dict | None:
    # Often: ['bid_vol','bid','last','ask','ask_vol','oi',change,'code']  (no strike)
    # Sometimes mirrors call with strike.
    if not row or len(row) < 8:
        return None
    code = str(row[-1])
    strike = strike_hint
    if len(row) >= 9:
        strike = _f(row[7]) or strike
        code = str(row[8])
    if strike is None:
        m = re.search(r"[CP](\d+)$", code, re.I)
        strike = _f(m.group(1)) if m else None
    bid, last, ask = _f(row[1]), _f(row[2]), _f(row[3])
    mid = None
    if bid is not None and ask is not None and ask > 0:
        mid = round((bid + ask) / 2.0, 3)
    elif last is not None:
        mid = last
    return {
        "code": code,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": mid,
        "bid_vol": _i(row[0]),
        "ask_vol": _i(row[4]),
        "oi": _i(row[5]),
        "change": _f(row[6]),
    }


def _estimate_iv_pct(spot: float, strike: float, mid: float, dte: int) -> float | None:
    """Brenner–Subrahmanyam style ATM approx (percent)."""
    try:
        if spot <= 0 or mid is None or mid <= 0 or dte is None or dte <= 0:
            return None
        T = max(dte, 1) / 365.0
        prem = mid / spot
        iv = (2.0 * math.pi / T) ** 0.5 * prem * 100.0
        moneyness = strike / spot
        if abs(moneyness - 1.0) > 0.03:
            # lightly adjust away from ATM
            iv *= 1.0 + min(0.25, abs(moneyness - 1.0) * 2.0)
        if iv < 3 or iv > 200:
            return None
        return round(float(iv), 1)
    except Exception:
        return None


def _normalize_month(
    product: str,
    month_code: str,
    chain: dict,
    spot: float | None,
) -> dict:
    info = chain.get("info") or {}
    expire = info.get("expire_date")
    days = _i(info.get("days"))
    underlying = info.get("relate") or PRODUCTS[product]["relate_fallback"]
    underlying_name = info.get("realte_name") or info.get("name") or PRODUCTS[product]["index_name"]

    calls: dict[float, dict] = {}
    for row in chain.get("up") or []:
        c = _parse_call_row(row if isinstance(row, list) else [])
        if c and c.get("strike") is not None:
            calls[float(c["strike"])] = c

    puts: dict[float, dict] = {}
    put_rows = chain.get("down") or []
    call_strikes_sorted = sorted(calls.keys())
    for idx, row in enumerate(put_rows):
        hint = call_strikes_sorted[idx] if idx < len(call_strikes_sorted) else None
        p = _parse_put_row(row if isinstance(row, list) else [], strike_hint=hint)
        if p and p.get("strike") is not None:
            puts[float(p["strike"])] = p

    strikes_set = sorted(set(calls) | set(puts))
    strike_rows: list[dict] = []
    for k in strikes_set:
        c = calls.get(k)
        p = puts.get(k)
        row: dict[str, Any] = {"strike": k}
        if c:
            crow = {kk: c[kk] for kk in ("code", "bid", "ask", "last", "mid", "oi", "change", "bid_vol", "ask_vol") if kk in c}
            if spot and days and c.get("mid"):
                iv = _estimate_iv_pct(spot, k, float(c["mid"]), days)
                if iv is not None:
                    crow["iv_est"] = iv
            row["call"] = crow
        if p:
            prow = {kk: p[kk] for kk in ("code", "bid", "ask", "last", "mid", "oi", "change", "bid_vol", "ask_vol") if kk in p}
            if spot and days and p.get("mid"):
                iv = _estimate_iv_pct(spot, k, float(p["mid"]), days)
                if iv is not None:
                    prow["iv_est"] = iv
            row["put"] = prow
        strike_rows.append(row)

    atm = None
    if spot and strike_rows:
        atm = min(strike_rows, key=lambda r: abs(r["strike"] - spot))["strike"]
    elif strike_rows:
        # fallback: densest OI around middle
        mid_i = len(strike_rows) // 2
        atm = strike_rows[mid_i]["strike"]

    band: list[dict] = []
    if atm is not None:
        ordered = sorted(strike_rows, key=lambda r: abs(r["strike"] - atm))
        band = sorted(ordered[: ATM_BAND * 2 + 1], key=lambda r: r["strike"])

    atm_iv = None
    if atm is not None:
        for r in strike_rows:
            if r["strike"] == atm:
                civ = (r.get("call") or {}).get("iv_est")
                piv = (r.get("put") or {}).get("iv_est")
                vals = [v for v in (civ, piv) if v is not None]
                if vals:
                    atm_iv = round(sum(vals) / len(vals), 1)
                break

    return {
        "code": month_code,
        "expire": expire,
        "days": days,
        "underlying": underlying,
        "underlying_name": underlying_name,
        "spot": round(spot, 2) if spot else None,
        "atm_strike": atm,
        "atm_iv_est": atm_iv,
        "n_strikes": len(strike_rows),
        "strikes": strike_rows,
        "atm_band": band,
    }


def fetch_board(product: str, *, front_n: int = FRONT_MONTHS) -> dict:
    """Fetch one product board: try CFFEX first (documented), then Sina."""
    product = product.lower()
    if product not in PRODUCTS:
        raise ValueError(f"unknown product {product}")
    meta = PRODUCTS[product]
    sess = _session()
    errors: list[str] = []
    source = "sina"

    official = try_official_cffex(sess)
    if official.get("ok"):
        source = "cffex"
        # Official delayed pages are mostly shells / proprietary feeds from this host;
        # still fall through to Sina for a usable chain until a stable CFFEX JSON is found.
        errors.append("cffex_reachable_but_no_chain_parser; using sina")
        source = "sina+cffex_reachable"
    else:
        for e in (official.get("errors") or [])[:3]:
            errors.append(f"cffex: {e}")

    months: list[str] = []
    try:
        months = list_months_sina(sess, product)
    except Exception as exc:
        errors.append(f"months: {type(exc).__name__}: {exc}")

    months = months[:front_n] if months else []
    month_payloads: list[dict] = []
    spot: float | None = None
    relate = meta["relate_fallback"]

    for mc in months:
        try:
            chain = fetch_chain_sina(sess, product, mc)
            info = chain.get("info") or {}
            relate = info.get("relate") or relate
            if spot is None:
                spot = fetch_spot_sina(sess, relate)
            month_payloads.append(_normalize_month(product, mc, chain, spot))
        except Exception as exc:
            errors.append(f"{mc}: {type(exc).__name__}: {exc}")

    return {
        "product": product,
        "short": meta["short"],
        "name": meta["name"],
        "index_id": meta["index_id"],
        "index_name": meta["index_name"],
        "source": source,
        "months": month_payloads,
        "errors": errors,
    }


def build_cn_index_options(*, front_n: int = FRONT_MONTHS, products: list[str] | None = None) -> dict:
    products = products or list(PRODUCTS.keys())
    boards: list[dict] = []
    all_errors: list[str] = []
    t0 = time.time()
    for p in products:
        try:
            board = fetch_board(p, front_n=front_n)
            boards.append(board)
            all_errors.extend(f"{p}: {e}" for e in (board.get("errors") or []))
        except Exception as exc:
            all_errors.append(f"{p}: FATAL {type(exc).__name__}: {exc}")
            boards.append(
                {
                    "product": p,
                    "short": PRODUCTS[p]["short"],
                    "name": PRODUCTS[p]["name"],
                    "index_id": PRODUCTS[p]["index_id"],
                    "index_name": PRODUCTS[p]["index_name"],
                    "source": "error",
                    "months": [],
                    "errors": [str(exc)],
                }
            )

    # by index_id for UI join
    by_index = {b["index_id"]: b for b in boards if b.get("index_id")}

    return {
        "updated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "sina_cffex_relay",
        "note": "中金所官网海外节点常不可达；期权链优先新浪转播延时行情。ATM IV 为估算值，非交易所官方隐含波动率。",
        "method": {
            "goal": "拉取 IO/HO/MO 近月期权链，服务 A 股指数看板",
            "official": "www.cffex.com.cn（探测失败则跳过，不阻塞）",
            "live": "新浪 OptionService.getOptionData + hq.sinajs.cn 标的现价",
            "iv": "Brenner–Subrahmanyam 近似，仅供参考",
        },
        "products": boards,
        "by_index": by_index,
        "errors": all_errors[:40],
        "elapsed_sec": round(time.time() - t0, 2),
    }


def write_snapshot(path: Path | None = None, **kwargs) -> dict:
    path = path or SEED_PATH
    payload = build_cn_index_options(**kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return payload


def load_seed() -> dict | None:
    if not SEED_PATH.exists():
        return None
    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("cn_index_options seed load failed", exc_info=True)
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="CFFEX IO/HO/MO options crawler")
    ap.add_argument("--write", action="store_true", help=f"write {SEED_PATH}")
    ap.add_argument("--front", type=int, default=FRONT_MONTHS, help="front months per product")
    ap.add_argument("--product", choices=list(PRODUCTS), action="append", help="limit products")
    args = ap.parse_args()
    if args.write:
        out = write_snapshot(front_n=args.front, products=args.product)
    else:
        out = build_cn_index_options(front_n=args.front, products=args.product)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:4000])
    for b in out.get("products") or []:
        months = b.get("months") or []
        n = sum(m.get("n_strikes") or 0 for m in months)
        codes = ",".join(m.get("code") or "?" for m in months) or "—"
        print(
            f"{b.get('short')}: months={codes} strikes={n} source={b.get('source')} "
            f"err={len(b.get('errors') or [])}"
        )
    if args.write:
        print(f"wrote {SEED_PATH} ({SEED_PATH.stat().st_size} bytes) elapsed={out.get('elapsed_sec')}s")


if __name__ == "__main__":
    main()
