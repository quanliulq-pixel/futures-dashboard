"""Cash-secured NG ETF short puts: model-priced research, NOT option trade history."""
from __future__ import annotations
import copy
import json
import math
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import ng_quant as nq

ROOT = Path(__file__).resolve().parent
SEED = ROOT/'docs/api/ng_sell_put.json'
RAW = ROOT/'data/ng_quant/put_prices.json'
PARAMS = dict(dte=30, min_dte=21, max_dte=45, otm_move=1.25, iv_hv_assumption=1.20,
              iv_hv_live_min=1.10, hv_cap=1.50, vol_jump_limit=1.50,
              take_profit=0.50, stop_premium_multiple=2.50, exit_dte=7,
              collateral_per_symbol=0.10, collateral_total=0.20, planned_risk=0.005,
              rate=0.03, dividend_yield=0.0, half_spread_premium=0.05, fee_notional=0.0005)
_cache = None
_lock = threading.Lock()
SYMBOLS = ('BOIL','UNG')


def norm_cdf(x):
    return .5*(1+math.erf(x/math.sqrt(2)))


def bs_put(spot, strike, years, vol, rate=.03, dividend=0.0):
    if spot <= 0 or strike <= 0 or not all(math.isfinite(x) for x in (spot,strike,years,vol,rate,dividend)):
        raise ValueError('invalid BS inputs')
    if years <= 0:
        return max(strike-spot,0.), (-1. if spot < strike else 0.)
    if vol <= 0:
        intrinsic = strike*math.exp(-rate*years)-spot*math.exp(-dividend*years)
        return max(intrinsic,0.), (-math.exp(-dividend*years) if intrinsic>0 else 0.)
    d1=(math.log(spot/strike)+(rate-dividend+.5*vol*vol)*years)/(vol*math.sqrt(years))
    d2=d1-vol*math.sqrt(years)
    price=strike*math.exp(-rate*years)*norm_cdf(-d2)-spot*math.exp(-dividend*years)*norm_cdf(-d1)
    return max(price,0.), math.exp(-dividend*years)*(norm_cdf(d1)-1)


def strike_model(spot, hv):
    """Synthetic continuous strike, not an actual listed option contract."""
    return spot*math.exp(-PARAMS['otm_move']*hv*math.sqrt(PARAMS['dte']/365))


def features(panel):
    p=panel.copy().sort_index()
    ng=p['NG=F_close']
    p['ng_ma20']=ng.rolling(20).mean(); p['ng_ma60']=ng.rolling(60).mean()
    p['ng_mom']=ng.pct_change(10,fill_method=None)
    p['ng_hv']=np.log(ng/ng.shift()).rolling(20).std()*math.sqrt(252)
    for s in SYMBOLS:
        c=p[s+'_close']; log=np.log(c/c.shift())
        p[s+'_ma20']=c.rolling(20).mean()
        p[s+'_hv']=log.rolling(20).std()*math.sqrt(252)
        p[s+'_hv60']=log.rolling(60).std()*math.sqrt(252)
    return p


def eligible(row, symbol):
    values=[row[k] for k in ('NG=F_close','ng_ma20','ng_ma60','ng_mom','ng_hv',symbol+'_close',symbol+'_ma20',symbol+'_hv',symbol+'_hv60')]
    if not all(math.isfinite(float(x)) for x in values):
        return False,'指标无数据'
    if not (row['NG=F_close']>row.ng_ma20>row.ng_ma60 and row.ng_mom>0):
        return False,'NG非一致上升趋势，不卖多头ETF的Put'
    if not (0<row.ng_hv<=.9 and 0<row[symbol+'_hv']<=PARAMS['hv_cap']):
        return False,'波动率超限或无效'
    if row[symbol+'_hv']>PARAMS['vol_jump_limit']*row[symbol+'_hv60']:
        return False,'HV20超过HV60的1.5倍，波动突升'
    if row[symbol+'_close']<=row[symbol+'_ma20']:
        return False,'标的跌破MA20'
    return True,'NG上升趋势、ETF站上MA20、HV及波动突升过滤通过'


