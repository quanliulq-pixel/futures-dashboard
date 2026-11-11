from datetime import date, datetime, timedelta
import json
from pathlib import Path
import math
import numpy as np
import pandas as pd
import pytest
import ng_put_quant as q

ROOT=Path(__file__).resolve().parents[1]


def panel(n=300):
    rng=np.random.default_rng(42)
    p=pd.DataFrame(index=pd.bdate_range('2022-01-03',periods=n))
    for s in ('BOIL','UNG','NG=F'):
        c=100*np.exp(np.cumsum(.003+rng.normal(0,.005,n)))
        for k,v in [('open',c),('close',c*1.001),('high',c*1.01),('low',c*.99),('raw_close',c)]:p[s+'_'+k]=v
    return p


def test_bs_put_sanity_and_delta():
    price,delta=q.bs_put(100,100,1,.2,rate=0)
    assert price==pytest.approx(7.9655674554)
    assert -.5<delta<0
    assert q.bs_put(80,100,0,.2)==(20,-1)
    assert q.bs_put(120,100,0,.2)==(0,0)
    assert q.bs_put(100,100,1,0,rate=0)[0]==0
    assert q.bs_put(100,110,1,.4)[0]>q.bs_put(100,110,1,.2)[0]
    with pytest.raises(ValueError):q.bs_put(-1,100,1,.2)


def test_strike_otm_and_risk_size():
    k=q.strike_model(100,.5)
    assert 0<k<100
    assert q.strike_model(100,1)<k
    assert 0<q.collateral_weight(100,95,2)<=.1
    weight=q.collateral_weight(100,95,10)
    assert weight*((2.5-1)*10+2*q.unit_fee(100,10))/95<=.005+1e-12


def test_exit_rules():
    base=dict(mark=1,credit=2,spot=100,strike=90,dte=25,regime_ok=True,eia_guard=False)
    assert q.exit_reason(**base)=='take_profit'
    assert q.exit_reason(**{**base,'mark':5})=='premium_stop'
    assert q.exit_reason(**{**base,'mark':2,'spot':89})=='strike_breach'
    assert q.exit_reason(**{**base,'mark':2,'dte':7})=='expiry_guard'
    assert q.exit_reason(**{**base,'mark':2,'dte':0})=='expiry'
    assert q.exit_reason(**{**base,'mark':2,'regime_ok':False})=='regime_exit'
    assert q.exit_reason(**{**base,'mark':2,'eia_guard':True})=='weekly_event_proxy'
    assert q.exit_reason(**{**base,'mark':2}) is None


def test_regime_filter():
    r=q.features(panel()).iloc[-1]
    okay,_=q.eligible(r,'BOIL');assert okay
    r.ng_mom=-1
    assert not q.eligible(r,'BOIL')[0]
    r.ng_mom=.1;r['BOIL_hv']=1.51
    assert not q.eligible(r,'BOIL')[0]
    r['BOIL_hv']=.5;r['BOIL_hv60']=.1
    assert not q.eligible(r,'BOIL')[0]


def test_no_lookahead_features_and_equity():
    raw=panel();p=q.features(raw)
    other=raw.copy();other.iloc[200:]*=1.5
    b=q.features(other)
    pd.testing.assert_frame_equal(p.iloc[:200],b.iloc[:200])
    start,end=str(p.index[70].date()),str(p.index[-1].date())
    a,z=q.backtest(p,start,end),q.backtest(b,start,end)
    assert a['metrics']['strategy']['trade_count']>0
    assert a['curve'][:130]==z['curve'][:130]
    assert [t for t in a['trades'] if t['exit']<str(p.index[200].date())]==[t for t in z['trades'] if t['exit']<str(p.index[200].date())]


def test_weekly_event_proxy_and_no_same_day_reentry():
    p=q.features(panel());d=q.backtest(p,str(p.index[70].date()),str(p.index[-1].date()))
    for t in d['trades']:
        assert date.fromisoformat(t['entry']).weekday() not in (2,3)
    for s in q.SYMBOLS:
        trades=[t for t in d['trades'] if t['symbol']==s]
        for a,b in zip(trades,trades[1:]):assert a['exit']<b['entry']
    assert d['metrics']['strategy']['assignment_count']==0
    assert d['metrics']['strategy']['worst_trade']==min(t['collateral_return'] for t in d['trades'])


