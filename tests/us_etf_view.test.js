/* 美股ETF页前端：分组表、排序、红涨绿跌、快照/无数据标注、轮动与风险卡片、无 NaN/undefined。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const V = require('../static/us_etf_view.js');
const seed = JSON.parse(fs.readFileSync('docs/api/us_etf.json', 'utf8'));

const el = { innerHTML: '' };
V.render(seed, el);
let html = el.innerHTML;
for (const bad of ['NaN', 'undefined', '>null<']) assert.ok(!html.includes(bad), bad);
assert.match(html, /主流 ETF 行情与技术面/);
assert.match(html, /风险偏好仪表盘/);
assert.match(html, /行业轮动/);
assert.match(html, /规则化摘要/);
for (const sym of ['SPY', 'QQQ', 'XLK', 'SMH', 'TLT', 'HYG', 'GLD', 'IBIT']) assert.ok(html.includes(`<strong>${sym}</strong>`), sym);
for (const g of ['宽基', '行业']) assert.ok(html.includes(g), g);
for (const id of ['etf-chart-risk', 'etf-chart-rrg', 'etf-chart-bar']) assert.ok(html.includes(`id="${id}"`), id);
assert.match(html, /资金流\/AUM/);
assert.match(html, /tabindex="0"/);
assert.ok(!/下单接口|买入按钮/.test(html));

// 红=上涨，绿=下跌
assert.equal(V.pct(0.0123), '<span class="up">+1.2%</span>');
assert.equal(V.pct(-0.05), '<span class="down">-5.0%</span>');
assert.match(V.pct(null), /无数据/);

// 排序：按 1月收益降序，缺失值置后
const rows = [
  { symbol: 'A', status: 'ok', r1m: 0.01 }, { symbol: 'B', status: 'missing' }, { symbol: 'C', status: 'ok', r1m: 0.05 }, { symbol: 'D', status: 'ok', r1m: -0.02 },
];
assert.deepEqual(V.sortedRows(rows, 'r1m', -1).map((r) => r.symbol), ['C', 'A', 'D', 'B']);
assert.deepEqual(V.sortedRows(rows, 'r1m', 1).map((r) => r.symbol), ['D', 'A', 'C', 'B']);
V.state.sort = 'r1m'; V.state.dir = -1; V.state.group = 'sector';
const t = V.table(seed);
assert.ok(!t.includes('etf-group'), 'sorted view is flat');
assert.ok(!t.includes('<strong>SPY</strong>'), 'group filter applied');
assert.match(t, /aria-sort="descending"/);
V.state.sort = null; V.state.group = 'all';

// 快照与无数据标注
const snap = JSON.parse(JSON.stringify(seed));
snap.groups[0].items[0].snapshot = true;
snap.groups[0].items[0].snapshot_date = '2026-11-12';
V.render(snap, el);
assert.match(el.innerHTML, /上次快照（2026-11-12）/);
const miss = JSON.parse(JSON.stringify(seed));
miss.groups[0].items[1] = { symbol: 'QQQ', name: '纳指100', status: 'missing', reason: 'Yahoo 本轮无该 ETF 数据' };
miss.risk.vix = { status: 'missing', reason: 'VIX 无数据' };
V.render(miss, el);
assert.match(el.innerHTML, /无数据：Yahoo 本轮无该 ETF 数据/);
assert.ok(!el.innerHTML.includes('NaN'));
V.render({ status: 'no_data' }, el);
assert.match(el.innerHTML, /无数据/);

// 图表配置
const rrg = V.rrgOption(seed.rotation);
assert.ok(rrg.series.length >= seed.rotation.items.length);
const risk = V.riskOption(seed.risk.chart);
assert.equal(risk.series.length, seed.risk.chart.series.length);
const bar = V.barOption(seed);
assert.ok(bar.series[0].data.every((d) => d.itemStyle.color === (d.value >= 0 ? '#ef5350' : '#26a69a')));
console.log('us_etf_view ok');