def exit_reason(mark, credit, spot, strike, dte, regime_ok, eia_guard):
    if dte<=0: return 'expiry'
    if dte<=PARAMS['exit_dte']: return 'expiry_guard'
    if spot<=strike: return 'strike_breach'
    if mark>=PARAMS['stop_premium_multiple']*credit: return 'premium_stop'
    if mark<=PARAMS['take_profit']*credit: return 'take_profit'
    if not regime_ok: return 'regime_exit'
    if eia_guard: return 'weekly_event_proxy'
    return None


def unit_fee(spot, premium):
    return PARAMS['half_spread_premium']*premium+PARAMS['fee_notional']*spot


def collateral_weight(spot, strike, premium):
    risk=((PARAMS['stop_premium_multiple']-1)*premium+2*unit_fee(spot,premium))/strike
    return min(PARAMS['collateral_per_symbol'],PARAMS['planned_risk']/risk) if risk>0 else 0.


def backtest(p,start,end):
    dates=p.index[(p.index>=pd.Timestamp(start))&(p.index<=pd.Timestamp(end))]
    if len(dates)<2: raise ValueError('回测无数据')
    cash=1.; positions={}; trades=[]; curve=[]; assignments=0
    for dt in dates:
        i=p.index.get_loc(dt)
        if i<61: raise ValueError('预热不足')
        prev,row=p.iloc[i-1],p.loc[dt]
        exited=set()
        # EIA calendar proxy only: close Wednesday open; no Wednesday/Thursday entries.
        eia_guard=dt.weekday()==2
        for s,pos in list(positions.items()):
            spot=float(row[s+'_open']); dte=(pos['expiry']-dt).days
            vol=float(prev[s+'_hv'])*PARAMS['iv_hv_assumption']
            mark,_=bs_put(spot,pos['strike'],max(dte,0)/365,vol,PARAMS['rate'])
            okay,_=eligible(prev,s)
            why=exit_reason(mark,pos['credit'],spot,pos['strike'],dte,okay,eia_guard)
            if why:
                debit=pos['units']*(mark+unit_fee(spot,mark))
                cash-=debit
                pnl=pos['net_credit']-debit
                assigned=why=='expiry' and spot<pos['strike']
                assignments+=int(assigned)
                trades.append(dict(symbol=s,entry=str(pos['entry'].date()),exit=str(dt.date()),
                                   reason=why,days=i-pos['entry_i'],pnl=pnl,
                                   collateral_return=pnl/pos['collateral'],assigned=bool(assigned)))
                del positions[s]; exited.add(s)
        if dt.weekday() not in (2,3):
            for s in SYMBOLS:
                if s in positions or s in exited: continue
                okay,_=eligible(prev,s)
                if not okay: continue
                spot=float(row[s+'_open']); hv=float(prev[s+'_hv'])
                strike=strike_model(float(prev[s+'_close']),hv)
                if spot<=strike: continue
                expiry=dt+pd.Timedelta(days=PARAMS['dte'])
                credit,_=bs_put(spot,strike,PARAMS['dte']/365,hv*PARAMS['iv_hv_assumption'],PARAMS['rate'])
                if credit<=unit_fee(spot,credit): continue
                liabilities=0.
                for old_s,pos in positions.items():
                    m,_=bs_put(float(row[old_s+'_open']),pos['strike'],max((pos['expiry']-dt).days,0)/365,
                               float(prev[old_s+'_hv'])*PARAMS['iv_hv_assumption'],PARAMS['rate'])
                    liabilities+=pos['units']*m
                equity=cash-liabilities
                reserved=sum(pos['collateral'] for pos in positions.values())
                available=max(0.,min(equity*PARAMS['collateral_total']-reserved,cash-reserved))
                collateral=min(equity*collateral_weight(spot,strike,credit),available)
                if collateral<=0: continue
                units=collateral/strike
                net_credit=units*(credit-unit_fee(spot,credit))
                cash+=net_credit
                positions[s]=dict(strike=strike,credit=credit,units=units,collateral=collateral,
                                  net_credit=net_credit,entry=dt,entry_i=i,expiry=expiry)
        liability=0.
        for s,pos in list(positions.items()):
            spot=float(row[s+'_close'])
            dte=max((pos['expiry']-dt).days,0)
            mark,_=bs_put(spot,pos['strike'],dte/365,float(prev[s+'_hv'])*PARAMS['iv_hv_assumption'],PARAMS['rate'])
            if dt==dates[-1]:
                debit=pos['units']*(mark+unit_fee(spot,mark)); cash-=debit
                pnl=pos['net_credit']-debit
                trades.append(dict(symbol=s,entry=str(pos['entry'].date()),exit=str(dt.date()),reason='period_end',
                                   days=i-pos['entry_i']+1,pnl=pnl,collateral_return=pnl/pos['collateral'],assigned=False))
                del positions[s]
            else: liability+=pos['units']*mark
        curve.append(dict(date=str(dt.date()),strategy=float(cash-liability)))
    mt=q_metrics([r['strategy'] for r in curve],trades)
    mt['assignment_count']=assignments
    results={'strategy':mt}
    for s in SYMBOLS:
        # Same entry/exit fee assumptions as stock buy-and-hold in the BOIL/KOLD system.
        units=1/(float(p.loc[dates[0],s+'_open'])*1.001)
        vals=[units*float(p.loc[d,s+'_close']) for d in dates]; vals[-1]*=.999
        results[s]=nq.metrics(vals,[{'return':vals[-1]-1,'days':len(dates)}])
        for point,v in zip(curve,vals): point[s]=float(v)
    return dict(start=str(dates[0].date()),end=str(dates[-1].date()),metrics=results,curve=curve,trades=trades)


