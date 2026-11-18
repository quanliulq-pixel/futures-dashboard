/* 黄金卖Put 前端：研究声明、候选无数据、红涨绿跌、近似标注。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const V = require('../static/gold_put_view.js');

const seedPath = 'docs/api/gold_put.json';
const seed = JSON.parse(fs.readFileSync(seedPath, 'utf8'));
const el = { innerHTML: '' };
V.render(seed, el);
const html = el.innerHTML;
for (const bad of ['NaN', 'undefined', '>null<']) assert.ok(!html.includes(bad), bad);
assert.match(html, /研究参考，不能下单/);
assert.match(html, /模型定价回测，非真实期权成交/);
assert.match(html, /Black-76|Black-Scholes/);
assert.match(html, /2020–2023样本内/);
assert.match(html, /2024起留出检验/);
assert.match(html, /沪金期权/);

const perf = V.table({
  start: '2020-01-02', end: '2023-12-29',
  metrics: {
    strategy: { total_return: 0.1, cagr: 0.02, max_drawdown: -0.05, sharpe: 0.4, win_rate: 0.55, trade_count: 10, assignment_count: 0, worst_trade: -0.08 },
    AU0: { total_return: -0.02, cagr: -0.01, max_drawdown: -0.2, sharpe: 0.1, win_rate: 0, trade_count: 1, assignment_count: null, worst_trade: null },
  },
});
assert.match(perf, /class="num up">10\.00%/);
assert.match(perf, /class="num down">-5\.00%/);
assert.match(perf, /不适用/);

const miss = V.liveCard({ symbol: 'au', name: '上期所黄金期权', action: '不卖', reason: 'AU0 非一致上升趋势', chain_status: '所选 30 天附近到期链无满足全部规则的合约，候选无数据', updated_at: '2026-11-18T10:00:00+08:00', spot: 834.66, hv: 0.18, trend_T: -0.2, event_calendar: '无数据：需人工核验' });
assert.match(miss, /候选行权价[\s\S]*无数据/);
assert.match(miss, /不用模型报价冒充/);
assert.doesNotMatch(miss, /au2701P/);

const ok = V.liveCard({
  symbol: 'au', name: '上期所黄金期权', action: '不卖', reason: '事件日历未核验',
  chain_status: '已获取新浪黄金期权链', updated_at: '2026-11-18T10:00:00+08:00',
  spot: 834.66, hv: 0.18, trend_T: 0.4, event_calendar: '无数据',
  quote: { contract: 'au2701P760', expiry: '2026-12-25', dte: 37, strike: 760, bid: 8, ask: 8.4, iv: 0.22, delta: -0.25, premium_yield: 0.12, annualized_yield: 1.1, margin_per_lot: 50000, otm_pct: 0.09, collateral_weight: 0.2 },
});
assert.match(ok, /au2701P760/);
assert.match(ok, /近似IV/);
assert.match(ok, /Black-76/);

V.render({ status: 'no_data' }, el);
assert.match(el.innerHTML, /无数据/);
console.log('gold_put_quant view ok');
