"""Second preregistered NG round. Free fundamentals, explicit conservative availability.
No network or research execution on API requests. No claim of strict historical vintages.
"""
from __future__ import annotations
import copy,hashlib,json,math
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from ng_quant import JST
from ng_strategy_lab import performance as base_performance,passes
ROOT=Path(__file__).resolve().parent
FOLDER=ROOT/'data/ng_quant'
PREREG=FOLDER/'lab2_prereg.md'
DATA=FOLDER/'lab2_panel.json'
SEED=ROOT/'docs/api/ng_strategy_lab2.json'
LEDGER=FOLDER/'lab2_experiment.json'
COST=.001;BORROW=.10;MULTIPLE_TESTS=12
QUARANTINES=(('2013-10-01','2013-11-15'),('2018-12-22','2019-03-15'),('2025-10-01','2025-12-31'))
PERIODS={'in_sample':('2010-01-01','2022-12-31'),'validation':('2023-01-01','2023-12-31'),'holdout':('2024-01-01','2026-11-09')}
CANDIDATES=[
 dict(id='storage_level',name='库存水平偏差',rules='库存比当年五年均值低5%做多UNG，高5%卖空UNG，否则现金。'),
 dict(id='storage_flow',name='注采季节性偏差',rules='周注采量减五年基准周变化：≤-10 Bcf做多UNG，≥+10 Bcf卖空，否则现金；不是市场共识惊喜。'),
 dict(id='storage_weather',name='库存×已实现天气确认',rules='库存水平/流量方向均值，与已实现HDD/CDD异常方向一致才交易；天气z均值±0.5阈值。'),
 dict(id='cot_contrarian',name='COT拥挤反向',rules='管理资金净仓/OI相对前156周z≤-1做多UNG，z≥+1卖空；至少104份预热。'),
 dict(id='storage_cot_filter',name='库存×COT反向过滤',rules='库存合成方向交易；COT极端方向反对时现金，中性允许；缺COT数据则现金。'),
 dict(id='equal_composite',name='库存/天气/COT等权',rules='库存、已实现天气、COT三类分数等权，合成≥0.5做多UNG，≤-0.5卖空，否则现金；三类皆须可用。'),
]
REQUIRED={'storage_level':('s',),'storage_flow':('s',),'storage_weather':('s','w'),'cot_contrarian':('c',),'storage_cot_filter':('s','c'),'equal_composite':('s','w','c')}

def quarantined(dt):
    dt=pd.Timestamp(dt)
    return any(pd.Timestamp(a)<=dt<=pd.Timestamp(b) for a,b in QUARANTINES)

def available_session(reference,calendar,kind):
    """Normal report date + first strictly later ETF open + TWO extra sessions.
    Actual exchange sessions handle market holidays; long/unverified delays quarantined.
    Weather assumes publication a full week after observed Friday. Never backfills.
    """
    offset={'storage':6,'cot':3,'weather':7}[kind]
    nominal=pd.Timestamp(reference)+pd.Timedelta(days=offset)
    i=calendar.searchsorted(nominal,side='right')+2
    return calendar[i] if i<len(calendar) else pd.NaT

def asof_join(calendar,source,prefix,kind):
    src=source.copy();src['date']=pd.to_datetime(src['date'])
    src=src[~src.date.map(quarantined)]
    src['available']=src.date.map(lambda x:available_session(x,calendar,kind))
    src=src.dropna(subset=['available']).sort_values(['available','date']).drop_duplicates('available',keep='last')
    src=src.rename(columns={c:prefix+'_'+c for c in src})
    left=pd.DataFrame({'session':calendar})
    return pd.merge_asof(left,src,left_on='session',right_on=prefix+'_available',direction='backward').set_index('session')

def storage_features(raw):
    d=raw.copy().sort_values('date');d['date']=pd.to_datetime(d.date)
    d['level_gap']=d.stock/d.avg-1
    d['L']=np.select([d.level_gap<=-.05,d.level_gap>=.05],[1.,-1.],default=0.)
    d.loc[~np.isfinite(d.level_gap),'L']=np.nan
    adjacent=d.date.diff().dt.days.eq(7)&d.date.dt.year.eq(d.date.dt.year.shift())&d.region.eq(d.region.shift())
    d['flow_gap']=(d.stock.diff()-d.avg.diff()).where(adjacent)
    d['F']=np.select([d.flow_gap<=-10,d.flow_gap>=10],[1.,-1.],default=0.)
    d.loc[~np.isfinite(d.flow_gap),'F']=np.nan
    d['S']=(d.L+d.F)/2
    return d