def q_metrics(values,trades):
    converted=[{'return':t['pnl'],'days':t['days']} for t in trades]
    m=nq.metrics(values,converted)
    m['worst_trade']=min((t['collateral_return'] for t in trades),default=None)
    return m


def select_contract(puts,spot,hv,expiry,today):
    """Use real bid/ask/IV only. Never replace a missing chain with BS quotes."""
    dte=(datetime.fromisoformat(expiry).date()-today).days
    if not PARAMS['min_dte']<=dte<=PARAMS['max_dte']: return None
    candidates=[]
    for _,r in puts.iterrows():
        try:
            if r.get('contractSize','REGULAR')!='REGULAR' or r.get('currency','USD')!='USD': continue
            k,bid,ask,iv,oi=[float(r[x]) for x in ('strike','bid','ask','impliedVolatility','openInterest')]
            if not all(math.isfinite(x) for x in (k,bid,ask,iv,oi)): continue
            if not (0<k<spot and 0<bid<=ask and iv>0 and oi>=100): continue
            mid=(bid+ask)/2
            if (ask-bid)/mid>.25: continue
            _,delta=bs_put(spot,k,dte/365,iv,PARAMS['rate'])
            if not .20<=abs(delta)<=.30: continue
            if iv<hv*PARAMS['iv_hv_live_min']: continue
            last=r.get('lastTradeDate')
            if last is None or pd.isna(last): continue
            last=pd.Timestamp(last)
            if last.tzinfo is None: last=last.tz_localize('UTC')
            age=(today-last.tz_convert('America/New_York').date()).days
            if not 0<=age<=3: continue
            candidate=dict(contract=str(r.get('contractSymbol','')),strike=k,expiry=expiry,dte=dte,
                           bid=bid,ask=ask,iv=iv,delta=delta,open_interest=int(oi),
                           premium_yield=bid/k,breakeven=k-bid,collateral_per_contract=100*k,
                           last_trade_at=last.tz_convert('Asia/Tokyo').isoformat(),
                           collateral_weight=collateral_weight(spot,k,bid))
            candidates.append(candidate)
        except (ValueError,TypeError,KeyError): continue
    return min(candidates,key=lambda x:abs(abs(x['delta'])-.25)) if candidates else None


