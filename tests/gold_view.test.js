/* 黄金页前端：颜色约定、快照/无数据标注、渲染不出现 NaN/undefined、ECharts 配置。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const G = require('../static/gold_view.js');
const seed = JSON.parse(fs.readFileSync('docs/api/gold.json', 'utf8'));

// 红=上涨/流入/增加，绿=下跌/流出/减少
assert.equal(G.cls(1.2), 'up');
assert.equal(G.cls(-0.1), 'down');
assert.equal(G.cls(0), '');
assert.equal(G.cls(null), '');
assert.equal(G.signed(1.234, 2, '%'), '+1.23%');
assert.equal(G.signed(-2, 0), '-2');
assert.equal(G.fmt(undefined), '无数据');
assert.equal(G.fmt(NaN), '无数据');

// 快照与无数据标注
assert.match(G.badge({ status: 'ok', snapshot: true, snapshot_date: '2026-11-12' }), /上次快照（2026-11-12）/);
assert.match(G.badge({ status: 'missing', reason: 'CFTC 无数据' }), /无数据：CFTC 无数据/);
assert.match(G.badge(null), /无数据/);
assert.match(G.badge({ status: 'ok', asof: '2026-11-12' }), /截至 2026-11-12/);

// 信号卡：研究参考声明 + 四因子
const sig = G.signalCard(seed.signal);
assert.match(sig, /研究参考，不能下单/);
for (const name of ['趋势', '实际利率/美元', '持仓/资金', '溢价/情绪']) assert.ok(sig.includes(name), name);
assert.match(G.signalCard(null), /无数据/);

// 实时比价卡：公式与单位
const rt = G.realtimeCard(seed.realtime);
assert.match(rt, /31\.1034768/);
assert.match(rt, /元\/克/);
assert.match(G.realtimeCard({ status: 'missing', reason: '新浪实时无数据' }), /无数据/);

// 全量渲染：不出现 NaN / undefined / null 字样
const el = { innerHTML: '' };
G.render(seed, el);
for (const bad of ['NaN', 'undefined', '>null<']) assert.ok(!el.innerHTML.includes(bad), bad);
for (const id of ['gold-chart-macro', 'gold-chart-au', 'gold-chart-flows']) assert.ok(el.innerHTML.includes(`id="${id}"`), id);
assert.match(el.innerHTML, /SPDR GLD 持仓/);
assert.match(el.innerHTML, /CFTC 投机净多/);
assert.match(el.innerHTML, /518880/);
assert.match(el.innerHTML, /10Y TIPS 实际利率/);
assert.match(el.innerHTML, /tabindex="0"/);

// 部分模块回退快照：页面顶部与模块都标注
const snap = JSON.parse(JSON.stringify(seed));
snap.positioning.spdr.snapshot = true;
snap.positioning.spdr.snapshot_date = '2026-11-11';
snap.snapshot_blocks = { 'positioning.spdr': '2026-11-11' };
G.render(snap, el);
assert.match(el.innerHTML, /显示上次快照：positioning\.spdr（2026-11-11）/);
assert.match(el.innerHTML, /上次快照（2026-11-11）/);

// 整体无数据：诚实提示，不渲染数字
G.render({ status: 'no_data', error: '无数据' }, el);
assert.match(el.innerHTML, /无数据/);

// 缺失模块不会抛错
const partial = JSON.parse(JSON.stringify(seed));
partial.positioning = { spdr: { status: 'missing', reason: 'SPDR 持仓无数据' } };
partial.technicals = { GC: { status: 'missing', reason: '日线不足' } };
partial.macro = { status: 'missing' };
partial.chart = { dates: [] };
G.render(partial, el);
assert.ok(!el.innerHTML.includes('NaN'));

// ECharts 配置：实际利率右轴倒序；溢价柱红正绿负；时间轴
const mo = G.macroOption(seed.chart);
assert.equal(mo.yAxis[1].inverse, true);
assert.equal(mo.xAxis.type, 'time');
assert.equal(mo.series[0].data.length, seed.chart.dates.length);
const ao = G.auOption(seed.chart);
const colorFn = ao.series[3].itemStyle.color;
assert.equal(colorFn({ value: ['2026-01-01', 1] }), '#ef5350');
assert.equal(colorFn({ value: ['2026-01-01', -1] }), '#26a69a');
const fo = G.flowsOption(seed.positioning);
assert.ok(fo.series[0].data.length > 0 && fo.series[1].data.length > 0);
assert.deepEqual(G.flowsOption(undefined).series[0].data, []);

console.log('gold view tests passed');