def cot_features(raw):
    d=raw.copy().sort_values('date');d['date']=pd.to_datetime(d.date)
    d['net_fraction']=(d.long-d.short)/d.oi.where(d.oi>0)
    past=d.net_fraction.shift();mu=past.rolling(156,min_periods=104).mean();sd=past.rolling(156,min_periods=104).std()
    d['z']=(d.net_fraction-mu)/sd.where(sd>0)
    d['C']=np.select([d.z<=-1,d.z>=1],[1.,-1.],default=0.)
    d.loc[~np.isfinite(d.z),'C']=np.nan
    return d

def weather_features(raw):
    d=raw.copy();d.index=pd.to_datetime(d.pop('date'));d=d.sort_index().asfreq('D')
    daily=d[['value_hdd','value_cdd']].where(d[['divisions_hdd','divisions_cdd']].to_numpy()==9)
    rolling=daily.rolling(14,min_periods=14).mean();rows=[]
    for dt in rolling.index[rolling.index.weekday==4]:
        z=[]
        for col in rolling:
            past=[]
            for years in range(1,6):
                ref=dt-pd.DateOffset(years=years)
                val=rolling.at[ref,col] if ref in rolling.index else np.nan
                if np.isfinite(val):past.append(val)
            cur=rolling.at[dt,col]
            if len(past)<4 or not np.isfinite(cur):z.append(np.nan);continue
            sd=float(np.std(past,ddof=1));mean=float(np.mean(past))
            # A zero historical variance and equal current value is neutral, not missing.
            score=0. if sd==0 and cur==mean else (math.copysign(2,cur-mean) if sd==0 else (cur-mean)/sd)
            z.append(float(np.clip(score,-2,2)))
        score=sum(z)/2
        W=1. if score>=.5 else -1. if score<=-.5 else 0. if np.isfinite(score) else np.nan
        rows.append(dict(date=dt,hdd_z=z[0],cdd_z=z[1],z=score,W=W))
    return pd.DataFrame(rows)

def build_panel(prices,storage,cot,weather):
    p=prices.copy().sort_index();p=p.loc[:'2026-11-09']
    if not p.index.is_unique or (p[['UNG_open','UNG_close']]<=0).any().any():raise ValueError('invalid ETF data')
    p['hv60']=np.log(p.UNG_close/p.UNG_close.shift()).rolling(60).std().shift()*math.sqrt(252)
    for d,prefix,kind in [(storage_features(storage),'s','storage'),(cot_features(cot),'c','cot'),(weather_features(weather),'w','weather')]:
        p=p.join(asof_join(p.index,d,prefix,kind))
    return p

def decision(row,identifier,dt):
    if identifier not in REQUIRED:raise ValueError('unregistered strategy')
    if quarantined(dt):return 0,False,'保守停摆/延迟隔离'
    for prefix in REQUIRED[identifier]:
        ref=row.get(prefix+'_date');av=row.get(prefix+'_available')
        if pd.isna(ref) or pd.isna(av) or pd.Timestamp(av)>dt or not 0<=(dt-pd.Timestamp(ref)).days<=28:
            return 0,False,'输入缺失或参考日超过28天'
    def valid(*names):return all(pd.notna(row.get(n)) and np.isfinite(float(row[n])) for n in names)
    if identifier=='storage_level':value=row['s_L'];ok=valid('s_L')
    elif identifier=='storage_flow':value=row['s_F'];ok=valid('s_F')
    elif identifier=='cot_contrarian':value=row['c_C'];ok=valid('c_C')
    elif identifier=='storage_weather':
        ok=valid('s_S','w_W');value=np.sign(row['s_S']) if ok and np.sign(row['s_S'])==row['w_W'] else 0.
    elif identifier=='storage_cot_filter':
        ok=valid('s_S','c_C');value=np.sign(row['s_S']) if ok and row['c_C']*row['s_S']>=0 else 0.
    else:
        ok=valid('s_S','w_W','c_C');score=(row['s_S']+row['w_W']+row['c_C'])/3 if ok else np.nan
        value=1. if score>=.5 else -1. if score<=-.5 else 0.
    return int(np.sign(value)) if ok else 0,ok,'固定周度规则' if ok else '特征预热/缺失'