def fetch_live(panel):
    import yfinance as yf
    now=datetime.now(nq.JST); today=now.astimezone(ZoneInfo('America/New_York')).date()
    f=features(panel); last=f.iloc[-1]; out=[]; archives=[]
    for s in SYMBOLS:
        okay,reason=eligible(last,s)
        item=dict(symbol=s,action='不卖',regime_ok=okay,reason=reason,quote=None,
                  chain_status='无数据',spot=float(last[s+'_raw_close']),hv=float(last[s+'_hv']),
                  event_calendar='无数据：需人工核实EIA节假日调整、分红、拆并股公告')
        try:
            ticker=yf.Ticker(s)
            expiries=[e for e in ticker.options if PARAMS['min_dte']<=(datetime.fromisoformat(e).date()-today).days<=PARAMS['max_dte']]
            if expiries:
                expiry=min(expiries,key=lambda e:abs((datetime.fromisoformat(e).date()-today).days-PARAMS['dte']))
                puts=ticker.option_chain(expiry).puts
                archives.append(dict(symbol=s,expiry=expiry,retrieved_at=now.isoformat(timespec='seconds'),
                                     rows=json.loads(puts.to_json(orient='records',date_format='iso'))))
                item['chain_rows']=len(puts)
                item['selected_expiry']=expiry
                samples=[]
                for _,r in puts.iterrows():
                    try:
                        k,bid,ask,iv=[float(r[x]) for x in ('strike','bid','ask','impliedVolatility')]
                        if not all(math.isfinite(v) for v in (k,bid,ask,iv)) or not (0<k<item['spot'] and 0<bid<=ask and iv>0): continue
                        _,delta=bs_put(item['spot'],k,(datetime.fromisoformat(expiry).date()-today).days/365,iv,PARAMS['rate'])
                        samples.append(dict(strike=k,bid=bid,ask=ask,iv=iv,delta=delta,expiry=expiry))
                    except (ValueError,KeyError,TypeError): pass
                item['chain_sample']=sorted(samples,key=lambda r:abs(abs(r['delta'])-.25))[:2]
                item['chain_status']='已获取真实期权链；不等于实时可成交报价'
                item['quote']=select_contract(puts,item['spot'],item['hv'],expiry,today)
                if item['quote'] is None: item['chain_status']='所选30天附近到期链无满足全部delta/IV/HV/价差/OI/交易时间规则的合约，候选无数据'
            item['reason']=reason+'；事件日历未核验，研究候选不作为下单建议'
        except Exception as exc:
            item['chain_status']='无数据：Yahoo期权链获取失败（'+type(exc).__name__+'）'
        # Even qualified chain candidates stay gated until the external event calendar is verified.
        item['updated_at']=now.isoformat(timespec='seconds')
        out.append(item)
    if archives:
        path=ROOT/'data/ng_quant/put_chain_snapshot.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(archives,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return out


def fetch_panel():
    import net_limits
    p=pd.read_json(nq.RAW,orient='table')
    d=net_limits.yf_download('UNG',start='2016-01-01',auto_adjust=False,threads=False,progress=False,timeout=20)
    if isinstance(d.columns,pd.MultiIndex): d=d.xs('UNG',axis=1,level=-1)
    if d.empty: raise ValueError('UNG 无数据')
    ratio=d['Adj Close']/d['Close']
    f=pd.DataFrame({'UNG_'+k.lower():d[k]*ratio for k in ('Open','High','Low','Close')})
    f['UNG_raw_close']=d['Close']; f.index=pd.to_datetime(f.index).tz_localize(None).normalize()
    p=p.join(f,how='inner').dropna()
    quality=['BOIL / NG日线使用原量化系统的最近完整归档，不强制刷新其他数据。']
    missing=d.index[d[['Close','Adj Close']].isna().any(axis=1)]
    if len(missing): quality.append('UNG不完整日线未填充：'+', '.join(str(x.date()) for x in missing[-5:]))
    p.attrs['data_quality']=quality
    if len(p)<300 or not np.isfinite(p.to_numpy()).all() or (p<=0).any().any(): raise ValueError('历史价格无数据或无效')
    return p


