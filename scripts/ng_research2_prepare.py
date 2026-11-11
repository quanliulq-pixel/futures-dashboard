#!/usr/bin/env python3
"""Fetch FREE NG research inputs only; no accounts, no evaluation of strategy returns."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import concurrent.futures,hashlib,io,json,sys
import requests,numpy as np,pandas as pd,yfinance as yf
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path('/workspace/ng-research2-data');OLD=Path('/workspace/ng-data-samples')
OUT=ROOT/'data/ng_quant';CACHE.mkdir(exist_ok=True)
if (OUT/'lab2_panel.json').exists() or (OUT/'lab2_experiment.json').exists():
 raise FileExistsError('Inputs are frozen; do not overwrite research history')
manifest=[]
def fetch(n,u):
 p=CACHE/n
 if not p.exists():
  if (OLD/n).exists():p.write_bytes((OLD/n).read_bytes())
  else:
   r=requests.get(u,timeout=45);r.raise_for_status();p.write_bytes(r.content)
 return dict(file=n,url=u,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size)
items=[('eia_revisions.xls','https://ir.eia.gov/ngs/revisions.xls'),('eia_archrevisions.xls','https://ir.eia.gov/ngs/archrevisions.xls'),('eia_ngshistory.xls','https://ir.eia.gov/ngs/ngshistory.xls'),('eia_archngshistory.xls','https://ir.eia.gov/ngs/archngshistory.xls'),('eia_ngsstats.xls','https://ir.eia.gov/ngs/ngsstats.xls'),('eia_archngsstats.xls','https://ir.eia.gov/ngs/archngsstats.xls'),('cot_all.json',"https://publicreporting.cftc.gov/resource/72hh-3qpy.json?$where=cftc_contract_market_code=%27023651%27&$order=report_date_as_yyyy_mm_dd%20ASC&$limit=5000")]
for year in range(2004,2027):
 for kind in ['UtilityGas.Heating','Population.Cooling']:
  items.append((f'noaa_{year}_{kind}.txt',f'https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data/{year}/{kind}.txt'))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
 for f in concurrent.futures.as_completed([ex.submit(fetch,*x) for x in items]):
  row=f.result();manifest.append(row);print(row['file'],row['bytes'],flush=True)
# EIA original estimates when provided; no prices or strategy results inspected.
records=[]
for old in [True,False]:
 fn='eia_archrevisions.xls' if old else 'eia_revisions.xls'
 hist='eia_archngshistory.xls' if old else 'eia_ngshistory.xls'
 for name,original in [(hist,False),(fn,True)]:
  x=pd.read_excel(CACHE/name,sheet_name='original_data' if original else 0,header=None)
  header=next(i for i,r in x.iterrows() if any('lower 48' in str(v).lower() for v in r))
  col=next(i for i,v in x.iloc[header].items() if 'lower 48' in str(v).lower())
  for _,r in x.iloc[header+1:].iterrows():
   if not isinstance(r.iloc[0],(datetime,pd.Timestamp)) or not pd.notna(r.iloc[col]):continue
   dt=pd.Timestamp(r.iloc[0]);cut=pd.Timestamp('2015-11-06')
   if old and dt>cut or not old and dt<=cut:continue
   records.append(dict(date=dt.strftime('%Y-%m-%d'),stock=float(r.iloc[col]),original=original,region='three' if old else 'five',source=name))
storage=pd.DataFrame(records).sort_values(['date','original']).drop_duplicates('date',keep='last').sort_values('date')
avgs=[]
for name,region in [('eia_archngsstats.xls','three'),('eia_ngsstats.xls','five')]:
 x=pd.ExcelFile(CACHE/name)
 for sh in x.sheet_names:
  df=pd.read_excel(CACHE/name,sheet_name=sh,header=None)
  col=next(i for i,v in df.iloc[2].items() if 'lower 48' in str(v).lower())
  for _,r in df.iloc[3:].iterrows():
   if isinstance(r.iloc[0],(datetime,pd.Timestamp)):avgs.append(dict(date=pd.Timestamp(r.iloc[0]).strftime('%Y-%m-%d'),region=region,avg=float(r.iloc[col])))
storage=storage.merge(pd.DataFrame(avgs),on=['date','region'],how='left')
# NOAA Region 1–9: use national aggregate only if actually supplied; else compute fixed equal-region mean.
weather=[]
for year in range(2004,2027):
 for kind,label in [('UtilityGas.Heating','hdd'),('Population.Cooling','cdd')]:
  df=pd.read_csv(CACHE/f'noaa_{year}_{kind}.txt',sep='|',skiprows=3)
  region=pd.to_numeric(df['Region'],errors='coerce')
  # Files contain 9 Census divisions only in some vintages. Fixed equal-division averaging avoids inventing national weights.
  df=df[region.between(1,9)].set_index('Region').apply(pd.to_numeric,errors='coerce')
  df=df.where(df>=0)
  for day,val in df.mean(axis=0).items():
   if len(str(day))==8:weather.append(dict(date=pd.to_datetime(day).strftime('%Y-%m-%d'),kind=label,value=None if pd.isna(val) else float(val),divisions=int(df[day].count())))
weather=pd.DataFrame(weather).pivot(index='date',columns='kind',values=['value','divisions']);weather.columns=['_'.join(x) for x in weather.columns];weather=weather.reset_index()
cot=pd.DataFrame(json.loads((CACHE/'cot_all.json').read_text()))
cot=pd.DataFrame(dict(date=pd.to_datetime(cot.report_date_as_yyyy_mm_dd).dt.strftime('%Y-%m-%d'),long=pd.to_numeric(cot.m_money_positions_long_all),short=pd.to_numeric(cot.m_money_positions_short_all),oi=pd.to_numeric(cot.open_interest_all)))
pricefile=CACHE/'ung_prices.json'
if not pricefile.exists():
 p=yf.download('UNG',start='2007-01-01',end='2026-11-10',auto_adjust=False,threads=False,progress=False,timeout=30)
 if isinstance(p.columns,pd.MultiIndex):p.columns=p.columns.get_level_values(0)
 if p.empty:raise ValueError('无数据：UNG')
 factor=p['Adj Close']/p.Close
 df=pd.DataFrame(dict(UNG_open=p.Open*factor,UNG_close=p['Adj Close'],UNG_raw_close=p.Close));df.index=pd.to_datetime(df.index).tz_localize(None);df.index.name='date';df=df.dropna()
 df.to_json(pricefile,orient='table',double_precision=15)
manifest.append(dict(file=pricefile.name,url='Yahoo Finance UNG chart via yfinance; 2007 start, end exclusive 2026-11-10',sha256=hashlib.sha256(pricefile.read_bytes()).hexdigest(),bytes=pricefile.stat().st_size))
for name,df in [('lab2_storage.csv',storage),('lab2_weather.csv',weather),('lab2_cot.csv',cot)]:df.to_csv(OUT/name,index=False)
(OUT/'lab2_prices.json').write_bytes(pricefile.read_bytes())
(OUT/'lab2_manifest.json').write_text(json.dumps(dict(fetched_at=datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'),sources=sorted(manifest,key=lambda x:x['file']),weather_aggregation='fixed equal mean of Census divisions 1–9; each division already carries source UtilityGas/Population weights; require all9 in research',storage_original_share=float(storage.original.mean())),ensure_ascii=False,indent=2))
print('PREPARED ONLY. No strategy returns evaluated.',len(storage),len(weather),len(cot))