def chain(today):
    return pd.DataFrame([dict(strike=95,bid=1,ask=1.1,impliedVolatility=.3,openInterest=100,
                             contractSymbol='TEST_P95',lastTradeDate=pd.Timestamp(today,tz='UTC'))])


def test_real_chain_selection_and_no_synthetic_fallback():
    today=date(2026,11,11);expiry=str(today+timedelta(days=30));df=chain(today)
    selected=q.select_contract(df,100,.2,expiry,today)
    assert selected and selected['strike']==95 and selected['bid']==1
    assert -.3<=selected['delta']<=-.2
    assert selected['breakeven']==94
    assert selected['collateral_per_contract']==9500
    for column,value in [('bid',0),('ask',2),('impliedVolatility',.01),('openInterest',2),('lastTradeDate',pd.Timestamp('2000-01-01',tz='UTC'))]:
        changed=df.copy();changed[column]=value
        assert q.select_contract(changed,100,.2,expiry,today) is None
    assert q.select_contract(pd.DataFrame(),100,.2,expiry,today) is None
    assert q.select_contract(df,100,.2,str(today+timedelta(days=10)),today) is None


def test_chain_error_is_no_data_not_bs_quote(monkeypatch):
    import yfinance
    class NoChain:
        @property
        def options(self):raise RuntimeError('unavailable')
    monkeypatch.setattr(yfinance,'Ticker',lambda _:NoChain())
    live=q.fetch_live(panel())
    assert all(x['action']=='不卖' and x['quote'] is None for x in live)
    assert all('无数据' in x['chain_status'] for x in live)


def test_endpoint_shape_and_nonblocking(monkeypatch):
    monkeypatch.setenv('SKIP_BG_WARMUP','1')
    import app
    value=json.loads(q.SEED.read_text())
    monkeypatch.setattr(q,'snapshot',lambda:value.copy());monkeypatch.setattr(q,'needs_refresh',lambda _:True)
    calls=[];monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**kw:calls.append((a,kw)) or 'queued')
    monkeypatch.setattr(q,'refresh',lambda:pytest.fail('network in request'))
    d=app.app.test_client().get('/api/ng-sell-put').get_json()
    assert d['refresh_status']=='queued' and '模型定价回测' in d['label']
    assert set(d['in_sample']['metrics'])=={'strategy','BOIL','UNG'}
    assert 'assignment_count' in d['out_of_sample']['metrics']['strategy']
    assert calls[0][0][0]=='ng-sell-put'


def test_stale_chain_and_no_data(monkeypatch,tmp_path):
    value=json.loads(q.SEED.read_text());value['updated_at']='2000-01-01T00:00:00+09:00'
    monkeypatch.setattr(q,'_cache',value);monkeypatch.setattr(q,'ROOT',tmp_path);monkeypatch.setattr(q,'SEED',tmp_path/'none')
    d=q.snapshot()
    assert d['stale'] and all(x['action']=='不卖' and x['quote'] is None for x in d['live'])
    monkeypatch.setattr(q,'_cache',None)
    assert q.snapshot()['status']=='no_data'


def test_mirrors_and_archived_reproduction():
    saved=json.loads(q.SEED.read_text())
    rebuilt=q.build_payload(pd.read_json(q.RAW,orient='table'),saved['live'],datetime.fromisoformat(saved['updated_at']))
    for k in ('in_sample','out_of_sample'):
        for symbol in ('strategy','BOIL','UNG'):
            for metric,value in saved[k]['metrics'][symbol].items():
                if value is None:assert rebuilt[k]['metrics'][symbol][metric] is None
                else:assert rebuilt[k]['metrics'][symbol][metric]==pytest.approx(value)
    for f in ('templates/index.html','docs/index.html'):
        text=(ROOT/f).read_text()
        assert 'ng-put-quant-content' in text and '模型定价回测，非真实期权成交' in text
        assert 'data-view="ai"' in text and 'ng_put_view.js' in text
    assert (ROOT/'static/ng_put_view.js').read_bytes()==(ROOT/'docs/static/ng_put_view.js').read_bytes()
    json.dumps(rebuilt,allow_nan=False)
