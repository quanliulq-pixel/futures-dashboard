/* 美股ETF页：快照优先加载（/api/us-etf 或 Pages ./api/us_etf.json），可排序分组表 + 行业轮动 + 风险偏好；研究参考。 */
(function (root) {
  'use strict';
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (x, d = 2) => (isNum(x) ? x.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '无数据');
  // 红=上涨，绿=下跌（与全站一致）
  const cls = (x) => (isNum(x) ? (x > 0 ? 'up' : x < 0 ? 'down' : '') : '');
  const pct = (x, d = 1) => (isNum(x) ? `<span class="${cls(x)}">${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%</span>` : '<span class="etf-na">无数据</span>');
  const pp = (x, d = 1) => (isNum(x) ? `<span class="${cls(x)}">${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}pp</span>` : '<span class="etf-na">无数据</span>');
  const isStatic = () => root.STATIC_MODE === true || /github\.io$/i.test((root.location && root.location.hostname) || '') ||
    String((root.location && root.location.search) || '').includes('static=1');
  const TREND_CLS = { '多头排列': 'up', '偏多': 'up', '偏空': 'down', '空头排列': 'down' };
  const COLUMNS = [
    ['symbol', '代码'], ['close', '收盘'], ['r1d', '1日'], ['r1w', '1周'], ['r1m', '1月'], ['r3m', '3月'], ['ytd', 'YTD'], ['r1y', '1年'],
    ['from_high', '距52周高'], ['vs_ma200', '趋势（MA50/200）'], ['rsi14', 'RSI14'], ['hv20', '20日波动'], ['rs_3m', '相对SPY 3月'], ['vol_ratio', '量比'],
  ];
  const state = { sort: null, dir: -1, group: 'all', data: null };

  function badge(r) {
    if (r && r.snapshot) return `<span class="etf-snap" title="${esc(r.snapshot_reason || '')}">上次快照（${esc(r.snapshot_date || r.asof || '')}）</span>`;
    return '';
  }

  function rsiText(x) {
    if (!isNum(x)) return '<span class="etf-na">无数据</span>';
    const tag = x >= 70 ? ' <small class="etf-tag up">超买</small>' : x <= 30 ? ' <small class="etf-tag down">超卖</small>' : '';
    return fmt(x, 1) + tag;
  }

  function row(r) {
    if (!r || r.status !== 'ok') {
      return `<tr><td><strong>${esc(r && r.symbol)}</strong><br><small class="sub">${esc(r && r.name)}</small></td><td colspan="${COLUMNS.length - 1}" class="etf-na">无数据${r && r.reason ? '：' + esc(r.reason) : ''}</td></tr>`;
    }
    return `<tr>
      <td><strong>${esc(r.symbol)}</strong> ${badge(r)}<br><small class="sub">${esc(r.name)}</small></td>
      <td class="num">${fmt(r.close, 2)}</td>
      <td class="num">${pct(r.r1d, 2)}</td><td class="num">${pct(r.r1w)}</td><td class="num">${pct(r.r1m)}</td><td class="num">${pct(r.r3m)}</td>
      <td class="num">${pct(r.ytd)}</td><td class="num">${pct(r.r1y)}</td>
      <td class="num">${pct(r.from_high)}<br><small class="sub">52周位置 ${fmt(r.pos_52w, 0)}%</small></td>
      <td><span class="${TREND_CLS[r.trend] || ''}">${esc(r.trend)}</span><br><small class="sub">距MA200 ${isNum(r.vs_ma200) ? (r.vs_ma200 > 0 ? '+' : '') + (r.vs_ma200 * 100).toFixed(1) + '%' : '无数据'}</small></td>
      <td class="num">${rsiText(r.rsi14)}</td>
      <td class="num">${isNum(r.hv20) ? (r.hv20 * 100).toFixed(1) + '%' : '<span class="etf-na">无数据</span>'}</td>
      <td class="num">${pp(r.rs_3m)}</td>
      <td class="num">${isNum(r.vol_ratio) ? `<span class="${r.vol_ratio >= 1.5 ? 'etf-hot' : ''}">${fmt(r.vol_ratio, 2)}</span>` : '<span class="etf-na">无数据</span>'}</td>
    </tr>`;
  }

  function sortVal(r, key) {
    if (!r || r.status !== 'ok') return null;
    const v = r[key];
    return key === 'symbol' ? String(v || '') : isNum(v) ? v : null;
  }

  function sortedRows(rows, key, dir) {
    return rows.slice().sort((a, b) => {
      const x = sortVal(a, key), y = sortVal(b, key);
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      if (typeof x === 'string') return dir * x.localeCompare(y);
      return dir * (x - y);
    });
  }

  function table(p) {
    const groups = (p.groups || []).filter((g) => state.group === 'all' || g.id === state.group);
    const head = COLUMNS.map(([k, label]) => {
      const active = state.sort === k;
      const arrow = active ? (state.dir < 0 ? ' ▼' : ' ▲') : '';
      const sortAttr = active ? (state.dir < 0 ? 'descending' : 'ascending') : 'none';
      return `<th class="${k === 'symbol' || k === 'vs_ma200' ? '' : 'num'}" aria-sort="${sortAttr}"><button type="button" class="etf-sort" data-sort="${k}">${label}${arrow}</button></th>`;
    }).join('');
    let body;
    if (state.sort) {
      body = sortedRows(groups.flatMap((g) => g.items || []), state.sort, state.dir).map(row).join('');
    } else {
      body = groups.map((g) => `<tr class="etf-group"><td colspan="${COLUMNS.length}">${esc(g.name)}</td></tr>` + (g.items || []).map(row).join('')).join('');
    }
    const chips = [['all', '全部']].concat((p.groups || []).map((g) => [g.id, g.name])).map(([id, name]) =>
      `<button type="button" class="etf-chip${state.group === id ? ' active' : ''}" data-group="${esc(id)}" aria-pressed="${state.group === id}">${esc(name)}</button>`).join('');
    return `<section class="panel etf-card" aria-label="主流ETF行情表">
      <h3>主流 ETF 行情与技术面 <span class="etf-asof">点表头排序（再点切换升/降，${state.sort ? '<button type="button" class="etf-sort etf-reset" data-sort="">恢复分组</button>' : '默认按分组'}）</span></h3>
      <div class="etf-chips" role="group" aria-label="ETF 分组筛选">${chips}</div>
      <div class="table-wrap" tabindex="0" aria-label="美股主流ETF行情表"><table class="etf-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
      <p class="sub">收益为 Yahoo 复权口径（含分红）；相对SPY＝同期超额收益（百分点）；量比＝当日量/前20日均量（≥1.5 高亮）；RSI≥70 超买、≤30 超卖。</p>
    </section>`;
  }

  function summaryCard(p) {
    const s = p.summary || {}, rk = p.risk || {};
    const rcls = rk.regime === '偏 Risk-on' ? 'up' : rk.regime === '偏 Risk-off' ? 'down' : 'etf-flat';
    return `<section class="panel etf-card" aria-label="规则摘要">
      <div class="etf-head"><div><span class="sub">规则化摘要（非预测）</span>
        <h3 class="etf-regime ${rcls}">${esc(rk.regime || '无数据')} ${isNum(rk.score) ? `<small>${rk.score > 0 ? '+' : ''}${rk.score} / ±${esc(rk.max_score)}</small>` : ''}</h3></div></div>
      <ul class="etf-lines">${(s.lines || []).map((l) => `<li>${esc(l)}</li>`).join('') || '<li class="etf-na">无数据</li>'}</ul>
    </section>`;
  }

  function riskCard(rk) {
    rk = rk || {};
    const chip = (x) => (isNum(x) ? `<span class="etf-chip-score ${cls(x)}">${x > 0 ? '+1 Risk-on' : x < 0 ? '−1 Risk-off' : '0 中性'}</span>` : '');
    const kpis = (rk.pairs || []).map((pr) => `<div class="etf-kpi"><span class="etf-kpi-label">${esc(pr.label)}</span>
      ${pr.status === 'ok' ? `<strong>${fmt(pr.ratio, 3)} ${chip(pr.score)}</strong><span class="etf-kpi-sub">21日 ${pct(pr.chg_21d)} · 距MA50 ${pct(pr.vs_ma50)}</span>` : `<strong class="etf-na">无数据</strong>`}
      <span class="etf-kpi-sub">${esc(pr.meaning)}</span></div>`).join('');
    const v = rk.vix || {};
    const vix = `<div class="etf-kpi"><span class="etf-kpi-label">VIX（恐慌指数）</span>
      ${v.status === 'ok' ? `<strong>${fmt(v.vix, 2)} ${chip(v.score)}</strong><span class="etf-kpi-sub">5日 ${isNum(v.vix_chg_5d) ? `<span class="${cls(v.vix_chg_5d)}">${v.vix_chg_5d > 0 ? '+' : ''}${v.vix_chg_5d.toFixed(2)}</span>` : '无数据'} · 1年分位 ${fmt(v.vix_rank_1y, 0)}%</span>
      <span class="etf-kpi-sub">VIX/VIX3M ${fmt(v.term_ratio, 3)}${isNum(v.term_ratio) ? (v.term_ratio > 1 ? '（倒挂）' : '（正常升水）') : ''}</span>` : '<strong class="etf-na">无数据</strong>'}</div>`;
    return `<section class="panel etf-card" aria-label="风险偏好仪表盘">
      <h3>风险偏好仪表盘（Risk-on / Risk-off）</h3>
      <div class="etf-kpis">${kpis}${vix}</div>
      <div class="etf-chart" id="etf-chart-risk" role="img" aria-label="风险偏好比值走势（近1年，起点=100）"></div>
      <p class="sub">比值走势：近 1 年，起点＝100。</p>
      <p class="sub">${esc(rk.rule || '')} 比值上升＝分子相对走强。VIX 方向：红＝上升（风险上升），绿＝下降。</p>
    </section>`;
  }

  function rotationCard(rot) {
    const items = (rot && rot.items) || [];
    const body = items.map((r) => `<tr><td><strong>${esc(r.symbol)}</strong> <small class="sub">${esc(r.name)}</small></td>
      <td class="num">${pp(r.x)}</td><td class="num">${pp(r.y)}</td><td><span class="etf-q etf-q-${esc(r.quadrant)}">${esc(r.quadrant)}</span></td></tr>`).join('');
    return `<section class="panel etf-card" aria-label="行业轮动">
      <h3>行业轮动（类 RRG：相对 SPY 强度 × 动量）</h3>
      <div class="etf-rot">
        <div class="etf-chart etf-chart-rrg" id="etf-chart-rrg" role="img" aria-label="行业轮动象限图"></div>
        <div class="table-wrap" tabindex="0" aria-label="行业轮动象限表"><table class="etf-table"><thead><tr><th>行业ETF</th><th class="num">相对强度 x</th><th class="num">动量 y</th><th>象限</th></tr></thead>
        <tbody>${body || `<tr><td colspan="4" class="etf-na">无数据</td></tr>`}</tbody></table></div>
      </div>
      <p class="sub">${esc((rot && rot.method) || '')}</p>
    </section>`;
  }

  // ---------- ECharts ----------
  const charts = {};
  function chart(id) {
    const el = root.document && root.document.getElementById(id);
    if (!el || typeof root.echarts === 'undefined' || !root.echarts.init) return null;
    if (!charts[id] || charts[id].isDisposed?.()) charts[id] = root.echarts.init(el, null, { renderer: 'canvas' });
    return charts[id];
  }
  const axisStyle = { axisLine: { lineStyle: { color: '#4b5d78' } }, axisLabel: { color: '#a0afc3' }, splitLine: { lineStyle: { color: 'rgba(160,175,195,0.12)' } } };
  const PALETTE = ['#f5cb7a', '#79b8ff', '#c084fc', '#4ade80', '#fb923c', '#f472b6', '#22d3ee', '#a3e635', '#e879f9', '#facc15', '#60a5fa', '#f87171'];

  function riskOption(c) {
    return {
      backgroundColor: 'transparent', animation: false, color: PALETTE,
      tooltip: { trigger: 'axis' }, legend: { type: 'scroll', textStyle: { color: '#a0afc3' }, pageTextStyle: { color: '#a0afc3' }, top: 0 },
      grid: { left: 48, right: 24, top: 40, bottom: 48 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6 }],
      xAxis: { type: 'time', ...axisStyle },
      yAxis: { type: 'value', scale: true, ...axisStyle },
      series: ((c && c.series) || []).map((s) => ({ name: s.name, type: 'line', showSymbol: false, lineStyle: { width: 1.4 },
        data: (c.dates || []).map((d, i) => [d, s.data && s.data[i] != null ? s.data[i] : null]) })),
    };
  }

  function rrgOption(rot) {
    const items = (rot && rot.items) || [];
    const all = items.flatMap((r) => (r.tail || []).flatMap((p) => [Math.abs(p.x || 0), Math.abs(p.y || 0)]));
    const lim = Math.ceil(Math.max(0.02, ...all) * 1.1 * 100 / 5) * 5;
    const series = items.map((r, i) => ({
      name: r.symbol, type: 'line', smooth: true, symbolSize: (v, p) => (p.dataIndex === (r.tail || []).length - 1 ? 10 : 4),
      lineStyle: { width: 1.2, color: PALETTE[i % PALETTE.length] }, itemStyle: { color: PALETTE[i % PALETTE.length] },
      label: { show: true, formatter: (p) => (p.dataIndex === (r.tail || []).length - 1 ? r.symbol : ''), color: '#e5edf7', position: 'top' },
      data: (r.tail || []).map((p) => [p.x * 100, p.y * 100, p.d]),
    }));
    series.push({ type: 'scatter', silent: true, data: [], markArea: { silent: true, data: [
      [{ coord: [0, 0], itemStyle: { color: 'rgba(239,83,80,0.06)' }, label: { show: true, position: 'insideTopRight', color: '#a0afc3', formatter: '领先' } }, { coord: [lim, lim] }],
      [{ coord: [0, 0], itemStyle: { color: 'rgba(245,203,122,0.06)' }, label: { show: true, position: 'insideBottomRight', color: '#a0afc3', formatter: '转弱' } }, { coord: [lim, -lim] }],
      [{ coord: [0, 0], itemStyle: { color: 'rgba(38,166,154,0.06)' }, label: { show: true, position: 'insideBottomLeft', color: '#a0afc3', formatter: '落后' } }, { coord: [-lim, -lim] }],
      [{ coord: [0, 0], itemStyle: { color: 'rgba(121,184,255,0.06)' }, label: { show: true, position: 'insideTopLeft', color: '#a0afc3', formatter: '改善' } }, { coord: [-lim, lim] }],
    ] } });
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'item', formatter: (p) => (p.value ? `${esc(p.seriesName)} ${esc(p.value[2] || '')}<br>相对强度 ${p.value[0].toFixed(1)}pp · 动量 ${p.value[1].toFixed(1)}pp` : '') },
      grid: { left: 52, right: 24, top: 36, bottom: 44 },
      xAxis: { type: 'value', name: '相对SPY 63日(pp)', nameLocation: 'middle', nameGap: 26, min: -lim, max: lim, interval: lim / 5, ...axisStyle, axisLabel: { color: '#a0afc3', formatter: (v) => v.toFixed(0) } },
      yAxis: { type: 'value', name: '21日动量(pp)', nameTextStyle: { align: 'left' }, min: -lim, max: lim, interval: lim / 5, ...axisStyle, axisLabel: { color: '#a0afc3', formatter: (v) => v.toFixed(0) } },
      series,
    };
  }

  function barOption(p) {
    const rows = (p.groups || []).flatMap((g) => g.items || []).filter((r) => r.status === 'ok' && isNum(r.r1m)).sort((a, b) => a.r1m - b.r1m);
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 64, right: 24, top: 12, bottom: 24 },
      xAxis: { type: 'value', ...axisStyle, axisLabel: { color: '#a0afc3', formatter: (v) => v + '%' } },
      yAxis: { type: 'category', data: rows.map((r) => r.symbol), ...axisStyle },
      series: [{ type: 'bar', name: '近1月收益%', barMaxWidth: 12,
        data: rows.map((r) => ({ value: +(r.r1m * 100).toFixed(2), itemStyle: { color: r.r1m >= 0 ? '#ef5350' : '#26a69a' } })) }],
    };
  }

  function drawCharts(p) {
    const a = chart('etf-chart-risk');
    if (a && p.risk && p.risk.chart && (p.risk.chart.dates || []).length) a.setOption(riskOption(p.risk.chart), true);
    const b = chart('etf-chart-rrg');
    if (b && p.rotation && (p.rotation.items || []).length) b.setOption(rrgOption(p.rotation), true);
    const c = chart('etf-chart-bar');
    if (c) c.setOption(barOption(p), true);
  }

  function resize() {
    Object.values(charts).forEach((ch) => { try { ch.resize(); } catch (_) { /* ignore */ } });
  }

  function sourcesBlock(p) {
    const errs = Object.entries(p.errors || {});
    return `<details class="panel etf-card"><summary>数据来源、口径与局限</summary>
      <ul>${(p.notes || []).map((n) => `<li>${esc(n)}</li>`).join('')}</ul>
      <ul>${(p.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a></li>`).join('')}</ul>
      ${errs.length ? `<p class="etf-warn">本轮失败（已回退上次快照或显示无数据）：${errs.map(([k, v]) => esc(k) + '（' + esc(v) + '）').join('；')}</p>` : ''}
    </details>`;
  }

  function render(p, el) {
    state.data = p;
    if (!p || p.status !== 'ok') {
      el.innerHTML = '<p class="etf-na">无数据：美股ETF行情获取失败或尚未生成快照，稍后重试。</p>';
      return;
    }
    const snapRows = (p.groups || []).flatMap((g) => g.items || []).filter((r) => r.snapshot);
    el.innerHTML = `
      <p class="sub">数据截至美东 ${esc(p.asof || '无数据')}${p.intraday ? '（含盘中价，未收盘）' : ' 收盘'} · 更新 ${esc(String(p.updated_at || '').replace('T', ' ').slice(0, 16))}（北京时间）${isNum(p.age_hours) ? ` · ${fmt(p.age_hours, 1)} 小时前` : ''}${p.from_snapshot ? ' · 上次快照' : ''}</p>
      ${snapRows.length ? `<p class="etf-warn">部分 ETF 本轮未取到数据，显示上次快照：${snapRows.map((r) => esc(r.symbol) + '（' + esc(r.snapshot_date) + '）').join('、')}</p>` : ''}
      ${summaryCard(p)}
      ${riskCard(p.risk)}
      ${rotationCard(p.rotation)}
      ${table(p)}
      <section class="panel etf-card" aria-label="近1月收益排行"><h3>近 1 月收益排行（全部 ETF）</h3><div class="etf-chart etf-chart-bar" id="etf-chart-bar" role="img" aria-label="近1月收益横向条形图"></div></section>
      ${sourcesBlock(p)}`;
    drawCharts(p);
  }

  function rerenderTable() {
    const el = root.document && root.document.getElementById('usetf-content');
    if (el && state.data) render(state.data, el);
  }

  function onClick(ev) {
    const t = ev.target && ev.target.closest ? ev.target.closest('[data-sort],[data-group]') : null;
    if (!t) return;
    if (t.hasAttribute('data-group')) {
      state.group = t.getAttribute('data-group');
    } else {
      const k = t.getAttribute('data-sort');
      if (!k) { state.sort = null; } else if (state.sort === k) { state.dir = -state.dir; } else { state.sort = k; state.dir = k === 'symbol' ? 1 : -1; }
    }
    rerenderTable();
  }

  let loading = false, loadedAt = 0;
  async function load(force) {
    const el = root.document && root.document.getElementById('usetf-content');
    if (!el || loading) return;
    if (!force && state.data && Date.now() - loadedAt < 60000) { setTimeout(resize, 60); return; }
    loading = true;
    const status = root.document.getElementById('usetf-status');
    if (status) status.textContent = force ? '已请求后台刷新，先显示快照…' : '加载美股ETF快照…';
    try {
      const url = isStatic() ? './api/us_etf.json' : (force ? '/api/us-etf/refresh' : '/api/us-etf');
      const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      render(data, el);
      loadedAt = Date.now();
      if (status) status.textContent = data.note ? String(data.note).replace(/^\s*·\s*/, '') : (data.refresh_status === 'started' ? '快照较旧，已在后台刷新，稍后重新进入可看到新数据' : '');
    } catch (e) {
      if (!state.data) el.innerHTML = `<p class="etf-na">无数据：${esc(e.message)}。重新进入本页可重试。</p>`;
      if (status) status.textContent = '加载失败：' + e.message;
    } finally {
      loading = false;
    }
  }

  function bind() {
    const el = root.document.getElementById('usetf-content');
    if (el) el.addEventListener('click', onClick);
    const btn = root.document.getElementById('usetf-refresh');
    if (!btn) return;
    if (isStatic()) { btn.hidden = true; return; }
    btn.addEventListener('click', () => load(true));
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
    if (typeof window !== 'undefined') window.addEventListener('resize', resize);
  }
  const api = { load, render, resize, state, table, row, sortedRows, summaryCard, riskCard, rotationCard, riskOption, rrgOption, barOption, cls, pct, onClick };
  root.UsEtfView = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
