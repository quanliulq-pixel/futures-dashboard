# -*- coding: utf-8 -*-
"""各 A 股指数卡片「主力净流入」：新浪板块资金流向（特大单 r0），不走东方财富 push2。

MoneyFlow.ssl_bkzj_zjlrqs，与两市合计同一接口、同一口径（主力=特大单 r0 净额，
不是交易所官方申报，也不同于东方财富「主力=超大单+大单」）。

指数 → Sina bankuai 对照（经 ssi_bankuai_mingcheng + 历史接口核验）：
- 沪深300  csi300  → hs300            （跨市场「沪深300」板块，有历史）
- 科创50   star50  → zhishu_000688    （有历史）
- 创业板指 chinext → zhishu_399006    （有历史）
- 上证50   sse50   → 无               （HQ 指数成分节点 zhishu_000016 不是资金流板块，
                                      mingcheng 为 null，zjlrqs 返回空；显示无数据）
- 中证500  csi500  → 无               （同上，zhishu_000905 无资金流板块）
- 中证1000 csi1000 → 无               （Sina 资金流/指数成分均无 000852 板块）

只在后台 refresh 时联网；请求线程只读已提交的 seed / 运行时缓存。缺数据一律「无数据」，
不插补，不回退东方财富 push2。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from main_flow_history import (
    CST,
    HTTP_RETRIES,
    PAGE_SIZE,
    SINA_PAGE,
    SINA_ZJLRQS_URL,
    SRC as MARKET_SRC,
    WINDOWS,
    _r,
    fetch_main_flow_history,
    load_history_seed,
    merge_series,
    rows_from_series,
    save_history_seed,
    series_from_rows,
    window_sum,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / "data" / "index_main_flow_history.json"
RUNTIME_PATH = ROOT / "data" / "index_main_flow_history_latest.json"
BUNDLE_SEED_PATH = ROOT / "data" / "cn_index_flows.json"
NO_DATA = "无数据"

# bankuai=None：新浪资金流未提供该指数板块，保持 无数据。
INDEX_BOARDS: dict[str, dict] = {
    "csi300": {
        "id": "csi300",
        "name": "沪深300",
        "bankuai": "hs300",
        "index_code": "000300",
        "universe": "沪深300指数成分股（Sina 跨市场板块 hs300）",
    },
    "sse50": {
        "id": "sse50",
        "name": "上证50",
        "bankuai": None,
        "index_code": "000016",
        "universe": "上证50指数成分股",
        "missing_reason": (
            "新浪板块资金流向未提供上证50板块（ssi_bankuai_mingcheng 无名称，"
            "MoneyFlow.ssl_bkzj_zjlrqs 对 zhishu_000016 返回空），不回退东方财富 push2"
        ),
    },
    "csi1000": {
        "id": "csi1000",
        "name": "中证1000",
        "bankuai": None,
        "index_code": "000852",
        "universe": "中证1000指数成分股",
        "missing_reason": (
            "新浪板块资金流向未提供中证1000板块（行情中心指数成分与资金流接口均无 000852），"
            "不回退东方财富 push2"
        ),
    },
    "csi500": {
        "id": "csi500",
        "name": "中证500",
        "bankuai": None,
        "index_code": "000905",
        "universe": "中证500指数成分股",
        "missing_reason": (
            "新浪板块资金流向未提供中证500板块（ssi_bankuai_mingcheng 无名称，"
            "MoneyFlow.ssl_bkzj_zjlrqs 对 zhishu_000905 返回空），不回退东方财富 push2"
        ),
    },
    "star50": {
        "id": "star50",
        "name": "科创50",
        "bankuai": "zhishu_000688",
        "index_code": "000688",
        "universe": "科创50指数成分股（Sina 板块 zhishu_000688）",
    },
    "chinext": {
        "id": "chinext",
        "name": "创业板指",
        "bankuai": "zhishu_399006",
        "index_code": "399006",
        "universe": "创业板指数成分股（Sina 板块 zhishu_399006）",
    },
}

SRC = {
    "label": "新浪财经 板块资金流向",
    "url": SINA_PAGE,
    "function": "MoneyFlow.ssl_bkzj_zjlrqs",
    "definition": (
        "主力净流入=特大单（r0）净额，为新浪按成交单规模划分的估算口径，不是交易所官方数据；"
        "与东方财富「主力=超大单+大单」不同。各指数用对应新浪指数板块的成分股合计；"
        "新浪未提供板块的指数显示无数据，不回退东方财富 push2。"
    ),
}


def definition_for(meta: dict) -> str:
    base = (
        "主力净流入=特大单（r0）净额，为新浪按成交单规模划分的估算口径，不是交易所官方数据；"
        "与东方财富「主力=超大单+大单」不同。"
    )
    if meta.get("bankuai"):
        return (
            f"{base}本项为{meta['name']}指数板块（Sina bankuai={meta['bankuai']}）"
            f"成分股合计。"
        )
    return base + (meta.get("missing_reason") or "新浪未提供该指数板块，显示无数据。")


def _missing_main(meta: dict, reason: str | None = None) -> dict:
    return {
        "status": "missing",
        "reason": reason or meta.get("missing_reason") or NO_DATA,
        "asof": None,
        "net_1d": None,
        "net_5d": None,
        "net_20d": None,
        "unit": "亿元",
        "source": SRC["label"],
        "source_url": SRC["url"],
        "definition": definition_for(meta),
        "bankuai": meta.get("bankuai"),
        "index_code": meta.get("index_code"),
    }


def stats_for_index(series: list[tuple[date, float]], meta: dict) -> dict:
    base = _missing_main(meta)
    if not series:
        if not meta.get("bankuai"):
            return base
        base["reason"] = "无数据"
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


def load_index_main_seed(paths: tuple[Path, ...] | None = None) -> dict | None:
    return load_history_seed(paths=paths or (RUNTIME_PATH, SEED_PATH))


# load_history_seed looks for sh/sz keys. Our seed uses "series" instead.
# Need a custom loader.
def load_index_history_seed(paths: tuple[Path, ...] | None = None) -> dict | None:
    cands: list[dict] = []
    for p in paths or (RUNTIME_PATH, SEED_PATH):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("index main seed unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, dict) and (data.get("series") or data.get("stats")):
            data = dict(data)
            data["_path"] = str(p)
            cands.append(data)
    if not cands:
        return None

    def _asof_key(d: dict) -> str:
        a = d.get("asof")
        if isinstance(a, dict):
            return max((str(v) for v in a.values() if v), default="")
        return str(a or "")

    return max(cands, key=_asof_key)


def seed_series_map(seed: dict | None) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    if not isinstance(seed, dict):
        return out
    raw = seed.get("series") or {}
    for iid in INDEX_BOARDS:
        out[iid] = series_from_rows(raw.get(iid))
    return out


def refresh_index_main_history(*, live: bool = True,
                               fetch: Callable[..., list[tuple[date, float]]] | None = None,
                               seed: dict | None = None,
                               today: date | None = None,
                               write: bool = True) -> dict:
    """合并 seed 与（可选）增量抓取。live=False 时不联网。无板块的指数不请求。"""
    today = today or datetime.now(CST).date()
    seed = seed if seed is not None else load_index_history_seed()
    have = seed_series_map(seed)
    live_ok_ids: list[str] = []
    errors: dict[str, str] = {}

    if live:
        fn = fetch or fetch_main_flow_history
        for iid, meta in INDEX_BOARDS.items():
            bk = meta.get("bankuai")
            if not bk:
                continue
            existing = have.get(iid) or []
            pages = 8 if not existing else 1
            page_size = PAGE_SIZE if not existing else 260
            try:
                part = fn(bk, pages=pages, page_size=page_size)
                have[iid] = merge_series(existing, part)
                if part:
                    live_ok_ids.append(iid)
            except Exception as exc:  # noqa: BLE001
                errors[iid] = f"{type(exc).__name__}: {str(exc)[:80]}"
                logger.warning("index main flow %s (%s) fetch failed: %s", iid, bk, errors[iid])

    stats = {iid: stats_for_index(have.get(iid) or [], meta)
             for iid, meta in INDEX_BOARDS.items()}
    asof = {iid: st.get("asof") for iid, st in stats.items() if st.get("asof")}
    coverage = None
    for seq in have.values():
        if seq:
            d = seq[0][0].isoformat()
            coverage = min(coverage, d) if coverage else d
    payload = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "coverage_start": coverage,
        "asof": asof,
        "sources": SRC,
        "boards": {iid: {"bankuai": m.get("bankuai"), "name": m["name"],
                         "index_code": m.get("index_code"),
                         "universe": m.get("universe")}
                   for iid, m in INDEX_BOARDS.items()},
        "series": {iid: rows_from_series(have.get(iid) or []) for iid in INDEX_BOARDS},
        "stats": stats,
        "live_ok": bool(live and live_ok_ids),
        "errors": errors,
    }
    if not payload["live_ok"]:
        if any(have.values()):
            payload["from_snapshot"] = True
            payload["snapshot_asof"] = max(asof.values()) if asof else None
        else:
            payload["from_snapshot"] = False
    else:
        payload["from_snapshot"] = False
        if write and any(have.values()):
            try:
                save_history_seed(payload, RUNTIME_PATH)
            except Exception:  # noqa: BLE001
                logger.warning("write index main runtime cache failed", exc_info=True)
    return payload


def attach_index_mains(bundle: dict | None, payload: dict | None) -> dict | None:
    """用新浪指数板块主力覆盖 bundle.indices[*].main（不改 ETF / holders）。"""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("indices"), dict):
        return bundle
    if not isinstance(payload, dict) or not payload.get("stats"):
        return bundle
    out = dict(bundle)
    indices = dict(out["indices"])
    snap = bool(payload.get("from_snapshot"))
    snap_asof = payload.get("snapshot_asof")
    for iid, meta in INDEX_BOARDS.items():
        row = dict(indices.get(iid) or {"id": iid, "name": meta["name"]})
        st = dict(payload["stats"].get(iid) or _missing_main(meta))
        if snap and st.get("status") == "ok":
            st["from_snapshot"] = True
            if snap_asof:
                st["snapshot_asof"] = snap_asof
        else:
            st.pop("from_snapshot", None)
            st.pop("snapshot_asof", None)
        row["main"] = st
        indices[iid] = row
    out["indices"] = indices
    out["index_main_source"] = SRC["label"]
    out["index_main_definition"] = SRC["definition"]
    if snap:
        out["index_main_from_snapshot"] = True
        if snap_asof:
            out["index_main_snapshot_asof"] = snap_asof
    else:
        out.pop("index_main_from_snapshot", None)
        out.pop("index_main_snapshot_asof", None)
    return out


def ensure_index_mains(bundle: dict | None, *, live: bool = False) -> dict | None:
    if not isinstance(bundle, dict):
        return bundle
    if not live:
        idx = bundle.get("indices") or {}
        needed = [iid for iid, meta in INDEX_BOARDS.items() if meta.get("bankuai")]
        if needed and all(
            isinstance((idx.get(iid) or {}).get("main"), dict)
            and (idx[iid]["main"].get("status") == "ok")
            and (idx[iid]["main"].get("net_1d") is not None)
            for iid in needed
            if iid in idx
        ) and all(iid in idx for iid in needed):
            # 仍补上无板块指数的说明，避免残留东财字段。
            payload = refresh_index_main_history(live=False, write=False)
            return attach_index_mains(bundle, payload)
    try:
        payload = refresh_index_main_history(live=live, write=live)
        return attach_index_mains(bundle, payload)
    except Exception:  # noqa: BLE001
        logger.warning("ensure index main flow failed", exc_info=True)
        return bundle


def public_index_main_payload(*, live: bool = False) -> dict:
    return refresh_index_main_history(live=live, write=live)


__all__ = [
    "INDEX_BOARDS", "SRC", "SEED_PATH", "RUNTIME_PATH", "BUNDLE_SEED_PATH",
    "definition_for", "stats_for_index", "load_index_history_seed",
    "refresh_index_main_history", "attach_index_mains", "ensure_index_mains",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_seed = os.environ.get("WRITE_INDEX_MAIN_SEED") == "1"
    payload = refresh_index_main_history(live=True, write=True)
    if write_seed:
        save_history_seed(payload, SEED_PATH, pretty=True)
        print("wrote", SEED_PATH)
    for iid, st in (payload.get("stats") or {}).items():
        print("====", iid, INDEX_BOARDS[iid].get("bankuai"))
        print({k: st.get(k) for k in ("status", "asof", "net_1d", "net_5d", "net_20d",
                                      "hist_n", "reason")})
    print("coverage_start", payload.get("coverage_start"), "live_ok", payload.get("live_ok"),
          "errors", payload.get("errors"))
