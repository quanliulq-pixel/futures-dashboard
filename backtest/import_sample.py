"""Local-only adapter for HistoricalData.net 2022H2 free ZIP. No download/sign-in.

Use --acknowledge-reference-assumptions: source lacks quote clocks, deliverable,
multiplier, exercise/settlement metadata verification. This importer DOES NOT make
those facts verified. It creates OBSERVED_EOD_REFERENCE-only USER_ASSUMPTION rows.
Third-party rows remain local: never commit dataset directories or input ZIP.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from .engine import DataError, day, number

SOURCE = "https://historicaldata.net/file/options_sample_2022H2.zip"
EXPECTED_ZIP_SHA256 = "8517b069f4d41e44f950dc79a3cdab694359b0fae91664190011e39dd8e48b76"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def convert(zip_path, out, start, end, acknowledge=False):
    if not acknowledge:
        raise DataError("must explicitly acknowledge EOD/reference & unverified deliverable assumptions")
    if day(start) > day(end) or start < "2022-07-01" or end > "2022-12-30":
        raise DataError("this adapter supports only the documented 2022H2 SPY sample")
    actual_sha = sha(zip_path)
    if actual_sha != EXPECTED_ZIP_SHA256:
        raise DataError("sample ZIP SHA256 mismatch; quarantine the file manually and obtain the documented sample; do not silently accept a changed vendor archive")
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise DataError("output must be absent or empty; never overwrite prior dataset")
    cs, qs, us, ss = {}, [], [], []
    skipped, source_files = Counter(), []
    with zipfile.ZipFile(zip_path) as z:
        manifest = json.loads(z.read("day_by_date/manifest.json"))
        files = [f for f in manifest["files"] if start <= f["name"][:10] <= end]
        if not files or manifest.get("trading_days_missing"):
            raise DataError("no interval data or source reports calendar gaps")
        if files != sorted(files, key=lambda f: f["name"]):
            raise DataError("source manifest dates not ordered")
        for f in files:
            sd = f["name"][:10]
            b = z.read("day_by_date/" + f["name"])
            if hashlib.sha256(b).hexdigest() != f["sha256"]:
                raise DataError("vendor manifest checksum mismatch")
            source_files.append({"file": f["name"], "sha256": f["sha256"]})
            ss.append({"session_date": sd, "decision_at": ""})
            prices = set()
            for r in csv.DictReader(io.StringIO(b.decode("utf-8-sig"))):
                if r["underlying"] != "SPY" or r["type"] != "put":
                    continue
                p = number(r["underlying_close"], "source underlying", minimum=0)
                if p <= 0:
                    raise DataError("nonpositive underlying")
                prices.add(p)
                if r["quote_date"] != sd:
                    raise DataError("file/row date mismatch")
                # Only vanilla SPY root matches. Do not turn adjusted roots into standard.
                if not re.fullmatch(r"SPY\d{6}P\d{8}", r["contract"]):
                    skipped["nonstandard_root"] += 1
                    continue
                strike = number(r["strike"], "strike")
                if strike <= 0 or r["style"] != "A" or r["settlement_time"] != "PM":
                    skipped["unsupported_style_or_settlement"] += 1
                    continue
                # Check symbol fields rather than accepting a supplied contract label blindly.
                if r["contract"][3:9] != day(r["expiration"]).strftime("%y%m%d") or int(r["contract"][-8:]) != round(strike * 1000):
                    raise DataError("contract identity inconsistent with source fields")
                if day(sd) > day(r["expiration"]):
                    skipped["post_expiry"] += 1
                    continue
                try:
                    bid, ask = number(r["bid"], "bid"), number(r["ask"], "ask")
                    if ask <= 0 or ask < bid:
                        raise DataError("crossed/zero ask")
                    sizes = [number(r[k], k, optional=True) for k in ("bid_size", "ask_size")]
                except DataError:
                    skipped["invalid_or_missing_quote"] += 1
                    continue
                cid = r["contract"]
                cs[cid] = {"contract_id": cid, "symbol": "SPY", "right": "put", "strike": strike,
                           "expiry": r["expiration"], "expiry_at": "", "currency": "USD", "multiplier": 100,
                           "settlement": "physical", "exercise_style": "American", "deliverable": "100_SHARES",
                           "metadata_basis": "USER_ASSUMPTION", "metadata_source": "USER_ACK: standard100 unadjusted SPY assumed, not verified by source fields"}
                qs.append({"session_date": sd, "contract_id": cid, "observed_at": r["quote_time"],
                           "available_at": "", "bid": bid, "ask": ask, "bid_size": sizes[0], "ask_size": sizes[1]})
            if len(prices) != 1:
                raise DataError(f"inconsistent/missing underlying observations {sd}: {prices}")
            us.append({"session_date": sd, "symbol": "SPY", "price": prices.pop(), "observed_at": "",
                       "available_at": "", "bid": "", "ask": "", "expiry_settlement_eligible": "true",
                       "settlement_source": "USER_ASSUMPTION: vendor unadjusted EOD underlying_close, not verified settlement tape"})
    if not cs or not qs:
        raise DataError("no usable quotes")
    out.mkdir(parents=True, exist_ok=True)
    tables = {"contracts": list(cs.values()), "quotes": qs, "underlying": us, "sessions": ss}
    for name, rows in tables.items():
        write_csv(out / f"{name}.csv", list(rows[0]), rows)
    m = {"dataset_kind": "HISTORICAL_OBSERVED", "source": SOURCE,
         "provenance": {"source_zip_sha256": sha(zip_path), "source_files": source_files,
                        "adapter": "backtest.import_sample v1", "symbol": "SPY", "interval": [start, end]},
         "expected_sessions": [s["session_date"] for s in ss],
         "calendar_source": "vendor 127-session 2022H2 manifest; no independent exchange calendar validation",
         "eod_reference_assumptions_acknowledged": True,
         "timestamp_precision": "SESSION_DATE_ONLY: null clock times NOT invented",
         "excluded_quote_counts": dict(skipped),
         "disclosures": [
             "EOD quotes are not synchronized; quote update clocks unavailable in this sample. No real-time/strict execution claim.",
             "USER ASSUMPTION: unadjusted SPY contracts deliver 100 shares, USD, physical settlement. Source metadata does not verify these facts.",
             "USER ASSUMPTION: unadjusted underlying_close is used for EOD marks and expiry assessment; no independently verified official settlement tape.",
             "USER ASSUMPTION: missing stock bid uses EOD close minus configured slippage when liquidating assignment.",
             "Calendar derived from vendor manifest; daily-file observations considered known after that session solely for reference-mode one-session-lag decisions.",
             "Skipped unusable quote rows are reported in manifest; never synthesized or forward-filled.",
             "License: free sample local evaluation only; no redistribution of input rows in this project."],
         "file_sha256": {f"{n}.csv": sha(out / f"{n}.csv") for n in tables}}
    (out / "manifest.json").write_text(json.dumps(m, indent=2))
    return {"sessions": len(ss), "contracts": len(cs), "quotes": len(qs), "excluded": dict(skipped)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--zip", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--start", default="2022-07-01")
    p.add_argument("--end", default="2022-07-29")
    p.add_argument("--acknowledge-reference-assumptions", action="store_true")
    a = p.parse_args()
    print(json.dumps(convert(a.zip, a.out, a.start, a.end, a.acknowledge_reference_assumptions), indent=2))


if __name__ == "__main__":
    main()
