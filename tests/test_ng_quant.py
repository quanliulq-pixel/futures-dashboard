from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import ng_quant as q

ROOT = Path(__file__).resolve().parents[1]


def panel(n=180, falling=False):
    dates = pd.bdate_range('2023-01-02', periods=n)
    p = pd.DataFrame(index=dates)
    for s in ('BOIL','KOLD','NG=F'):
        c = 100*np.exp(np.arange(n)*(-0.002 if falling else 0.002))
        for k, v in [('open',c),('close',c),('high',c*1.01),('low',c*.99),('raw_close',c)]:
            p[s+'_'+k] = v
    return p


def test_signal_trend_both_sides_and_hv():
    up = q.indicators(panel()).iloc[-1]
    down = q.indicators(panel(falling=True)).iloc[-1]
    assert q.decision(up)[0] == 'BOIL'
    assert q.decision(down)[0] == 'KOLD'
    up.ng_hv = .91
    assert q.decision(up)[0] == 'CASH'
    up.ng_hv, up.momentum = .2, -1
    assert q.decision(up)[0] == 'CASH'
    up.ma20 = np.nan
    assert q.decision(up)[0] == 'CASH'


def test_size_and_stop_gap():
    assert q.position_weight(100, 2, .5) == pytest.approx(.125)
    assert q.position_weight(100, .1, .1) == .25
    assert q.position_weight(100, .1, 2) == .075
    assert q.position_weight(100, 50, .5) == 0
    assert q.position_weight(100, np.nan, .5) == 0
    assert q.stop_fill(90, 89, 95) == 90
    assert q.stop_fill(100, 94, 95) == 95
    assert q.stop_fill(100, 96, 95) is None


def test_no_lookahead_indicators_and_trades():
    raw = panel()
    original = q.indicators(raw)
    mutated = raw.copy()
    mutated.iloc[120:] *= 10
    changed = q.indicators(mutated)
    pd.testing.assert_frame_equal(original.iloc[:120], changed.iloc[:120])
    start, end = str(raw.index[65].date()), str(raw.index[-1].date())
    a, b = q.backtest(original,start,end), q.backtest(changed,start,end)
    assert a['curve'][:120-65] == b['curve'][:120-65]
    assert [t for t in a['trades'] if t['exit']<str(raw.index[120].date())] == [t for t in b['trades'] if t['exit']<str(raw.index[120].date())]


def test_prior_close_signal_not_same_close():
    p = q.indicators(panel())
    start = p.index[70]
    # Today close may flip the signal, but entry at this open uses yesterday.
    p.loc[start,'ma20'] = 1e6
    result = q.backtest(p,str(start.date()),str(p.index[75].date()))
    assert result['trades'][0]['entry'] == str(start.date())
    assert result['trades'][0]['exit'] == str(p.index[71].date())


def test_entry_day_stop_and_max_holding():
    p = q.indicators(panel())
    start = p.index[70]
    p.loc[start,'BOIL_low'] = 1
    result = q.backtest(p,str(start.date()),str(p.index[-1].date()))
    assert result['trades'][0]['reason'] == 'entry_day_stop'
    assert result['trades'][0]['return'] < 0
    assert max(t['days'] for t in result['trades']) <= 10
    for a,b in zip(result['trades'],result['trades'][1:]):
        assert a['exit'] < b['entry']


def test_metrics_and_costs_flat_prices():
    p = q.indicators(panel())
    p['BOIL_open'] = p['BOIL_close'] = 100
    p['BOIL_low'] = 99
    result = q.backtest(p,str(p.index[70].date()),str(p.index[71].date()))
    assert result['metrics']['BOIL']['total_return'] == pytest.approx(.999/1.001-1)
    assert result['metrics']['strategy']['total_return'] < 0
    assert q.metrics([1,1],[])['sharpe'] is None
    assert q.metrics([.9,.8],[])['max_drawdown'] == pytest.approx(-.2)


