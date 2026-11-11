"""Synthetic timing/logic tests must pass before one real evaluation."""
from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
import pytest
import ng_strategy_lab2 as q
ROOT=Path(__file__).resolve().parents[1]

def row(dt='2024-02-05'):
    d={}
    for prefix in ('s','w','c'):
        d[prefix+'_date']=pd.Timestamp(dt)-pd.Timedelta(days=14)
        d[prefix+'_available']=pd.Timestamp(dt)-pd.Timedelta(days=3)
    return pd.Series(dict(**d,s_L=1.,s_F=1.,s_S=1.,w_W=1.,c_C=0.,hv60=.4))

def synthetic(n=400):
    idx=pd.bdate_range('2022-06-01',periods=n)
    p=pd.DataFrame([row(str(dt.date())) for dt in idx],index=idx)
    rng=np.random.default_rng(36)
    p['UNG_open']=100*np.exp(np.cumsum(rng.normal(.0002,.01,n)));p['UNG_close']=p.UNG_open*1.001;p['UNG_raw_close']=p.UNG_close
    return p

def test_six_new_candidates_twelve_total():
    assert len(q.CANDIDATES)==6 and q.MULTIPLE_TESTS==12
    assert set(q.REQUIRED)=={c['id'] for c in q.CANDIDATES}

def test_release_lags_strictly_after_release_and_extra_sessions():
    cal=pd.bdate_range('2024-01-01','2024-03-01')
    assert q.available_session('2024-01-05',cal,'storage')==pd.Timestamp('2024-01-16')
    assert q.available_session('2024-01-09',cal,'cot')==pd.Timestamp('2024-01-17')
    assert q.available_session('2024-01-05',cal,'weather')==pd.Timestamp('2024-01-17')
    # Actual market calendar excludes MLK Monday. No made-up Monday open.
    real=cal[cal!=pd.Timestamp('2024-01-15')]
    assert q.available_session('2024-01-05',real,'storage')==pd.Timestamp('2024-01-17')
    assert q.available_session('2024-01-09',real,'cot')==pd.Timestamp('2024-01-18')
    assert pd.isna(q.available_session('2024-03-01',real,'storage'))

def test_asof_join_never_backfills_and_rejects_quarantined_reference():
    cal=pd.bdate_range('2024-01-01','2024-02-29')
    d=pd.DataFrame(dict(date=['2024-01-05','2024-01-12'],stock=[100.,200.]))
    a=q.asof_join(cal,d,'s','storage')
    assert pd.isna(a.loc['2024-01-15','s_stock'])
    assert a.loc['2024-01-16','s_stock']==100
    assert a.loc['2024-01-22','s_stock']==100
    assert a.loc['2024-01-23','s_stock']==200
    assert (a.dropna().s_available<=a.dropna().index).all()
    bad=pd.DataFrame(dict(date=['2025-11-01'],stock=[999.]))
    b=q.asof_join(pd.bdate_range('2025-10-01','2026-01-30'),bad,'s','storage')
    assert b.s_stock.isna().all()

def test_fixed_decision_boundaries_and_missing_not_neutral():
    dt=pd.Timestamp('2024-02-05');r=row();expected=[1,1,1,0,1,1]
    for c,sign in zip(q.CANDIDATES,expected):assert q.decision(r,c['id'],dt)[:2]==(sign,True)
    r['c_C']=-1
    assert q.decision(r,'storage_cot_filter',dt)[0]==0
    r['w_W']=-1
    assert q.decision(r,'storage_weather',dt)[0]==0
    r['c_C']=np.nan
    assert q.decision(r,'storage_cot_filter',dt)[:2]==(0,False)
    assert q.decision(row('2025-10-06'),'storage_level',pd.Timestamp('2025-10-06'))[:2]==(0,False)
    r=row();r['s_date']=dt-pd.Timedelta(days=29)
    assert q.decision(r,'storage_level',dt)[:2]==(0,False)
    r=row();r['s_available']=dt+pd.Timedelta(days=1)
    assert q.decision(r,'storage_level',dt)[:2]==(0,False)

def test_storage_features_year_boundary_and_thresholds():
    d=pd.DataFrame(dict(date=['2023-12-29','2024-01-05','2024-01-12','2024-01-19'],stock=[90.,80.,120.,100.],avg=[100.]*4,region=['five']*4))
    f=q.storage_features(d)
    assert f.L.tolist()==[1.,1.,-1.,0.]
    assert pd.isna(f.iloc[1].F) # cannot cross annual baseline change
    assert f.iloc[2].F==-1 and f.iloc[3].F==1
    d.loc[3,'date']='2024-01-26'
    assert pd.isna(q.storage_features(d).iloc[3].F)

def test_cot_prior_only_statistics():
    n=180;d=pd.DataFrame(dict(date=pd.date_range('2018-01-02',periods=n,freq='7D'),long=np.arange(n)+100,short=100,oi=1000))
    a=q.cot_features(d);d.loc[150:,'long']=1e6;b=q.cot_features(d)
    pd.testing.assert_frame_equal(a.iloc[:150],b.iloc[:150])
    assert pd.isna(a.iloc[103].C) and np.isfinite(a.iloc[104].C)
    # Current extreme does not enter its own rolling mean.
    assert b.iloc[150].z>1000

