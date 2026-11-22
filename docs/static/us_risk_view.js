/* 美股风险区块：快照优先加载（/api/us/risk 或 Pages ./api/us_risk.json）；研究参考，不能下单。 */
(function (root) {
  'use strict';
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (x, d = 2) => (isNum(x) ? x.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '无数据');
  const cls = (x) => (isNum(x) ? (x > 0 ? 'up' : x < 0 ? 'down' : '') : '');
  const pct = (x, d = 2) => (isNum(x) ? `<span class="${cls(x)}">${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%</span>` : '<span class="etf-na">无数据</span>');
  const signed = (x, d = 2) => (isNum(x) ? `<span class="${cls(x)}">${x > 0 ? '+' : ''}${x.toFixed(d)}</span>` : '<span class="etf-na">无数据</span>');
  const na = (text) => `<span class="etf-na">${esc(text || '无数据')}</span>`;
  const isStatic = () => root.STATIC_MODE === true || /github\.io$/i.test((root.location && root.location.hostname) || '') ||
    String((root.location && root.location.search) || '').includes('static=1');
  const BAND_CLS = { '偏热': 'up', '偏冷': 'down', '中性': 'etf-flat' };
  const state = { data: null };

  function snapBadge(block) {
    if (block && (block.snapshot || block.from_snapshot) && (block.snapshot_date || block.asof || block.month)) {
      return `<span class="etf-snap" title="${esc(block.snapshot_reason || '')}">上次快照（${esc(block.snapshot_date || block.asof || block.month)}）</span>`;
    }
    return '';
  }

  function asofLine(block, fallback) {
    if (!block || block.status !== 'ok') return na(block && block.reason);
    const d = block.asof || block.month || fallback;
    return d ? `数据日期 ${esc(d)}` : '无数据';
  }

  function band(v) {
    if (!v) return na();
    return `<strong class="${BAND_CLS[v] || ''}">${esc(v)}</strong>`;
  }

  function indexRow(ix) {
    if (!ix || ix.status !== 'ok') {
      return `<div class="etf-kpi"><span class="etf-kpi-label">${esc((ix && ix.name) || '')}</span><strong class="etf-na">无数据</strong></div>`;
    }
    return `<div class="etf-kpi"><span class="etf-kpi-label">${esc(ix.name)} <small>${esc(ix.symbol)}</small></span>
      <strong>${fmt(ix.close, 2)} ${pct(ix.chg_pct, 2)}</strong>
      <span class="etf-kpi-sub">收盘 ${esc(ix.asof || '无数据')}</span></div>`;
  }

  function marketCard(m) {
    m = m || {};
    if (m.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="总市值"><h3>总市值 ${snapBadge(m)}</h3><p class="etf-na">${esc(m.reason || '无数据')}</p></section>`;
    }
    const b = m.buffett || {};
    const cap = isNum(m.total_cap_trillion) ? `${fmt(m.total_cap_trillion, 2)} 万亿美元` : '无数据';
    const buffett = b.status === 'ok' && isNum(b.ratio_pct)
      ? `<strong>${fmt(b.ratio_pct, 1)}%</strong><span class="etf-kpi-sub">历史分位 ${isNum(b.percentile) ? fmt(b.percentile, 1) + '%' : '无数据'} · GDP ${esc(b.gdp_period || '无数据')}${isNum(b.gdp_trillion) ? '（' + fmt(b.gdp_trillion, 2) + ' 万亿美元，SAAR）' : ''}</span>`
      : `<strong class="etf-na">无数据</strong>${b.reason ? `<span class="etf-kpi-sub">${esc(b.reason)}</span>` : ''}`;
    const indices = Object.values(m.indices || {}).map(indexRow).join('');
    return `<section class="panel etf-card" aria-label="总市值与巴菲特指标">
      <h3>总市值 ${snapBadge(m)} <span class="etf-asof">${esc(asofLine(m))}</span></h3>
      <div class="etf-kpis">
        <div class="etf-kpi"><span class="etf-kpi-label">美股全市场总市值</span><strong>${esc(cap)}</strong>
          <span class="etf-kpi-sub">Wilshire 5000（^W5000） ${isNum(m.w5000) ? fmt(m.w5000, 2) : '无数据'} 点 × 10 亿美元</span></div>
        <div class="etf-kpi"><span class="etf-kpi-label">巴菲特指标（总市值/GDP）</span>${buffett}</div>
        ${indices}
      </div>
      <p class="sub">${esc(m.method || '')}</p>
    </section>`;
  }

  function leverageCard(lv) {
    lv = lv || {};
    if (lv.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="杠杆率"><h3>杠杆率 ${snapBadge(lv)}</h3><p class="etf-na">${esc(lv.reason || '无数据')}</p></section>`;
    }
    const debit = isNum(lv.debit_trillion) ? `${fmt(lv.debit_trillion, 2)} 万亿美元` : (isNum(lv.debit_usd_millions) ? fmt(lv.debit_usd_millions, 0) + ' 百万美元' : '无数据');
    const peaks = (lv.peaks || []).map((pk) => {
      if (!pk || pk.status !== 'ok') return `<li>${esc(String((pk && pk.year) || ''))} 高点：无数据</li>`;
      return `<li>${esc(String(pk.year))} 高点（${esc(pk.peak_month)}，${fmt(pk.peak_usd_millions, 0)} 百万美元）相对变化 ${pct(pk.chg_pct, 1)}</li>`;
    }).join('') || '<li class="etf-na">无数据</li>';
    return `<section class="panel etf-card" aria-label="杠杆率">
      <h3>杠杆率 ${snapBadge(lv)} <span class="etf-asof">${esc(lv.month_label || lv.month || '无数据')}</span></h3>
      <div class="etf-kpis">
        <div class="etf-kpi"><span class="etf-kpi-label">FINRA 融资余额（margin debt）</span><strong>${esc(debit)}</strong>
          <span class="etf-kpi-sub">${esc(lv.units || '')}</span></div>
        <div class="etf-kpi"><span class="etf-kpi-label">环比 / 同比</span><strong>${pct(lv.mom_pct, 2)} / ${pct(lv.yoy_pct, 2)}</strong></div>
        <div class="etf-kpi"><span class="etf-kpi-label">占月末总市值</span>
          ${isNum(lv.cap_ratio_pct) ? `<strong>${fmt(lv.cap_ratio_pct, 2)}%</strong>` : na()}
          <span class="etf-kpi-sub">月末市值日期 ${esc(lv.cap_asof || '无数据')} · 历史分位 ${isNum(lv.cap_ratio_percentile) ? fmt(lv.cap_ratio_percentile, 1) + '%' : '无数据'}</span></div>
      </div>
      <ul class="etf-lines">${peaks}</ul>
    </section>`;
  }

  function vixCard(v) {
    v = v || {};
    if (v.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="恐慌指数"><h3>恐慌指数 ${snapBadge(v)}</h3><p class="etf-na">${esc(v.reason || '无数据')}</p></section>`;
    }
    return `<section class="panel etf-card" aria-label="恐慌指数">
      <h3>恐慌指数 ${snapBadge(v)} <span class="etf-asof">${esc(asofLine(v))}</span></h3>
      <div class="etf-kpis">
        <div class="etf-kpi"><span class="etf-kpi-label">VIX 收盘</span><strong>${fmt(v.close, 2)} ${signed(v.chg, 2)}</strong>
          <span class="etf-kpi-sub">1日变化 ${pct(v.chg_pct, 2)}（红＝上升）</span></div>
        <div class="etf-kpi"><span class="etf-kpi-label">期限结构 VIX9D / VIX / VIX3M</span>
          <strong>${esc(v.term || '无数据')}</strong>
          <span class="etf-kpi-sub">${esc(v.term_detail || '')}</span></div>
        <div class="etf-kpi"><span class="etf-kpi-label">VVIX</span>${isNum(v.vvix) ? `<strong>${fmt(v.vvix, 2)}</strong>` : na()}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">VIX 1年分位</span>
          ${isNum(v.percentile_1y) ? `<strong>${fmt(v.percentile_1y, 1)}%</strong>` : na()}
          <span class="etf-kpi-sub">近 ${esc(String(v.percentile_sample || ''))} 个交易日</span></div>
      </div>
    </section>`;
  }

  function judgmentCard(j, fromSnapshot, asof) {
    j = j || {};
    const line = j.line || '无数据';
    return `<section class="panel etf-card usrisk-judge" aria-label="综合判断">
      <h3>综合判断 ${fromSnapshot && asof ? `<span class="etf-snap">上次快照（${esc(asof)}）</span>` : ''} <span class="etf-asof">估值 / 杠杆 / 情绪</span></h3>
      <p class="etf-regime">${band(j.valuation)}　${band(j.leverage)}　${band(j.sentiment)}</p>
      <p>${esc(line)}</p>
      <p class="sub">${esc(j.rules || '')}</p>
    </section>`;
  }

  function sourcesBlock(p) {
    const errs = Object.entries(p.errors || {});
    return `<details class="panel etf-card"><summary>美股风险：数据来源、口径与局限</summary>
      <ul>${(p.notes || []).map((n) => `<li>${esc(n)}</li>`).join('')}</ul>
      <ul>${(p.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a></li>`).join('')}</ul>
      ${errs.length ? `<p class="etf-warn">本轮失败（已回退上次快照或显示无数据）：${errs.map(([k, v]) => esc(k) + '（' + esc(v) + '）').join('；')}</p>` : ''}
    </details>`;
  }

  function render(p, el) {
    state.data = p;
    if (!p || p.status !== 'ok') {
      el.innerHTML = `<section class="panel etf-card"><h3>美股风险</h3><p class="etf-na">无数据：美股风险指标获取失败或尚未生成快照，稍后重试。</p><p class="etf-warn">研究参考，不能下单。</p></section>`;
      return;
    }
    const snap = p.from_snapshot ? `<p class="etf-warn">刷新失败或沿用缓存，显示上次快照（${esc(p.asof || p.updated_at || '')}）</p>` : '';
    el.innerHTML = `
      <h2 class="usrisk-title">美股风险</h2>
      <p class="etf-warn">研究参考，不能下单，不构成投资建议。失败时显示「上次快照（日期）」或「无数据」，不填造数字。红＝上涨，绿＝下跌。</p>
      <p class="sub">数据日期 ${esc(p.asof || '无数据')} · 更新 ${esc(String(p.updated_at || '').replace('T', ' ').slice(0, 16))}${isNum(p.age_hours) ? ` · ${fmt(p.age_hours, 1)} 小时前` : ''}</p>
      ${snap}
      ${judgmentCard(p.judgment, p.from_snapshot, p.asof)}
      ${marketCard(p.market)}
      ${leverageCard(p.leverage)}
      ${vixCard(p.vix)}
      ${sourcesBlock(p)}`;
  }

  let loading = false, loadedAt = 0;
  async function load(force) {
    const el = root.document && root.document.getElementById('usrisk-content');
    if (!el || loading) return;
    if (!force && state.data && Date.now() - loadedAt < 60000) return;
    loading = true;
    const status = root.document.getElementById('usrisk-status');
    if (status) status.textContent = force ? '已请求美股风险后台刷新，先显示快照…' : '加载美股风险快照…';
    try {
      const url = isStatic() ? './api/us_risk.json' : (force ? '/api/us/risk/refresh' : '/api/us/risk');
      const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      render(data, el);
      loadedAt = Date.now();
      if (status) status.textContent = data.note ? String(data.note).replace(/^\s*·\s*/, '') : (data.refresh_status === 'started' ? '美股风险快照较旧，已在后台刷新' : '');
    } catch (e) {
      if (!state.data) el.innerHTML = `<p class="etf-na">无数据：${esc(e.message)}。重新进入本页可重试。</p>`;
      if (status) status.textContent = '美股风险加载失败：' + e.message;
    } finally {
      loading = false;
    }
  }

  function bind() {
    const btn = root.document.getElementById('usetf-refresh');
    if (!btn) return;
    if (isStatic()) return;
    btn.addEventListener('click', () => load(true));
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  }
  const api = { load, render, state, marketCard, leverageCard, vixCard, judgmentCard, cls, pct, signed };
  root.UsRiskView = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
