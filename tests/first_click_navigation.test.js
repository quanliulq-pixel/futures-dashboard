/* Exercise the actual app navigation functions with a delayed initial quote response. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js','utf8');
function between(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
const quotes = between('  async function loadQuotes(', '  async function loadExtremes(');
const nav = between('  async function selectInstrument(', '  function bindEvents(');
function node(id, view) {
  const classes = new Set(view === 'watch' ? ['active'] : []);
  return {id, dataset:{view}, classList:{toggle(cls, on) {on ? classes.add(cls) : classes.delete(cls);}, contains:cls => classes.has(cls)}};
}
const views = ['watch','ai','cnidx'].map(v => node('view-'+v,v));
const buttons = ['watch','ai','cnidx'].map(v => node('tab-'+v,v));
let resolveQuotes, loadedAI = 0;
const state = {view:'watch',quotes:[],selectedId:null,lows:[],highs:[]};
const ctx = {state,window:{AIAppsView:{load(){loadedAI++;}}},AbortSignal,console,
  document:{querySelectorAll:sel => sel === '.view' ? views : buttons},
  apiUrl:p=>p, fetch:()=>new Promise(resolve => {resolveQuotes=resolve;}),
  setStatus(){},rebuildSectors(){},renderTable(){},renderExtremeCards(){},
  quoteMeta:{},lowsCards:{},highsCards:{},loadAnalysis:async()=>{},loadHistory:async()=>{},
  setTimeout(){},resizeChart(){},resizeNgCharts(){},loadMoversBoard(){},loadNgDashboard(){},
  loadShortPut(){},loadShortCall(){},loadCnIndices(){}};
vm.runInNewContext(quotes + nav + '\nthis.fns={loadQuotes,switchView,selectInstrument};',ctx);
(async()=>{
  const pending=ctx.fns.loadQuotes(false);
  ctx.fns.switchView('ai'); // The first and only user click, before cold-start quotes finish.
  assert.equal(loadedAI,1);
  assert.ok(views[1].classList.contains('active'));
  assert.ok(!views[0].classList.contains('active'));
  resolveQuotes({ok:true,json:async()=>({items:[{id:'test',market:'cn',price:1}]})});
  await pending;
  assert.equal(state.selectedId,'test','default instrument still initialized');
  assert.equal(state.view,'ai','late quotes must not steal the selected tab');
  assert.ok(views[1].classList.contains('active'));
  assert.ok(!views[0].classList.contains('active'));
  assert.ok(buttons[1].classList.contains('active'));
  // Explicit selections from a card still intentionally open the watch/chart view.
  await ctx.fns.selectInstrument('manual');
  assert.equal(state.view,'watch');
  assert.equal(state.selectedId,'manual');
  console.log('First-click navigation race regression passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
