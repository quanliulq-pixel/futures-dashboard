/* 黄金量化纸面跟踪前端：研究声明、5票明细、账本、回测表、颜色约定、无 NaN/undefined。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const G = require('../static/gold_view.js');
const seed = JSON.parse(fs.readFileSync('docs/api/gold_quant.json', 'utf8'));

const el = { innerHTML: '' };
G.renderQuant(seed, el);
const html = el.innerHTML;
for (const bad of ['NaN', 'undefined', '>null<']) assert.ok(!html.includes(bad), bad);
assert.match(html, /研究参考，不能下单/);
assert.match(html, /没有下单接口/);
assert.match(html, /C1 · 沪金AU0/);
assert.match(html, /稳健版 · 518880/);
for (const name of ['21日动量', '63日动量', '126日动量', '252日动量', 'EMA50 vs EMA200']) assert.ok(html.includes(name), name);
assert.match(html, /纸面账本（自 2026-11-13 收盘起/);
assert.match(html, /样本外（2021~）/);
assert.match(html, /买入持有/);
assert.match(html, /依赖杠杆/);
assert.match(html, /起点敏感/);
assert.match(html, /统计不显著/);
assert.match(html, /1手名义价值|1 手名义约/);
for (const id of ['gold-quant-chart-c1', 'gold-quant-chart-robust']) assert.ok(html.includes(`id="${id}"`), id);
assert.match(html, /tabindex="0"/);

// 红=上涨，绿=下跌（收益正红负绿；回撤为负→绿）
const perf = G.quantPerfTable({ strategy: { full: { status: 'ok', cagr: 0.1, sharpe: 0.8, max_drawdown: -0.27, start: '2010-01-22' } }, buy_hold: {}, cost_2x: {} });
assert.match(perf, /class="up">\+10\.0%/);
assert.match(perf, /class="down">-27\.0%/);

// 快照与无数据标注
const snap = JSON.parse(JSON.stringify(seed));
snap.strategies.c1.snapshot = true;
snap.strategies.c1.snapshot_date = '2026-11-13';
G.renderQuant(snap, el);
assert.match(el.innerHTML, /上次快照（2026-11-13）/);
const miss = JSON.parse(JSON.stringify(seed));
miss.strategies.robust = { status: 'missing', reason: '新浪 518880 日线不足' };
G.renderQuant(miss, el);
assert.match(el.innerHTML, /无数据：新浪 518880 日线不足/);
G.renderQuant({ status: 'no_data' }, el);
assert.match(el.innerHTML, /无数据/);

// ECharts 配置：净值对数轴 + 回撤子图
const opt = G.quantOption(seed.strategies.c1.curves, 'C1');
assert.equal(opt.yAxis[0].type, 'log');
assert.equal(opt.series.length, 4);
assert.equal(opt.series[0].data.length, seed.strategies.c1.curves.dates.length);
console.log('gold_quant_view ok');
