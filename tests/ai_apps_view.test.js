'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const view = require('../static/ai_apps_view.js');
const data = JSON.parse(fs.readFileSync('data/ai_apps.json', 'utf8'));
assert.equal(view.esc('<script>"\'&'), '&lt;script&gt;&quot;&#39;&amp;');
assert.equal(view.esc(null), '无数据');
assert.equal(view.safeUrl('javascript:alert(1)'), '');
assert.equal(view.safeUrl('data:text/html,evil'), '');
assert.equal(view.safeUrl('/relative'), '');
assert.ok(view.safeUrl('https://example.com/a?x=1&y=2').includes('&amp;'));
const filters = {market:'A股', tier:'观察', segment:'office'};
assert.equal(view.filterCompanies(data.companies, filters).length, 3);
assert.equal(view.filterCompanies(data.companies, {market:'A股', tier:'领先'}).length, 0);
assert.equal(view.filterCompanies(data.companies, {market:'all', tier:'all', segment:'all'}).length, data.companies.length);
assert.equal(view.filterCompanies(data.companies, {cap:'中小盘'}).length, 12);
assert.equal(view.filterCompanies(data.companies, {cap:'大盘'}).length, 14);
assert.equal(view.filterCompanies(data.companies, {cap:'中小盘', market:'美股'}).length, 6);
assert.equal(view.filterCompanies(data.companies, {cap:'中小盘', market:'A股'}).length, 5);
assert.equal(view.filterCompanies(data.companies, {cap:'中小盘', market:'港股'}).length, 1);
const html = view.companyHTML(data.companies[0], data);
for (const s of ['noopener noreferrer', '关键风险', '无数据', '下一步验证', '已核验的披露']) assert.ok(html.includes(s));
assert.ok(!view.companyHTML({...data.companies[0], name:'<img src=x onerror=alert(1)>'}, data).includes('<img'));
assert.equal(view.sourceLinks(['bad'], data.sources), '无数据（来源缺失）');
assert.ok(view.segmentHTML(data.segments[0], data).includes('反面情景与依据'));

// Minimal DOM verifies lazy load, duplicate-request guard, static URL, failure and retry.
async function lifecycle() {
  const elements = {};
  const ids = ['ai-content','ai-cap','ai-market','ai-tier','ai-segment','ai-count','ai-companies','ai-heading',
    'ai-summary','ai-asof','ai-disclaimer','ai-method-body','ai-segments','ai-sources','ai-status','ai-retry'];
  for (const id of ids) elements[id] = {value:'all', hidden:true, listeners:{},
    addEventListener(name, fn) { this.listeners[name] = fn; }};
  const calls = [];
  let resolveResponse;
  const context = {window:{}, URL, document:{getElementById:id => elements[id]},
    apiUrl:p => { assert.equal(p, '/api/ai-apps'); return './api/ai_apps.json'; },
    fetch:path => { calls.push(path); return new Promise(resolve => {resolveResponse = resolve;}); }};
  vm.runInNewContext(fs.readFileSync('static/ai_apps_view.js','utf8'), context);
  assert.equal(calls.length, 0, 'no eager fetch');
  const api = context.window.AIAppsView;
  const first = api.load();
  await api.load();
  assert.equal(calls.length, 1, 'in-flight dedup');
  resolveResponse({ok:false, status:503});
  await first;
  assert.ok(elements['ai-status'].textContent.includes('无数据'));
  assert.equal(elements['ai-retry'].hidden, false);
  const retry = elements['ai-retry'].listeners.click();
  resolveResponse({ok:true, json:async () => data});
  await retry;
  assert.equal(elements['ai-content'].hidden, false);
  assert.equal(calls[1], './api/ai_apps.json');
  elements['ai-cap'].value = '中小盘';
  elements['ai-cap'].listeners.change();
  assert.equal(elements['ai-count'].textContent, '12 / 26 家候选');
  assert.ok(elements['ai-companies'].innerHTML.includes('市值规模与来源'));
  assert.ok(elements['ai-asof'].textContent.includes('Asia/Tokyo'));
  await api.load();
  assert.equal(calls.length, 2, 'loaded cache');
  elements['ai-market'].value = 'A股';
  elements['ai-tier'].value = '领先';
  elements['ai-market'].listeners.change();
  assert.ok(elements['ai-companies'].innerHTML.includes('无数据'));
}
lifecycle().then(() => console.log('AI apps view tests passed')).catch(e => {console.error(e); process.exitCode = 1;});
