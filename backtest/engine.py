"""Deterministic daily CSP ledger. Prices are observations, fills are assumptions.

No early assignment model; no calls, leverage, corporate actions or nonstandard
contracts. STRICT verifies timestamps but does not assert historical fills.
OBSERVED_EOD_REFERENCE explicitly uses ordered session observations when clocks
are unknown; it is NOT timestamp-accurate execution replay or a pricing model.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

VERSION = "csp-research-1.0.1"
MODES = ("STRICT", "OBSERVED_EOD_REFERENCE")
DISCLAIMER = [
    "RESEARCH ONLY: historical observations with hypothetical bid/ask fills; no guaranteed executions.",
    "American puts: early exercise/assignment is NOT modeled. Expiry-only assignment assumption can materially change results.",
    "No dividends, interest on cash, taxes or corporate actions. Assigned shares are liquidated next session; distributions before liquidation are unsupported.",
]


class DataError(ValueError):
    pass


def day(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError) as e:
        raise DataError(f"invalid date: {s!r}") from e


def timestamp(s):
    if s in (None, ""):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("timezone required")
        return t
    except (ValueError, TypeError, AttributeError) as e:
        raise DataError(f"invalid aware timestamp: {s!r}") from e


def number(x, name, minimum=0, optional=False):
    if x in (None, "") and optional:
        return None
    try:
        v = float(x)
    except (ValueError, TypeError) as e:
        raise DataError(f"invalid number {name}: {x!r}") from e
    if not math.isfinite(v) or v < minimum:
        raise DataError(f"nonfinite/negative {name}: {x!r}")
    return v


def flag(x, name):
    if x not in (True, False, "true", "false"):
        raise DataError(f"{name} must be true/false")
    return x is True or x == "true"


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    symbol: str = "SPY"
    capital: float = 100000
    contracts: int = 1
    target_dte: int = 30
    min_dte: int = 25
    max_dte: int = 40
    otm: float = 0.05
    close_dte: int = 7
    fee_per_contract: float = 0.65
    option_slippage: float = 0.01  # $ per share, adverse to bid/ask
    stock_slippage: float = 0.01
    mode: str = "STRICT"
    gap_policy: str = "STOP"  # no quote carry/interpolation
    end_policy: str = "MARK_INCOMPLETE"  # optional CLOSE_IF_QUOTED
    assignment_itm_threshold: float = 0.01  # expiry-only automatic exercise assumption ($/share)

    def validate(self):
        if self.mode not in MODES or self.gap_policy != "STOP":
            raise DataError("unsupported mode/gap policy")
        if self.end_policy not in ("MARK_INCOMPLETE", "CLOSE_IF_QUOTED"):
            raise DataError("bad end policy")
        if self.contracts != 1 or isinstance(self.contracts, bool):
            raise DataError("MVP supports exactly one CSP contract at a time")
        for k in ("capital", "target_dte", "min_dte", "max_dte", "otm", "close_dte", "fee_per_contract", "option_slippage", "stock_slippage", "assignment_itm_threshold"):
            number(getattr(self, k), k)
        if self.capital <= 0 or not 0 < self.otm < 1:
            raise DataError("capital/otm outside bounds")
        for k in ("target_dte", "min_dte", "max_dte", "close_dte"):
            if not isinstance(getattr(self, k), int) or isinstance(getattr(self, k), bool):
                raise DataError(f"{k} must be integer calendar days")
        if not 0 <= self.close_dte < self.min_dte <= self.target_dte <= self.max_dte:
            raise DataError("invalid DTE bounds")


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or len(r.fieldnames) != len(set(r.fieldnames)):
            raise DataError(f"missing/duplicate columns: {path}")
        out = list(r)
        if any(None in x for x in out):
            raise DataError(f"extra CSV values: {path}")
        return out


def load_dataset(path, config):
    """Fixed filenames only; caller chooses local directory. No network or uploads."""
    path = Path(path)
    m = json.loads((path / "manifest.json").read_text())
    if not m.get("source") or not m.get("provenance"):
        raise DataError("source/provenance required")
    data = {"manifest": m}
    hashes = {}
    for key in ("contracts", "sessions", "quotes", "underlying"):
        p = path / f"{key}.csv"
        hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        data[key] = _read_csv(p)
    # Verify immutable source hashes from manifest, not silently bless altered files.
    if hashes != m.get("file_sha256"):
        raise DataError("CSV checksum mismatch")
    validate(data, config)
    return data


def validate(data, cfg):
    cfg.validate()
    m = data["manifest"]
    if not m.get("source") or not m.get("provenance"):
        raise DataError("source/provenance required")
    if not isinstance(m.get("disclosures", []), list) or not all(isinstance(x, str) for x in m.get("disclosures", [])):
        raise DataError("disclosures must be list of strings")
    if m.get("dataset_kind") not in ("HISTORICAL_OBSERVED", "SYNTHETIC_TEST_ONLY"):
        raise DataError("dataset_kind required")
    if cfg.mode == "OBSERVED_EOD_REFERENCE" and m.get("eod_reference_assumptions_acknowledged") is not True:
        raise DataError("explicit EOD-reference assumptions acknowledgment required")
    expected = m.get("expected_sessions")
    if not expected or len(expected) != len(set(expected)) or expected != sorted(expected):
        raise DataError("ordered explicit expected_sessions calendar required")
    ss = data["sessions"]
    if [s["session_date"] for s in ss] != expected:
        raise DataError("session calendar gap/order/duplicate")
    for s in ss:
        day(s["session_date"])
        t = timestamp(s.get("decision_at"))
        if cfg.mode == "STRICT" and t is None:
            raise DataError("STRICT requires session decision_at")
        if t and t.astimezone(NY).date() != day(s["session_date"]):
            raise DataError("session date/clock mismatch")
    ends = [timestamp(s.get("decision_at")) for s in ss]
    if all(ends) and any(a >= b for a, b in zip(ends, ends[1:])):
        raise DataError("session clocks not increasing")
    session_map = {s["session_date"]: s for s in ss}
    cs, economics = {}, set()
    for c in data["contracts"]:
        cid = c["contract_id"]
        if not cid or cid in cs:
            raise DataError("duplicate/empty contract identity")
        cs[cid] = c
        if c["symbol"] != cfg.symbol or c["right"] != "put" or c["currency"] != "USD":
            raise DataError("only selected-symbol USD puts supported")
        if number(c["multiplier"], "multiplier") != 100 or number(c["strike"], "strike") <= 0:
            raise DataError("only positive strike / standard100 supported")
        if c["settlement"] != "physical" or c["exercise_style"] != "American" or c["deliverable"] != "100_SHARES":
            raise DataError("unsupported/unknown settlement, style or deliverable")
        basis = c.get("metadata_basis")
        if basis not in ("VERIFIED", "USER_ASSUMPTION"):
            raise DataError("metadata basis must be explicit")
        if basis == "USER_ASSUMPTION" and cfg.mode == "STRICT":
            raise DataError("STRICT rejects unverified contract metadata")
        if not c.get("metadata_source"):
            raise DataError("contract metadata source/disclosure required")
        day(c["expiry"])
        economic_key = (c["symbol"], c["right"], c["expiry"], float(c["strike"]), c["deliverable"])
        if economic_key in economics:
            raise DataError("duplicate economic contract identity")
        economics.add(economic_key)
        exp = timestamp(c.get("expiry_at"))
        if cfg.mode == "STRICT" and exp is None:
            raise DataError("STRICT requires expiry_at")
        if exp and exp.astimezone(NY).date() != day(c["expiry"]):
            raise DataError("expiry timestamp/date mismatch")
    for key, ident in (("quotes", lambda r: (r["session_date"], r["contract_id"])),
                       ("underlying", lambda r: (r["session_date"], r["symbol"]))):
        seen = set()
        for r in data[key]:
            identity = ident(r)
            if identity in seen:
                raise DataError(f"duplicate {key}: {identity}")
            seen.add(identity)
            sd = r["session_date"]
            if sd not in session_map:
                raise DataError("row outside explicit calendar")
            obs = timestamp(r.get("observed_at"))
            avail = timestamp(r.get("available_at"))
            end = timestamp(session_map[sd].get("decision_at"))
            if cfg.mode == "STRICT" and (obs is None or avail is None):
                raise DataError("STRICT requires observed_at and available_at")
            if obs and avail and obs > avail:
                raise DataError("observation after availability")
            if obs and (obs.astimezone(NY).date() > day(sd) or (end and obs > end)):
                raise DataError("future observation timestamp")
            # available_at can be after decision time: valid data, NEVER usable yet.
            if key == "quotes":
                cid = r["contract_id"]
                if cid not in cs:
                    raise DataError("unknown contract_id")
                c = cs[cid]
                if day(sd) > day(c["expiry"]) or (obs and timestamp(c.get("expiry_at")) and obs > timestamp(c["expiry_at"])):
                    raise DataError("quote after contract expiry")
                bid = number(r["bid"], "bid")
                ask = number(r["ask"], "ask")
                if ask <= 0 or ask < bid:
                    raise DataError("invalid/crossed bid ask")
                for name in ("bid_size", "ask_size"):
                    v = number(r.get(name), name, optional=True)
                    if v is not None and v != int(v):
                        raise DataError("quote size must be integer")
            else:
                if r["symbol"] != cfg.symbol or number(r["price"], "underlying price") <= 0:
                    raise DataError("underlying identity/price")
                for name in ("bid", "ask"):
                    number(r.get(name), name, optional=True)
                ub, ua = number(r.get("bid"), "bid", optional=True), number(r.get("ask"), "ask", optional=True)
                if ub is not None and ua is not None and ua < ub:
                    raise DataError("crossed underlying quote")
                settlement_ok = flag(r["expiry_settlement_eligible"], "expiry_settlement_eligible")
                if settlement_ok and cfg.mode == "STRICT" and not r.get("settlement_source"):
                    raise DataError("STRICT expiry settlement eligibility needs source/attestation")
    return data


def _eligible(row, session, cfg):
    if row is None:
        return False
    if cfg.mode == "OBSERVED_EOD_REFERENCE":
        # Daily-file labels ONLY: do not invent clocks. Explicit lagged-session convention.
        avail = timestamp(row.get("available_at"))
        if avail and avail.astimezone(NY).date() > day(session["session_date"]):
            return False
        return True
    end = timestamp(session["decision_at"])
    obs = timestamp(row["observed_at"])
    return obs.astimezone(NY).date() == day(session["session_date"]) and obs <= timestamp(row["available_at"]) <= end


def run(data, cfg=Config()):
    """Returns JSON-compatible ledger. No I/O, clocks, RNG, API or forward searches."""
    validate(data, cfg)
    contracts = {c["contract_id"]: c for c in data["contracts"]}
    quotes = {(r["session_date"], r["contract_id"]): r for r in data["quotes"]}
    unds = {r["session_date"]: r for r in data["underlying"]}
    cash, shares, pos, pending = float(cfg.capital), 0, None, None
    ledger, events, gaps = [], [], []
    status = "COMPLETE"

    def log(sd, kind, **kw):
        events.append({"session_date": sd, "event": kind, **kw})

    def fail(sd, reason, cid=None):
        nonlocal status
        status = "INCOMPLETE"
        gaps.append({"session_date": sd, "reason": reason, "contract_id": cid})

    def q_for(sd, cid, sess):
        q = quotes.get((sd, cid))
        return q if _eligible(q, sess, cfg) else None

    for i, sess in enumerate(data["sessions"]):
        sd, d = sess["session_date"], day(sess["session_date"])
        last = i == len(data["sessions"]) - 1
        u = unds.get(sd)
        u = u if _eligible(u, sess, cfg) else None
        active_start = bool(pos or shares)
        if not u:
            if active_start:
                fail(sd, "MISSING_OR_NOT_YET_AVAILABLE_UNDERLYING", pos["cid"] if pos else None)
                break
            # No opening/selection on incomplete flat session; capital remains known.
            if pending:
                log(sd, "ORDER_CANCELLED", reason="underlying unavailable")
                pending = None
            log(sd, "SKIP_FLAT_SESSION", reason="underlying unavailable")
            ledger.append({"session_date": sd, "cash": cash, "collateral": 0.0, "free_cash": cash,
                           "shares": 0, "stock_value": 0.0, "option_liability": 0.0, "equity": cash, "contract_id": None})
            continue
        price = float(u["price"])
        # Shares only arise via assignment at prior session: liquidate at THIS session observation.
        if shares:
            stock_bid = number(u.get("bid"), "underlying bid", optional=True)
            if cfg.mode == "STRICT" and stock_bid is None:
                fail(sd, "MISSING_STOCK_LIQUIDATION_BID")
                break
            fill = max(0.0, (stock_bid if stock_bid is not None else price) - cfg.stock_slippage)
            cash += shares * fill
            log(sd, "SELL_ASSIGNED_SHARES", shares=shares, fill=fill, source="bid" if stock_bid is not None else "EOD_CLOSE_ASSUMPTION")
            shares = 0
        # Daily expiry-settlement-first convention: on expiry session, physical
        # settlement precedes any pending close. No intraday buyback is inferred.
        expired = False
        if pos:
            c = contracts[pos["cid"]]
            if d > day(c["expiry"]):
                fail(sd, "MISSING_EXPIRY_SESSION", pos["cid"])
                break
            if d == day(c["expiry"]):
                if not flag(u["expiry_settlement_eligible"], "settlement"):
                    fail(sd, "MISSING_VERIFIED_EXPIRY_SETTLEMENT_PRICE", pos["cid"])
                    break
                if cfg.mode == "STRICT" and (timestamp(u["observed_at"]) != timestamp(c["expiry_at"]) or timestamp(sess["decision_at"]) < timestamp(c["expiry_at"])):
                    fail(sd, "UNSUPPORTED_EXPIRY_TIMING", pos["cid"])
                    break
                if pending and pending["side"] == "close":
                    log(sd, "ORDER_CANCELLED", contract_id=pos["cid"],
                        reason="EXPIRY_SETTLEMENT_FIRST: queued buyback cancelled on expiry session")
                strike = float(c["strike"])
                if strike - price >= cfg.assignment_itm_threshold and price < strike:  # expiry-only automatic ITM assumption
                    cash -= strike * 100
                    shares += 100
                    log(sd, "ASSIGN_PUT", contract_id=pos["cid"], strike=strike, shares=100, stock_price=price)
                else:
                    log(sd, "EXPIRE_WORTHLESS", contract_id=pos["cid"])
                pos, pending, expired = None, None, True
        if pending:
            order, pending = pending, None
            cid = order["cid"]
            c = contracts[cid]
            q = q_for(sd, cid, sess)
            if q and cfg.mode == "STRICT" and timestamp(q["observed_at"]) <= timestamp(order["decided_at"]):
                q = None  # no retroactive fill against the signal's old quote
            if order["side"] == "close" and pos:
                if q is None or number(q.get("ask_size"), "ask_size", optional=True) == 0 or (cfg.mode == "STRICT" and q.get("ask_size") in (None, "")):
                    fail(sd, "MISSING_FRESH_CLOSE_QUOTE_OR_SIZE", cid)
                    break
                fill = float(q["ask"]) + cfg.option_slippage
                cash -= fill * 100 + cfg.fee_per_contract
                log(sd, "BUY_TO_CLOSE", contract_id=cid, fill=fill, fee=cfg.fee_per_contract)
                pos = None
            elif order["side"] == "open" and not pos and not shares:
                # No fill on expiry session and no replacement with another contract.
                if q is None or d >= day(c["expiry"]):
                    log(sd, "ORDER_CANCELLED", contract_id=cid, reason="missing fresh quote or expiry reached")
                else:
                    fill = float(q["bid"]) - cfg.option_slippage
                    size = number(q.get("bid_size"), "bid_size", optional=True)
                    required = float(c["strike"]) * 100
                    if fill <= 0 or size == 0 or (size is None and cfg.mode == "STRICT"):
                        log(sd, "ORDER_CANCELLED", contract_id=cid, reason="nonpositive bid or unavailable size")
                    elif cash < required + cfg.fee_per_contract:
                        log(sd, "ORDER_CANCELLED", contract_id=cid, reason="insufficient pre-premium collateral")
                    else:
                        cash += fill * 100 - cfg.fee_per_contract
                        pos = {"cid": cid}
                        log(sd, "SELL_TO_OPEN", contract_id=cid, fill=fill, fee=cfg.fee_per_contract,
                            size_assumption="unknown size accepted only in EOD_REFERENCE" if size is None else None)
        liability, collateral = 0.0, 0.0
        if pos:
            cid = pos["cid"]
            q = q_for(sd, cid, sess)
            if not q:
                fail(sd, "MISSING_DAILY_OPTION_MARK", cid)
                break
            c = contracts[cid]
            if last and cfg.end_policy == "CLOSE_IF_QUOTED":
                if number(q.get("ask_size"), "ask_size", optional=True) == 0 or (cfg.mode == "STRICT" and q.get("ask_size") in (None, "")):
                    fail(sd, "MISSING_END_CLOSE_SIZE", cid)
                    break
                fill = float(q["ask"]) + cfg.option_slippage
                cash -= fill * 100 + cfg.fee_per_contract
                log(sd, "FORCED_END_CLOSE", contract_id=cid, fill=fill, fee=cfg.fee_per_contract,
                    assumption="predeclared end-of-data close; not a strategy signal")
                pos = None
            else:
                liability = float(q["ask"]) * 100
                collateral = float(c["strike"]) * 100
        # Every ledger row is valued from THIS session; never carry an old mark.
        equity = cash + shares * price - liability
        ledger.append({"session_date": sd, "cash": cash, "collateral": collateral,
                       "free_cash": cash - collateral, "shares": shares, "stock_value": shares * price,
                       "option_liability": liability, "equity": equity, "contract_id": pos["cid"] if pos else None})
        if last:
            if pos or shares:
                fail(sd, "END_WITH_OPEN_POSITION", pos["cid"] if pos else None)
            continue
        # Decisions occur only after current-day event processing and marking.
        if pos:
            c = contracts[pos["cid"]]
            if (day(c["expiry"]) - d).days <= cfg.close_dte:
                pending = {"side": "close", "cid": pos["cid"], "decided_at": sess.get("decision_at")}
                log(sd, "QUEUE_CLOSE", contract_id=pos["cid"])
        elif not shares and not expired:
            choices = []
            for cid, c in contracts.items():
                dte = (day(c["expiry"]) - d).days
                k = float(c["strike"])
                q = q_for(sd, cid, sess)
                if cfg.min_dte <= dte <= cfg.max_dte and k < price and q and float(q["bid"]) > cfg.option_slippage:
                    if cash >= k * 100 + cfg.fee_per_contract:
                        choices.append((abs(dte - cfg.target_dte), abs((1 - k / price) - cfg.otm), c["expiry"], k, cid))
            if choices:
                cid = min(choices)[-1]
                if cfg.end_policy == "CLOSE_IF_QUOTED" and i == len(data["sessions"]) - 2:
                    log(sd, "OPEN_SKIPPED_AT_DATA_END", contract_id=cid,
                        reason="Next session is predeclared final liquidation session; no pointless open/close at same quote")
                else:
                    pending = {"side": "open", "cid": cid, "decided_at": sess.get("decision_at")}
                    log(sd, "QUEUE_OPEN", contract_id=cid)
    metrics = None
    if status == "COMPLETE" and ledger:
        peak, drawdown = float(cfg.capital), 0.0
        for r in ledger:
            peak = max(peak, r["equity"])
            drawdown = min(drawdown, r["equity"] / peak - 1)
        metrics = {"ending_equity": ledger[-1]["equity"], "return_pct": (ledger[-1]["equity"] / cfg.capital - 1) * 100,
                   "max_drawdown_pct": drawdown * 100, "fees": sum(e.get("fee", 0) for e in events)}
    inputs_hash = fingerprint(data)
    config = asdict(cfg)
    return {"engine_version": VERSION, "mode": cfg.mode, "status": status,
            "dataset_kind": data["manifest"]["dataset_kind"], "config": config,
            "input_fingerprint": inputs_hash, "run_fingerprint": fingerprint({"version": VERSION, "data": inputs_hash, "config": config}),
            "disclosures": DISCLAIMER + data["manifest"].get("disclosures", []) +
                (["OBSERVED_EOD_REFERENCE: clocks unknown; ordered daily-file convention; next-session bid/ask fills are hypothetical, not timestamp-validated."] if cfg.mode != "STRICT" else []),
            "provenance": data["manifest"].get("provenance"), "metrics": metrics,
            "ledger": ledger, "events": events, "gaps": gaps,
            "open_position": pos, "shares": shares,
            "completed_sessions": len(ledger), "expected_sessions": len(data["sessions"])}
