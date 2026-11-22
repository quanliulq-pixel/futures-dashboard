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