def test_weather_only_past_years_and_complete_divisions():
    idx=pd.date_range('2004-01-01','2011-12-31');rng=np.random.default_rng(19)
    raw=pd.DataFrame(dict(date=idx,value_hdd=rng.uniform(0,40,len(idx)),value_cdd=rng.uniform(0,15,len(idx)),divisions_hdd=9,divisions_cdd=9))
    a=q.weather_features(raw);changed=raw.copy();changed.loc[changed.date>=pd.Timestamp('2011-01-01'),'value_hdd']=10000;b=q.weather_features(changed)
    pd.testing.assert_frame_equal(a[a.date<'2011-01-01'],b[b.date<'2011-01-01'])
    assert a[a.date>'2010-01-01'].W.notna().any()
    raw.loc[raw.date>='2010-12-01','divisions_hdd']=8
    z=q.weather_features(raw)
    assert z[z.date>'2011-01-01'].W.isna().all()

def test_future_changes_cannot_change_prior_orders():
    p=synthetic();d=p.copy();d.loc[p.index[250]:,'UNG_open']*=2;d.loc[p.index[250]:,'UNG_close']*=2
    d.loc[p.index[250]:,'s_L']=-1;d.loc[p.index[250]:,'s_S']=-1
    for c in q.CANDIDATES:
        a=q.backtest(p,c['id'],str(p.index[0].date()),str(p.index[-1].date()));b=q.backtest(d,c['id'],str(p.index[0].date()),str(p.index[-1].date()))
        assert a['curve'][:250]==b['curve'][:250]
        assert [x for x in a['orders'] if x['date']<str(p.index[250].date())]==[x for x in b['orders'] if x['date']<str(p.index[250].date())]

def test_fees_borrow_weekly_sizing_and_stale_exit():
    p=synthetic(100);p[['UNG_open','UNG_close','UNG_raw_close']]=100.;p['s_L']=-1.
    a=q.backtest(p,'storage_level',str(p.index[0].date()),str(p.index[-1].date()))
    assert a['metrics']['total_return']<0 and a['metrics']['borrow_paid']>0
    assert a['orders'][0]['notional']==pytest.approx(.25)
    assert all(x['fee']==pytest.approx(x['notional']*.001) for x in a['orders'])
    assert len(a['orders'])<len(p)/2
    p.loc[p.index[3]:,'s_date']=pd.Timestamp('2000-01-01')
    a=q.backtest(p,'storage_level',str(p.index[0].date()),str(p.index[-1].date()))
    assert a['orders'][1]['date']==str(p.index[3].date()) and a['orders'][1]['new_qty']==0

def test_refuse_repeat_before_any_evaluation(tmp_path):
    f=tmp_path/'ledger.json';f.write_text('{}')
    with pytest.raises(FileExistsError):q.run_once(f)
    f.unlink();f.with_suffix('.started.json').write_text('{}')
    with pytest.raises(FileExistsError):q.run_once(f)

# These checks use the frozen artifact, not a second historical evaluation.
def test_frozen_artifacts_and_shape():
    if not q.LEDGER.exists():pytest.skip('awaiting first one-shot evaluation')
    d=json.loads(q.LEDGER.read_text());public=json.loads(q.SEED.read_text())
    assert len(d['candidates'])==6 and d['multiple_tests']==12
    assert d['pass_count']==sum(c['passed'] for c in d['candidates'])
    for name,h in d['input_hashes'].items():assert hashlib.sha256((q.FOLDER/name).read_bytes()).hexdigest()==h
    assert hashlib.sha256(q.PREREG.read_bytes()).hexdigest()==d['prereg_sha256']
    for a,b in zip(d['candidates'],public['candidates']):
        assert q.passes(a['periods'])==(a['passed'],a['fail_reasons'])
        for k in q.PERIODS:
            assert a['periods'][k]['metrics']==b['periods'][k]['metrics']
            assert 'orders' not in b['periods'][k]
            assert a['periods'][k]['end']<='2026-11-09'

def test_endpoint_read_only(monkeypatch):
    if not q.SEED.exists():pytest.skip('awaiting first evaluation')
    monkeypatch.setenv('SKIP_BG_WARMUP','1');import app
    monkeypatch.setattr(q,'run_once',lambda *a,**k:pytest.fail('no evaluation on GET'))
    monkeypatch.setattr(q,'build_panel',lambda *a,**k:pytest.fail('no rebuild on GET'))
    monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**k:pytest.fail('no network on GET'))
    d=app.app.test_client().get('/api/ng-strategy-lab2').get_json()
    assert d['status']=='ok' and d['round']==2 and d['multiple_tests']==12

def test_stale_and_missing_snapshot(monkeypatch,tmp_path):
    if not q.SEED.exists():pytest.skip('awaiting first evaluation')
    d=json.loads(q.SEED.read_text());d['asof_session']='2000-01-01';f=tmp_path/'x.json';f.write_text(json.dumps(d));monkeypatch.setattr(q,'SEED',f)
    assert all(c['model_state']['positions']==[] for c in q.snapshot()['candidates'])
    monkeypatch.setattr(q,'SEED',tmp_path/'none');assert q.snapshot()['status']=='no_data'

def test_benchmark_copy_does_not_mutate_panel():
    p=synthetic(20);before=p.copy(deep=True)
    d=q.benchmark(p,str(p.index[0].date()),str(p.index[-1].date()))
    pd.testing.assert_frame_equal(p,before)
    assert np.isfinite(d['metrics']['total_return']) and d['metrics']['trades']==2

def test_round2_present_in_both_surfaces_and_export_is_readonly():
    for name in ('templates/index.html','docs/index.html'):
        text=(ROOT/name).read_text()
        assert 'ng-strategy-lab2-content' in text and 'ng_strategy_lab2_view.js' in text
        assert '两轮12个NG' in text and 'data-view="ai"' in text and 'data-view="cnidx"' in text
    assert (ROOT/'static/ng_strategy_lab2_view.js').read_bytes()==(ROOT/'docs/static/ng_strategy_lab2_view.js').read_bytes()
    assert 'lab2_snapshot()' in (ROOT/'export_static_pages.py').read_text()
