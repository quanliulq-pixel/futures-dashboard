#!/usr/bin/env python3
"""Regenerate only BOIL/KOLD research; --offline uses the archived Yahoo panel."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import ng_quant

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.offline:
        old = json.loads(ng_quant.SEED.read_text(encoding='utf-8'))
        panel = pd.read_json(ng_quant.RAW, orient='table')
        panel.attrs['data_quality'] = json.loads((ng_quant.RAW.parent/'prices_meta.json').read_text())['data_quality']
        value = ng_quant.build_payload(panel, now=datetime.fromisoformat(old['updated_at']))
    else:
        value = ng_quant.refresh()
    ng_quant.SEED.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(value['updated_at'], value['asof_session'], value['signal'])
