"""Defined-risk NG ETF short calls. Frozen model research, never broker orders."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import NormalDist

import pandas as pd
import ng_put_quant as put
import ng_quant as nq

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data/ng_quant/put_prices.json'
SEED = ROOT / 'docs/api/ng_sell_call.json'
ARCHIVE = ROOT / 'data/ng_quant/call_experiment.json'
PARAMS = dict(dte=30, delta=.25, wing_spot=.10, iv_hv=1.20, rate=.03,
              hv_cap=1.50, ng_hv_cap=.90, vol_jump_limit=1.50,
              take_profit=.50, stop_multiple=2.50, exit_dte=7,
              risk_per_symbol=.005, risk_total=.01,
              half_spread=.05, fee_notional_two_legs=.001)
VARIANTS = {'UNG': ('UNG',), 'BOIL': ('BOIL',), 'combined': ('UNG', 'BOIL')}
LABEL = '模型定价非真实期权报价；模型定价回测，非真实期权成交'


def bs_call(spot, strike, years, vol, rate=.03):
    """European call, q=0; return price and delta, never a live quotation."""
    if not all(math.isfinite(x) for x in (spot, strike, years, vol, rate)) or spot <= 0 or strike <= 0:
        raise ValueError('invalid BS inputs')
    if years <= 0:
        return max(spot-strike, 0.), float(spot > strike)
    if vol <= 0:
        v = spot-strike*math.exp(-rate*years)
        return max(v, 0.), float(v > 0)
    d1 = (math.log(spot/strike)+(rate+.5*vol*vol)*years)/(vol*math.sqrt(years))
    d2 = d1-vol*math.sqrt(years)
    return max(spot*put.norm_cdf(d1)-strike*math.exp(-rate*years)*put.norm_cdf(d2), 0.), put.norm_cdf(d1)


def strikes(spot, hv, iv_hv=1.20):
    vol = hv*iv_hv
    if not math.isfinite(vol) or vol <= 0 or spot <= 0:
        raise ValueError('invalid strike inputs')
    t = PARAMS['dte']/365
    k = spot*math.exp((PARAMS['rate']+.5*vol*vol)*t-NormalDist().inv_cdf(PARAMS['delta'])*vol*math.sqrt(t))
    return k, k+PARAMS['wing_spot']*spot


def spread_mark(spot, short, long, dte, vol):
    if not 0 < short < long:
        raise ValueError('long wing must be above short call')
    a, _ = bs_call(spot, short, max(dte, 0)/365, vol, PARAMS['rate'])
    b, _ = bs_call(spot, long, max(dte, 0)/365, vol, PARAMS['rate'])
    # Enforce payoff bounds against floating-point cancellation only.
    return min(long-short, max(a-b, 0.))


def fee(mark, entry_spot, cost_scale=1.):
    """Net spread execution assumption, not two independently observed leg spreads."""
    return cost_scale*(PARAMS['half_spread']*mark+PARAMS['fee_notional_two_legs']*entry_spot)


def max_unit_loss(short, long, credit, entry_spot, cost_scale=1.):
    width = long-short
    return width-credit+fee(credit, entry_spot, cost_scale)+fee(width, entry_spot, cost_scale)


def eligible(row, symbol):
    keys = ('NG=F_close', 'ng_ma20', 'ng_ma60', 'ng_mom', 'ng_hv',
            symbol+'_close', symbol+'_ma20', symbol+'_hv', symbol+'_hv60')
    if not all(math.isfinite(float(row[k])) for k in keys):
        return False, '指标无数据'
    if not row['NG=F_close'] < row.ng_ma20 < row.ng_ma60 or row.ng_mom >= 0:
        return False, 'NG非一致下降趋势（需收盘<MA20<MA60、10日动量负）'
    if not 0 < row.ng_hv <= PARAMS['ng_hv_cap'] or not 0 < row[symbol+'_hv'] <= PARAMS['hv_cap']:
        return False, '波动率超限或无效'
    if row[symbol+'_hv'] > PARAMS['vol_jump_limit']*row[symbol+'_hv60']:
        return False, 'HV20超过HV60的1.5倍，波动突升'
    if row[symbol+'_close'] >= row[symbol+'_ma20']:
        return False, 'ETF未跌破MA20'
    return True, '下降趋势、ETF低于MA20、HV及波动突升过滤通过'


def exit_reason(mark, credit, spot, short, dte, regime_ok, eia_guard):
    if dte <= 0: return 'expiry'
    if dte <= PARAMS['exit_dte']: return 'expiry_guard'
    if spot >= short: return 'short_strike_breach'
    if mark >= PARAMS['stop_multiple']*credit: return 'premium_stop'
    if mark <= PARAMS['take_profit']*credit: return 'take_profit'
    if not regime_ok: return 'regime_exit'
    if eia_guard: return 'weekly_event_proxy'
    return None


def backtest(p, start, end, symbols=('UNG', 'BOIL'), iv_hv=1.20, cost_scale=1.):
    """Prior-close indicators, next-open fills; independently funded split, no fitting."""
    dates = p.index[(p.index >= pd.Timestamp(start)) & (p.index <= pd.Timestamp(end))]
    if len(dates) < 2: raise ValueError('回测无数据')
    if not symbols or any(s not in ('UNG', 'BOIL') for s in symbols): raise ValueError('invalid symbols')
    cash = 1.; positions = {}; trades = []; curve = []

    def mark_at(pos, symbol, row, prev, dt, field):
        return spread_mark(float(row[symbol+'_'+field]), pos['short'], pos['long'],
                           (pos['expiry']-dt).days, float(prev[symbol+'_hv'])*iv_hv)

    def close(symbol, dt, i, mark, why):
        nonlocal cash
        pos = positions.pop(symbol)
        debit = pos['units']*(mark+fee(mark, pos['entry_spot'], cost_scale))
        cash -= debit
        pnl = pos['net_credit']-debit
        trades.append(dict(symbol=symbol, entry=str(pos['entry'].date()), exit=str(dt.date()),
                           days=i-pos['entry_i']+1, reason=why, pnl=pnl,
                           risk_return=pnl/pos['risk'], risk=pos['risk'],
                           max_loss=pos['units']*max_unit_loss(pos['short'],pos['long'],pos['credit'],pos['entry_spot'],cost_scale),
                           units=pos['units'], short=pos['short'], long=pos['long'],
                           credit=pos['credit'], close_mark=mark))

    for dt in dates:
        i = p.index.get_loc(dt)
        if i < 61: raise ValueError('预热不足')
        prev, row = p.iloc[i-1], p.loc[dt]
        exited = set()
        for s, pos in list(positions.items()):
            mark = mark_at(pos,s,row,prev,dt,'open')
            why = exit_reason(mark,pos['credit'],float(row[s+'_open']),pos['short'],
                              (pos['expiry']-dt).days,eligible(prev,s)[0],dt.weekday()==2)
            if why:
                close(s,dt,i,mark,why); exited.add(s)
        if dt.weekday() not in (2,3):
            for s in symbols:
                if s in positions or s in exited or not eligible(prev,s)[0]: continue
                spot = float(row[s+'_open'])
                short,long = strikes(float(prev[s+'_close']),float(prev[s+'_hv']),iv_hv)
                if spot >= short: continue  # gap through short strike: no optimistic fill
                credit = spread_mark(spot,short,long,PARAMS['dte'],float(prev[s+'_hv'])*iv_hv)
                if credit <= fee(credit,spot,cost_scale): continue
                liability = sum(pos['units']*mark_at(pos,k,row,prev,dt,'open') for k,pos in positions.items())
                equity = cash-liability
                reserved = sum(pos['risk'] for pos in positions.values())
                risk = max(0., min(equity*PARAMS['risk_per_symbol'],equity*PARAMS['risk_total']-reserved))
                if risk <= 0: continue
                # Stress fees do not mechanically shrink position per unit: baseline fee budget.
                unit_risk = max_unit_loss(short,long,credit,spot)
                units = risk/unit_risk
                net_credit = units*(credit-fee(credit,spot,cost_scale))
                cash += net_credit
                positions[s] = dict(entry=dt,entry_i=i,expiry=dt+pd.Timedelta(days=PARAMS['dte']),
                                    entry_spot=spot,short=short,long=long,credit=credit,units=units,
                                    risk=risk,net_credit=net_credit)
        liability = 0.
        for s,pos in list(positions.items()):
            mark = mark_at(pos,s,row,prev,dt,'close')
            if dt == dates[-1]: close(s,dt,i,mark,'period_end')
            else: liability += pos['units']*mark
        curve.append(dict(date=str(dt.date()),strategy=float(cash-liability)))
    m = nq.metrics([x['strategy'] for x in curve],[{'return':t['pnl'],'days':t['days']} for t in trades])
    m.update(worst_trade=min((t['risk_return'] for t in trades),default=None),
             worst_trade_account=min((t['pnl'] for t in trades),default=None))
    return dict(start=str(dates[0].date()),end=str(dates[-1].date()),metrics=m,curve=curve,trades=trades,
                worst_trades=sorted(trades,key=lambda t:t['risk_return'])[:5],tail_windows=tail_windows(curve,trades))


def tail_windows(curve, trades):
    windows=[('2022天然气尖峰','2022-01-01','2022-12-31')]
    for year in range(2017,2027):
        windows.append((f'{year-1}/{year}冬季',f'{year-1}-11-01',f'{year}-03-31'))
    result=[]
    for label,start,end in windows:
        points=[v for v in curve if start<=v['date']<=end]
        if not points: continue
        prior=next((v['strategy'] for v in reversed(curve) if v['date']<points[0]['date']),1.)
        vals=[v['strategy']/prior for v in points]
        worst=0.;peak=1.
        for v in vals:
            peak=max(peak,v);worst=min(worst,v/peak-1)
        closed=[t for t in trades if points[0]['date']<=t['exit']<=points[-1]['date']]
        result.append(dict(label=label,start=points[0]['date'],end=points[-1]['date'],
                           total_return=vals[-1]-1,max_drawdown=worst,closed_trades=len(closed),
                           worst_trade=min((t['risk_return'] for t in closed),default=None)))
    return result


def current_signal(panel, now=None):
    now=now or datetime.now(nq.JST)
    last=put.features(panel).iloc[-1]
    asof=panel.index[-1].date()
    stale=(now.date()-asof).days>5
    items=[]
    for s in ('UNG','BOIL'):
        okay,reason=eligible(last,s)
        items.append(dict(symbol=s,action='不卖',paper_signal='满足价格过滤' if okay and not stale else '空仓',
                          regime_ok=bool(okay),reason=reason if not stale else '无数据：日线超过5个日历日，禁用纸面信号',
                          asof_session=str(asof),spot=float(last[s+'_raw_close']),hv=float(last[s+'_hv']),
                          quote=None,chain_status='无数据：未获取可核验的实时双腿期权链，不用模型报价冒充',
                          gate='纸面信号，不构成下单；真实IV/HV、双腿流动性、整数合约、官方EIA/分红/拆并股日历未核验，默认不卖',
                          updated_at=now.isoformat(timespec='seconds')))
    return items


LIMITATIONS = [
    '只研究UNG、BOIL的熊市看涨信用价差：卖低行权价Call、同时买同到期高行权价Call；不做裸卖。裸卖NG Call损失无上限，BOIL长期衰减不能抵消短期暴涨风险。KOLD反向，未纳入以免混淆方向。',
    '固定规则：前日NG收盘<MA20<MA60、10日动量负；ETF收盘<MA20；NG HV20≤90%，ETF 0<HV20≤150%且≤1.5×HV60。没有可用的点时库存/COT输入，本系统不冒充基本面策略。',
    '短腿行权价由前日收盘、IV=HV20×1.20的30日Black-Scholes delta=0.25确定；长腿高出前日收盘的10%。连续合成行权价、拆并股复权OHLC、分数合约，不是历史挂牌期权。真实选约目标21–45天、短腿Δ0.20–0.30、IV/HV≥1.10；两腿OI≥100、bid/ask有效且价差/中价≤25%，无法核验均不卖。',
    '期权全部为欧式BS模型，r=3%、q=0。HV20×1.20只是预设IV代理，不是实际历史IV或已证实溢价。现金与Sharpe无风险收益0，年化252，未计个人税费。',
    '单边成本=净价差模型价5%+入场标的名义额10bp（两腿各5bp），而非逐腿真实报价；对冲腿大额价内、分腿成交时可能严重低估成本。固定压力情景IV/HV=1.0、费用翻倍全部报告，不按结果调参。',
    '基准每笔最大模型损失预算0.5%净值、组合1%，以宽度-毛信用+入场费+退出费上界计量，入场后权重漂移。不滚仓、不加码。压力费率仍按基准单位风险定数量，可能超过基准预算；不是保持完全相同数量的精确配对实验。资金不足100股整数价差不卖。',
    '次日开盘入场、用前日HV盯市；回购价≤初始信用50%止盈，≥2.5倍止损、标的到短腿、趋势失效、剩7天退出。周三开盘清仓，周三/四不新开是EIA星期代理，不包含节假日调整。退出日不重入，期末强平计费。',
    '价差的到期支付封顶，不代表盘中风险可完全封顶：美式提前指派、分腿执行、流动性、保证金、长腿无法行权/临近到期pin风险、费用与税费可能额外损失；本模型未模拟真实指派。不得裸卖或先卖后补保护腿。',
    '2016预热，2017–2023样本内，2024起事后留出，每段独立现金1；未做拟合或参数优化，不称为真正前瞻/未见样本或已完成滚动验证。3个组合共享标的，不独立；此前已看过市场/卖Put/12个NG假设，多重研究使收益更易被偶然发现。',
    '2022与冬季压力表为固定过滤和小仓位下的已实现盯市区间；无交易/零损失不证明能抵抗未来逼空。最差单笔按风险准备金与账户净值分别展示。',
    '冻结既有Yahoo日线归档，不强制刷新或扫描全市场；缺失日线不填造。API只读归档不自动回测。最近共同完整交易日不是今天，日线超过5个日历日禁用纸面信号；真实链与日历无数据默认不卖。'
]


def build_payload(panel, now=None):
    now=now or datetime.now(nq.JST)
    p=put.features(panel)
    result={}
    for name,symbols in VARIANTS.items():
        segments={}
        for key,start,end in [('in_sample','2017-01-01','2023-12-31'),
                              ('out_of_sample','2024-01-01',str(p.index[-1].date()))]:
            base=backtest(p,start,end,symbols)
            base['stress']={
                'iv_hv_1_0':backtest(p,start,end,symbols,iv_hv=1.)['metrics'],
                'cost_x2':backtest(p,start,end,symbols,cost_scale=2.)['metrics']}
            segments[key]=base
        result[name]=segments
    combined=result['combined']
    positive=all(combined[k]['metrics']['total_return']>0 for k in ('in_sample','out_of_sample'))
    all_losing=all(s[k]['metrics']['total_return']<0 for s in result.values() for k in ('in_sample','out_of_sample'))
    return dict(schema_version=1,status='ok',updated_at=now.isoformat(timespec='seconds'),timezone='Asia/Tokyo',
                asof_session=str(p.index[-1].date()),label=LABEL,params=PARAMS,variants=result,
                live=current_signal(panel,now),limitations=LIMITATIONS,
                verdict=('UNG、BOIL及组合在样本内与留出均亏损，尚未验证盈利能力；不是盈利交易系统。' if all_losing else
                         '组合两个区间模型收益均为正，但未证明真实期权可盈利，须前瞻纸面验证。' if positive else
                         '组合至少一个区间模型亏损，尚未验证盈利能力；不得宣称稳定盈利。'),
                data_quality=['只读既有卖Put日线归档；最近共同完整日线不是今天；未重新抓取全市场。'],
                source=dict(path=str(RAW.relative_to(ROOT)),sha256=hashlib.sha256(RAW.read_bytes()).hexdigest(),
                            prereg_sha256=hashlib.sha256((ROOT/'data/ng_quant/call_prereg.md').read_bytes()).hexdigest()))


def snapshot(now=None):
    """Read-only and bounded: never fetch market data or recompute historical returns."""
    now=now or datetime.now(nq.JST)
    try:
        value=json.loads(SEED.read_text(encoding='utf-8'))
        if value.get('status')!='ok': raise ValueError('no data')
        value=copy.deepcopy(value)
        asof=datetime.fromisoformat(value['asof_session']).date()
        value['stale']=(now.date()-asof).days>5
        for item in value['live']:
            item['action']='不卖';item['quote']=None
            if value['stale']:
                item.update(paper_signal='空仓',reason='无数据：日线超过5个日历日，禁用纸面信号')
        return value
    except (OSError,ValueError,KeyError,TypeError):
        return dict(schema_version=1,status='no_data',label=LABEL,error='无数据',live=[])
