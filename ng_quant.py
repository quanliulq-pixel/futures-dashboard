"""Daily BOIL/KOLD research model. No orders; no optimized parameters.
All decisions use completed bar t; executions start at t+1 open.
"""
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

ROOT = Path(__file__).resolve().parent
SEED = ROOT / 'docs/api/ng_boil_kold.json'
RAW = ROOT / 'data/ng_quant/prices.json'
JST = ZoneInfo('Asia/Tokyo')
PARAMS = dict(fast_ma=20, slow_ma=60, momentum=10, hv_window=20,
              ng_hv_limit=0.90, atr_window=14, stop_atr=2.0,
              max_holding_days=10, risk_budget=0.005, max_weight=0.25,
              target_vol=0.15, cost_per_side=0.001)
_cache = None
_lock = threading.Lock()


def indicators(panel):
    """Input columns SYMBOL_open/high/low/close; no filling missing sessions."""
    p = panel.copy().sort_index()
    c = p['NG=F_close']
    p['ma20'] = c.rolling(20).mean()
    p['ma60'] = c.rolling(60).mean()
    p['momentum'] = c.pct_change(10, fill_method=None)
    p['ng_hv'] = np.log(c / c.shift()).rolling(20).std() * np.sqrt(252)
    for s in ('BOIL', 'KOLD'):
        c = p[s + '_close']
        tr = pd.concat([p[s+'_high'] - p[s+'_low'],
                        (p[s+'_high'] - c.shift()).abs(),
                        (p[s+'_low'] - c.shift()).abs()], axis=1).max(axis=1)
        p[s+'_atr'] = tr.rolling(14).mean()
        p[s+'_hv'] = np.log(c / c.shift()).rolling(20).std() * np.sqrt(252)
    return p


def decision(row):
    keys = ['ma20', 'ma60', 'momentum', 'ng_hv', 'NG=F_close']
    if any(not math.isfinite(float(row[k])) for k in keys):
        return 'CASH', ['指标不足，空仓']
    if row.ng_hv > PARAMS['ng_hv_limit']:
        return 'CASH', ['NG 20日年化波动率超过90%，空仓']
    if row['NG=F_close'] > row.ma20 > row.ma60 and row.momentum > 0:
        return 'BOIL', ['NG 收盘 > MA20 > MA60，10日动量为正']
    if row['NG=F_close'] < row.ma20 < row.ma60 and row.momentum < 0:
        return 'KOLD', ['NG 收盘 < MA20 < MA60，10日动量为负']
    return 'CASH', ['趋势与动量不一致，空仓']


def position_weight(price, atr, hv):
    if not all(math.isfinite(float(v)) and v > 0 for v in (price, atr, hv)):
        return 0.0
    if PARAMS['stop_atr'] * atr >= price:
        return 0.0
    return min(PARAMS['max_weight'], PARAMS['risk_budget'] / (PARAMS['stop_atr'] * atr / price),
               PARAMS['target_vol'] / hv)


def stop_fill(open_price, low, stop):
    """Gap-through stops fill at open (not the more favorable stop)."""
    return min(open_price, stop) if low <= stop else None


def metrics(values, trades):
    v = np.array(values, dtype=float)
    r = v / np.r_[1.0, v[:-1]] - 1
    dd = v / np.maximum.accumulate(np.r_[1.0, v])[1:] - 1
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return dict(total_return=float(v[-1]-1), cagr=float(v[-1] ** (252/len(v))-1),
                max_drawdown=float(dd.min()), sharpe=float(r.mean()/sd*np.sqrt(252)) if sd else None,
                win_rate=sum(t['return'] > 0 for t in trades)/len(trades) if trades else None,
                trade_count=len(trades), avg_holding_days=float(np.mean([t['days'] for t in trades])) if trades else None)


