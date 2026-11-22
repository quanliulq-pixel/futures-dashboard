const V = require('../static/us_risk_view.js');

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'assert failed');
}

const missing = V.marketCard({ status: 'missing', reason: '无数据' });
assert(missing.includes('无数据'), 'missing market shows 无数据');

const snap = V.leverageCard({
  status: 'ok', month: '2026-10', month_label: '2026年10月', debit_trillion: 1.448,
  debit_usd_millions: 1448360, mom_pct: -0.0214, yoy_pct: 0.2236, cap_ratio_pct: 2.0,
  snapshot: true, snapshot_date: '2026-10', units: '百万美元',
  peaks: [{ year: 2000, status: 'ok', peak_month: '2000-03', peak_usd_millions: 299933, chg_pct: 3.829 }],
});
assert(snap.includes('上次快照（2026-10）'), 'snapshot badge');
assert(snap.includes('up') && snap.includes('+'), 'yoy red/up');
assert(snap.includes('down'), 'mom green/down');

const vix = V.vixCard({ status: 'ok', asof: '2026-11-20', close: 35.89, chg: 7.45, chg_pct: 0.262, term: '倒挂', vvix: 138.03, percentile_1y: 99.2, percentile_sample: 252 });
assert(vix.includes('35.89') && vix.includes('倒挂') && vix.includes('up'), 'vix up is red');

const judge = V.judgmentCard({ valuation: '偏热', leverage: '偏热', sentiment: '偏冷', line: '估值偏热，杠杆偏热，情绪偏冷', rules: '规则' });
assert(judge.includes('偏热') && judge.includes('偏冷') && judge.includes('估值偏热，杠杆偏热，情绪偏冷'), 'judgment line');

const empty = { document: { getElementById: () => null } };
// render no-data path via a fake element
const el = { innerHTML: '' };
V.render({ status: 'no_data' }, el);
assert(el.innerHTML.includes('无数据') && el.innerHTML.includes('不能下单'), 'no data + disclaimer');

console.log('us_risk_view ok');

const dd = V.drawdownCard({
  drawdowns: {
    status: 'ok', asof: '2026-11-20', overview: '当前距历史最高收盘回撤 -3.7%。',
    method: '口径',
    indices: [{
      key: 'sp500', name: '标普500', symbol: '^GSPC', status: 'ok', asof: '2026-11-20', history_start: '1990-01-02', annual_from: 1990,
      highs: { status: 'ok', close: 6910.39, ath: 7175.83, ath_date: '2026-11-19', dd_ath: -0.037, ytd_high: 7175.83, ytd_high_date: '2026-11-19', dd_ytd: -0.037, asof: '2026-11-20' },
      annual: [{ year: 2026, max_dd: -0.037, peak_date: '2026-11-19', peak: 7175.83, trough_date: '2026-11-20', trough: 6910.39, year_return: 0.10, incomplete: true, asof: '2026-11-20' }],
      major: [{ label: '2020-03 新冠', peak_date: '2020-02-19', peak: 3386.15, trough_date: '2020-03-23', trough: 2237.4, dd: -0.3392, duration_days: 33, recovery_days: 148, recovery_date: '2020-08-18', vix_peak: 82.69, vix_peak_date: '2020-03-16' }],
    }],
  },
});
if (!dd.includes('距高点') && !dd.includes('历史最高') && !dd.includes('2020-03 新冠') && !dd.includes('down')) {
  throw new Error('drawdown card missing expected text');
}
if (!dd.includes('历史最高收盘') || !dd.includes('2020-03 新冠') || !dd.includes('年内最大回撤')) throw new Error('drawdown sections missing');
const missingDd = V.drawdownCard({ drawdowns: { status: 'missing', reason: '无数据' } });
if (!missingDd.includes('无数据')) throw new Error('missing drawdowns should be 无数据');

