#!/usr/bin/env python3
"""Build only NG cash-secured put research; --offline reproduces the archive."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import ng_put_quant as q

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    if args.offline:
        old=json.loads(q.SEED.read_text())
        panel=pd.read_json(q.RAW,orient='table')
        panel.attrs['data_quality']=json.loads((q.RAW.parent/'put_prices_meta.json').read_text())['data_quality']
        value=q.build_payload(panel,old['live'],datetime.fromisoformat(old['updated_at']))
    else:value=q.refresh()
    q.SEED.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(value['updated_at'],value['asof_session'],json.dumps(value['live'],ensure_ascii=False))
    for k in ('in_sample','out_of_sample'): print(k,value[k]['start'],value[k]['end'],value[k]['metrics'])
