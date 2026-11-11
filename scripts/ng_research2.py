#!/usr/bin/env python3
"""Separate input freezing, ONE evaluation, and read-only re-export."""
from pathlib import Path
import argparse,hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import ng_strategy_lab2 as q
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--freeze-inputs',action='store_true');ap.add_argument('--run-once',action='store_true');ap.add_argument('--export',action='store_true');ap.add_argument('--recover-unobserved-write-failure',action='store_true');args=ap.parse_args()
    if args.freeze_inputs:
        if q.DATA.exists() or q.LEDGER.exists():raise FileExistsError('already frozen')
        p=q.build_panel(pd.read_json(q.FOLDER/'lab2_prices.json',orient='table'),pd.read_csv(q.FOLDER/'lab2_storage.csv'),pd.read_csv(q.FOLDER/'lab2_cot.csv'),pd.read_csv(q.FOLDER/'lab2_weather.csv'))
        p.to_json(q.DATA,orient='table',double_precision=15,date_format='iso')
        print('FROZEN FEATURES ONLY',p.shape,str(p.index.min()),str(p.index.max()))
        for name in ['lab2_panel.json','lab2_prices.json','lab2_storage.csv','lab2_cot.csv','lab2_weather.csv','lab2_manifest.json']:
            path=q.FOLDER/name;print(name,hashlib.sha256(path.read_bytes()).hexdigest())
    elif args.run_once or args.recover_unobserved_write_failure:
        d=q.run_once(recover_unobserved_write_failure=args.recover_unobserved_write_failure)
        print('FROZEN RESULTS; computational attempts',d['execution_attempts'],'passes',d['pass_count'])
        for c in d['candidates']:
            print(c['id'],c['passed'],c['fail_reasons'])
            for name,p in c['periods'].items():print(name,p['start'],p['end'],json.dumps(p['metrics']))
    if args.export:
        d=json.loads(q.LEDGER.read_text());q.SEED.write_text(json.dumps(q.public_snapshot(d),ensure_ascii=False,allow_nan=False))
        print('EXPORTED FROZEN RESULT ONLY')
