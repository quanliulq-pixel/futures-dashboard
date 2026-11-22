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
  const state = { data: null, ddIndex: 'sp500' };

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
    const darkBand = j.dark ? `<span class="${({'偏多':'up','偏少':'down','中性':'etf-flat'})[j.dark] || ''}">暗盘${esc(j.dark)}</span>` : na('暗盘无数据');
    return `<section class="panel etf-card usrisk-judge" aria-label="综合判断">
      <h3>综合判断 ${fromSnapshot && asof ? `<span class="etf-snap">上次快照（${esc(asof)}）</span>` : ''} <span class="etf-asof">估值 / 杠杆 / 情绪 / 暗盘</span></h3>
      <p class="etf-regime">${band(j.valuation)}　${band(j.leverage)}　${band(j.sentiment)}　${darkBand}</p>
      <p>${esc(line)}</p>
      <p class="sub">${esc(j.rules || '')}</p>
    </section>`;
  }

  function daysText(n, missingLabel) {
    return isNum(n) ? `${n} 天` : (missingLabel || '无数据');
  }

  function highsGrid(ix) {
    const h = (ix && ix.highs) || {};
    if (!ix || ix.status !== 'ok' || !h || h.status !== 'ok') {
      return `<div class="etf-kpi"><span class="etf-kpi-label">${esc((ix && ix.name) || '')}</span><strong class="etf-na">无数据</strong></div>`;
    }
    return `<div class="etf-kpi"><span class="etf-kpi-label">${esc(ix.name)} <small>${esc(ix.symbol)}</small></span>
      <strong>${pct(h.dd_ath, 1)}</strong>
      <span class="etf-kpi-sub">历史最高收盘 ${isNum(h.ath) ? fmt(h.ath, 2) : '无数据'}（${esc(h.ath_date || '无数据')}）</span>
      <span class="etf-kpi-sub">今年最高 ${isNum(h.ytd_high) ? fmt(h.ytd_high, 2) : '无数据'}（${esc(h.ytd_high_date || '无数据')}）· 今年回撤 ${pct(h.dd_ytd, 1)}</span>
      <span class="etf-kpi-sub">最新 ${isNum(h.close) ? fmt(h.close, 2) : '无数据'} · ${esc(h.asof || '无数据')}</span></div>`;
  }

  function annualTable(ix) {
    const rows = (ix && ix.annual) || [];
    if (!rows.length) return '<p class="etf-na">无数据</p>';
    const body = rows.slice().reverse().map((r) => `<tr>
      <td>${esc(String(r.year))}${r.incomplete ? ' <small>截至 ' + esc(r.asof || '') + '</small>' : ''}</td>
      <td class="num">${pct(r.max_dd, 1)}</td>
      <td>${esc(r.peak_date || '无数据')}${isNum(r.peak) ? '（' + fmt(r.peak, 2) + '）' : ''}</td>
      <td>${esc(r.trough_date || '无数据')}${isNum(r.trough) ? '（' + fmt(r.trough, 2) + '）' : ''}</td>
      <td class="num">${pct(r.year_return, 1)}</td>
    </tr>`).join('');
    return `<div class="table-wrap" tabindex="0"><table class="etf-table usrisk-dd-table"><thead><tr>
      <th>年份</th><th class="num">年内最大回撤</th><th>高点日期</th><th>低点日期</th><th class="num">年度涨跌</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function majorTable(ix) {
    const rows = (ix && ix.major) || [];
    if (!rows.length) return '<p class="etf-na">无数据</p>';
    const body = rows.map((r) => `<tr>
      <td>${esc(r.label || (r.ongoing ? '当前回撤' : '回撤'))}${r.ongoing ? ' <small>进行中</small>' : ''}</td>
      <td>${esc(r.peak_date || '无数据')}${isNum(r.peak) ? '（' + fmt(r.peak, 2) + '）' : ''}</td>
      <td>${esc(r.trough_date || '无数据')}${isNum(r.trough) ? '（' + fmt(r.trough, 2) + '）' : ''}</td>
      <td class="num">${pct(r.dd, 1)}</td>
      <td class="num">${esc(daysText(r.duration_days))}</td>
      <td class="num">${r.ongoing ? na('未收复') : esc(daysText(r.recovery_days))}${!r.ongoing && r.recovery_date ? '<br><small>' + esc(r.recovery_date) + '</small>' : ''}</td>
      <td class="num">${isNum(r.vix_peak) ? fmt(r.vix_peak, 2) : '无数据'}${r.vix_peak_date ? '<br><small>' + esc(r.vix_peak_date) + '</small>' : ''}</td>
    </tr>`).join('');
    return `<div class="table-wrap" tabindex="0"><table class="etf-table usrisk-dd-table"><thead><tr>
      <th>事件</th><th>峰值</th><th>谷底</th><th class="num">跌幅</th><th class="num">持续天数</th><th class="num">恢复到前高</th><th class="num">VIX 峰值</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
  }


  function ownLine(o) {
    if (!o || o.status !== 'ok') return na((o && o.reason) || '无数据');
    const bits = [
      isNum(o.hh_direct_pct) ? `家庭直接 ${fmt(o.hh_direct_pct, 1)}%` : null,
      isNum(o.mf_etf_pct) ? `基金+ETF ${fmt(o.mf_etf_pct, 1)}%` : null,
      isNum(o.pension_pct) ? `养老金 ${fmt(o.pension_pct, 1)}%` : null,
      isNum(o.foreign_pct) ? `外资 ${fmt(o.foreign_pct, 1)}%` : null,
    ].filter(Boolean);
    return bits.length ? `${bits.join(' · ')}<br><small>季度 ${esc(o.quarter || '无数据')}</small>` : na();
  }

  function capCell(m) {
    if (!m || m.status !== 'ok') return na((m && m.reason) || '无数据');
    const cap = isNum(m.total_cap_trillion) ? `${fmt(m.total_cap_trillion, 2)} 万亿` : '无数据';
    const b = isNum(m.buffett_ratio_pct) ? `巴菲特 ${fmt(m.buffett_ratio_pct, 1)}%` : '巴菲特 无数据';
    const g = m.gdp_period ? `GDP ${esc(m.gdp_period)}` : 'GDP 无数据';
    return `${esc(cap)}<br><small>${esc(b)} · ${g}<br>${esc(m.asof || '')}</small>`;
  }

  function levCell(l) {
    if (!l || l.status !== 'ok') return na((l && l.reason) || '无数据');
    const d = isNum(l.debit_trillion) ? `${fmt(l.debit_trillion, 2)} 万亿` : (isNum(l.debit_usd_millions) ? fmt(l.debit_usd_millions, 0) + ' 百万美元' : '无数据');
    const r = isNum(l.cap_ratio_pct) ? `占市值 ${fmt(l.cap_ratio_pct, 2)}%` : '占市值 无数据';
    const mon = l.month || '无数据';
    const lag = (l.month_requested && l.month && l.month_requested !== l.month) ? `（${esc(l.month_requested)} 未公布，用 ${esc(mon)}）` : esc(mon);
    return `${esc(d)}<br><small>${esc(r)} · ${lag}</small>`;
  }

  function flowLine(z) {
    if (!z || z.status !== 'ok') return na((z && (z.reason || z.status)) || '无数据');
    const q = (z.quarters || []).length ? `季度 ${esc((z.quarters || []).join('、'))}` : '无季度';
    const v = (lab, x) => (isNum(x) ? `${lab} <span class="${cls(x)}">${x > 0 ? '+' : ''}${fmt(x, 0)}</span>` : `${lab} 无数据`);
    return `${v('家庭', z.hh_direct_usd_millions)}；${v('基金+ETF', z.mf_etf_usd_millions)}；${v('养老金', z.pension_usd_millions)}；${v('外资', z.foreign_usd_millions)}<br><small>${esc(q)} · 百万美元</small>`;
  }

  function havenGold(h) {
    if (!h || h.status !== 'ok') return na((h && h.reason) || '无数据');
    const p = h.peak && isNum(h.peak.close) ? fmt(h.peak.close, 1) : '无数据';
    const q = h.trough && isNum(h.trough.close) ? fmt(h.trough.close, 1) : '无数据';
    return `${esc(p)} → ${esc(q)} ${pct(h.chg_pct, 1)}<br><small>${esc((h.peak && h.peak.asof) || '')} → ${esc((h.trough && h.trough.asof) || '')}</small>`;
  }

  function havenYield(h) {
    if (!h || h.status !== 'ok') return na((h && h.reason) || '无数据');
    const p = h.peak && isNum(h.peak.close) ? fmt(h.peak.close, 2) + '%' : '无数据';
    const q = h.trough && isNum(h.trough.close) ? fmt(h.trough.close, 2) + '%' : '无数据';
    const pp = isNum(h.chg_pp) ? `<span class="${cls(h.chg_pp)}">${h.chg_pp > 0 ? '+' : ''}${h.chg_pp.toFixed(2)}pp</span>` : na();
    return `${esc(p)} → ${esc(q)} ${pp}<br><small>${esc((h.peak && h.peak.asof) || '')} → ${esc((h.trough && h.trough.asof) || '')}</small>`;
  }

  function cycleTable(ctx) {
    ctx = ctx || {};
    if (ctx.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="历次重大下跌对比"><h3>历次重大下跌对比 ${snapBadge(ctx)}</h3><p class="etf-na">${esc((ctx && ctx.reason) || '无数据')}</p></section>`;
    }
    const rows = (ctx.cycles || []).map((c) => {
      const haven = c.haven || {};
      const tlt = haven.tlt && haven.tlt.status === 'ok' ? `TLT ${pct(haven.tlt.chg_pct, 1)}` : 'TLT 无数据';
      const dark = c.dark || {};
      return `<tr>
        <td><strong>${esc(c.label || '')}</strong>${c.ongoing ? ' <small>进行中</small>' : ''}<br><small>${esc(c.peak_date || '')} → ${esc(c.trough_date || '')}</small></td>
        <td>${capCell((c.market || {}).peak)}<hr class="usrisk-hr">${capCell((c.market || {}).trough)}${isNum((c.market || {}).cap_chg_pct) ? `<br>${pct(c.market.cap_chg_pct, 1)}` : ''}</td>
        <td>${levCell((c.leverage || {}).peak)}<hr class="usrisk-hr">${levCell((c.leverage || {}).trough)}${isNum((c.leverage || {}).debit_chg_pct) ? `<br>余额 ${pct(c.leverage.debit_chg_pct, 1)}` : ''}</td>
        <td>${ownLine((c.ownership || {}).peak)}<hr class="usrisk-hr">${ownLine((c.ownership || {}).trough)}</td>
        <td>${flowLine(((c.flows || {}).z1))}
          <br><small>ICI ${((c.flows || {}).ici && (c.flows.ici.status === 'ok')) ? '有数据' : '无数据'}</small></td>
        <td>${havenGold(haven.gold)}</td>
        <td>${havenYield(haven.ust10y)}<br><small>${tlt}</small></td>
        <td>${darkCell(dark.peak)}<hr class="usrisk-hr">${darkCell(dark.trough)}</td>
      </tr>`;
    }).join('') || `<tr><td colspan="7" class="etf-na">无数据</td></tr>`;
    return `<section class="panel etf-card" aria-label="历次重大下跌对比">
      <h3>历次重大下跌对比 ${snapBadge(ctx)} <span class="etf-asof">${esc(asofLine(ctx))}</span></h3>
      <p class="sub">每一轮按标普500 峰值日→谷底日窗口。上格为峰值时（或峰值月/当时已公布季度），下格为谷底时。日期频率不同已标注。ICI 资金流向无数据，改列 Z.1 净买入。</p>
      <div class="table-wrap" tabindex="0"><table class="etf-table usrisk-dd-table usrisk-cycle-table"><thead><tr>
        <th>事件</th><th>总市值 / 巴菲特指标</th><th>杠杆率（FINRA）</th><th>持股比例（Z.1）</th><th>资金流向（期间累计）</th><th>黄金</th><th>10年美债 / TLT</th><th>暗盘 DIX / GEX / 场外占比</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
      <p class="sub">${esc(ctx.method || '')}</p>
    </section>`;
  }

  function currentExtrasCard(ctx) {
    const cur = (ctx && ctx.current) || {};
    if (!ctx || ctx.status !== 'ok') return '';
    const o = cur.ownership_latest || {};
    const g = cur.gold || {};
    const y = cur.ust10y || {};
    const tlt = cur.tlt || {};
    const fq = cur.flows_latest_quarter || {};
    const f4 = cur.flows_last_four_quarters || {};
    return `<section class="panel etf-card" aria-label="当前持股、资金流与避险">
      <h3>当前持股 / 资金流向 / 黄金与美债 <span class="etf-asof">${esc(cur.asof || ctx.asof || '无数据')}</span></h3>
      <div class="etf-kpis">
        <div class="etf-kpi"><span class="etf-kpi-label">持股结构（最新已公布季）</span>
          ${o.status === 'ok' ? `<strong>家庭直接 ${isNum(o.hh_direct_pct) ? fmt(o.hh_direct_pct, 1) + '%' : '无数据'}</strong>
          <span class="etf-kpi-sub">基金+ETF ${isNum(o.mf_etf_pct) ? fmt(o.mf_etf_pct, 1) + '%' : '无数据'} · 养老金 ${isNum(o.pension_pct) ? fmt(o.pension_pct, 1) + '%' : '无数据'} · 外资 ${isNum(o.foreign_pct) ? fmt(o.foreign_pct, 1) + '%' : '无数据'}</span>
          <span class="etf-kpi-sub">季度 ${esc(o.quarter || '无数据')} · 分母为 Z.1 公司股票总额</span>` : na(o.reason)}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">Z.1 净买入（最新季）</span>
          ${fq.status === 'ok' ? `<strong>${isNum(fq.mf_etf_usd_millions) ? `<span class="${cls(fq.mf_etf_usd_millions)}">${fq.mf_etf_usd_millions > 0 ? '+' : ''}${fmt(fq.mf_etf_usd_millions, 0)}</span> 百万美元（基金+ETF）` : '无数据'}</strong>
          <span class="etf-kpi-sub">${esc((fq.quarters || []).join('、') || '无数据')}</span>` : na(fq.reason)}
          <span class="etf-kpi-sub">ICI 无数据。近四季基金+ETF ${f4.status === 'ok' && isNum(f4.mf_etf_usd_millions) ? ((f4.mf_etf_usd_millions > 0 ? '+' : '') + fmt(f4.mf_etf_usd_millions, 0) + ' 百万美元') : '无数据'}</span></div>
        <div class="etf-kpi"><span class="etf-kpi-label">黄金 GC=F</span>
          ${g.status === 'ok' ? `<strong>${fmt(g.close, 1)} ${pct(g.chg_1d_pct, 2)}</strong>
          <span class="etf-kpi-sub">自本轮股市高点（${esc(cur.equity_peak_date || '无数据')}） ${isNum(g.from_equity_peak_chg_pct) ? pct(g.from_equity_peak_chg_pct, 1) : '无数据'} · ${esc(g.asof || '')}</span>` : na(g.reason)}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">10年期美债收益率</span>
          ${y.status === 'ok' ? `<strong>${fmt(y.close, 2)}% ${isNum(y.chg_1d_pp) ? `<span class="${cls(y.chg_1d_pp)}">${y.chg_1d_pp > 0 ? '+' : ''}${y.chg_1d_pp.toFixed(2)}pp</span>` : ''}</strong>
          <span class="etf-kpi-sub">自本轮股市高点 ${isNum(y.from_equity_peak_chg_pp) ? `<span class="${cls(y.from_equity_peak_chg_pp)}">${y.from_equity_peak_chg_pp > 0 ? '+' : ''}${y.from_equity_peak_chg_pp.toFixed(2)}pp</span>` : '无数据'} · TLT ${isNum(tlt.from_equity_peak_chg_pct) ? pct(tlt.from_equity_peak_chg_pct, 1) : '无数据'}</span>` : na(y.reason)}</div>
      </div>
      <p class="sub">${esc((o && o.note) || '')} ${esc(cur.flows_note || '')} ${esc(cur.haven_note || '')}</p>
    </section>`;
  }


  function sparkline(points, key) {
    const pts = (points || []).map((p) => (isNum(p.value) ? p.value : (isNum(p[key]) ? p[key] : null))).filter(isNum);
    if (pts.length < 2) return '<span class="etf-na">无数据</span>';
    const w = 120, h = 28, pad = 2;
    const lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    const span = hi - lo || 1;
    const coords = pts.map((v, i) => {
      const x = pad + (i * (w - pad * 2) / (pts.length - 1));
      const y = pad + (h - pad * 2) * (1 - (v - lo) / span);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    const last = pts[pts.length - 1];
    const first = pts[0];
    const clsName = last > first ? 'up' : last < first ? 'down' : '';
    return `<svg class="usrisk-spark ${clsName}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="${coords}" /></svg>`;
  }

  function darkCard(p) {
    const d = (p && p.dark) || {};
    if (d.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="暗盘"><h3>暗盘（暗池 / 场外成交） ${snapBadge(d)}</h3><p class="etf-na">${esc((d && d.reason) || '无数据')}</p></section>`;
    }
    const dg = d.dix_gex || {};
    const dix = dg.dix || {};
    const gex = dg.gex || {};
    const off = d.off_exchange || {};
    const ats = d.ats_weekly || {};
    const dixVal = dix.status === 'ok' && isNum(dix.value_pct)
      ? `<strong>${fmt(dix.value_pct, 2)}% ${isNum(dix.chg_1d_pp) ? `<span class="${cls(dix.chg_1d_pp)}">${dix.chg_1d_pp > 0 ? '+' : ''}${dix.chg_1d_pp.toFixed(2)}pp</span>` : ''}</strong>
         <span class="etf-kpi-sub">1年分位 ${isNum(dix.percentile_1y) ? fmt(dix.percentile_1y, 1) + '%' : '无数据'} · ${esc(dix.asof || '')}</span>
         <span class="etf-kpi-sub">DIX 高=暗池买盘偏多。近20日 ${sparkline(dix.spark)}</span>`
      : na(dix.reason);
    const gexVal = gex.status === 'ok' && isNum(gex.value_billion)
      ? `<strong>${gex.value_billion > 0 ? '+' : ''}${fmt(gex.value_billion, 2)} 十亿美元 ${isNum(gex.chg_1d_billion) ? `<span class="${cls(gex.chg_1d_billion)}">${gex.chg_1d_billion > 0 ? '+' : ''}${gex.chg_1d_billion.toFixed(2)}</span>` : ''}</strong>
         <span class="etf-kpi-sub">1年分位 ${isNum(gex.percentile_1y) ? fmt(gex.percentile_1y, 1) + '%' : '无数据'} · ${esc(gex.asof || '')}</span>
         <span class="etf-kpi-sub">正值或抑制波动，负值或放大波动。近20日 ${sparkline(gex.spark)}</span>`
      : na(gex.reason);
    const offVal = off.status === 'ok' && isNum(off.share_pct)
      ? `<strong>${fmt(off.share_pct, 2)}% ${isNum(off.chg_1d_pp) ? `<span class="${cls(off.chg_1d_pp)}">${off.chg_1d_pp > 0 ? '+' : ''}${off.chg_1d_pp.toFixed(2)}pp</span>` : ''}</strong>
         <span class="etf-kpi-sub">1年分位 ${isNum(off.percentile_1y) ? fmt(off.percentile_1y, 1) + '%' : '无数据'} · ${esc(off.asof || '')}</span>
         <span class="etf-kpi-sub">FINRA TRF 占全市场成交（含批发商/内化，不等于纯暗池）。近20日 ${sparkline(off.spark)}</span>`
      : na(off.reason);
    const tops = (ats.top_ats || []).slice(0, 5).map((a) => {
      if (!a) return '';
      const pcts = isNum(a.share_of_ats_pct) ? fmt(a.share_of_ats_pct, 1) + '%' : '无数据';
      return `${esc((a.name || a.mpid || '').replace(/^[A-Z0-9]{3,4}\s/, ''))} ${pcts}`;
    }).filter(Boolean).join(' · ') || '无数据';
    const symLine = (x, name) => {
      if (!x || x.status !== 'ok') return `${name} 无数据`;
      const sh = isNum(x.ats_shares) ? fmt(x.ats_shares, 0) + ' 股' : '无数据';
      return `${name} ATS ${sh}`;
    };
    const atsVal = ats.status === 'ok'
      ? `<strong>周次 ${esc(ats.week_start || '无数据')}</strong>
         <span class="etf-kpi-sub">${esc(ats.lag_note || '')}</span>
         <span class="etf-kpi-sub">前五大 ATS（占 NMS 暗池）：${esc(tops)}</span>
         <span class="etf-kpi-sub">${esc(symLine(ats.spy, 'SPY'))} · ${esc(symLine(ats.qqq, 'QQQ'))} · 占该 ETF 总成交比例无数据</span>`
      : na(ats.reason);
    return `<section class="panel etf-card" aria-label="暗盘">
      <h3>暗盘（暗池 / 场外成交） ${snapBadge(d)} <span class="etf-asof">${esc(asofLine(d))}</span></h3>
      <div class="etf-kpis">
        <div class="etf-kpi"><span class="etf-kpi-label">DIX 暗池指数</span>${dixVal}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">GEX gamma 敞口</span>${gexVal}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">场外成交占比（FINRA TRF）</span>${offVal}</div>
        <div class="etf-kpi"><span class="etf-kpi-label">FINRA ATS 周度（暗池）</span>${atsVal}</div>
      </div>
      <p class="sub">${esc((dg.note || '') + ' ' + (off.note || '') + ' ' + (ats.note || ''))}</p>
    </section>`;
  }

  function darkCell(side) {
    if (!side) return na();
    const dix = side.dix || {};
    const gex = side.gex || {};
    const off = side.off_exchange || {};
    const d = dix.status === 'ok' && isNum(dix.value_pct) ? `DIX ${fmt(dix.value_pct, 2)}%` : 'DIX 无数据';
    const g = gex.status === 'ok' && isNum(gex.value_billion) ? `GEX ${gex.value_billion > 0 ? '+' : ''}${fmt(gex.value_billion, 2)} 十亿` : 'GEX 无数据';
    const o = off.status === 'ok' && isNum(off.share_pct) ? `场外 ${fmt(off.share_pct, 2)}%` : '场外 无数据';
    const ds = [dix.asof, gex.asof, off.asof].filter(Boolean)[0] || '';
    return `${esc(d)}<br>${esc(g)}<br>${esc(o)}${ds ? `<br><small>${esc(ds)}</small>` : ''}`;
  }

  function drawdownCard(p) {
    const dd = p.drawdowns || {};
    if (dd.status !== 'ok') {
      return `<section class="panel etf-card" aria-label="历年回撤"><h3>历年回撤与高点 ${snapBadge(dd)}</h3><p class="etf-na">${esc((dd && dd.reason) || '无数据')}</p></section>`;
    }
    const items = dd.indices || [];
    const curKey = items.some((x) => x.key === state.ddIndex) ? state.ddIndex : ((items[0] && items[0].key) || 'sp500');
    const ix = items.find((x) => x.key === curKey) || items[0];
    const chips = items.map((x) =>
      `<button type="button" class="etf-chip${x.key === curKey ? ' active' : ''}" data-dd-index="${esc(x.key)}" aria-pressed="${x.key === curKey}">${esc(x.name)}</button>`
    ).join('');
    const highs = items.map(highsGrid).join('');
    const startNote = ix && ix.annual_from ? `${esc(ix.name)} 历年表自 ${esc(String(ix.annual_from))} 年` : (ix ? `${esc(ix.name)} 历年表从有数据起（${esc(ix.history_start || '无数据')}）` : '');
    return `<section class="panel etf-card" aria-label="历年回撤与高点">
      <h3>历年回撤与高点 ${snapBadge(dd)} <span class="etf-asof">${esc(asofLine(dd))}</span></h3>
      <div class="etf-kpis">${highs || '<p class="etf-na">无数据</p>'}</div>
      <p>${esc(dd.overview || (dd.lines || []).join('；') || '无数据')}</p>
      <div class="etf-chips" role="tablist" aria-label="选择指数">${chips}</div>
      <h4 class="usrisk-sub">${esc((ix && ix.name) || '')} 历年年内最大回撤 <span class="etf-asof">${esc(startNote)}</span></h4>
      ${annualTable(ix)}
      <h4 class="usrisk-sub">${esc((ix && ix.name) || '')} 历史重大回撤</h4>
      ${majorTable(ix)}
      <p class="sub">${esc(dd.method || '')}</p>
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
      ${darkCard(p)}
      ${drawdownCard(p)}
      ${cycleTable(p.event_context)}
      ${currentExtrasCard(p.event_context)}
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

  function onClick(ev) {
    const t = ev.target && ev.target.closest ? ev.target.closest('[data-dd-index]') : null;
    if (!t) return;
    state.ddIndex = t.getAttribute('data-dd-index') || 'sp500';
    const el = root.document.getElementById('usrisk-content');
    if (el && state.data) render(state.data, el);
  }

  function bind() {
    const el = root.document.getElementById('usrisk-content');
    if (el) el.addEventListener('click', onClick);
    const btn = root.document.getElementById('usetf-refresh');
    if (!btn) return;
    if (isStatic()) return;
    btn.addEventListener('click', () => load(true));
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  }
  const api = { load, render, state, marketCard, leverageCard, vixCard, judgmentCard, darkCard, drawdownCard, cycleTable, currentExtrasCard, highsGrid, annualTable, majorTable, cls, pct, signed };
  root.UsRiskView = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
