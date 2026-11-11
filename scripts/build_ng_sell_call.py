#!/usr/bin/env python3
"""One-time frozen NG call spread study. No network or market-wide refresh."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import ng_call_quant as q

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-once',action='store_true')
    parser.add_argument('--export',action='store_true')
    args=parser.parse_args()
    if args.run_once:
        if q.ARCHIVE.exists():parser.error('冻结实验已存在，拒绝重新评估留出或按结果调参')
        value=q.build_payload(pd.read_json(q.RAW,orient='table'))
        q.ARCHIVE.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
    elif args.export:
        value=json.loads(q.ARCHIVE.read_text(encoding='utf-8'))
    else:parser.error('请选择 --run-once 或 --export（导出绝不重测）')
    q.SEED.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
    print(value['updated_at'],value['asof_session'],value['verdict'])
    for name,segments in value['variants'].items():
        for key,r in segments.items():print(name,key,r['metrics'],'stress',r['stress'])
    print(json.dumps(value['live'],ensure_ascii=False))
