#!/usr/bin/env python3
"""Build only SHFE gold cash/margin short-put research; --offline reproduces the archive."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gold_put_quant as q

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.offline:
        value = q.refresh(use_remote=False)
    else:
        value = q.refresh(use_remote=True)
    # Write committed seeds (caller decides whether to commit)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    q.SEED_PATHS[0].parent.mkdir(parents=True, exist_ok=True)
    q.SEED_PATHS[0].write_text(text, encoding="utf-8")
    q.SEED_PATHS[1].parent.mkdir(parents=True, exist_ok=True)
    q.SEED_PATHS[1].write_text(text, encoding="utf-8")
    print(value["updated_at"], value["asof_session"], json.dumps(value.get("live"), ensure_ascii=False)[:500])
    for k in ("in_sample", "out_of_sample"):
        print(k, value[k]["start"], value[k]["end"], value[k]["metrics"])