def metrics(values,orders,borrow,round_trips,insolvent):
    m=base_performance(values,orders,borrow,round_trips,insolvent)
    sh=m['sharpe'];z=sh*math.sqrt(len(values)/252) if sh is not None else None
    m['bonferroni_p_iid']=min(1.,MULTIPLE_TESTS*.5*math.erfc(z/math.sqrt(2))) if z is not None else None
    m['cost_paid']=float(sum(o['fee'] for o in orders))
    return m

def backtest(panel,identifier,start,end):
    dates=panel.index[(panel.index>=pd.Timestamp(start))&(panel.index<=pd.Timestamp(end))]
    if len(dates)<2:raise ValueError('无数据：区间不足')
    cash=1.;qty=0.;last=None;week=None;curve=[];orders=[];borrow=0.;round_trips=0;eligible_count=0;insolvent=False
    for dt in dates:
        i=panel.index.get_loc(dt);r=panel.loc[dt];prev=panel.iloc[i-1] if i else r
        if last is not None:
            fee=max(-qty,0)*float(prev.UNG_close)*BORROW*(dt-last).days/365
            cash-=fee;borrow+=fee
        eq=cash+qty*float(r.UNG_open);current_week=tuple(dt.isocalendar()[:2])
        sign,eligible,reason=decision(r,identifier,dt);eligible_count+=int(eligible)
        if eq<=0:insolvent=True
        target=None
        if insolvent or not eligible:target=0.
        elif current_week!=week:
            hv=float(r.hv60);weight=min(.5,.10/hv) if math.isfinite(hv) and hv>0 else 0.
            target=sign*weight*max(eq,0)/float(r.UNG_open)
        if target is not None:
            delta=target-qty;turnover=abs(delta)*float(r.UNG_open)
            if turnover>1e-12:
                fee=turnover*COST;cash-=delta*float(r.UNG_open)+fee
                if qty and (not target or qty*target<0):round_trips+=1
                orders.append(dict(date=str(dt.date()),symbol='UNG',price=float(r.UNG_open),old_qty=qty,new_qty=target,notional=turnover,fee=fee,reason=reason))
                qty=target
        value=cash+qty*float(r.UNG_close)
        if dt==dates[-1]:
            state=dict(equity=float(value),positions=[dict(symbol='UNG',weight=float(qty*r.UNG_close/value),reference_price=float(r.UNG_raw_close))] if qty and value>0 else [],eligible=eligible,reason=reason,
                input_dates={prefix:str(pd.Timestamp(r[prefix+'_date']).date()) if pd.notna(r[prefix+'_date']) else None for prefix in ('s','w','c')},
                available_dates={prefix:str(pd.Timestamp(r[prefix+'_available']).date()) if pd.notna(r[prefix+'_available']) else None for prefix in ('s','w','c')})
            if qty:
                turnover=abs(qty)*float(r.UNG_close);fee=turnover*COST;cash+=qty*float(r.UNG_close)-fee
                orders.append(dict(date=str(dt.date()),symbol='UNG',price=float(r.UNG_close),old_qty=qty,new_qty=0.,notional=turnover,fee=fee,reason='period_end'));qty=0.;round_trips+=1
            value=cash
        if value<=0:insolvent=True
        curve.append(dict(date=str(dt.date()),equity=float(max(value,0))));week=current_week;last=dt
    m=metrics([x['equity'] for x in curve],orders,borrow,round_trips,insolvent);m['input_coverage']=eligible_count/len(dates)
    return dict(start=str(dates[0].date()),end=str(dates[-1].date()),metrics=m,curve=curve,orders=orders,model_state=state)

def benchmark(panel,start,end):
    d=panel.loc[start:end];qty=1/(float(d.iloc[0].UNG_open)*(1+COST));vals=(d.UNG_close*qty).to_numpy(copy=True);vals[-1]*=1-COST
    orders=[dict(fee=qty*float(d.iloc[0].UNG_open)*COST),dict(fee=qty*float(d.iloc[-1].UNG_close)*COST)]
    return dict(start=str(d.index[0].date()),end=str(d.index[-1].date()),metrics=metrics(vals,orders,0,1,False))