def test_endpoint_shape_and_does_not_fetch(monkeypatch):
    monkeypatch.setenv('SKIP_BG_WARMUP', '1')
    import app
    value = json.loads(q.SEED.read_text())
    monkeypatch.setattr(q, 'snapshot', lambda:value.copy())
    monkeypatch.setattr(q, 'needs_refresh', lambda _:True)
    calls = []
    monkeypatch.setattr(app,'_spawn_bg_refresh',lambda *a,**kw:calls.append((a,kw)) or 'queued')
    monkeypatch.setattr(q,'fetch_prices',lambda:pytest.fail('network in request handler'))
    data = app.app.test_client().get('/api/ng-boil-kold').get_json()
    assert data['refresh_status'] == 'queued'
    assert data['schema_version']==1 and data['timezone']=='Asia/Tokyo'
    assert set(data['in_sample']['metrics']) == {'strategy','BOIL','KOLD'}
    assert data['signal']['action'] in ('做BOIL','做KOLD','空仓')
    assert calls[0][0][0] == 'ng-boil-kold'


def test_stale_snapshot_disables_signal(monkeypatch,tmp_path):
    value = json.loads(q.SEED.read_text())
    value['asof_session']='2000-01-01'
    monkeypatch.setattr(q,'_cache',value)
    monkeypatch.setattr(q,'ROOT',tmp_path)
    monkeypatch.setattr(q,'SEED',tmp_path/'none')
    d=q.snapshot()
    assert d['stale'] and d['signal']['symbol']=='CASH'
    assert d['signal']['entry_reference'] is None and d['signal']['weight']==0
    assert value['signal'] != d['signal']


def test_no_data_fallback(monkeypatch,tmp_path):
    monkeypatch.setattr(q,'_cache',None)
    monkeypatch.setattr(q,'ROOT',tmp_path)
    monkeypatch.setattr(q,'SEED',tmp_path/'none')
    assert q.snapshot()['status']=='no_data'


def test_mirrors_and_reproducible_snapshot():
    for f in ('templates/index.html','docs/index.html'):
        text=(ROOT/f).read_text()
        assert 'ng-quant-content' in text and 'BOIL / KOLD' in text
        assert 'ng_quant_view.js' in text and 'data-view="ai"' in text
    assert (ROOT/'static/ng_quant_view.js').read_bytes()==(ROOT/'docs/static/ng_quant_view.js').read_bytes()
    saved=json.loads(q.SEED.read_text())
    rebuilt=q.build_payload(pd.read_json(q.RAW,orient='table'),now=datetime.fromisoformat(saved['updated_at']))
    for key in ('in_sample','out_of_sample'):
        for symbol in ('strategy','BOIL','KOLD'):
            for metric,value in saved[key]['metrics'][symbol].items():
                assert rebuilt[key]['metrics'][symbol][metric] == pytest.approx(value) if value is not None else rebuilt[key]['metrics'][symbol][metric] is None
    json.dumps(rebuilt,allow_nan=False)


def test_fetch_failure_never_fabricates(monkeypatch):
    import net_limits
    monkeypatch.setattr(net_limits,'yf_download',lambda *a,**k:pd.DataFrame())
    with pytest.raises(ValueError,match='无数据'):
        q.fetch_prices()


def test_fetch_excludes_incomplete_prices(monkeypatch):
    import net_limits
    dates=pd.bdate_range('2020-01-01',periods=400)
    def download(symbol,**kwargs):
        d=pd.DataFrame({'Open':100.,'High':102.,'Low':99.,'Close':101.,'Adj Close':101.},index=dates)
        if symbol == 'BOIL':
            d.loc[dates[-1],['Close','Adj Close']]=np.nan
        return d
    monkeypatch.setattr(net_limits,'yf_download',download)
    p=q.fetch_prices()
    assert p.index[-1] == dates[-2]
    assert 'BOIL' in p.attrs['data_quality'][0]
    assert not p.isna().any().any()