def backtest(p, start, end):
    """Each split starts flat. Warm-up is allowed before its start, never future data.
    Fractional shares, cash return/risk-free rate zero, no leverage at portfolio level.
    """
    dates = p.index[(p.index >= pd.Timestamp(start)) & (p.index <= pd.Timestamp(end))]
    if len(dates) < 2:
        raise ValueError('回测区间无数据')
    cash, qty, symbol, held = 1.0, 0.0, None, 0
    entry_cost = stop = 0.0
    entry_date = None
    trades, curve = [], []
    fee = PARAMS['cost_per_side']
    for dt in dates:
        i = p.index.get_loc(dt)
        if i < 60:
            raise ValueError('至少需要60个交易日预热')
        prev, row = p.iloc[i-1], p.loc[dt]
        target, _ = decision(prev)
        exited = False
        if symbol:
            op = row[symbol+'_open']
            fill = op if (target != symbol or held >= PARAMS['max_holding_days']) else stop_fill(op, row[symbol+'_low'], stop)
            if fill is not None:
                proceeds = qty * fill * (1-fee)
                cash += proceeds
                trades.append(dict(entry=str(entry_date.date()), exit=str(dt.date()), symbol=symbol,
                                   days=held if target != symbol or held >= PARAMS['max_holding_days'] else held+1, reason='signal/time' if target != symbol or held >= PARAMS['max_holding_days'] else 'stop',
                                   return_value=proceeds/entry_cost-1))
                trades[-1]['return'] = trades[-1].pop('return_value')
                qty, symbol, held, exited = 0.0, None, 0, True
        if not symbol and not exited and target != 'CASH':
            op = row[target+'_open']
            atr = prev[target+'_atr']
            # Size at actual open using only prior indicators.
            weight = position_weight(op, atr, prev[target+'_hv'])
            if weight > 0:
                entry_cost = cash * weight
                qty = entry_cost / (op*(1+fee))
                cash -= entry_cost
                symbol, stop, held, entry_date = target, op-PARAMS['stop_atr']*atr, 0, dt
                fill = stop_fill(op, row[symbol+'_low'], stop)
                if fill is not None:
                    proceeds = qty*fill*(1-fee)
                    cash += proceeds
                    trades.append(dict(entry=str(dt.date()), exit=str(dt.date()), symbol=symbol,
                                       days=1, reason='entry_day_stop', **{'return': proceeds/entry_cost-1}))
                    qty, symbol = 0.0, None
        if symbol:
            held += 1
        value = cash + (qty*row[symbol+'_close'] if symbol else 0)
        if dt == dates[-1] and symbol:
            proceeds = qty*row[symbol+'_close']*(1-fee)
            value = cash+proceeds
            trades.append(dict(entry=str(entry_date.date()), exit=str(dt.date()), symbol=symbol,
                               days=held, reason='period_end', **{'return': proceeds/entry_cost-1}))
        curve.append(dict(date=str(dt.date()), strategy=float(value)))
    results = {'strategy': metrics([x['strategy'] for x in curve], trades)}
    for s in ('BOIL', 'KOLD'):
        q = 1/(p.loc[dates[0], s+'_open']*(1+fee))
        vals = [q*p.loc[d, s+'_close'] for d in dates]
        vals[-1] *= 1-fee
        results[s] = metrics(vals, [dict(return_value=0, days=len(dates), **{'return': vals[-1]-1})])
        for point, val in zip(curve, vals):
            point[s] = float(val)
    return dict(start=str(dates[0].date()), end=str(dates[-1].date()), sessions=len(dates),
                metrics=results, curve=curve, trades=trades)


def fetch_prices():
    import net_limits
    frames = []
    quality = []
    now_et = datetime.now(ZoneInfo('America/New_York'))
    for s in ('BOIL', 'KOLD', 'NG=F'):
        d = net_limits.yf_download(s, start='2016-01-01', auto_adjust=False,
                                   progress=False, threads=False, timeout=20)
        if isinstance(d.columns, pd.MultiIndex):
            d = d.xs(s, axis=1, level=-1)
        if d.empty:
            raise ValueError(s + ' 无数据')
        missing = d.index[d[['Open','High','Low','Close','Adj Close']].isna().any(axis=1)]
        if len(missing):
            quality.append(s + ' 不完整日线（未填充/未交易）：' + ', '.join(str(x.date()) for x in missing[-5:]))
        ratio = d['Adj Close'] / d['Close']
        f = pd.DataFrame({s+'_'+k.lower(): d[k]*ratio for k in ('Open','High','Low','Close')})
        f[s+'_raw_close'] = d['Close']
        f.index = pd.to_datetime(f.index).tz_localize(None).normalize()
        # Exclude the current US calendar day until after both ETF close and the NG futures session close.
        cutoff = now_et.date() if (now_et.hour, now_et.minute) >= (18, 15) else now_et.date()-timedelta(days=1)
        f = f.loc[f.index.date <= cutoff]
        frames.append(f)
    p = pd.concat(frames, axis=1, join='inner').dropna().sort_index()
    p = p[~p.index.duplicated()]
    if len(p) < 300 or not np.isfinite(p.to_numpy()).all() or (p <= 0).any().any():
        raise ValueError('历史数据不足或价格无效')
    p.attrs['data_quality'] = quality
    return p


