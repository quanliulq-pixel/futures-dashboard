from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import ng_call_quant as q
import ng_put_quant as put

ROOT=Path(__file__).resolve().parents[1]


def panel(n=320):
    rng=np.random.default_rng(707)
    p=pd.DataFrame(index=pd.bdate_range('2022-01-03',periods=n))
    for s in ('BOIL','UNG','NG=F'):
        c=100*np.exp(np.cumsum(-.003+rng.normal(0,.008,n)))
        for k,v in [('open',c),('close',c*.999),('high',c*1.01),('low',c*.99),('raw_close',c)]:p[s+'_'+k]=v
    return p


def test_call_bs_delta_and_put_call_parity():
    price,delta=q.bs_call(100,100,1,.2,rate=0)
    assert price==pytest.approx(7.9655674554)
    assert .5<delta<1
    for s,k,t,vol in [(100,110,1,.4),(1,3,.01,2),(100,95,.1,.8)]:
        assert q.bs_call(s,k,t,vol)[0]-put.bs_put(s,k,t,vol)[0]==pytest.approx(s-k*math.exp(-.03*t))
    assert q.bs_call(120,100,0,.2)==(20,1)
    assert q.bs_call(80,100,0,.2)==(0,0)
    assert q.bs_call(100,100,1,0,0)==(0,0)
    with pytest.raises(ValueError):q.bs_call(-1,100,1,.3)


def test_strikes_and_defined_risk_payoff_including_fees():
    short,long=q.strikes(100,.5)
    assert 100<short<long
    assert long-short==pytest.approx(10)
    assert q.bs_call(100,short,30/365,.6)[1]==pytest.approx(.25)
    credit=q.spread_mark(100,short,long,30,.6)
    risk=q.max_unit_loss(short,long,credit,100)
    for spot in (.01,100,150,10000):
        for dte in (30,7,0):
            mark=q.spread_mark(spot,short,long,dte,2.)
            assert 0<=mark<=long-short
            pnl=credit-q.fee(credit,100)-mark-q.fee(mark,100)
            assert pnl>=-risk-1e-10
    with pytest.raises(ValueError):q.spread_mark(100,120,110,30,.5)


def test_bearish_filters_and_missing_features():
    r=put.features(panel()).iloc[-1]
    assert q.eligible(r,'UNG')[0]
    for key,value in [('ng_mom',.1),('ng_hv',1.0),('UNG_hv',1.51),('UNG_close',1000),('ng_ma20',float('nan'))]:
        x=r.copy();x[key]=value
        assert not q.eligible(x,'UNG')[0]
    r['UNG_hv']=.5;r['UNG_hv60']=.1
    assert not q.eligible(r,'UNG')[0]


def test_exit_rules_and_no_roll():
    d=dict(mark=1.5,credit=2,spot=100,short=110,dte=25,regime_ok=True,eia_guard=False)
    assert q.exit_reason(**d) is None
    for kw,why in [({'mark':1},'take_profit'),({'mark':5},'premium_stop'),({'spot':120},'short_strike_breach'),
                   ({'dte':7},'expiry_guard'),({'dte':0},'expiry'),({'regime_ok':False},'regime_exit'),
                   ({'eia_guard':True},'weekly_event_proxy')]:
        assert q.exit_reason(**{**d,**kw})==why


def test_no_lookahead_equity_and_trade_audit():
    a=panel();b=a.copy();b.iloc[200:]*=1.5
    p,z=put.features(a),put.features(b)
    pd.testing.assert_frame_equal(p.iloc[:200],z.iloc[:200])
    start,end=str(p.index[70].date()),str(p.index[-1].date())
    x,y=q.backtest(p,start,end),q.backtest(z,start,end)
    assert x['metrics']['trade_count']>0
    assert x['curve'][:130]==y['curve'][:130]
    assert [t for t in x['trades'] if t['exit']<str(p.index[200].date())]==[t for t in y['trades'] if t['exit']<str(p.index[200].date())]
    assert sum(t['pnl'] for t in x['trades'])==pytest.approx(x['metrics']['total_return'])
    assert all(t['pnl']>=-t['max_loss']-1e-12 and t['risk_return']>=-1-1e-10 for t in x['trades'])


def test_weekly_event_proxy_no_reentry_and_risk_limit():
    p=put.features(panel());x=q.backtest(p,str(p.index[70].date()),str(p.index[-1].date()))
    for t in x['trades']:
        assert datetime.fromisoformat(t['entry']).weekday() not in (2,3)
        prior=next((v['strategy'] for v in reversed(x['curve']) if v['date']<t['entry']),1.)
        # Actual sizing uses opening equity after any exits, not previous close NAV.
        assert t['risk']<prior*.0051
    for s in ('BOIL','UNG'):
        ts=[t for t in x['trades'] if t['symbol']==s]
        assert all(a['exit']<b['entry'] for a,b in zip(ts,ts[1:]))
    assert x['metrics']['worst_trade']==min(t['risk_return'] for t in x['trades'])