def build_payload(panel,live=None,now=None):
    now=now or datetime.now(nq.JST)
    p=features(panel)
    return dict(schema_version=1,status='ok',updated_at=now.isoformat(timespec='seconds'),timezone='Asia/Tokyo',
                asof_session=str(p.index[-1].date()),params=PARAMS,
                label='模型定价回测，非真实期权成交',live=live or [],data_quality=panel.attrs.get('data_quality',[]),
                in_sample=backtest(p,'2017-01-01','2023-12-31'),
                out_of_sample=backtest(p,'2024-01-01',str(p.index[-1].date())),
                limitations=[
                    '无免费可验证的历史期权成交序列。本页全部期权回测为Black-Scholes欧式模型，IV=前日HV20×1.20是固定假设，不是真实历史IV或已观察到的波动率溢价。',
                    '连续合成行权价=前日收盘×exp(-1.25×HV20×sqrt(30/365))，30个日历日合成到期；使用拆并股/分红复权OHLC和可分数合约单位，不是历史挂牌行权价、100股整数合约或真实成交。',
                    '固定利率3%、股息率0；买卖各扣5%期权模型价作为半价差，另扣标的名义额5bp作为费用。持有标的对照单边10bp。模型价差/费用均为假设。',
                    '波动率、趋势与行权价仅使用前日信息；次日开盘交易/触发退出；收盘按前日HV模型盯市。止盈50%，期权价达初始2.5倍止损、标的跌到行权价、趋势失效或余期≤7天退出。不是盘中可保证执行的止损。',
                    '回测使用EIA星期代理，实盘面板在完整事件日历未核验时暂停；两者信息条件不同。每周三开盘平仓，周三/周四不新开，作为EIA事件的保守星期代理；没有历史节假日调整与完整分红/拆并股事件日历，不能认为已经覆盖所有事件。',
                    '到期价内次数仅表示模型到期结算（按内在价值结算并假设立即处置股票）。美式提前行权、真实指派率和股数调整无数据、未模拟，不能把0次当作无指派风险。',
                    'BOIL每日杠杆重置与长期衰减使卖Put接盘风险很高；UNG也有期货展期风险。未纳入KOLD，避免混淆其卖Put是偏空天然气的方向。',
                    '入场时每标的担保金≤10%、合计≤20%，持仓后比例随净值变动；0.5%计划止损预算不是最大亏损承诺，真实最大亏损接近全部担保金。现金担保、不使用保证金杠杆。',
                    '样本内2017–2023，2024起事后留出检验，固定规则未调参，非真实前瞻验证；100%持有标的与≤20%担保金组合风险敞口不同。',
                    '真实期权链候选只展示真实bid/ask/IV；delta为基于真实IV计算的欧式近似。Yahoo缺少可靠报价时标，lastTradeDate仅是最近交易时间。事件日历无数据时默认不卖，候选需人工重新核价。'])


def refresh():
    global _cache
    with _lock:
        p=fetch_panel(); live=fetch_live(p); value=build_payload(p,live)
        RAW.write_text(p.to_json(orient='table',date_format='iso'),encoding='utf-8')
        (RAW.parent/'put_prices_meta.json').write_text(json.dumps(dict(retrieved_at=value['updated_at'],
            source='Yahoo Finance; BOIL/NG from archived daily panel, UNG newly fetched',
            data_quality=value['data_quality'],start=str(p.index[0].date()),end=str(p.index[-1].date())),
            ensure_ascii=False,indent=2),encoding='utf-8')
        path=ROOT/'data/ng_quant/put_latest.json'
        tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8'); tmp.replace(path)
        _cache=value
        return value


def snapshot():
    values=[_cache]
    for path in (ROOT/'data/ng_quant/put_latest.json',SEED):
        try: values.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError): pass
    for value in values:
        if value and value.get('status')=='ok':
            value=copy.deepcopy(value)
            value['stale']=(datetime.now(nq.JST)-datetime.fromisoformat(value['updated_at'])).total_seconds()>24*3600
            if value['stale']:
                for item in value['live']:
                    item.update(action='不卖',quote=None,chain_sample=[],chain_status='无数据：期权链快照超过24小时')
            return value
    return dict(schema_version=1,status='no_data',error='无数据',label='模型定价回测，非真实期权成交',live=[])


def needs_refresh(value):
    return value.get('status')!='ok' or datetime.now(nq.JST)-datetime.fromisoformat(value['updated_at'])>timedelta(hours=6)