def run_once(output=LEDGER, *, recover_unobserved_write_failure=False):
    output=Path(output);marker=output.with_suffix('.started.json')
    if output.exists() or marker.exists() and not recover_unobserved_write_failure:raise FileExistsError('第二轮结果/启动标记已冻结，禁止重复评估')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [DATA,FOLDER/'lab2_prices.json',FOLDER/'lab2_storage.csv',FOLDER/'lab2_cot.csv',FOLDER/'lab2_weather.csv',FOLDER/'lab2_manifest.json']}
    if not all(h in PREREG.read_text() for h in hashes.values()):raise ValueError('输入尚未在预注册中冻结')
    if recover_unobserved_write_failure:
        ticket=json.loads((FOLDER/'lab2_technical_recovery.json').read_text())
        if not marker.exists() or ticket['original_input_hashes']!=hashes or ticket['core_before']!=ticket['core_after']:
            raise ValueError('技术恢复审计不匹配')
        recovery_marker=output.with_suffix('.recovery.started.json')
        with recovery_marker.open('x') as f:json.dump(dict(recovery_at=datetime.now(JST).isoformat(timespec='seconds'),note=ticket['note']),f)
    else:
        with marker.open('x') as f:json.dump(dict(started_at=datetime.now(JST).isoformat(timespec='seconds'),input_hashes=hashes),f)
    panel=pd.read_json(DATA,orient='table');results=[]
    for c in CANDIDATES:
        periods={k:backtest(panel,c['id'],*bounds) for k,bounds in PERIODS.items()}
        okay,reasons=passes(periods)
        results.append(dict(**c,passed=okay,fail_reasons=reasons,periods=periods,model_state=periods['holdout']['model_state']))
    checkpoint=output.with_suffix('.candidates.json')
    with checkpoint.open('x') as f:json.dump(results,f,ensure_ascii=False,allow_nan=False)
    value=dict(execution_attempts=2 if recover_unobserved_write_failure else 1,execution_note='首次进程在基准只读NumPy数组写操作处失败，绩效未输出/未被观察；原始冻结规则和数据不变，作一次确定性技术重算（共2次计算进程），不算新的参数候选。' if recover_unobserved_write_failure else '单次执行',schema_version=1,round=2,status='ok',timezone='Asia/Tokyo',updated_at=datetime.now(JST).isoformat(timespec='seconds'),asof_session=str(panel.index[-1].date()),
        cost_per_side=COST,borrow_annual=BORROW,cash_yield=0,multiple_tests=MULTIPLE_TESTS,pass_count=sum(c['passed'] for c in results),candidates=results,
        benchmark={k:benchmark(panel,*bounds) for k,bounds in PERIODS.items()},input_hashes=hashes,prereg_sha256=hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        methodology='六个固定基本面候选，跨两轮共12次检验；2010–2022样本内、2023验证、2024–2026-11-09一次历史留出。周首开盘、单边10bp、UNG短仓借券10%，现金0息。',
        caveats=['历史留出已在上轮见过，非真正前瞻盲测；通过仍须未来纸面验证。','发布时间采用正常日历后首个实际ETF开盘再多等2日；长停摆/未审计延迟区间全策略现金。','EIA年度基准、COT分类和NOAA权重可能后修订，不是严格逐次首发点时数据库。','NOAA为9个Census division等权均值，各division保留源权重；仅已实现天气，无预报。','UNG空头可借券、借券费跳升、召回/停牌和保证金风险未由数据证明可执行。'])
    with output.open('x') as f:json.dump(value,f,ensure_ascii=False,allow_nan=False)
    return value

def public_snapshot(value):
    d=copy.deepcopy(value)
    for c in d['candidates']:
        for name,p in c['periods'].items():
            p.pop('orders',None);p.pop('model_state',None)
            if name!='holdout':p.pop('curve',None)
    return d

def snapshot():
    try:
        d=json.loads(SEED.read_text());d['stale']=(datetime.now(JST).date()-datetime.fromisoformat(d['asof_session']).date()).days>5
        if d['stale']:
            for c in d['candidates']:c['model_state']=dict(positions=[],reason='无数据：冻结归档超过5日，纸面状态禁用')
        return d
    except (ValueError,OSError):return dict(schema_version=1,round=2,status='no_data',error='无数据',candidates=[])
