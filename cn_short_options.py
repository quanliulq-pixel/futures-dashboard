#!/usr/bin/env python3
"""国内期权卖方候选（股指 IO/HO/MO + 商品期权）→ docs/api/cn_short_put.json / cn_short_call.json

设计要点
- 只使用实测可用的公开行情：新浪期权 T 型报价 OptionService.getOptionData、
  hq.sinajs.cn P_OP_<合约> 单合约报价（含报价时间）、nf_<期货合约> 标的期货报价、
  新浪期货合约规格页（交易单位）。所有外部文本只当数据解析。
- 活跃月份从新浪期权行情页动态发现，排除合约月 <= 当前北京月份的已到期/临近交割合约。
- 卖方每手权利金 = 买一价(bid) × 合约乘数；乘数未核验则为 null。不使用最新价/历史价当可成交权利金。
- 不计算 IV、保证金或年化收益（无已验证的求解器/保证金参数）——字段为 null 并说明。
- 刷新失败时保留各市场上一份有效结果并标记 stale，不用空结果覆盖。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "docs" / "api"
INDEX_SNAPSHOT = API_DIR / "cn_index_options.json"
OUT = {"put": API_DIR / "cn_short_put.json", "call": API_DIR / "cn_short_call.json"}
CST = timezone(timedelta(hours=8))
TIMEOUT = 10
UA = "Mozilla/5.0 (compatible; futures-dashboard/1.0)"

# 保守默认筛选
# 抓取范围（快照里只有此范围内的行；页面默认再收窄到 3–15% / OI≥100 / 7–60 天）
DEFAULTS = {
    "otm_min": 0.0,
    "otm_max": 0.25,
    "min_oi": 1,
    "max_spread_ratio": 0.5,  # (ask-bid)/mid 上限
    "index_dte_min": 1,
    "index_dte_max": 120,
    "front_months": 2,
}
UI_DEFAULTS = {"otm_min_pct": 3, "otm_max_pct": 15, "min_oi": 100, "dte_min": 7, "dte_max": 60}
FUTURE_TOLERANCE = timedelta(minutes=10)

# 商品期权：sina 产品码 / 交易所 / 期货代码前缀 / 期权代码前缀（P_OP 用）
COMMODITY_PRODUCTS = {
    "cu": {"name": "沪铜期权", "exchange": "shfe", "sina_product": "cu_o", "fut_root": "CU", "opt_root": "cu"},
    "au": {"name": "沪金期权", "exchange": "shfe", "sina_product": "au_o", "fut_root": "AU", "opt_root": "au"},
    "m": {"name": "豆粕期权", "exchange": "dce", "sina_product": "m_o", "fut_root": "M", "opt_root": "m"},
    "sr": {"name": "白糖期权", "exchange": "czce", "sina_product": "SR", "fut_root": "SR", "opt_root": "sr"},
}
INDEX_PRODUCTS = {"io": "IO", "ho": "HO", "mo": "MO"}
# 中金所官网合约表「合约乘数 每点人民币100元」「行权方式 欧式」「交割方式 现金交割」
# 「到期日 合约到期月份的第三个星期五」— 2026-11-06 读取以下官方页面核验：
INDEX_SPEC = {
    "io": {"multiplier": 100.0, "source": "http://www.cffex.com.cn/hs300gzqq/"},
    "ho": {"multiplier": 100.0, "source": "http://www.cffex.com.cn/sz50gzqq/"},
    "mo": {"multiplier": 100.0, "source": "http://www.cffex.com.cn/zz1000gzqq/"},
}
# 商品期权 1 手对应 1 手期货的规则尚未从交易所官方文件核验 → 不计算每手权利金。

Fetch = Callable[[str], str]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    return s


def make_fetch(sess: requests.Session | None = None) -> Fetch:
    sess = sess or _session()

    def fetch(url: str) -> str:
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        if "sinajs.cn" in url or "quotes/" in url:
            return r.content.decode("gb18030", errors="ignore")
        return r.text

    return fetch


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    except Exception:
        return None


def now_cst() -> datetime:
    return datetime.now(CST)


# ---------------------------------------------------------------- parsing helpers

def month_of(code: str) -> int | None:
    """'cu2612' / 'SR2701' → 2612 (YYMM)."""
    m = re.search(r"(\d{4})$", code) or re.search(r"(\d{4})[CP]", code, re.I)
    return int(m.group(1)) if m else None


def is_active_month(yymm: int | None, today: datetime) -> bool:
    """合约月份必须晚于当前月份；当月及以前的期权已到期（商品期权在标的交割月前一月到期）。"""
    if yymm is None:
        return False
    cur = (today.year % 100) * 100 + today.month
    return yymm > cur


def next_month_yymm(today: datetime) -> int:
    y, m = today.year % 100, today.month + 1
    if m == 13:
        y, m = y + 1, 1
    return y * 100 + m


def discover_months(html: str, root: str, today: datetime) -> list[str]:
    found = sorted({m.lower() for m in re.findall(rf"\b({re.escape(root)}\d{{4}})\b", html, re.I)})
    return [c for c in found if is_active_month(month_of(c), today)]


def parse_hq(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'var hq_str_([A-Za-z0-9_]+)="([^"]*)"', text):
        out[m.group(1)] = m.group(2).split(",") if m.group(2) else []
    return out


def parse_future_quote(parts: list[str]) -> dict | None:
    """商品期货 nf_ 报价：[0]名称 [1]时间HHMMSS [8]最新 [17]交易日。"""
    if not parts or len(parts) < 18:
        return None
    last = _f(parts[8])
    if not last or last <= 0:
        return None
    return {"name": parts[0], "last": last, "trade_date": parts[17] or None, "time": parts[1] or None}


def parse_option_quote(parts: list[str]) -> dict | None:
    """P_OP_ 报价：[1]买一 [2]最新 [3]卖一 [5]持仓 [7]行权价 [32]报价时间。"""
    if not parts or len(parts) < 33:
        return None
    ts = parts[32].strip() or None
    return {
        "bid": _f(parts[1]), "last": _f(parts[2]), "ask": _f(parts[3]),
        "oi": _f(parts[5]), "strike": _f(parts[7]), "quote_time": ts,
    }


def parse_multiplier(spec_html: str) -> float | None:
    text = re.sub(r"<[^>]+>", " ", spec_html)
    m = re.search(r"交易单位\s*([\d.]+)\s*(吨|克|千克|桶|立方米|元)", text)
    return _f(m.group(1)) if m else None


def parse_last_trade_rule(spec_html: str) -> str | None:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", spec_html))
    m = re.search(r"最后交易日\s*(\S{2,40})", text)
    return m.group(1) if m else None


def _parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text.strip()[:n], fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def freshness(quote_time: str | None, fetched: datetime) -> str:
    """报价时效分类（新浪转播本身即延时行情，任何状态都不是实时可成交价）。

    unknown        源未给报价时间
    invalid        报价时间晚于抓取时间 10 分钟以上（数据异常，剔除）
    delayed        30 分钟内（盘中延时）
    last_session   24 小时内（多为上一交易时段收盘后快照）
    stale          超过 24 小时（含周末/假期）——仅作参考
    """
    qt = _parse_ts(quote_time)
    if qt is None:
        return "unknown"
    age = fetched - qt
    if age < -FUTURE_TOLERANCE:
        return "invalid"
    if age <= timedelta(minutes=30):
        return "delayed"
    if age <= timedelta(days=1):
        return "last_session"
    return "stale"


def future_quote_ts(trade_date: str | None, hhmmss: str | None, fetched: datetime) -> str | None:
    """nf_ 报价的交易日在夜盘时是「下一交易日」，回退至不晚于抓取时间的最近日历日。"""
    d = _parse_ts(trade_date)
    if d is None or not hhmmss or not re.fullmatch(r"\d{6}", hhmmss):
        return None
    t = d.replace(hour=int(hhmmss[:2]), minute=int(hhmmss[2:4]), second=int(hhmmss[4:]))
    for back in range(0, 4):
        cand = t - timedelta(days=back)
        if cand <= fetched + FUTURE_TOLERANCE:
            return cand.strftime("%Y-%m-%d %H:%M:%S")
    return None


def is_expired_row(row: dict, today: datetime) -> bool:
    exp = _parse_ts(row.get("expiry"))
    if exp is not None:
        return exp.date() < today.date()
    if row.get("market") == "cn_commodity":
        return not is_active_month(month_of(str(row.get("contract_month") or "")), today)
    return True  # 股指行必须有到期日


# ---------------------------------------------------------------- candidate filter

def executable_bid(bid: float | None, ask: float | None, max_spread_ratio: float) -> bool:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return False
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid <= max_spread_ratio


def otm_ok(side: str, strike: float | None, und: float | None, lo: float, hi: float) -> float | None:
    """返回虚值比例；不满足则 None。side: put/call。"""
    if not strike or not und or und <= 0:
        return None
    otm = (und - strike) / und if side == "put" else (strike - und) / und
    return round(otm, 4) if lo <= otm <= hi else None


def make_candidate(side: str, base: dict, q: dict, und: float | None, multiplier: float | None,
                   fetched: datetime, cfg: dict) -> dict | None:
    if not executable_bid(q.get("bid"), q.get("ask"), cfg["max_spread_ratio"]):
        return None
    if (q.get("oi") or 0) < cfg["min_oi"]:
        return None
    otm = otm_ok(side, q.get("strike"), und, cfg["otm_min"], cfg["otm_max"])
    if otm is None:
        return None
    fr = freshness(q.get("quote_time"), fetched)
    if fr == "invalid":
        return None
    bid = q["bid"]
    return {
        **base,
        "side": side,
        "strike": q["strike"],
        "bid": bid,
        "ask": q["ask"],
        "spread": round(q["ask"] - bid, 4),
        "oi": int(q["oi"]) if q.get("oi") is not None else None,
        "otm_pct": round(otm * 100, 2),
        "underlying_price": und,
        "multiplier": multiplier,
        "premium_ref_per_lot_bid": round(bid * multiplier, 2) if multiplier else None,
        "quote_time": q.get("quote_time"),
        "freshness": fr,
        "iv": None,
        "margin": None,
    }


# ---------------------------------------------------------------- builders

def _batch_quotes(fetch: Fetch, keys: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i in range(0, len(keys), 40):
        chunk = keys[i:i + 40]
        out.update(parse_hq(fetch("https://hq.sinajs.cn/list=" + ",".join(chunk))))
    return out


def build_commodity(fetch: Fetch, today: datetime, cfg: dict) -> dict:
    rows: dict[str, list[dict]] = {"put": [], "call": []}
    products: list[dict] = []
    errors: list[str] = []
    for key, meta in COMMODITY_PRODUCTS.items():
        try:
            page = fetch(f"https://stock.finance.sina.com.cn/futures/view/optionsDP.php/{meta['sina_product']}/{meta['exchange']}")
            months = discover_months(page, meta["opt_root"], today)[: cfg["front_months"]]
            if not months:
                errors.append(f"{key}: 未发现活跃月份")
                continue
            for mc in months:
                yymm = month_of(mc)
                fut_code = f"{meta['fut_root']}{yymm}"
                spec = fetch(f"https://finance.sina.com.cn/futures/quotes/{fut_code}.shtml")
                fut_unit = parse_multiplier(spec)
                fq = parse_future_quote(_batch_quotes(fetch, [f"nf_{fut_code}"]).get(f"nf_{fut_code}", []))
                if not fq:
                    errors.append(f"{mc}: 标的期货 {fut_code} 无报价")
                    continue
                und_ts = future_quote_ts(fq["trade_date"], fq["time"], today)
                und_status = freshness(und_ts, today)
                if und_status == "invalid":
                    errors.append(f"{mc}: 标的期货时间异常")
                    continue
                pin = (meta["fut_root"] + str(yymm)) if meta["exchange"] == "czce" else mc
                raw = json.loads(fetch(
                    "https://stock.finance.sina.com.cn/futures/api/openapi.php/OptionService.getOptionData"
                    f"?type=futures&product={meta['sina_product']}&exchange={meta['exchange']}&pinzhong={pin}"))
                data = ((raw or {}).get("result") or {}).get("data") or {}
                codes = [str(r[-1]) for r in (data.get("up") or []) + (data.get("down") or []) if r]
                # P_OP 需小写品种根
                qkeys = ["P_OP_" + re.sub(r"^[A-Za-z]+", meta["opt_root"], c) for c in codes]
                qs = _batch_quotes(fetch, qkeys)
                base = {
                    "market": "cn_commodity", "exchange": meta["exchange"].upper(), "product": key,
                    "product_name": meta["name"], "contract_month": mc,
                    "underlying_contract": fut_code, "underlying_trade_date": fq["trade_date"],
                    "underlying_time": und_ts, "underlying_status": und_status,
                    "candidate_label": "观察候选：到期日未核验，交易前须核验",
                    "futures_trade_unit": fut_unit,
                    "expiry": None, "dte": None, "expiry_status": "unverified",
                    # 标的为下月合约的期权通常在本月到期：到期日未核验，提示临近到期
                    "near_expiry_risk": yymm == next_month_yymm(today),
                    "underlying_last_trade_rule": parse_last_trade_rule(spec),
                    "exercise_style": None, "settlement": "行权后成为标的期货头寸（实物交割期货的期权）",
                    "multiplier_source": None,
                }
                n = 0
                for c, k in zip(codes, qkeys):
                    q = parse_option_quote(qs.get(k, []))
                    if not q:
                        continue
                    side = "call" if re.search(r"\d{4}C", c, re.I) else "put"
                    cand = make_candidate(side, {**base, "option_code": c}, q, fq["last"], None, today, cfg)
                    if cand:
                        rows[side].append(cand)
                        n += 1
                products.append({"product": key, "month": mc, "underlying": fut_code,
                                 "underlying_price": fq["last"], "chain_size": len(codes), "candidates": n})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
    return {"rows": rows, "products": products, "errors": errors}


def build_index(fetch: Fetch, today: datetime, cfg: dict, snapshot: dict | None) -> dict:
    rows: dict[str, list[dict]] = {"put": [], "call": []}
    errors: list[str] = []
    products: list[dict] = []
    if not snapshot:
        return {"rows": rows, "products": products, "errors": ["缺少 cn_index_options.json 快照"]}
    for prod in snapshot.get("products") or []:
        pk = str(prod.get("product") or "").lower()
        if pk not in INDEX_PRODUCTS:
            continue
        for m in (prod.get("months") or [])[: cfg["front_months"]]:
            try:
                exp = m.get("expire")
                try:
                    exp_d = datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=CST) if exp else None
                except ValueError:
                    exp_d = None
                if exp_d is None or exp_d.date() < today.date():
                    errors.append(f"{m.get('code')}: 到期日缺失或已过 ({exp})")
                    continue
                dte = (exp_d.date() - today.date()).days
                if not (cfg["index_dte_min"] <= dte <= cfg["index_dte_max"]):
                    continue
                spot = _f(m.get("spot"))
                snap_dt = None
                try:
                    snap_dt = datetime.fromisoformat(str(snapshot.get("fetched_at"))).astimezone(CST)
                except ValueError:
                    pass
                snap_ts = snap_dt.strftime("%Y-%m-%d %H:%M:%S") if snap_dt else None
                if freshness(snap_ts, today) == "invalid":
                    errors.append(f"{m.get('code')}: 指数快照时间异常")
                    continue
                # 快照只有抓取时间，没有指数报价时间 → 标的时效记为 unknown，单列快照抓取时间
                und_ts, und_status = None, "unknown"
                codes = []
                for s in m.get("strikes") or []:
                    for side in ("call", "put"):
                        c = (s.get(side) or {}).get("code")
                        if c:
                            codes.append(c)
                qs = _batch_quotes(fetch, ["P_OP_" + c for c in codes])  # 实时刷新 bid/ask 与报价时间
                base = {
                    "market": "cn_index", "exchange": "CFFEX", "product": INDEX_PRODUCTS[pk],
                    "product_name": prod.get("name"), "contract_month": m.get("code"),
                    "underlying_contract": m.get("underlying"), "underlying_name": m.get("underlying_name"),
                    "expiry": exp, "dte": dte, "expiry_status": "source:sina_option_info",
                    "exercise_style": "欧式", "settlement": "现金交割（股指期权）",
                    "multiplier_source": INDEX_SPEC[pk]["source"],
                    "candidate_label": "股指期权候选（延时行情，交易前须核对）",
                    "underlying_price_source": "cn_index_options.json spot（指数现货，非期货）",
                    "underlying_time": und_ts, "underlying_status": und_status,
                    "underlying_snapshot_at": snap_ts,
                }
                n = 0
                for c in codes:
                    q = parse_option_quote(qs.get("P_OP_" + c, []))
                    if not q:
                        continue
                    side = "call" if re.search(r"\d{4}C", c, re.I) else "put"
                    cand = make_candidate(side, {**base, "option_code": c}, q, spot,
                                          INDEX_SPEC[pk]["multiplier"], today, cfg)
                    if cand:
                        rows[side].append(cand)
                        n += 1
                products.append({"product": INDEX_PRODUCTS[pk], "month": m.get("code"), "expiry": exp,
                                 "underlying_price": spot, "chain_size": len(codes), "candidates": n})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{m.get('code')}: {type(exc).__name__}: {exc}")
    return {"rows": rows, "products": products, "errors": errors}


NOTES = {
    "premium": "每手权利金参考(买一) = 买一价 × 合约乘数，仅股指（乘数经中金所官网核验）计算；来自延时转播行情，仅供参考，不保证可成交。商品期权乘数未核验，不计算。",
    "iv": "未计算隐含波动率（尚无经验证的 Black-76 求解器）。",
    "margin": "不提供保证金/收益率：卖方保证金按交易所及期货公司规则另算。",
    "freshness": "delayed=30分钟内延时；last_session=24小时内（多为上一交易时段收盘快照）；stale=超过24小时仅供参考；unknown=无时间戳。均非实时行情。",
    "commodity": "商品期权为观察候选：期权到期日未核验，交易前须核验；标的为下月期货的标注「待核验到期风险」。",
}


def _sort(side_rows: list[dict]) -> list[dict]:
    return sorted(side_rows, key=lambda r: (r["product"], r["contract_month"], -r["otm_pct"]))


def merge_market(prev: dict | None, fresh: dict, side: str, fetched: datetime) -> dict:
    """只有在本次刷新报错且无行时才保留上一份结果（剔除已到期行并显著标 stale）；
    成功但 0 命中不复活旧行。"""
    rows = _sort([r for r in fresh["rows"][side] if not is_expired_row(r, fetched)])
    block = {
        "rows": rows, "products": fresh["products"], "errors": fresh["errors"],
        "fetched_at": fetched.isoformat(timespec="seconds"), "stale": False,
        "last_good_at": fetched.isoformat(timespec="seconds") if rows else None,
    }
    if rows or not fresh["errors"]:
        if not rows:
            block["empty_reason"] = "本次刷新成功，但无满足条件的候选"
        return block
    kept_rows = [r for r in (prev or {}).get("rows") or [] if not is_expired_row(r, fetched)]
    if kept_rows:
        return {
            **prev, "rows": kept_rows, "stale": True,
            "stale_reason": "本次刷新失败，显示上次成功结果",
            "refresh_failed_at": block["fetched_at"], "errors": fresh["errors"],
        }
    block["empty_reason"] = "刷新失败：" + "; ".join(fresh["errors"])
    return block


def build_all(fetch: Fetch | None = None, today: datetime | None = None, cfg: dict | None = None,
              write: bool = False) -> dict[str, dict]:
    fetch = fetch or make_fetch()
    today = today or now_cst()
    cfg = {**DEFAULTS, **(cfg or {})}
    snap = None
    if INDEX_SNAPSHOT.exists():
        try:
            snap = json.loads(INDEX_SNAPSHOT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            snap = None
    idx = build_index(fetch, today, cfg, snap)
    com = build_commodity(fetch, today, cfg)
    out: dict[str, dict] = {}
    for side, path in OUT.items():
        prev = {}
        if path.exists():
            try:
                prev = json.loads(path.read_text(encoding="utf-8")).get("markets") or {}
            except Exception:  # noqa: BLE001
                prev = {}
        payload = {
            "side": side,
            "updated_at": today.strftime("%Y-%m-%d %H:%M:%S CST"),
            "fetch_range": cfg,
            "ui_defaults": UI_DEFAULTS,
            "notes": NOTES,
            "sources": {
                "chain": "新浪 OptionService.getOptionData（月份/合约列表）",
                "quote": "hq.sinajs.cn P_OP_*（买一/卖一/持仓/报价时间）",
                "underlying": "hq.sinajs.cn nf_*（商品期货）；股指用 cn_index_options.json 现货",
                "spec": "新浪期货合约规格页（交易单位）",
            },
            "markets": {
                "cn_index": merge_market(prev.get("cn_index"), idx, side, today),
                "cn_commodity": merge_market(prev.get("cn_commodity"), com, side, today),
            },
        }
        out[side] = payload
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    res = build_all(write=a.write)
    for side, p in res.items():
        for mk, blk in p["markets"].items():
            print(side, mk, len(blk["rows"]), "stale" if blk.get("stale") else "", blk.get("errors")[:3])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
