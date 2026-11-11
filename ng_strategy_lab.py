"""Frozen six-candidate NG experiment. Offline prices, next-open execution, no optimization."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from ng_quant import JST

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data/ng_quant/lab_prices.json'
PREREG=ROOT/'data/ng_quant/lab_prereg.md'
SEED=ROOT/'docs/api/ng_strategy_lab.json'
SYMBOLS=('BOIL','KOLD','UNG')
COST=.001
BORROW=.10
CANDIDATES=[
    dict(id='monthly_pair_decay',name='月度双空杠杆损耗',rules='每月首日BOIL/KOLD各卖空10%，月内固定股数；单腿上日收盘较入场上涨50%则次日退出，本月不补。',short=True),
    dict(id='seasonal_ung',name='UNG冬前/季末季节性',rules='9–11月做多UNG，2–4月卖空UNG，其余现金；日度按前日HV60取min(50%,10%/HV60)。',short=True),
    dict(id='slow_momentum',name='半年趋势切换',rules='每月按NG过去126日收益方向做多UNG或KOLD；仓位min(50%,10%/ETF HV60)。',short=False),
    dict(id='extreme_reversion',name='极端波动反转',rules='NG五日跌15%做多UNG、涨15%做多KOLD，NG HV20≤150%；仓位min(25%,10%/ETF HV20)，持5日或跌10%退出。',short=False),
    dict(id='monthly_short_boil',name='月度小仓位卖空BOIL',rules='每月首日卖空BOIL 10%，固定股数；上日收盘较入场上涨50%则次日退出，本月不补。',short=True),
    dict(id='defensive_kold',name='防守型KOLD趋势',rules='每周首日，若NG过去63日收益<0且收盘<MA60则做多KOLD；仓位min(50%,10%/KOLD HV60)，否则现金。',short=False),
]
PERIODS={'in_sample':('2017-01-01','2022-12-31'),'validation':('2023-01-01','2023-12-31'),'holdout':('2024-01-01','2099-12-31')}


def features(raw):
    p=raw.copy().sort_index()
    for s in SYMBOLS+('NG=F',):
        c=p[s+'_close']; r=np.log(c/c.shift())
        for n in (20,60):p[s+'_hv'+str(n)]=r.rolling(n).std()*math.sqrt(252)
    ng=p['NG=F_close']
    p['ng_ma60']=ng.rolling(60).mean()
    for n in (5,63,126):p['ng_mom'+str(n)]=ng.pct_change(n,fill_method=None)
    return p


def vol_weight(row,symbol,window=60,cap=.5):
    hv=float(row[symbol+'_hv'+str(window)])
    return min(cap,.10/hv) if math.isfinite(hv) and hv>0 else 0.


def scheduled_weights(identifier,row,dt):
    """Decision uses row from previous completed session, date is known execution calendar."""
    w={s:0. for s in SYMBOLS}
    if identifier=='monthly_pair_decay':w.update(BOIL=-.10,KOLD=-.10)
    elif identifier=='monthly_short_boil':w['BOIL']=-.10
    elif identifier=='seasonal_ung':
        sign=1 if dt.month in (9,10,11) else -1 if dt.month in (2,3,4) else 0
        w['UNG']=sign*vol_weight(row,'UNG')
    elif identifier=='slow_momentum':
        momentum=float(row.ng_mom126)
        if math.isfinite(momentum) and momentum!=0:
            s='UNG' if momentum>0 else 'KOLD';w[s]=vol_weight(row,s)
    elif identifier=='defensive_kold':
        if row.ng_mom63<0 and row['NG=F_close']<row.ng_ma60:w['KOLD']=vol_weight(row,'KOLD')
    elif identifier=='extreme_reversion':
        if not (math.isfinite(row['NG=F_hv20']) and 0<row['NG=F_hv20']<=1.5):return w
        s='UNG' if row.ng_mom5<=-.15 else 'KOLD' if row.ng_mom5>=.15 else None
        if s:w[s]=vol_weight(row,s,20,.25)
    else:raise ValueError('unknown registered strategy')
    return w


def performance(values,orders,borrow_paid,round_trips,insolvent):
    v=np.array(values,dtype=float)
    prev=np.r_[1.,v[:-1]]
    returns=np.divide(v,prev,out=np.ones_like(v),where=prev>0)-1
    peak=np.maximum.accumulate(np.r_[1.,v])[1:]
    dd=v/peak-1
    sd=returns.std(ddof=1) if len(v)>1 else 0
    sharpe=float(returns.mean()/sd*math.sqrt(252)) if sd>0 else None
    # Diagnostic only. Serial dependence, skew, and prior market exposure invalidate a strict IID test.
    z=sharpe*math.sqrt(len(v)/252) if sharpe is not None else None
    p=min(1.,6*.5*math.erfc(z/math.sqrt(2))) if z is not None else None
    return dict(total_return=float(v[-1]-1),cagr=float(v[-1]**(252/len(v))-1),
                max_drawdown=float(dd.min()),sharpe=sharpe,trades=len(orders),round_trips=round_trips,
                borrow_paid=float(borrow_paid),insolvent=insolvent,bonferroni_p_iid=p,sessions=len(v))


def backtest(panel,identifier,start,end,borrow=BORROW):
    """Cash + signed shares. Costs on actual turnover; shorts pay calendar-day borrow.
    No spread/curve/futures returns are fabricated. Corporate actions use adjusted prices.
    """
    dates=panel.index[(panel.index>=pd.Timestamp(start))&(panel.index<=pd.Timestamp(end))]
    if len(dates)<2:raise ValueError('无数据：回测区间不足')
    qty={s:0. for s in SYMBOLS};entry={};entry_i={}
    cash=1.;curve=[];orders=[];borrow_paid=0.;round_trips=0;insolvent=False
    prev_month=prev_week=pause_month=None
    last_dt=None
    risk_exits=0
    for dt in dates:
        i=panel.index.get_loc(dt)
        if i<127:raise ValueError('无数据：至少127个交易日预热')
        prev,row=panel.iloc[i-1],panel.loc[dt]
        month=(dt.year,dt.month); week=tuple(dt.isocalendar()[:2])
        new_month=month!=prev_month;new_week=week!=prev_week
        if last_dt is not None:
            days=(dt-last_dt).days
            charge=sum(max(-qty[s],0)*float(prev[s+'_close']) for s in SYMBOLS)*borrow*days/365
            cash-=charge;borrow_paid+=charge
        equity=cash+sum(qty[s]*float(row[s+'_open']) for s in SYMBOLS)
        prev_equity=(curve[-1]['equity'] if curve else 1.)
        gross=sum(abs(qty[s])*float(prev[s+'_close']) for s in SYMBOLS)
        if equity<=0 or prev_equity<=0:insolvent=True
        risk=insolvent or (prev_equity>0 and gross/prev_equity>1)
        if risk:
            pause_month=month;risk_exits+=1
        blocked=pause_month==month or insolvent
        # None means keep existing shares; a dict means reset target weights at open.
        target=None;reason='hold';exit_symbols=[]
        if blocked:target={s:0. for s in SYMBOLS};reason='risk_pause'
        elif identifier in ('monthly_pair_decay','monthly_short_boil'):
            if new_month:target=scheduled_weights(identifier,prev,dt);reason='monthly_rebalance'
            else:
                for s in SYMBOLS:
                    if qty[s]<0 and prev[s+'_close']>=1.5*entry[s]:exit_symbols.append(s)
                reason='short_stop'
        elif identifier=='seasonal_ung':target=scheduled_weights(identifier,prev,dt);reason='daily_vol_rebalance'
        elif identifier=='slow_momentum' and new_month:target=scheduled_weights(identifier,prev,dt);reason='monthly_trend'
        elif identifier=='defensive_kold' and new_week:target=scheduled_weights(identifier,prev,dt);reason='weekly_trend'
        elif identifier=='extreme_reversion':
            active=[s for s in SYMBOLS if qty[s]>0]
            if active:
                s=active[0]
                if i-entry_i[s]>=5 or prev[s+'_close']<=.9*entry[s]:exit_symbols=[s];reason='time_or_stop'
            else:target=scheduled_weights(identifier,prev,dt);reason='extreme_entry'
        new_qty=qty.copy()
        if target is not None:
            for s in SYMBOLS:new_qty[s]=target[s]*max(equity,0)/float(row[s+'_open'])
        for s in exit_symbols:new_qty[s]=0.
        for s in SYMBOLS:
            delta=new_qty[s]-qty[s];price=float(row[s+'_open'])
            if abs(delta)*price<1e-12:continue
            turnover=abs(delta)*price;fee=turnover*COST
            cash-=delta*price+fee
            if qty[s]!=0 and (new_qty[s]==0 or qty[s]*new_qty[s]<0):round_trips+=1
            if new_qty[s]!=0 and (qty[s]==0 or new_month and identifier in ('monthly_pair_decay','monthly_short_boil')):
                entry[s]=price;entry_i[s]=i
            orders.append(dict(date=str(dt.date()),symbol=s,notional=float(turnover),fee=float(fee),reason=reason,
                               old_qty=float(qty[s]),new_qty=float(new_qty[s]),price=price))
            qty[s]=new_qty[s]
        value=cash+sum(qty[s]*float(row[s+'_close']) for s in SYMBOLS)
        if dt==dates[-1]:
            preliquidation_state=dict(equity=float(value),cash=float(cash),pause_month=pause_month,
                positions=[dict(symbol=s,weight=float(qty[s]*row[s+'_close']/value) if value>0 else 0.,
                                reference_price=float(row[s+'_raw_close']) if s+'_raw_close' in row else float(row[s+'_close']),
                                entry_reference=float(entry.get(s,row[s+'_close']))) for s in SYMBOLS if qty[s]!=0])
            for s in SYMBOLS:
                if qty[s]==0:continue
                price=float(row[s+'_close']);turnover=abs(qty[s])*price;fee=turnover*COST
                cash+=qty[s]*price-fee
                orders.append(dict(date=str(dt.date()),symbol=s,notional=float(turnover),fee=float(fee),reason='period_end',
                                   old_qty=float(qty[s]),new_qty=0.,price=price))
                qty[s]=0.;round_trips+=1
            value=cash
        if value<=0:insolvent=True
        curve.append(dict(date=str(dt.date()),equity=float(max(value,0))))
        prev_month,prev_week,last_dt=month,week,dt
    mt=performance([p['equity'] for p in curve],orders,borrow_paid,round_trips,insolvent)
    # End-of-period book is flat, so latest state is recomputed by a separate truncated replay.
    return dict(start=str(dates[0].date()),end=str(dates[-1].date()),metrics=mt,curve=curve,orders=orders,risk_exits=risk_exits,model_state=preliquidation_state)


def passes(results):
    train,hold=results['in_sample']['metrics'],results['holdout']['metrics']
    reasons=[]
    if train['total_return']<=0:reasons.append('样本内不盈利')
    if hold['total_return']<=0:reasons.append('留出不盈利')
    if hold['sharpe'] is None or hold['sharpe']<=.5:reasons.append('留出Sharpe未超过0.5')
    if any(r['metrics']['max_drawdown']<-.25 for r in results.values()):reasons.append('至少一个区间最大回撤超过25%')
    if any(r['metrics']['insolvent'] for r in results.values()):reasons.append('曾破产')
    return not reasons,reasons


def current_signal(panel,identifier):
    """Rules for next scheduled rebalance, NOT a reconstructed current broker holding."""
    row=panel.iloc[-1]
    # First next business day is a reference only; exchange holidays must be verified.
    last=panel.index[-1]
    dt=last+pd.offsets.BDay(1)
    if identifier in ('monthly_pair_decay','monthly_short_boil','slow_momentum'):dt=last+pd.offsets.BMonthBegin(1)
    elif identifier=='defensive_kold':dt=last+pd.Timedelta(days=(7-last.weekday())%7 or 7)
    weights=scheduled_weights(identifier,row,dt)
    action=' / '.join(('做多' if w>0 else '卖空')+s+' '+str(round(abs(w)*100,2))+'%' for s,w in weights.items() if w) or '空仓'
    return dict(action=action,weights=weights,basis_session=str(panel.index[-1].date()),
                reference_session=str(dt.date()),scope='下次计划调仓的规则目标，不是当前账户或模型实际持仓；须核验交易日、止损/暂停状态及可借券。')


def run_frozen_experiment(raw,output):
    """One-shot persisted ledger. Refuse overwrite to prevent iterative holdout mining."""
    output=Path(output)
    if output.exists():raise FileExistsError('实验结果已冻结，禁止覆盖/重复择优评估留出')
    digest=hashlib.sha256(DATA.read_bytes()).hexdigest()
    if digest not in PREREG.read_text():raise ValueError('冻结数据与预注册哈希不匹配')
    p=features(raw);all_results=[]
    for candidate in CANDIDATES:
        results={k:backtest(p,candidate['id'],*period) for k,period in PERIODS.items()}
        okay,reasons=passes(results)
        stress=backtest(p,candidate['id'],*PERIODS['holdout'],borrow=.15)
        all_results.append(dict(**candidate,passed=okay,fail_reasons=reasons,periods=results,
                                borrow15_holdout=stress['metrics'],signal=current_signal(p,candidate['id']),model_state=results['holdout']['model_state']))
    value=dict(schema_version=1,status='ok',updated_at=datetime.now(JST).isoformat(timespec='seconds'),
               asof_session=str(raw.index[-1].date()),timezone='Asia/Tokyo',data_sha256=digest,
               prereg_sha256=hashlib.sha256(PREREG.read_bytes()).hexdigest(),candidates=all_results,
               pass_count=sum(x['passed'] for x in all_results),cost_per_side=COST,borrow_annual=BORROW,
               methodology='六候选预注册；2017–2022样本内、2023验证、2024起一次留出；事后研究，不是前瞻盲测。')
    output.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return value


def public_snapshot(experiment):
    result=copy.deepcopy(experiment)
    for candidate in result['candidates']:
        for key,period in candidate['periods'].items():
            period.pop('orders',None)
            if key!='holdout':period.pop('curve',None)
    return result


def snapshot():
    try:
        value=json.loads(SEED.read_text(encoding='utf-8'))
        value['stale']=(datetime.now(JST).date()-datetime.fromisoformat(value['asof_session']).date()).days>5
        if value['stale']:
            for c in value['candidates']:
                c['signal']=dict(action='无数据：研究价格归档超过5日，目标信号禁用',weights={},scope='历史实验仍保留')
                c['model_state']=dict(positions=[],note='无数据：归档过期，纸面持仓仅保留在原始冻结实验记录中')
        return value
    except (OSError,ValueError):return dict(schema_version=1,status='no_data',error='无数据',candidates=[])
