#!/usr/bin/env python3
"""One-shot preregistered research. --export publishes frozen results without retesting."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import ng_strategy_lab as q

LEDGER=q.ROOT/'data/ng_quant/lab_experiment.json'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-once',action='store_true')
    parser.add_argument('--export',action='store_true')
    args=parser.parse_args()
    if args.run_once:
        value=q.run_frozen_experiment(pd.read_json(q.DATA,orient='table'),LEDGER)
    else:value=json.loads(LEDGER.read_text(encoding='utf-8'))
    if args.export:
        q.SEED.write_text(json.dumps(q.public_snapshot(value),ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print('Frozen',value['updated_at'],'passes',value['pass_count'])
    for c in value['candidates']:
        print('\n'+c['id'],c['name'],'PASS' if c['passed'] else 'FAIL',c['fail_reasons'])
        for k,p in c['periods'].items():print(k,p['start'],p['end'],json.dumps(p['metrics'],ensure_ascii=False))
        print('borrow15_holdout',json.dumps(c['borrow15_holdout']))
        print('model_state',json.dumps(c['model_state'],ensure_ascii=False))
