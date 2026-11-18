/* 黄金页：沪金期权卖Put研究。模型定价回测，非真实期权成交；研究参考，不能下单。 */
(function (root) {
  'use strict';
  const pct = (x) => Number.isFinite(x) ? (100 * x).toFixed(2) + '%' : '无数据';
  const num = (x, d) => Number.isFinite(x) ? x.toFixed(d == null ? 2 : d) : '无数据';
  const esc = (x) => String(x ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const color = (x) => (x > 0 ? 'up' : x < 0 ? 'down' : '');

  function table(d) {
    const names = { strategy: '沪金期权卖Put（模型，保证金占用≤20%）', AU0: '100%持有沪金主连 AU0' };
    return `<p>${esc(d.start)} — ${esc(d.end)}</p><div class="table-wrap" tabindex="0" aria-label="黄金卖Put回测绩效表"><table class="gold-table"><thead><tr><th>组合</th><th class="num">累计</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">Sharpe</th><th class="num">胜率</th><th class="num">交易数</th><th class="num">模型到期指派*</th><th class="num">最差单笔/保证金</th></tr></thead><tbody>${Object.entries(d.metrics).map(([k, m]) => `<tr><td>${names[k] || esc(k)}</td><td class="num ${color(m.total_return)}">${pct(m.total_return)}</td><td class="num ${color(m.cagr)}">${pct(m.cagr)}</td><td class="num down">${pct(m.max_drawdown)}</td><td class="num">${num(m.sharpe)}</td><td class="num">${pct(m.win_rate)}</td><td class="num">${m.trade_count}</td><td class="num">${k === 'strategy' ? (Number.isFinite(m.assignment_count) ? m.assignment_count : '无数据') : '不适用'}</td><td class="num">${k === 'strategy' ? pct(m.worst_trade) : '不适用'}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function curve(points) {
    if (!points || points.length < 2) return '<p class="gold-na">净值曲线：无数据</p>';
    const keys = ['strategy', 'AU0'], colors = ['#f5c76a', '#f87171'];
    const logs = points.flatMap((p) => keys.map((k) => Math.log(Math.max(p[k], 1e-12))));
    const lo = Math.min(0, ...logs), hi = Math.max(0.01, ...logs), y = (v) => 210 - (v - lo) / (hi - lo) * 175;
    return `<svg viewBox="0 0 780 245" role="img" aria-label="模型卖Put与AU0买入持有净值对比，对数纵轴"><title>黄色模型卖Put、红色AU0买入持有。对数纵轴。</title>${[lo, (lo + hi) / 2, hi].map((v) => `<text x="3" y="${y(v)}" fill="#b8c7dd" font-size="11">${Math.exp(v).toPrecision(2)}</text>`).join('')}${keys.map((k, i) => `<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${points.map((p, j) => `${55 + 690 * j / Math.max(points.length - 1, 1)},${y(Math.log(Math.max(p[k], 1e-12)))}`).join(' ')}"/>`).join('')}<text x="55" y="237" fill="#b8c7dd" font-size="11">${esc(points[0].date)}</text><text x="650" y="237" fill="#b8c7dd" font-size="11">${esc(points.at(-1).date)}</text></svg>`;
  }

  function liveCard(x) {
    const q = x.quote;
    const stale = x.chain_status && String(x.chain_status).includes('上次快照') ? ` · ${esc(x.chain_status)}` : '';
    return `<article class="gold-card gold-put-card" aria-label="黄金期权卖Put候选"><h3>${esc(x.name || x.symbol)} · ${esc(x.action)}</h3>
      <p>${esc(x.reason)}</p>
      <p class="sub">${esc(x.chain_status)} · 更新 ${esc(String(x.updated_at || '').replace('T', ' ').slice(0, 19))}（北京时间）${stale}</p>
      <p>AU0 最近完整日线 ${num(x.spot)} 元/克 · HV20 ${pct(x.hv)} · 趋势分 T ${num(x.trend_T, 2)}${x.spot_used && x.spot_used !== x.spot ? ` · 所用标的期货最新 ${num(x.spot_used)}（${esc(x.futures_month || '')}）` : ''}</p>
      ${q ? `<div class="table-wrap" tabindex="0" aria-label="黄金期权候选合约"><table class="gold-table"><thead><tr><th>真实合约（非下单建议）</th><th>到期（估算）</th><th class="num">行权价</th><th class="num">虚值</th><th class="num">买 / 卖</th><th class="num">近似IV / Δ*</th><th class="num">权利金/保证金</th><th class="num">年化估算</th><th class="num">1手保证金估算（元）</th></tr></thead><tbody><tr><td>${esc(q.contract)}</td><td>${esc(q.expiry)}（${q.dte}天）</td><td class="num">${num(q.strike)}</td><td class="num">${pct(q.otm_pct)}</td><td class="num">${num(q.bid)} / ${num(q.ask)}</td><td class="num">${pct(q.iv)} / ${num(q.delta)}</td><td class="num">${pct(q.premium_yield)}</td><td class="num">${pct(q.annualized_yield)}</td><td class="num">${num(q.margin_per_lot, 0)}</td></tr></tbody></table></div>
        <p class="sub">Δ 与 IV 由买卖中间价反推 Black-76，是近似，不是交易所官方隐含波动率。收益率按买价、未扣费；保证金按上期所卖方公式、期货保证金率 10% 估算。到期日按「交割月前一月倒数第5个交易日」估算，未核验假期调整。资金比例上限 ${pct(q.collateral_weight)}；账户不足以 1 手保证金时不卖。</p>` : '<p class="gold-na">候选行权价 / 到期 / 买价 / 卖价 / 近似IV / Δ / 收益率 / 保证金：无数据（没有满足规则的真实链候选，不用模型报价冒充）。</p>'}
      ${(x.chain_sample || []).length ? `<details><summary>真实链示例（未通过全套筛选，不是推荐）</summary><ul>${x.chain_sample.map((r) => `<li>${esc(r.contract || '')} 到期 ${esc(r.expiry)}，Put ${num(r.strike)}，买/卖 ${num(r.bid)} / ${num(r.ask)}，近似IV ${pct(r.iv)}，Δ约 ${num(r.delta)}</li>`).join('')}</ul><p class="sub">基于标的最新价或最近完整收盘计算 Δ，不是可成交报价；未核验事件日历，不可据此下单。</p></details>` : ''}
      <p class="gold-warn">${esc(x.event_calendar || '无数据')}。事件未核验，默认不卖；请人工核验并重新获取报价。</p>
    </article>`;
  }

  function render(d, el) {
    if (!d || d.status !== 'ok') {
      el.innerHTML = '<section class="panel gold-card"><h3>沪金期权 · 卖Put研究</h3><p class="gold-na">无数据：卖Put日线与模型快照尚未生成。</p></section>';
      return;
    }
    const bothLoss = d.in_sample && d.out_of_sample
      && d.in_sample.metrics.strategy.total_return < 0
      && d.out_of_sample.metrics.strategy.total_return < 0;
    el.innerHTML = `<section class="panel gold-card gold-put-head" aria-label="沪金期权卖Put研究">
      <h3>沪金期权 · 卖Put研究 <span class="gold-asof">更新 ${esc(String(d.updated_at || '').replace('T', ' ').slice(0, 16))}（北京时间）${d.stale ? ' · 上次快照（' + esc(d.asof_session || '') + '）' : ''}</span></h3>
      <p class="gold-warn"><strong>${esc(d.label)}</strong>。${esc(d.disclaimer || '研究参考，不能下单')}。历史IV、历史挂牌期权成交无数据，以下不是历史可成交收益。回测为 Black-Scholes / Black-76 + 历史波动率近似。</p>
      <p class="sub">最近完整沪金交易日 ${esc(d.asof_session)} · 黄金期权上市 ${esc(d.listed_on)} · 非实时信号</p>
      ${(d.data_quality || []).map((x) => `<p class="sub">${esc(x)}</p>`).join('')}
    </section>
    ${(d.live || []).map(liveCard).join('')}
    ${bothLoss ? '<p class="gold-warn">此模型在样本内与留出检验均亏损，尚未验证盈利能力。</p>' : ''}
    ${['in_sample', 'out_of_sample'].map((k, i) => `<section class="panel gold-card" aria-label="${i ? '留出检验' : '样本内'}回测"><h3>${i ? '2024起留出检验（事后）' : '2020–2023样本内'} · 模型定价回测</h3>${table(d[k])}<p class="sub">净值（对数纵轴）：黄色模型卖Put / 红色AU0买入持有。保证金占用≤20%，持有对照100%，敞口不同。曲线为实际交易日点，无插值。</p>${curve(d[k].curve)}</section>`).join('')}
    <p class="gold-warn">* 模型到期指派按内在价值结算；欧式期权仍可能行权得到期货。最差单笔按该笔保证金计算，不是权利金回报。</p>
    <details><summary>模型假设、费用、数据与执行局限</summary><ul>${(d.limitations || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      <p>数据：${(d.sources || []).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join('；')}。期权回测为独立模型，不是新浪历史期权成交。</p>
    </details>`;
  }

  function isStatic() {
    return root.STATIC_MODE === true || (typeof location !== 'undefined' && (/github\.io$/i.test(location.hostname) || location.search.includes('static=1')));
  }

  let loading = false, last = 0, cached = null;
  async function load(force) {
    const el = root.document && root.document.getElementById('gold-put-content');
    if (!el || loading) return;
    if (!force && cached && Date.now() - last < 60000) return;
    loading = true;
    try {
      const url = isStatic() ? './api/gold_put.json' : (force ? '/api/gold/put/refresh' : '/api/gold/put');
      const response = await fetch(typeof root.apiUrl === 'function' ? root.apiUrl(url) : url, { signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const d = await response.json();
      if (d.updated_at && Date.now() - new Date(d.updated_at).getTime() > 86400000) {
        d.stale = true;
        (d.live || []).forEach((x) => { x.action = '不卖'; x.quote = null; x.chain_sample = []; x.chain_status = '无数据：期权链快照超过24小时'; });
      }
      render(d, el);
      cached = d;
      last = Date.now();
    } catch (e) {
      if (!cached) el.innerHTML = `<section class="panel gold-card"><h3>沪金期权 · 卖Put研究</h3><p class="gold-na">无数据：${esc(e.message)}。重新进入黄金页可重试。</p></section>`;
    } finally {
      loading = false;
    }
  }

  const api = { load, render, table, curve, liveCard, pct, num };
  root.GoldPutView = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