const cycle = V.cycleTable({
  status: 'ok', asof: '2026-11-20', method: '口径',
  cycles: [{
    label: '2020-03 新冠', peak_date: '2020-02-19', trough_date: '2020-03-23',
    market: { peak: { status: 'ok', total_cap_trillion: 30, buffett_ratio_pct: 140, gdp_period: '2019Q4', asof: '2020-02-19' }, trough: { status: 'ok', total_cap_trillion: 22, buffett_ratio_pct: 110, gdp_period: '2019Q4', asof: '2020-03-23' }, cap_chg_pct: -0.27 },
    leverage: { peak: { status: 'ok', debit_trillion: 0.5, cap_ratio_pct: 2.1, month: '2020-02' }, trough: { status: 'ok', debit_trillion: 0.4, cap_ratio_pct: 1.8, month: '2020-03' }, debit_chg_pct: -0.2 },
    ownership: { peak: { status: 'ok', hh_direct_pct: 36.6, mf_etf_pct: 22, pension_pct: 8, foreign_pct: 16, quarter: '2019Q3' }, trough: { status: 'missing', reason: '无数据' } },
    flows: { ici: { status: 'missing', reason: 'ICI 无数据' }, z1: { status: 'ok', mf_etf_usd_millions: -100, hh_direct_usd_millions: 50, pension_usd_millions: -10, foreign_usd_millions: 20, quarters: ['2020Q1'] } },
    haven: { gold: { status: 'ok', peak: { close: 1600, asof: '2020-02-19' }, trough: { close: 1480, asof: '2020-03-23' }, chg_pct: -0.075 }, ust10y: { status: 'ok', peak: { close: 1.5, asof: '2020-02-19' }, trough: { close: 0.8, asof: '2020-03-23' }, chg_pp: -0.7 }, tlt: { status: 'missing', reason: '无数据' } },
  }],
});
if (!cycle.includes('2020-03 新冠') || !cycle.includes('巴菲特') || !cycle.includes('ICI') || !cycle.includes('无数据')) throw new Error('cycle table missing text');
if (!cycle.includes('down')) throw new Error('cycle table should color down moves green');
const missingCycle = V.cycleTable({ status: 'missing', reason: '无数据' });
if (!missingCycle.includes('无数据')) throw new Error('missing cycle should be 无数据');

const darkMissing = V.darkCard({ dark: { status: 'missing', reason: '无数据' } });
if (!darkMissing.includes('无数据') || !darkMissing.includes('暗盘')) throw new Error('dark missing');
const darkOk = V.darkCard({
  dark: {
    status: 'ok', asof: '2026-11-20',
    dix_gex: {
      dix: { status: 'ok', value_pct: 38.85, chg_1d_pp: -4.23, percentile_1y: 1.2, asof: '2026-11-20', spark: [{asof:'2026-11-19', value:0.43},{asof:'2026-11-20', value:0.39}] },
      gex: { status: 'ok', value_billion: -2.16, chg_1d_billion: -1.26, percentile_1y: 1.2, asof: '2026-11-20', spark: [{value:-0.9},{value:-2.16}] },
    },
    off_exchange: { status: 'ok', share_pct: 47.07, chg_1d_pp: -0.31, percentile_1y: 13.9, asof: '2026-11-20', spark: [{value:0.47},{value:0.47}] },
    ats_weekly: { status: 'ok', week_start: '2026-10-12', lag_note: '滞后约 35 天', top_ats: [{name:'INTELLIGENT CROSS LLC', share_of_ats_pct: 14.3}], spy: {status:'ok', ats_shares: 48147874}, qqq: {status:'missing', reason:'无数据'} },
  },
});
if (!darkOk.includes('38.85') || !darkOk.includes('down') || !darkOk.includes('2026-10-12')) throw new Error('dark card values');
const judgeDark = V.judgmentCard({ valuation: '偏热', leverage: '中性', sentiment: '偏冷', dark: '偏少', line: '估值偏热，杠杆中性，情绪偏冷，暗盘暗池买盘偏少', rules: '规则' });
if (!judgeDark.includes('暗盘偏少') || !judgeDark.includes('买盘偏少')) throw new Error('judgment dark');
