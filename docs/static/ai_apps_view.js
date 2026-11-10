/* Curated AI application research. No quotes, live scraping, or scoring side effects. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIAppsView = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const esc = value => String(value == null ? '无数据' : value).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  function safeUrl(value) {
    try { const u = new URL(value); return /^https?:$/.test(u.protocol) ? esc(u.href) : ''; }
    catch (_) { return ''; }
  }
  function sourceLinks(ids, sources) {
    return (ids || []).map(id => {
      const s = sources.find(x => x.id === id);
      const url = s && safeUrl(s.url);
      return url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${esc(s.title)} · ${esc(s.published_at)}</a>` : '无数据（来源缺失）';
    }).join(' · ');
  }
  function filterCompanies(items, filters) {
    return items.filter(c => (!filters.market || filters.market === 'all' || c.market === filters.market)
      && (!filters.tier || filters.tier === 'all' || c.tier === filters.tier)
      && (!filters.segment || filters.segment === 'all' || c.segment_id === filters.segment));
  }
  function companyHTML(c, data) {
    const segment = data.segments.find(s => s.id === c.segment_id);
    const tierClass = {'领先':'leader', '观察':'watch', '高风险':'risk'}[c.tier] || 'watch';
    return `<article class="ai-company panel">
      <div class="ai-company-top"><span class="ai-tier ai-tier-${tierClass}">${esc(c.tier)}</span><span class="sub">${esc(c.market)} · ${esc(segment ? segment.name : '无数据')}</span></div>
      <h4>${esc(c.name)} <small>${esc(c.ticker)}</small></h4>
      <p><b>可能赢在哪里（判断）</b>${esc(c.thesis)}</p>
      <div class="ai-evidence"><b>已核验的披露</b><span class="sub">${esc(c.evidence_kind)} · ${esc(c.evidence_period)}</span><p>${esc(c.evidence)}</p>${sourceLinks(c.source_ids, data.sources)}</div>
      <p class="ai-revenue"><b>独立 / 明确口径 AI 收入</b>${esc(c.ai_revenue)}</p>
      <details><summary>关键风险、验证点与政策</summary><p><b>风险</b>${esc(c.risks)}</p><p><b>下一步验证</b>${esc(c.next_check)}</p><p><b>中国政策语境</b>${esc(c.policy_context)}</p>${c.market !== '美股' ? sourceLinks(data.policy.source_ids, data.sources) : ''}</details>
    </article>`;
  }
  function segmentHTML(s, data) {
    return `<article class="ai-segment panel"><div class="ai-rank">${esc(s.rank).padStart(2, '0')}</div><div><h4>${esc(s.name)}</h4><span class="ai-maturity">${esc(s.monetization)}</span><p>${esc(s.rationale)}</p><p class="sub"><b>验证：</b>${esc(s.monitor)}</p><details><summary>反面情景与依据</summary><p>${esc(s.risks)}</p>${sourceLinks(s.source_ids, data.sources)}</details></div></article>`;
  }
  let payload = null;
  let loading = false;
  let bound = false;
  const $ = id => document.getElementById(id);
  function renderCompanies() {
    const list = filterCompanies(payload.companies, {market:$('ai-market').value, tier:$('ai-tier').value, segment:$('ai-segment').value});
    $('ai-count').textContent = `${list.length} / ${payload.companies.length} 家候选`;
    $('ai-companies').innerHTML = list.length ? list.map(c => companyHTML(c, payload)).join('') : '<p class="ai-notice">无数据：该筛选组合没有候选，不代表该板块没有潜力。</p>';
  }
  function render(data) {
    $('ai-heading').textContent = data.title;
    $('ai-summary').textContent = data.summary;
    $('ai-asof').textContent = `研究截至 ${data.as_of}（JST / Asia/Tokyo） · 人工快照 · ${data.segments.length} 个板块 / ${data.companies.length} 家候选 · 来源日期见各卡片`;
    $('ai-disclaimer').textContent = data.disclaimer;
    $('ai-method-body').innerHTML = `<ul>${data.methodology.map(m => `<li>${esc(m)}</li>`).join('')}</ul><h4>中国政策背景</h4><p>${esc(data.policy.text)}</p>${sourceLinks(data.policy.source_ids, data.sources)}`;
    $('ai-segments').innerHTML = data.segments.map(s => segmentHTML(s, data)).join('');
    $('ai-segment').innerHTML = '<option value="all">全部板块</option>' + data.segments.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
    $('ai-sources').innerHTML = `<ul>${data.limitations.map(x => `<li>${esc(x)}</li>`).join('')}</ul><ol>${data.sources.map(s => `<li>${sourceLinks([s.id], data.sources)}<span class="sub"> · ${esc(s.kind)} · 核验 ${esc(s.accessed_at)} JST</span></li>`).join('')}</ol>`;
    renderCompanies();
    $('ai-content').hidden = false;
    $('ai-status').textContent = '研究资料已加载；不触发行情刷新。';
  }
  async function load() {
    if (loading || payload || typeof document === 'undefined' || !$('ai-content')) return;
    if (!bound) {
      ['ai-market', 'ai-tier', 'ai-segment'].forEach(id => $(id).addEventListener('change', renderCompanies));
      $('ai-retry').addEventListener('click', load);
      bound = true;
    }
    loading = true;
    $('ai-status').textContent = '加载 AI 应用研究快照…';
    $('ai-retry').hidden = true;
    try {
      const path = typeof apiUrl === 'function' ? apiUrl('/api/ai-apps') : '/api/ai-apps';
      const res = await fetch(path);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (data.schema_version !== 1 || !Array.isArray(data.companies) || !Array.isArray(data.segments) || !Array.isArray(data.sources)) throw new Error('研究资料格式不完整');
      payload = data;
      render(data);
    } catch (_) {
      payload = null;
      $('ai-content').hidden = true;
      $('ai-status').textContent = '无数据：AI 研究快照暂时无法读取，请重试；不会使用模拟数据。';
      $('ai-retry').hidden = false;
    } finally { loading = false; }
  }
  return {load, esc, safeUrl, sourceLinks, filterCompanies, companyHTML, segmentHTML};
});