def build_payload(panel, now=None):
    now = now or datetime.now(JST)
    p = indicators(panel)
    row = p.iloc[-1]
    target, reasons = decision(row)
    stale = (now.date()-p.index[-1].date()).days > 5
    if stale:
        target, reasons = 'CASH', ['价格超过5个日历日未更新，禁用信号（无数据）']
    ref = stop = None
    weight = 0.0
    if target != 'CASH':
        ratio = row[target+'_raw_close']/row[target+'_close']
        ref = float(row[target+'_raw_close'])
        stop = float((row[target+'_close']-PARAMS['stop_atr']*row[target+'_atr'])*ratio)
        weight = position_weight(row[target+'_close'], row[target+'_atr'], row[target+'_hv'])
        if not weight:
            target, reasons, ref, stop = 'CASH', ['风险指标无效，空仓'], None, None
    return dict(schema_version=1, status='ok', updated_at=now.isoformat(timespec='seconds'),
                asof_session=str(p.index[-1].date()), timezone='Asia/Tokyo', stale=stale,
                source='Yahoo Finance adjusted daily OHLC', params=PARAMS,
                data_quality=panel.attrs.get('data_quality', []),
                signal=dict(action={'BOIL':'做BOIL','KOLD':'做KOLD','CASH':'空仓'}[target],
                            symbol=target, entry_reference=ref, stop_reference=stop, weight=weight, reasons=reasons,
                            ng_price=float(row['NG=F_raw_close']), ng_hv=float(row.ng_hv)),
                in_sample=backtest(p, '2017-01-01','2023-12-31'),
                out_of_sample=backtest(p, '2024-01-01',str(p.index[-1].date())),
                limitations=['固定规则，未调参；2024年后仅为事后留出检验，不是真正前瞻样本外。',
                             'NG=F 是连续近月代理，换月跳空会影响信号；ETF跟踪期货指数而非现货或单一近月。',
                             '历史ETF杠杆目标曾变化，使用实际ETF历史而非假定全期恒定2倍。',
                             '库存/CFTC没有完整的历史发布时间快照，未纳入回测或信号；现有NG看板仅供另行参考。',
                             '复权OHLC包含拆并股/分红；止损只是日线成交近似，极端行情不能保证成交。',
                             '费用每边10bp含佣金滑点，未计税费；ETF管理费和展期影响已反映在价格中。',
                             '现金与无风险利率均按0；最大25%仓位，对照为100%买入持有，风险敞口不同。'])


def refresh():
    global _cache
    with _lock:
        p = fetch_prices()
        payload = build_payload(p)
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(p.to_json(orient='table', date_format='iso'), encoding='utf-8')
        (RAW.parent/'prices_meta.json').write_text(json.dumps(dict(
            retrieved_at=payload['updated_at'], source=payload['source'],
            data_quality=payload['data_quality'], start=str(p.index[0].date()),
            end=str(p.index[-1].date()), rows=len(p)), ensure_ascii=False, indent=2), encoding='utf-8')
        _cache = payload
        # Persist separately from committed static snapshot.
        path = ROOT / 'data/ng_quant/latest.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temp.replace(path)
        return payload


def snapshot():
    candidates = [_cache]
    for path in (ROOT/'data/ng_quant/latest.json', SEED):
        if path.exists():
            try:
                candidates.append(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
    for value in candidates:
        if value and value.get('status') == 'ok':
            value = copy.deepcopy(value)
            now = datetime.now(JST)
            value['stale'] = (now.date()-datetime.fromisoformat(value['asof_session']).date()).days > 5
            if value['stale']:
                value['signal'] = dict(action='空仓', symbol='CASH', entry_reference=None,
                                       stop_reference=None, weight=0, reasons=['快照过期，当前信号无数据'])
            return value
    return dict(schema_version=1, status='no_data', stale=True, signal=None,
                error='无数据', updated_at=datetime.now(JST).isoformat(timespec='seconds'))


def needs_refresh(value):
    if value.get('status') != 'ok':
        return True
    return datetime.now(JST)-datetime.fromisoformat(value['updated_at']) > timedelta(hours=6)
