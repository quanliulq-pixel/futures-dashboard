# -*- coding: utf-8 -*-
"""沪深两市主力净流入历史（新浪板块资金流向，非东方财富 push2）。

新浪 MoneyFlow.ssl_bkzj_zjlrqs：主力净流入 = 特大单（r0）净额，按成交单规模划分的
估算口径，不是交易所官方申报数据，也不同于东方财富「主力=超大单+大单」。
沪市用上证指数板块 zhishu_000001（上交所上市股票合计）；深市用深证综指板块
zhishu_399106（深交所上市股票合计，不用只含成分股的深证成指）；两市合计 = 同一
交易日相加，不含北交所。

只在后台 refresh 时联网；请求线程只读已提交的 seed / 运行时缓存。缺数据一律「无数据」，不插补。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
YI = 1e8
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
PAGE_SIZE = 2000
WINDOWS = (1, 5, 20)

SINA_ZJLRQS_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_zjlrqs"
SINA_PAGE = "https://vip.stock.finance.sina.com.cn/moneyflow/"

MARKET_SPEC = {
    "main_sh": {
        "label": "沪市",
        "bankuai": "zhishu_000001",
        "index_name": "上证指数",
        "index_code": "000001",
        "universe": "上交所上市股票（上证指数板块）",
    },
    "main_sz": {
        "label": "深市",
        "bankuai": "zhishu_399106",
        "index_name": "深证综指",
        "index_code": "399106",
        "universe": "深交所上市股票（深证综指板块，非仅成指成分）",
    },
}
MARKET_KEYS = ("main_sh", "main_sz", "main_total")
CHART_KEY_MAP = {"margin_sh": "main_sh", "margin_sz": "main_sz", "margin_total": "main_total"}

SRC = {
    "label": "新浪财经 板块资金流向",
    "url": SINA_PAGE,
    "function": "MoneyFlow.ssl_bkzj_zjlrqs",
    "definition": (
        "主力净流入=特大单（r0）净额，为新浪按成交单规模划分的估算口径，不是交易所官方数据；"
        "与东方财富「主力=超大单+大单」不同。沪市用上证指数板块，深市用深证综指板块"
        "（覆盖深市全部上市股票），两市合计为同一日相加（不含北交所）。"
    ),
}

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / "data" / "main_flow_history.json"
RUNTIME_PATH = ROOT / "data" / "main_flow_history_latest.json"
NO_DATA = "无数据"


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("/", "-")
    if " " in s:
        s = s.split(" ")[0]
    if s.isdigit() and len(s) == 8:
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        if isinstance(v, str):
            v = v.replace(",", "")
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _r(x: float | None, n: int = 4) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _get_json(url: str, params: dict, headers: dict | None = None,
              retries: int = HTTP_RETRIES) -> Any:
    import requests

    h = {"User-Agent": UA, "Referer": SINA_PAGE}
    h.update(headers or {})
    last_exc: Exception | None = None
    for i in range(max(1, retries)):
        try:
            r = requests.get(url, params=params, headers=h, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError("empty body")
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1.0 * (i + 1))
    raise last_exc or RuntimeError("request failed")


def parse_sina_zjlrqs(js: Any) -> list[tuple[date, float]]:
    """新浪 ssl_bkzj_zjlrqs 一页 → [(date, 主力净流入亿元)]，缺 r0_net 的行丢弃。"""
    rows = js if isinstance(js, list) else []
    out: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _to_date(row.get("opendate") or row.get("date"))
        v = _num(row.get("r0_net"))
        if d is not None and v is not None:
            out.append((d, v / YI))
    out.sort()
    return out


def merge_series(*seqs: list[tuple[date, float]]) -> list[tuple[date, float]]:
    by: dict[date, float] = {}
    for seq in seqs:
        for d, v in seq or []:
            by[d] = v
    return sorted(by.items())


def total_series(sh: list[tuple[date, float]], sz: list[tuple[date, float]]) -> list[tuple[date, float]]:
    sh_by, sz_by = dict(sh or []), dict(sz or [])
    return sorted((d, sh_by[d] + sz_by[d]) for d in set(sh_by) & set(sz_by))


def series_from_rows(rows: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for item in rows or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        d, v = _to_date(item[0]), _num(item[1])
        if d is not None and v is not None:
            out.append((d, v))
    return sorted(out)


def rows_from_series(series: list[tuple[date, float]], *, n: int = 4) -> list[list]:
    return [[d.isoformat(), _r(v, n)] for d, v in series]


def window_sum(series: list[tuple[date, float]], n: int) -> float | None:
    """最近 n 个交易日合计；不足 n 天返回 None，不拿短窗口冒充。"""
    if not series or len(series) < n:
        return None
    vals = [v for _, v in series[-n:]]
    if any(v is None for v in vals):
        return None
    return _r(sum(vals), 4)


def stats_for(series: list[tuple[date, float]], key: str) -> dict:
    spec = {
        "main_sh": MARKET_SPEC["main_sh"],
        "main_sz": MARKET_SPEC["main_sz"],
        "main_total": {
            "label": "两市合计",
            "bankuai": "zhishu_000001+zhishu_399106",
            "index_name": "沪深合计",
            "index_code": "",
            "universe": "上证指数板块+深证综指板块同一交易日相加（不含北交所）",
        },
    }[key]
    base = {
        "status": "missing",
        "reason": NO_DATA,
        "label": spec["label"],
        "index_name": spec["index_name"],
        "index_code": spec.get("index_code") or None,
        "universe": spec["universe"],
        "asof": None,
        "net_1d": None,
        "net_5d": None,
        "net_20d": None,
        "hist_n": 0,
        "hist_coverage_start": None,
        "hist_coverage_end": None,
        "source": SRC["label"],
        "source_url": SRC["url"],
        "definition": SRC["definition"],
        "unit": "亿元",
    }
    if not series:
        return base
    series = sorted(series)
    out = dict(base)
    out.update({
        "status": "ok",
        "reason": None,
        "asof": series[-1][0].isoformat(),
        "net_1d": window_sum(series, 1),
        "net_5d": window_sum(series, 5),
        "net_20d": window_sum(series, 20),
        "hist_n": len(series),
        "hist_coverage_start": series[0][0].isoformat(),
        "hist_coverage_end": series[-1][0].isoformat(),
    })
    return out


def load_history_seed(paths: tuple[Path, ...] | None = None) -> dict | None:
    cands: list[dict] = []
    for p in paths or (RUNTIME_PATH, SEED_PATH):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("main flow seed unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, dict) and (data.get("sh") or data.get("sz")):
            data = dict(data)
            data["_path"] = str(p)
            cands.append(data)
    if not cands:
        return None

    def _asof_key(d: dict) -> str:
        a = d.get("asof") or {}
        if isinstance(a, dict):
            return max((str(v) for v in a.values() if v), default="")
        return str(a or "")

    return max(cands, key=_asof_key)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_history_seed(payload: dict, path: Path | None = None, *, pretty: bool = False) -> Path:
    path = path or RUNTIME_PATH
    body = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    text = json.dumps(body, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    _atomic_write(path, text + ("\n" if pretty else ""))
    return path


def seed_series(seed: dict | None) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    if not isinstance(seed, dict):
        return [], []
    return series_from_rows(seed.get("sh")), series_from_rows(seed.get("sz"))


def fetch_main_flow_history(bankuai: str, *, pages: int | None = None, page_size: int = PAGE_SIZE,
                            get_json: Callable[..., Any] | None = None) -> list[tuple[date, float]]:
    get_json = get_json or _get_json
    all_rows: list[tuple[date, float]] = []
    max_pages = pages or 8
    for page in range(1, max_pages + 1):
        params = {
            "page": str(page),
            "num": str(page_size),
            "sort": "opendate",
            "asc": "0",
            "bankuai": bankuai,
        }
        js = get_json(SINA_ZJLRQS_URL, params)
        part = parse_sina_zjlrqs(js)
        all_rows.extend(part)
        if len(part) < page_size:
            break
    return merge_series(all_rows)


def _next_day(d: date | None) -> date | None:
    return (d + timedelta(days=1)) if d else None


def refresh_history(*, live: bool = True,
                    fetch: Callable[..., list[tuple[date, float]]] | None = None,
                    seed: dict | None = None,
                    today: date | None = None,
                    write: bool = True) -> dict:
    """合并 seed 与（可选）增量抓取。live=False 时不联网。"""
    today = today or datetime.now(CST).date()
    seed = seed if seed is not None else load_history_seed()
    sh, sz = seed_series(seed)
    sh_live = sz_live = False
    errors: dict[str, str] = {}

    if live:
        fn = fetch or fetch_main_flow_history
        for key, have, attr in (("sh", sh, "sh_live"), ("sz", sz, "sz_live")):
            spec = MARKET_SPEC["main_sh" if key == "sh" else "main_sz"]
            need_full = not have
            pages = 8 if need_full else 1
            page_size = PAGE_SIZE if need_full else 260
            try:
                part = fn(spec["bankuai"], pages=pages, page_size=page_size)
                if key == "sh":
                    sh = merge_series(have, part)
                    sh_live = True
                else:
                    sz = merge_series(have, part)
                    sz_live = True
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{type(exc).__name__}: {str(exc)[:80]}"
                logger.warning("main flow %s fetch failed: %s", key, errors[key])

    tot = total_series(sh, sz)
    stats = {
        "main_sh": stats_for(sh, "main_sh"),
        "main_sz": stats_for(sz, "main_sz"),
        "main_total": stats_for(tot, "main_total"),
    }
    coverage_start = None
    for seq in (sh, sz):
        if seq:
            d = seq[0][0].isoformat()
            coverage_start = min(coverage_start, d) if coverage_start else d
    asof = {
        "sh": sh[-1][0].isoformat() if sh else None,
        "sz": sz[-1][0].isoformat() if sz else None,
    }
    payload = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "coverage_start": coverage_start,
        "asof": asof,
        "sources": SRC,
        "sh": rows_from_series(sh),
        "sz": rows_from_series(sz),
        "stats": stats,
        "live_ok": bool(live and (sh_live or sz_live)),
        "errors": errors,
    }
    if not payload["live_ok"]:
        if sh or sz:
            payload["from_snapshot"] = True
            payload["snapshot_asof"] = max((v for v in asof.values() if v), default=None)
        else:
            payload["from_snapshot"] = False
    else:
        payload["from_snapshot"] = False
        if write and (sh or sz):
            try:
                save_history_seed(payload, RUNTIME_PATH)
            except Exception:  # noqa: BLE001
                logger.warning("write main flow runtime cache failed", exc_info=True)
    return payload


def chart_bundle(payload: dict | None) -> dict:
    if not isinstance(payload, dict) or not (payload.get("sh") or payload.get("sz") or payload.get("stats")):
        return {"status": "missing", "reason": "无数据", "markets": {}}
    sh, sz = seed_series(payload)
    tot = total_series(sh, sz)
    stats = payload.get("stats") or {}
    series_map = {"main_sh": sh, "main_sz": sz, "main_total": tot}
    markets: dict[str, dict] = {}
    for key in MARKET_KEYS:
        st = stats.get(key) or stats_for(series_map[key], key)
        markets[key] = dict(st)
        markets[key]["series"] = rows_from_series(series_map[key])
    any_series = any(m.get("series") for m in markets.values())
    out = {
        "status": "ok" if any_series else "missing",
        "coverage_start": payload.get("coverage_start"),
        "asof": payload.get("asof"),
        "fetched_at": payload.get("fetched_at"),
        "from_snapshot": bool(payload.get("from_snapshot")),
        "sources": payload.get("sources") or SRC,
        "markets": markets,
        "chart_key_map": dict(CHART_KEY_MAP),
    }
    if payload.get("from_snapshot"):
        out["snapshot_asof"] = payload.get("snapshot_asof")
    if not any_series:
        out["reason"] = "无数据"
    return out


def public_history_bundle(*, live: bool = False, seed: dict | None = None,
                          today: date | None = None) -> dict:
    payload = refresh_history(live=live, write=live, seed=seed, today=today)
    return chart_bundle(payload)


def attach_main_flow(bundle: dict | None, payload: dict | None) -> dict | None:
    """把沪/深/合计最新 1/5/20 日挂到资金流 bundle，不改既有东方财富主力字段。"""
    if not isinstance(bundle, dict):
        return bundle
    if not isinstance(payload, dict) or not payload.get("stats"):
        return bundle
    out = dict(bundle)
    market = dict(out.get("market") or {})
    snap = bool(payload.get("from_snapshot"))
    snap_asof = payload.get("snapshot_asof")
    for key in MARKET_KEYS:
        st = dict(payload["stats"].get(key) or {})
        if snap and st:
            st["from_snapshot"] = True
            if snap_asof:
                st["snapshot_asof"] = snap_asof
        else:
            st.pop("from_snapshot", None)
            st.pop("snapshot_asof", None)
        market[key] = st
    out["market"] = market
    out["main_flow_coverage_start"] = payload.get("coverage_start")
    out["main_flow_source"] = SRC["label"]
    out["main_flow_definition"] = SRC["definition"]
    if snap:
        out["main_flow_from_snapshot"] = True
        if snap_asof:
            out["main_flow_snapshot_asof"] = snap_asof
    else:
        out.pop("main_flow_from_snapshot", None)
        out.pop("main_flow_snapshot_asof", None)
    return out


def ensure_main_flow(bundle: dict | None, *, live: bool = False) -> dict | None:
    if not isinstance(bundle, dict):
        return bundle
    if not live:
        mk = bundle.get("market") or {}
        if isinstance(mk, dict) and all(
            isinstance(mk.get(k), dict) and mk[k].get("status") == "ok" and mk[k].get("net_1d") is not None
            for k in MARKET_KEYS
        ):
            return bundle
    try:
        payload = refresh_history(live=live, write=live)
        return attach_main_flow(bundle, payload)
    except Exception:  # noqa: BLE001
        logger.warning("ensure main flow history failed", exc_info=True)
        return bundle


__all__ = [
    "MARKET_SPEC", "MARKET_KEYS", "CHART_KEY_MAP", "SRC", "SEED_PATH", "RUNTIME_PATH",
    "parse_sina_zjlrqs", "merge_series", "total_series", "window_sum", "stats_for",
    "series_from_rows", "rows_from_series", "load_history_seed", "save_history_seed",
    "fetch_main_flow_history", "refresh_history", "chart_bundle", "public_history_bundle",
    "attach_main_flow", "ensure_main_flow",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_seed = os.environ.get("WRITE_MAIN_FLOW_SEED") == "1"
    payload = refresh_history(live=True, write=True)
    if write_seed:
        save_history_seed(payload, SEED_PATH)
        print("wrote", SEED_PATH, "sh", len(payload.get("sh") or []), "sz", len(payload.get("sz") or []))
    import pprint
    for k, v in (payload.get("stats") or {}).items():
        print("====", k)
        pprint.pp({kk: v[kk] for kk in ("status", "asof", "net_1d", "net_5d", "net_20d",
                                        "hist_n", "hist_coverage_start")})
    print("coverage_start", payload.get("coverage_start"), "asof", payload.get("asof"),
          "live_ok", payload.get("live_ok"), "errors", payload.get("errors"))
