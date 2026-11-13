from datetime import datetime
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import ng_strategy_lab as q

ROOT=Path(__file__).resolve().parents[1]


def panel(n=800):
    p=pd.DataFrame(index=pd.bdate_range('2016-01-04',periods=n));rng=np.random.default_rng(73)
    for s in q.SYMBOLS+('NG=F',):
        c=100*np.exp(np.cumsum(.0005+rng.normal(0,.01,n)))
        p[s+'_open']=c;p[s+'_close']=c*1.001;p[s+'_raw_close']=c*1.001
    return p


def test_six_rules_and_boundaries():
    assert len(q.CANDIDATES)==6
    r=q.features(panel()).iloc[-1];r.ng_mom126=.1;r.ng_mom63=-.1;r.ng_ma60=1e9
    r['UNG_hv60']=.4;r['KOLD_hv60']=.5
    assert q.scheduled_weights('monthly_pair_decay',r,pd.Timestamp('2023-01-03'))=={'BOIL':-.1,'KOLD':-.1,'UNG':0}
    assert q.scheduled_weights('monthly_short_boil',r,pd.Timestamp('2023-01-03'))['BOIL']==-.1
    assert q.scheduled_weights('seasonal_ung',r,pd.Timestamp('2023-10-03'))['UNG']==.25
    assert q.scheduled_weights('seasonal_ung',r,pd.Timestamp('2023-03-03'))['UNG']==-.25
    assert q.scheduled_weights('seasonal_ung',r,pd.Timestamp('2023-06-03'))['UNG']==0
    assert q.scheduled_weights('slow_momentum',r,pd.Timestamp('2023-01-03'))['UNG']==.25
    assert q.scheduled_weights('defensive_kold',r,pd.Timestamp('2023-01-03'))['KOLD']==.2
    r.ng_mom5=-.16;r['NG=F_hv20']=.6;r['UNG_hv20']=.4
    assert q.scheduled_weights('extreme_reversion',r,pd.Timestamp('2023-01-03'))['UNG']==.25
    r['NG=F_hv20']=2
    assert not any(q.scheduled_weights('extreme_reversion',r,pd.Timestamp('2023-01-03')).values())


def test_no_lookahead_all_candidates():
    raw=panel();original=q.features(raw)
    changed=raw.copy();changed.iloc[600:]*=1.4;b=q.features(changed)
    pd.testing.assert_frame_equal(original.iloc[:600],b.iloc[:600])
    start,end=str(raw.index[150].date()),str(raw.index[-1].date())
    for c in q.CANDIDATES:
        a,z=q.backtest(original,c['id'],start,end),q.backtest(b,c['id'],start,end)
        assert a['curve'][:450]==z['curve'][:450]
        assert [o for o in a['orders'] if o['date']<str(raw.index[600].date())]==[o for o in z['orders'] if o['date']<str(raw.index[600].date())]


def test_borrow_and_costs_on_flat_prices():
    raw=panel(500)
    for col in raw:raw[col]=100.
    p=q.features(raw);start,end=str(raw.index[150].date()),str(raw.index[-1].date())
    a=q.backtest(p,'monthly_short_boil',start,end,borrow=.10)
    b=q.backtest(p,'monthly_short_boil',start,end,borrow=.15)
    assert a['metrics']['borrow_paid']>0
    assert b['metrics']['total_return']<a['metrics']['total_return']<0
    assert all(o['fee']==pytest.approx(o['notional']*.001) for o in a['orders'])
    # First interval charges actual calendar days, including weekend if present.
    first=pd.Timestamp(a['orders'][0]['date']);next_day=raw.index[raw.index.get_loc(first)+1]
    first_value=1-.1*.001
    expected=first_value-.1*.10*(next_day-first).days/365
    assert a['curve'][1]['equity']==pytest.approx(expected)


def test_short_stop_and_no_same_month_reentry():
    raw=panel(500);p=q.features(raw);start_i=150
    start=raw.index[start_i]
    p.loc[raw.index[start_i+1],'BOIL_close']=p.loc[start,'BOIL_open']*1.6
    result=q.backtest(p,'monthly_short_boil',str(start.date()),str(raw.index[start_i+10].date()))
    stopped=[o for o in result['orders'] if o['reason']=='short_stop']
    assert stopped and stopped[0]['date']==str(raw.index[start_i+2].date())
    after=[o for o in result['orders'] if o['date']>stopped[0]['date'] and o['new_qty']<0]
    assert not any(pd.Timestamp(o['date']).month==pd.Timestamp(stopped[0]['date']).month for o in after)