def test_open_gap_breach_skips_new_entries():
    p=put.features(panel());start=str(p.index[70].date());end=str(p.index[-1].date())
    p['UNG_open']=p['UNG_close'].shift(1)*3
    x=q.backtest(p,start,end,('UNG',))
    assert x['metrics']['trade_count']==0
    assert x['metrics']['win_rate'] is None and x['metrics']['worst_trade'] is None


def test_tail_windows_mark_to_market_and_actual_coverage():
    curve=[dict(date='2022-01-03',strategy=.99),dict(date='2022-03-02',strategy=.98),dict(date='2022-04-01',strategy=1.01)]
    x=q.tail_windows(curve,[])
    assert x[0]['label']=='2022天然气尖峰'
    assert x[0]['max_drawdown']==pytest.approx(-.02)
    assert x[0]['total_return']==pytest.approx(.01)
    assert x[1]['end']=='2022-03-02' and x[1]['worst_trade'] is None


def test_today_signal_never_synthetic_quotes_and_stale_data():
    p=panel()
    live=q.current_signal(p,datetime(2023,3,25,tzinfo=q.nq.JST))
    assert all(x['quote'] is None and x['action']=='不卖' and '纸面信号，不构成下单' in x['gate'] for x in live)
    live=q.current_signal(p,datetime(2026,11,11,tzinfo=q.nq.JST))
    assert all(x['paper_signal']=='空仓' and '无数据' in x['reason'] for x in live)


def test_readonly_endpoint_no_data_and_staleness(monkeypatch,tmp_path):
    monkeypatch.setenv('SKIP_BG_WARMUP','1')
    import app
    monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**kw:pytest.fail('must never schedule network/backtest'))
    monkeypatch.setattr(q,'SEED',tmp_path/'none')
    monkeypatch.setattr(q,'backtest',lambda *a,**kw:pytest.fail('must never recompute'))
    assert app.app.test_client().get('/api/ng-sell-call').get_json()['status']=='no_data'
    value={'status':'ok','asof_session':'2000-01-01','live':[dict(paper_signal='满足价格过滤',quote={'synthetic':True})]}
    q.SEED.write_text(json.dumps(value))
    d=app.app.test_client().get('/api/ng-sell-call').get_json()
    assert d['stale'] and d['live'][0]['quote'] is None and d['live'][0]['paper_signal']=='空仓'


def test_frozen_archive_audit_and_mirror_integrity():
    import hashlib
    value=json.loads(q.ARCHIVE.read_text())
    assert value==json.loads(q.SEED.read_text())
    assert value['source']['sha256']==hashlib.sha256(q.RAW.read_bytes()).hexdigest()
    assert value['source']['prereg_sha256']==hashlib.sha256((ROOT/'data/ng_quant/call_prereg.md').read_bytes()).hexdigest()
    assert set(value['variants'])=={'UNG','BOIL','combined'}
    for name,segments in value['variants'].items():
        for key,r in segments.items():
            assert r['curve'][0]['date']==r['start'] and r['curve'][-1]['date']==r['end']
            assert r['metrics']['total_return']==pytest.approx(sum(t['pnl'] for t in r['trades']))
            assert r['metrics']['trade_count']==len(r['trades'])
            assert r['metrics']['win_rate']==pytest.approx(sum(t['pnl']>0 for t in r['trades'])/len(r['trades']))
            assert r['metrics']['worst_trade']==min(t['risk_return'] for t in r['trades'])
            assert all(t['pnl']>=-t['max_loss']-1e-12 for t in r['trades'])
            assert set(r['stress'])=={'iv_hv_1_0','cost_x2'}
    assert all(x['quote'] is None and x['action']=='不卖' for x in value['live'])
    json.dumps(value,allow_nan=False)
    assert (ROOT/'static/ng_call_view.js').read_bytes()==(ROOT/'docs/static/ng_call_view.js').read_bytes()
    for f in ('templates/index.html','docs/index.html'):
        html=(ROOT/f).read_text()
        assert 'ng-call-quant-content' in html and 'ng_call_view.js' in html
        assert 'data-view="ai"' in html and 'data-view="cnidx"' in html
        assert '纸面信号，不构成下单' in html


def test_export_does_not_recompute_and_run_once_refuses_existing():
    before=q.ARCHIVE.read_bytes()
    command=[sys.executable,str(ROOT/'scripts/build_ng_sell_call.py')]
    result=subprocess.run(command+['--run-once'],capture_output=True,text=True)
    assert result.returncode!=0 and '拒绝重新评估' in result.stderr
    result=subprocess.run(command+['--export'],capture_output=True,text=True)
    assert result.returncode==0
    assert q.ARCHIVE.read_bytes()==before and q.SEED.read_bytes()==before


def test_frozen_endpoint_returns_results_without_background_jobs(monkeypatch):
    monkeypatch.setenv('SKIP_BG_WARMUP','1')
    import app
    monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**kw:pytest.fail('network job forbidden'))
    result=app.app.test_client().get('/api/ng-sell-call')
    d=result.get_json()
    assert result.status_code==200 and d['status']=='ok'
    assert set(d['variants'])=={'UNG','BOIL','combined'}
    assert d['label'].startswith('模型定价非真实期权报价')
