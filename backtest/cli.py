"""python -m backtest.cli --dataset /local/data --output /local/run.json --mode STRICT"""
import argparse
import csv
import json
import sys
from dataclasses import fields
from pathlib import Path
from .engine import Config, DataError, load_dataset, run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    default = Config()
    for f in fields(Config):
        v = getattr(default, f.name)
        p.add_argument("--" + f.name.replace("_", "-"), type=type(v), default=v)
    a = vars(p.parse_args())
    data_path, output = a.pop("dataset"), Path(a.pop("output"))
    cfg = Config(**a)
    try:
        result = run(load_dataset(data_path, cfg), cfg)
    except (DataError, OSError, KeyError, json.JSONDecodeError) as e:
        print(f"VALIDATION_FAILED: {e}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False))
    for key in ("ledger", "events", "gaps"):
        rows = result[key]
        path = output.with_name(output.stem + f"_{key}.csv")
        with path.open("w", newline="") as f:
            names = list(dict.fromkeys(k for r in rows for k in r)) or ["session_date"]
            w = csv.DictWriter(f, fieldnames=names)
            w.writeheader()
            w.writerows(rows)
    print(json.dumps({k: result[k] for k in ("status", "mode", "dataset_kind", "completed_sessions", "expected_sessions", "run_fingerprint", "metrics", "gaps")}, indent=2))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