def test_pass_threshold_frozen_and_multiple_testing():
    def m(ret=.1,sharpe=.6,dd=-.1):return dict(total_return=ret,sharpe=sharpe,max_drawdown=dd,insolvent=False)
    d={k:{'metrics':m()} for k in q.PERIODS}
    assert q.passes(d)[0]
    d['holdout']['metrics']['sharpe']=.5;assert not q.passes(d)[0]
    d['holdout']['metrics']['sharpe']=.6;d['validation']['metrics']['max_drawdown']=-.26
    assert not q.passes(d)[0]
    mt=q.performance([1,1.01,1.005,1.02],[],0,0,False)
    assert 0<=mt['bonferroni_p_iid']<=1


def test_one_shot_ledger_refuses_overwrite(tmp_path):
    path=tmp_path/'result.json';path.write_text('{}')
    with pytest.raises(FileExistsError):q.run_frozen_experiment(panel(),path)


def test_scheduled_date_is_not_tomorrow_for_monthly():
    p=q.features(panel())
    s=q.current_signal(p,'monthly_short_boil')
    assert pd.Timestamp(s['reference_session']).month != p.index[-1].month
    assert '不是当前账户' in s['scope']


def test_frozen_hashes_and_all_candidates_preserved():
    import hashlib
    value=json.loads((ROOT/'data/ng_quant/lab_experiment.json').read_text())
    assert hashlib.sha256(q.DATA.read_bytes()).hexdigest()==value['data_sha256']
    assert hashlib.sha256(q.PREREG.read_bytes()).hexdigest()==value['prereg_sha256']
    assert len(value['candidates'])==6
    for c in value['candidates']:
        assert set(c['periods'])==set(q.PERIODS)
        assert q.passes(c['periods'])==(c['passed'],c['fail_reasons'])
    assert value['pass_count']==sum(c['passed'] for c in value['candidates'])
    public=json.loads(q.SEED.read_text())
    for a,b in zip(value['candidates'],public['candidates']):
        for k in q.PERIODS:
            assert a['periods'][k]['metrics']==b['periods'][k]['metrics']
            assert 'orders' not in b['periods'][k]


def test_endpoint_is_frozen_no_network_or_retesting(monkeypatch):
    monkeypatch.setenv('SKIP_BG_WARMUP','1')
    import app
    monkeypatch.setattr(q,'run_frozen_experiment',lambda *a,**kw:pytest.fail('must not retest holdout'))
    monkeypatch.setattr(q,'backtest',lambda *a,**kw:pytest.fail('must not retest'))
    monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**kw:pytest.fail('no background market scan'))
    d=app.app.test_client().get('/api/ng-strategy-lab').get_json()
    assert d['status']=='ok' and len(d['candidates'])==6
    assert d['timezone']=='Asia/Tokyo' and d['borrow_annual']==.10
    assert d['pass_count']==0


def test_stale_snapshot_disables_all_signals(monkeypatch,tmp_path):
    value=json.loads(q.SEED.read_text());value['asof_session']='2000-01-01'
    f=tmp_path/'snapshot.json';f.write_text(json.dumps(value));monkeypatch.setattr(q,'SEED',f)
    d=q.snapshot()
    assert d['stale']
    assert all(c['signal']['weights']=={} and c['model_state']['positions']==[] for c in d['candidates'])
    monkeypatch.setattr(q,'SEED',tmp_path/'none')
    assert q.snapshot()['status']=='no_data'


def test_lab_present_in_both_templates():
    for f in ('templates/index.html','docs/index.html'):
        text=(ROOT/f).read_text()
        assert 'ng-strategy-lab-content' in text and '策略实验室' in text
        assert 'ng_strategy_lab_view.js' in text
        assert 'ng-put-quant-content' in text and 'ng-quant-content' in text
        assert 'data-view="cnidx"' in text and 'data-view="ai"' not in text
    assert (ROOT/'static/ng_strategy_lab_view.js').read_bytes()==(ROOT/'docs/static/ng_strategy_lab_view.js').read_bytes()
