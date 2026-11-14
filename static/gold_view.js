/* 黄金页：快照优先加载（/api/gold、/api/gold/quant 或 Pages ./api/*.json），ECharts 交互图；仅研究参考，不能下单。 */
(function (root) {
  'use strict';
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (x, d = 2) => (isNum(x) ? x.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '无数据');
  const signed = (x, d = 2, unit = '') => (isNum(x) ? (x > 0 ? '+' : '') + x.toFixed(d) + unit : '无数据');
  // 红=上涨/流入/增加，绿=下跌/流出/减少（与全站一致）
  const cls = (x) => (isNum(x) ? (x > 0 ? 'up' : x < 0 ? 'down' : '') : '');
  const chg = (x, d = 2, unit = '%') => `<span class="${cls(x)}">${signed(x, d, unit)}</span>`;
  const ok = (b) => !!b && b.status === 'ok';
  const isStatic = () => root.STATIC_MODE === true || /github\.io$/i.test((root.location && root.location.hostname) || '') ||
    String((root.location && root.location.search) || '').includes('static=1');

  function badge(block) {
    if (!block) return '<span class="gold-na">无数据</span>';
    if (block.snapshot) return `<span class="gold-snap" title="${esc(block.snapshot_reason || '')}">上次快照（${esc(block.snapshot_date || block.asof || '')}）</span>`;
    if (block.status !== 'ok') return `<span class="gold-na">无数据${block.reason ? '：' + esc(block.reason) : ''}</span>`;
    return block.asof ? `<span class="gold-asof">截至 ${esc(block.asof)}</span>` : '';
  }

  function regimeClass(regime) {
    return regime === '偏多' ? 'up' : regime === '偏空' ? 'down' : 'gold-flat';
  }

  function signalCard(sig) {
    if (!sig) return '<section class="panel gold-card"><h3>多因子研究打分</h3><p class="gold-na">无数据</p></section>';
    const pct = Math.max(0, Math.min(100, ((sig.score + sig.max_score) / (2 * sig.max_score)) * 100));
    const factors = (sig.factors || []).map((f) => `
      <div class="gold-factor">
        <div class="gold-factor-head"><strong>${esc(f.name)}</strong><span class="gold-chip ${cls(f.score)}">${signed(f.score, 0)}</span></div>
        <ul>${(f.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      </div>`).join('');
    return `<section class="panel gold-card gold-signal" aria-label="黄金多因子研究打分">
      <div class="gold-signal-head">
        <div><span class="sub">多因子研究打分（趋势 · 实际利率/美元 · 持仓/资金 · 溢价/情绪，各 −2…+2）</span>
          <h3 class="gold-regime ${regimeClass(sig.regime)}">${esc(sig.regime)} <small>${signed(sig.score, 0)} / ±${esc(sig.max_score)}</small></h3></div>
        <div class="gold-meter" role="img" aria-label="打分 ${esc(sig.score)}"><span style="left:${pct.toFixed(1)}%"></span></div>
      </div>
      <div class="gold-factors">${factors}</div>
      <p class="gold-hint">思路：${esc(sig.hint)}${isNum(sig.hv_rank_1y) ? `（COMEX金 HV20 1年分位 ${fmt(sig.hv_rank_1y, 0)}%）` : ''}</p>
      <p class="gold-warn">${esc(sig.disclaimer || '研究参考，不能下单，不构成投资建议')}</p>
    </section>`;
  }

  function kpi(label, value, sub, extra) {
    return `<div class="gold-kpi"><span class="gold-kpi-label">${label}</span><strong>${value}</strong><span class="gold-kpi-sub">${sub || ''}</span>${extra ? `<span class="gold-kpi-sub">${extra}</span>` : ''}</div>`;
  }

  function realtimeCard(rt) {
    if (!ok(rt)) return `<section class="panel gold-card"><h3>实时内外盘比价 ${badge(rt)}</h3><p class="gold-na">无数据</p></section>`;
    const q = rt.quotes || {};
    const one = (k, label, d, unit) => {
      const v = q[k];
      return v ? kpi(label, fmt(v.last, d) + (unit ? `<small> ${unit}</small>` : ''), chg(v.chg_pct), esc(v.time || '')) : kpi(label, '无数据', '');
    };
    return `<section class="panel gold-card" aria-label="实时内外盘比价">
      <h3>实时内外盘比价 ${badge(rt)}</h3>
      <div class="gold-kpis">
        ${one('xau', '伦敦金现货 XAU', 2, '美元/盎司')}
        ${one('gc', 'COMEX金 GC', 2, '美元/盎司')}
        ${one('au0', '沪金主连 AU0', 2, '元/克')}
        ${one('usdcny', '在岸 USD/CNY', 4, '')}
        ${kpi('伦敦金折人民币', fmt(rt.xau_rmb_g) + '<small> 元/克</small>', 'COMEX折算 ' + fmt(rt.gc_rmb_g) + ' 元/克')}
        ${kpi('沪金溢价（vs 伦敦金）', `<span class="${cls(rt.premium_vs_xau)}">${signed(rt.premium_vs_xau, 2)}</span><small> 元/克</small>`, chg(rt.premium_vs_xau_pct), 'vs COMEX ' + signed(rt.premium_vs_gc, 2) + ' 元/克')}
        ${one('etf518880', '华安黄金ETF 518880', 3, '元')}
      </div>
      <p class="sub">${esc(rt.formula || '')}。各报价时间不同（沪金非交易时段为最近成交价），溢价含资金成本、进口与交割因素，非无风险套利。</p>
    </section>`;
  }

  function macroCard(p) {
    const m = p.macro || {}, r = p.ratio || {}, rr = m.real10y || {}, be = m.breakeven10y || {}, nm = m.nominal10y || {}, dx = m.dxy || {}, vx = m.vix || {};
    const bp = (x) => chg(x, 0, 'bp');
    return `<section class="panel gold-card" aria-label="宏观驱动">
      <h3>宏观驱动：实际利率 · 美元 · 避险 ${badge(m)}</h3>
      <div class="gold-kpis">
        ${kpi('10Y TIPS 实际利率', ok(rr) ? fmt(rr.last) + '%' : '无数据', ok(rr) ? '20日 ' + bp(rr.chg_20d_bp) + ' · 3年分位 ' + fmt(rr.rank_3y, 0) + '%' : '', ok(rr) ? 'FRED DFII10 · ' + esc(rr.asof) : '')}
        ${kpi('10Y 名义利率', ok(nm) ? fmt(nm.last) + '%' : '无数据', ok(nm) ? '20日 ' + bp(nm.chg_20d_bp) : '', ok(nm) ? 'FRED DGS10 · ' + esc(nm.asof) : '')}
        ${kpi('10Y 盈亏平衡通胀', ok(be) ? fmt(be.last) + '%' : '无数据', ok(be) ? '20日 ' + bp(be.chg_20d_bp) : '', '= DGS10 − DFII10')}
        ${kpi('美元指数 DXY', ok(dx) ? fmt(dx.last) : '无数据', ok(dx) ? '20日 ' + chg(dx.chg_20d_pct) + ' · 1年分位 ' + fmt(dx.rank_1y, 0) + '%' : '', ok(dx) ? esc(dx.asof) : '')}
        ${kpi('VIX', ok(vx) ? fmt(vx.last) : '无数据', ok(vx) ? '1日 ' + chg(vx.chg_1d_pct) : '', ok(vx) ? esc(vx.asof) : '')}
        ${kpi('金银比 GC/SI', ok(r) ? fmt(r.latest) : '无数据', ok(r) ? '3年分位 ' + fmt(r.rank_3y, 0) + '% · 20日 ' + chg(r.chg_20d, 2, '') : badge(r), ok(r) ? `3年区间 ${fmt(r.min_3y)}–${fmt(r.max_3y)}` : '')}
        ${kpi('金价×实际利率 120日相关', isNum(m.corr_gold_real_120d) ? fmt(m.corr_gold_real_120d) : '无数据', '负值越大＝实际利率驱动越强')}
      </div>
      <p class="sub">利率/指数按方向着色：红＝上升，绿＝下降（实际利率与美元上升通常压制金价）。</p>
    </section>`;
  }

  function techTable(t) {
    const rows = ['GC', 'AU0'].map((k) => {
      const x = (t || {})[k];
      if (!ok(x)) return `<tr><td>${k === 'GC' ? 'COMEX黄金' : '沪金主连 AU0'}</td><td colspan="10">${badge(x)}</td></tr>`;
      return `<tr><td>${esc(x.name)} ${x.snapshot ? badge(x) : ''}<br><small class="sub">${esc(x.unit)} · ${esc(x.asof)}</small></td>
        <td class="num">${fmt(x.last)}</td><td class="num">${chg(x.chg_20d_pct)}</td><td class="num">${fmt(x.ma20)}</td><td class="num">${fmt(x.ma60)}</td>
        <td class="num">${chg(x.vs_ma60_pct)}</td><td class="num">${fmt(x.rsi14, 1)}</td><td class="num">${fmt(x.hv20, 1)}%</td><td class="num">${fmt(x.hv_rank_1y, 0)}%</td>
        <td class="num">${fmt(x.low_52w)} – ${fmt(x.high_52w)}<br><small class="sub">位置 ${fmt(x.pos_52w, 0)}% · 距高 ${signed(x.from_high_pct, 1, '%')}</small></td><td>${esc(x.trend)}</td></tr>`;
    }).join('');
    return `<section class="panel gold-card" aria-label="技术指标"><h3>技术指标</h3><div class="table-wrap" tabindex="0" aria-label="黄金技术指标表"><table class="gold-table">
      <thead><tr><th>品种</th><th class="num">最新</th><th class="num">20日</th><th class="num">MA20</th><th class="num">MA60</th><th class="num">距MA60</th><th class="num">RSI14</th><th class="num">HV20</th><th class="num">HV 1年分位</th><th class="num">52周区间</th><th>趋势</th></tr></thead>
      <tbody>${rows}</tbody></table></div></section>`;
  }

  function positioningCard(pos, prem) {
    pos = pos || {};
    const sp = pos.spdr || {}, cf = pos.cftc || {}, ce = pos.cn_etfs || {}, ins = pos.inst || {}, pb = pos.pboc || {};
    const etfRows = ok(ce) ? (ce.items || []).map((i) => i.status === 'ok'
      ? `<tr><td>${esc(i.code)} ${esc(i.name)}</td><td class="num">${fmt(i.shares_yi)}</td><td class="num">${chg(i.net_1d, 2, '')}</td><td class="num">${chg(i.net_5d, 2, '')}</td><td class="num">${chg(i.net_20d, 2, '')}</td></tr>`
      : `<tr><td>${esc(i.code)} ${esc(i.name)}</td><td colspan="4" class="gold-na">无数据${i.reason ? '：' + esc(i.reason) : ''}</td></tr>`).join('')
      + `<tr class="gold-total"><td>合计</td><td class="num">${fmt(ce.shares_yi)}</td><td class="num">${chg(ce.net_1d, 2, '')}</td><td class="num">${chg(ce.net_5d, 2, '')}</td><td class="num">${chg(ce.net_20d, 2, '')}</td></tr>`
      : `<tr><td colspan="5">${badge(ce)}</td></tr>`;
    const pr = prem || {};
    return `<section class="panel gold-card" aria-label="机构与央行持仓">
      <h3>机构与央行持仓 · 资金流</h3>
      <div class="gold-kpis">
        ${kpi('SPDR GLD 持仓', ok(sp) ? fmt(sp.tonnes) + '<small> 吨</small>' : '无数据', ok(sp) ? `1日 ${chg(sp.chg_1d, 2, '')} · 5日 ${chg(sp.chg_5d, 2, '')} · 20日 ${chg(sp.chg_20d, 2, '')}` : '', badge(sp))}
        ${kpi('CFTC 投机净多（非商业）', ok(cf) ? fmt(cf.net, 0) + '<small> 手</small>' : '无数据', ok(cf) ? `1周 ${chg(cf.chg_1w, 0, '')} · 4周 ${chg(cf.chg_4w, 0, '')}` : '', ok(cf) ? `占持仓 ${fmt(cf.net_pct_oi, 1)}% · 3年分位 ${fmt(cf.rank_3y, 0)}% · ${badge(cf)}` : badge(cf))}
        ${kpi('中国黄金储备（价值）', ok(pb) ? fmt(pb.value) + '<small> 亿美元</small>' : '无数据', ok(pb) ? `${esc(pb.month)} 环比 ${chg(pb.mom_pct)} · 同比 ${chg(pb.yoy_pct)}` : '', ok(pb) ? (isNum(pb.gold_mom_pct) ? `同月COMEX金价 ${signed(pb.gold_mom_pct, 2, '%')} · ` : '') + badge(pb) : badge(pb))}
        ${kpi('518880 机构持有比例', ok(ins) ? fmt(ins.inst_pct) + '%' : '无数据', ok(ins) ? `个人 ${fmt(ins.indiv_pct)}% · 总份额 ${fmt(ins.total_shares_yi)} 亿份` : '', ok(ins) ? `报告期 ${esc(ins.asof)}（半年报）` : badge(ins))}
        ${kpi('沪金溢价（日线）', ok(pr) ? `<span class="${cls(pr.latest)}">${signed(pr.latest, 2)}</span><small> 元/克</small>` : '无数据', ok(pr) ? `${signed(pr.latest_pct, 2, '%')} · 1年分位 ${fmt(pr.rank_1y, 0)}% · 均值 ${signed(pr.mean_1y_pct, 2, '%')}` : '', ok(pr) ? (pr.sge ? `上金所Au99.99 ${signed(pr.sge.latest, 2)} 元/克 · ` : '') + badge(pr) : badge(pr))}
      </div>
      <h4>上交所黄金ETF 份额与估算净申赎（亿元） ${badge(ce)}</h4>
      <div class="table-wrap" tabindex="0" aria-label="上交所黄金ETF份额与净申赎表"><table class="gold-table"><thead><tr><th>ETF</th><th class="num">份额（亿份）</th><th class="num">1日</th><th class="num">5日</th><th class="num">20日</th></tr></thead><tbody>${etfRows}</tbody></table></div>
      <p class="sub">${esc(ce.method || '')} 中国黄金储备为官方价值口径（亿美元），吨数暂无已验证免 key 接口，不做估算；价值环比可对照同月金价变化。</p>
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
  const pairs = (dates, vals) => (dates || []).map((d, i) => [d, vals && vals[i] != null ? vals[i] : null]);

  function macroOption(c) {
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#a0afc3' }, top: 0 },
      grid: { left: 56, right: 56, top: 32, bottom: 56 },
      dataZoom: [{ type: 'inside', start: 50 }, { type: 'slider', height: 18, bottom: 8, start: 50 }],
      xAxis: { type: 'time', ...axisStyle },
      yAxis: [{ type: 'value', name: '美元/盎司', scale: true, ...axisStyle },
        { type: 'value', name: '实际利率%（倒序）', inverse: true, scale: true, ...axisStyle, splitLine: { show: false } }],
      series: [
        { name: 'COMEX金', type: 'line', showSymbol: false, data: pairs(c.dates, c.gc), lineStyle: { width: 1.6, color: '#f5cb7a' }, itemStyle: { color: '#f5cb7a' } },
        { name: 'MA60', type: 'line', showSymbol: false, data: pairs(c.dates, c.gc_ma60), lineStyle: { width: 1, type: 'dashed', color: '#79b8ff' }, itemStyle: { color: '#79b8ff' } },
        { name: '10Y实际利率（右轴倒序）', type: 'line', yAxisIndex: 1, showSymbol: false, connectNulls: true, data: pairs(c.dates, c.real10y), lineStyle: { width: 1.2, color: '#c084fc' }, itemStyle: { color: '#c084fc' } },
      ],
    };
  }

  function auOption(c) {
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#a0afc3' }, top: 0 },
      grid: { left: 56, right: 56, top: 32, bottom: 56 },
      dataZoom: [{ type: 'inside', start: 50 }, { type: 'slider', height: 18, bottom: 8, start: 50 }],
      xAxis: { type: 'time', ...axisStyle },
      yAxis: [{ type: 'value', name: '元/克', scale: true, ...axisStyle },
        { type: 'value', name: '溢价%', ...axisStyle, splitLine: { show: false } }],
      series: [
        { name: '沪金AU0', type: 'line', showSymbol: false, data: pairs(c.au_dates, c.au0), lineStyle: { width: 1.6, color: '#f5cb7a' }, itemStyle: { color: '#f5cb7a' } },
        { name: 'MA20', type: 'line', showSymbol: false, data: pairs(c.au_dates, c.au_ma20), lineStyle: { width: 1, color: '#79b8ff' }, itemStyle: { color: '#79b8ff' } },
        { name: 'MA60', type: 'line', showSymbol: false, data: pairs(c.au_dates, c.au_ma60), lineStyle: { width: 1, type: 'dashed', color: '#c084fc' }, itemStyle: { color: '#c084fc' } },
        { name: '内外盘溢价%（右轴）', type: 'bar', yAxisIndex: 1, barMaxWidth: 4, data: pairs(c.au_dates, c.prem_pct),
          itemStyle: { color: (p) => (p.value && p.value[1] < 0 ? '#26a69a' : '#ef5350'), opacity: 0.6 } },
      ],
    };
  }

  function flowsOption(pos) {
    const sp = (pos && pos.spdr && pos.spdr.series) || [];
    // CFTC 保留3年用于分位，但图上与 SPDR 序列同一时间窗口，便于对照。
    const cfAll = (pos && pos.cftc && pos.cftc.series) || [];
    const cf = sp.length ? cfAll.filter((r) => r.d >= sp[0].d) : cfAll;
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#a0afc3' }, top: 0 },
      grid: { left: 64, right: 64, top: 32, bottom: 32 },
      xAxis: { type: 'time', ...axisStyle },
      yAxis: [{ type: 'value', name: 'SPDR 吨', scale: true, ...axisStyle },
        { type: 'value', name: 'CFTC 净多（手）', scale: true, ...axisStyle, splitLine: { show: false } }],
      series: [
        { name: 'SPDR 持仓（吨）', type: 'line', showSymbol: false, data: sp.map((r) => [r.d, r.v]), lineStyle: { width: 1.6, color: '#f5cb7a' }, itemStyle: { color: '#f5cb7a' } },
        { name: 'CFTC 投机净多（右轴）', type: 'bar', yAxisIndex: 1, barMaxWidth: 6, data: cf.map((r) => [r.d, r.net]), itemStyle: { color: '#79b8ff', opacity: 0.55 } },
      ],
    };
  }

  function drawCharts(p) {
    const c = p.chart || {};
    const a = chart('gold-chart-macro');
    if (a && (c.dates || []).length) a.setOption(macroOption(c), true);
    const b = chart('gold-chart-au');
    if (b && (c.au_dates || []).length) b.setOption(auOption(c), true);
    const f = chart('gold-chart-flows');
    if (f) f.setOption(flowsOption(p.positioning), true);
  }

  function resize() {
    Object.values(charts).forEach((ch) => { try { ch.resize(); } catch (_) { /* ignore */ } });
  }

  function sourcesBlock(p) {
    const errs = Object.entries(p.errors || {});
    return `<details class="panel gold-card"><summary>数据来源、口径与局限</summary>
      <ul>${(p.notes || []).map((n) => `<li>${esc(n)}</li>`).join('')}</ul>
      <ul>${(p.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a></li>`).join('')}</ul>
      ${p.premium && p.premium.method ? `<p class="sub">溢价口径：${esc(p.premium.method)}</p>` : ''}
      ${errs.length ? `<p class="gold-warn">本轮失败的数据源（已回退上次快照或显示无数据）：${errs.map(([k, v]) => esc(k) + '（' + esc(v) + '）').join('；')}</p>` : ''}
    </details>`;
  }

  function render(p, el) {
    if (!p || p.status !== 'ok') {
      el.innerHTML = '<p class="gold-na">无数据：黄金行情获取失败或尚未生成快照，稍后重试。</p>';
      return;
    }
    const snaps = Object.entries(p.snapshot_blocks || {});
    el.innerHTML = `
      <p class="sub">更新 ${esc(String(p.updated_at || '').replace('T', ' ').slice(0, 16))}（北京时间）${isNum(p.age_hours) ? ` · ${fmt(p.age_hours, 1)} 小时前` : ''}${p.from_snapshot ? ' · 上次快照' : ''}</p>
      ${snaps.length ? `<p class="gold-warn">部分模块本轮未取到实时数据，显示上次快照：${snaps.map(([k, d]) => esc(k) + '（' + esc(d) + '）').join('、')}</p>` : ''}
      ${signalCard(p.signal)}
      ${realtimeCard(p.realtime)}
      ${macroCard(p)}
      <section class="panel gold-card"><h3>COMEX金价 vs 10Y实际利率（右轴倒序） ${badge(p.macro)}</h3><div class="gold-chart" id="gold-chart-macro" role="img" aria-label="COMEX金价与10年期实际利率走势"></div></section>
      <section class="panel gold-card"><h3>沪金AU0 · 均线 · 内外盘溢价% ${badge(p.premium)}</h3><div class="gold-chart" id="gold-chart-au" role="img" aria-label="沪金主连与内外盘溢价走势"></div></section>
      ${techTable(p.technicals)}
      ${positioningCard(p.positioning, p.premium)}
      <section class="panel gold-card"><h3>SPDR 持仓（吨） vs CFTC 投机净多</h3><div class="gold-chart" id="gold-chart-flows" role="img" aria-label="SPDR持仓与CFTC净多走势"></div></section>
      ${sourcesBlock(p)}`;
    drawCharts(p);
  }


  // ---------- 量化纸面跟踪（/api/gold/quant）：研究参考，不能下单；没有任何下单接口 ----------
  const pct = (x, d = 2) => (isNum(x) ? chg(x * 100, d, '%') : '无数据');
  const pctPlain = (x, d = 1) => (isNum(x) ? (x * 100).toFixed(d) + '%' : '无数据');
  const voteChip = (v) => (isNum(v) ? `<span class="gold-chip ${cls(v)}">${v > 0 ? '+1 多' : v < 0 ? '−1 空' : '0'}</span>` : '无数据');

  function quantPerfTable(bt) {
    const s = (bt && bt.strategy) || {}, b = (bt && bt.buy_hold) || {}, c2 = (bt && bt.cost_2x) || {};
    const cell = (m) => (m && m.status === 'ok'
      ? `<td class="num">${pct(m.cagr, 1)}</td><td class="num">${fmt(m.sharpe, 2)}</td><td class="num">${pct(m.max_drawdown, 1)}</td>`
      : '<td colspan="3" class="gold-na">无数据</td>');
    const row = (label, set) => `<tr><td>${label}</td>${cell(set.full)}${cell(set.is)}${cell(set.oos)}</tr>`;
    const f = s.full || {};
    return `<div class="table-wrap" tabindex="0" aria-label="回测绩效对比表"><table class="gold-table">
      <thead><tr><th rowspan="2">扣成本后</th><th colspan="3">全期 ${esc(f.start || '')} 起</th><th colspan="3">样本内（~2020）</th><th colspan="3">样本外（2021~）</th></tr>
      <tr><th class="num">年化</th><th class="num">夏普</th><th class="num">最大回撤</th><th class="num">年化</th><th class="num">夏普</th><th class="num">最大回撤</th><th class="num">年化</th><th class="num">夏普</th><th class="num">最大回撤</th></tr></thead>
      <tbody>${row('策略', s)}${row('买入持有', b)}</tbody></table></div>
      <p class="sub">成本×2（全期）：年化 ${pctPlain(c2.cagr)} · 夏普 ${fmt(c2.sharpe, 2)} · 最大回撤 ${pctPlain(c2.max_drawdown)}。
      平均仓位 ${fmt(f.avg_weight, 2)} · 持仓时间 ${pctPlain(f.exposure, 0)} · 单边换手 ${fmt(f.turnover, 1)} 次/年 · 成本拖累 ${pctPlain(f.cost_drag, 2)}/年${isNum(f.financing_drag) && f.financing_drag > 0 ? ' · 融资成本 ' + pctPlain(f.financing_drag, 2) + '/年' : ''} ·
      单笔胜率 ${pctPlain(f.win_rate, 0)} · 盈亏比 ${fmt(f.profit_factor, 1)}。夏普无风险利率取 0。</p>`;
  }

  function quantYearly(rows) {
    const body = (rows || []).slice().reverse().map((r) => `<tr><td>${esc(r.year)}${r.partial ? '<small class="sub">（不完整）</small>' : ''}</td>
      <td class="num">${pct(r.strategy, 1)}</td><td class="num">${pct(r.buy_hold, 1)}</td><td class="num">${pct(r.strategy_dd, 1)}</td><td class="num">${pct(r.buy_hold_dd, 1)}</td><td class="num">${fmt(r.avg_weight, 2)}</td></tr>`).join('');
    return `<details><summary>分年收益（策略 vs 买入持有）</summary><div class="table-wrap" tabindex="0" aria-label="分年收益表"><table class="gold-table">
      <thead><tr><th>年份</th><th class="num">策略</th><th class="num">买入持有</th><th class="num">策略年内回撤</th><th class="num">买入持有年内回撤</th><th class="num">平均仓位</th></tr></thead>
      <tbody>${body}</tbody></table></div></details>`;
  }

  function quantLedger(paper) {
    const rows = ((paper && paper.ledger) || []).slice(-30).reverse();
    if (!rows.length) return '<p class="gold-na">纸面账本：无数据</p>';
    const body = rows.map((r) => `<tr><td>${esc(r.date)}</td><td class="num">${fmt(r.close, 3)}</td><td class="num">${fmt(r.trend_score, 1)}</td>
      <td class="num">${fmt(r.held_weight, 2)}</td><td class="num">${fmt(r.target_weight, 2)}</td><td class="num">${pct(r.daily_return, 2)}</td><td class="num">${fmt(r.nav, 4)}</td></tr>`).join('');
    return `<h4>纸面账本（自 ${esc(paper.start)} 收盘起，只追加不改写；最近 30 条）· 纸面净值 ${fmt(paper.nav, 4)} · ${esc(paper.days)} 个交易日</h4>
      <div class="table-wrap" tabindex="0" aria-label="纸面跟踪账本"><table class="gold-table">
      <thead><tr><th>日期</th><th class="num">收盘</th><th class="num">趋势分</th><th class="num">当日持有</th><th class="num">收盘目标（次开盘执行）</th><th class="num">当日收益</th><th class="num">纸面净值</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
  }

  function quantCard(key, b) {
    if (!b || b.status !== 'ok') return `<section class="panel gold-card"><h3>${key === 'c1' ? 'C1 杠杆版' : '稳健版'} ${badge(b)}</h3><p class="gold-na">无数据</p></section>`;
    const sg = b.signal || {};
    const votes = (sg.votes || []).map((v) => `<tr><td>${esc(v.name)}</td><td class="num">${pct(v.value, 2)}</td><td class="num">${voteChip(v.vote)}</td></tr>`).join('');
    const lot = key === 'c1' ? kpi('AU 1手名义价值', fmt(sg.lot_notional_wan, 1) + '<small> 万元</small>', `0.1倍精度约需 ${fmt(sg.account_for_0p1_wan, 0)} 万元账户`, `每百万资金目标 ${fmt(sg.target_lots_per_million, 2)} 手`) : '';
    return `<section class="panel gold-card" aria-label="${esc(b.name)}">
      <h3>${esc(b.name)} ${badge(b)}</h3>
      <div class="gold-kpis">
        ${kpi('收盘目标仓位（下一开盘执行）', `<span class="${cls(sg.target_weight)}">${fmt(sg.target_weight, 2)}</span><small> 倍</small>`, esc(sg.action || ''), esc(sg.execution || ''))}
        ${kpi('当前纸面持有', fmt(sg.held_weight, 2) + '<small> 倍</small>', `收盘 ${fmt(sg.close, 3)} · ${esc(sg.asof || '')}`)}
        ${kpi('趋势分 T（−1…+1）', `<span class="${cls(sg.trend_score)}">${signed(sg.trend_score, 1)}</span>`, '5票等权，只取正部分做多')}
        ${kpi('EWMA 年化波动', pctPlain(sg.ewma_vol, 1), `目标 ${pctPlain(b.params && b.params.target_vol, 0)} · 上限 ${fmt(b.params && b.params.cap, 1)} 倍`)}
        ${lot}
      </div>
      <h4>5 票明细</h4>
      <div class="table-wrap" tabindex="0" aria-label="趋势5票明细"><table class="gold-table"><thead><tr><th>指标</th><th class="num">数值</th><th class="num">票</th></tr></thead><tbody>${votes}</tbody></table></div>
      ${quantLedger(b.paper)}
      <h4>回测（${esc(b.vehicle)}，扣成本）</h4>
      ${quantPerfTable(b.backtest)}
      <div class="gold-chart" id="gold-quant-chart-${esc(key)}" role="img" aria-label="${esc(b.short)}净值与回撤"></div>
      ${quantYearly(b.yearly)}
      <ul class="gold-warn">${(b.caveats || []).map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
    </section>`;
  }

  function quantOption(c, label) {
    const nav = pairs(c.dates, c.nav), bh = pairs(c.dates, c.nav_bh), dd = pairs(c.dates, (c.dd || []).map((x) => (isNum(x) ? x * 100 : null))), ddb = pairs(c.dates, (c.dd_bh || []).map((x) => (isNum(x) ? x * 100 : null)));
    return {
      backgroundColor: 'transparent', animation: false,
      tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#a0afc3' }, top: 0 },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [{ left: 56, right: 24, top: 32, height: '52%' }, { left: 56, right: 24, top: '70%', bottom: 48 }],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }, { type: 'slider', xAxisIndex: [0, 1], height: 16, bottom: 6 }],
      xAxis: [{ type: 'time', gridIndex: 0, ...axisStyle }, { type: 'time', gridIndex: 1, ...axisStyle }],
      yAxis: [{ type: 'log', name: '净值（对数）', gridIndex: 0, ...axisStyle }, { type: 'value', name: '回撤%', gridIndex: 1, max: 0, ...axisStyle }],
      series: [
        { name: label, type: 'line', showSymbol: false, data: nav, lineStyle: { width: 1.6, color: '#f5cb7a' }, itemStyle: { color: '#f5cb7a' } },
        { name: '买入持有', type: 'line', showSymbol: false, data: bh, lineStyle: { width: 1.2, color: '#79b8ff' }, itemStyle: { color: '#79b8ff' } },
        { name: label + '回撤', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: dd, areaStyle: { opacity: 0.25 }, lineStyle: { width: 1, color: '#26a69a' }, itemStyle: { color: '#26a69a' } },
        { name: '买入持有回撤', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: ddb, lineStyle: { width: 1, type: 'dashed', color: '#79b8ff' }, itemStyle: { color: '#79b8ff' } },
      ],
    };
  }

  function renderQuant(p, el) {
    if (!p || p.status !== 'ok') {
      el.innerHTML = '<section class="panel gold-card"><h3>量化纸面跟踪</h3><p class="gold-na">无数据：尚未生成量化快照或抓取失败，稍后重试。</p></section>';
      return;
    }
    const st = p.strategies || {}, r = p.rules || {};
    const errs = Object.entries(p.errors || {});
    el.innerHTML = `
      <section class="panel gold-card gold-quant-head" aria-label="量化纸面跟踪说明">
        <h3>量化纸面跟踪 · C1 杠杆版 vs 稳健版 <span class="gold-asof">更新 ${esc(String(p.updated_at || '').replace('T', ' ').slice(0, 16))}（北京时间）${p.from_snapshot ? ' · 上次快照' : ''}</span></h3>
        <p class="gold-warn">${esc(p.disclaimer || '研究参考，不能下单，不构成投资建议')}。纸面跟踪仅记录规则信号与假想净值，不连接任何券商/期货账户，也没有下单接口。</p>
        <ul class="sub">${['timing', 'signal', 'sizing', 'costs', 'periods', 'paper'].filter((k) => r[k]).map((k) => `<li>${esc(r[k])}</li>`).join('')}</ul>
        ${errs.length ? `<p class="gold-warn">本轮失败的数据源（已回退上次快照或显示无数据）：${errs.map(([k, v]) => esc(k) + '（' + esc(v) + '）').join('；')}</p>` : ''}
      </section>
      ${quantCard('c1', st.c1)}
      ${quantCard('robust', st.robust)}`;
    for (const key of ['c1', 'robust']) {
      const b = st[key];
      const ch = chart('gold-quant-chart-' + key);
      if (ch && b && b.status === 'ok' && b.curves && (b.curves.dates || []).length) ch.setOption(quantOption(b.curves, b.short || key), true);
    }
  }

  let qLoading = false, qLoadedAt = 0, qData = null;
  async function loadQuant(force) {
    const el = root.document && root.document.getElementById('gold-quant-content');
    if (!el || qLoading) return;
    if (!force && qData && Date.now() - qLoadedAt < 60000) return;
    qLoading = true;
    try {
      const url = isStatic() ? './api/gold_quant.json' : (force ? '/api/gold/quant/refresh' : '/api/gold/quant');
      const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      qData = await res.json();
      renderQuant(qData, el);
      qLoadedAt = Date.now();
    } catch (e) {
      if (!qData) el.innerHTML = `<p class="gold-na">量化纸面跟踪无数据：${esc(e.message)}。</p>`;
    } finally {
      qLoading = false;
    }
  }

  let loading = false, loadedAt = 0, lastData = null;
  async function load(force) {
    const el = root.document && root.document.getElementById('gold-content');
    if (!el || loading) return;
    loadQuant(force);
    if (!force && lastData && Date.now() - loadedAt < 60000) { setTimeout(resize, 60); return; }
    loading = true;
    const status = root.document.getElementById('gold-status');
    if (status) status.textContent = force ? '已请求后台刷新，先显示快照…' : '加载黄金快照…';
    try {
      const url = isStatic() ? './api/gold.json' : (force ? '/api/gold/refresh' : '/api/gold');
      const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      lastData = data;
      render(data, el);
      loadedAt = Date.now();
      if (status) status.textContent = data.note ? String(data.note).replace(/^\s*·\s*/, '') : (data.refresh_status === 'started' ? '快照较旧，已在后台刷新，稍后重新进入可看到新数据' : '');
    } catch (e) {
      if (!lastData) el.innerHTML = `<p class="gold-na">无数据：${esc(e.message)}。重新进入黄金页可重试。</p>`;
      if (status) status.textContent = '加载失败：' + e.message;
    } finally {
      loading = false;
    }
  }

  function bind() {
    const btn = root.document.getElementById('gold-refresh');
    if (!btn) return;
    if (isStatic()) { btn.hidden = true; return; }
    btn.addEventListener('click', () => load(true));
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
    if (typeof window !== 'undefined') window.addEventListener('resize', resize);
  }
  const api = { load, loadQuant, renderQuant, quantCard, quantOption, quantPerfTable, quantLedger, render, resize, fmt, signed, cls, badge, signalCard, realtimeCard, macroCard, techTable, positioningCard, macroOption, auOption, flowsOption };
  root.GoldView = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
