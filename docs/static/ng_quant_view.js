/* Independent lazy load: never waits for or refreshes the NG full-web dashboard. */
(function (root) {
  'use strict';
  const pct = x => Number.isFinite(x) ? (100 * x).toFixed(2) + '%' : '无数据';
  const num = x => Number.isFinite(x) ? x.toFixed(2) : '无数据';
  const esc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const color = x => Number.isFinite(x) ? (x > 0 ? 'ngq-up' : x < 0 ? 'ngq-down' : '') : '';
  function metricTable(split) {
    const names = {strategy:'规则策略（≤25%仓位）', BOIL:'买入持有 BOIL（100%）', KOLD:'买入持有 KOLD（100%）'};
    return `<p>${esc(split.start)} — ${esc(split.end)} · ${split.sessions}交易日 · 各段独立从净值1、空仓开始</p><div class="table-wrap"><table class="ngq-table"><thead><tr><th>组合</th><th>累计</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>胜率</th><th>交易数</th><th>平均持有日</th></tr></thead><tbody>${Object.entries(split.metrics).map(([k,m])=>`<tr><td>${names[k]}</td><td class="${color(m.total_return)}">${pct(m.total_return)}</td><td class="${color(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td><td>${pct(m.win_rate)}</td><td>${m.trade_count}</td><td>${num(m.avg_holding_days)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function curveSvg(points) {
    // Log scale preserves visibility when leveraged buy-and-hold decays by orders of magnitude.
    const keys = ['strategy','BOIL','KOLD'], colors = ['#f5c76a','#f87171','#4ade80'];
    const vals = points.flatMap(p=>keys.map(k=>Math.log(Math.max(p[k],1e-12))));
    let lo = Math.min(0,...vals), hi = Math.max(0,...vals);
    if (hi === lo) hi = lo + 1;
    const y = v => 210-(v-lo)/(hi-lo)*175;
    const paths = keys.map((k,i)=>`<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${points.map((p,j)=>`${55+j/Math.max(1,points.length-1)*690},${y(Math.log(Math.max(p[k],1e-12)))}`).join(' ')}"/>`).join('');
    const ticks = [lo, (lo+hi)/2, hi].map(v=>`<text x="3" y="${y(v)}" fill="#b8c7dd" font-size="11">${Math.exp(v).toPrecision(2)}</text>`).join('');
    return `<svg viewBox="0 0 780 245" role="img" aria-label="策略、BOIL、KOLD净值曲线，对数纵轴"><title>净值从1开始；黄色策略、红色BOIL、绿色KOLD。对数纵轴。</title>${ticks}<line x1="55" x2="745" y1="${y(0)}" y2="${y(0)}" stroke="#65748b" stroke-dasharray="4"/>${paths}<text x="55" y="237" fill="#b8c7dd" font-size="11">${esc(points[0].date)}</text><text x="650" y="237" fill="#b8c7dd" font-size="11">${esc(points[points.length-1].date)}</text></svg>`;
  }
  function render(data, el) {
    if (data.status !== 'ok' || !data.signal) { el.textContent='无数据：行情获取失败或尚未生成快照。稍后重新进入本页重试。'; return; }
    const s=data.signal;
    el.innerHTML=`<p class="sub">更新 ${esc(data.updated_at)}（JST） · 美股交易日 ${esc(data.asof_session)} · ${data.stale?'快照过期，当前信号禁用':'日线快照，非实时行情'}</p>
      ${(data.data_quality||[]).map(x=>`<p class="ngq-warning">${esc(x)}。使用最近完整共同交易日，不填造价格。</p>`).join('')}
      ${data.in_sample.metrics.strategy.total_return<0 && data.out_of_sample.metrics.strategy.total_return<0?'<p class="ngq-warning">此固定规则在样本内与留出检验均亏损，尚未验证盈利能力，请勿当作已验证实盘系统。</p>':''}
      <div class="ngq-signal"><div><span class="sub">最新日线方向 / 下一交易日参考</span><h3 class="${s.symbol==='BOIL'?'ngq-up':s.symbol==='KOLD'?'ngq-down':''}">${esc(s.action)}</h3></div><div>入场参考（USD）<strong>${num(s.entry_reference)}</strong></div><div>参考止损（USD）<strong>${num(s.stop_reference)}</strong></div><div>建议资金比例上限<strong>${pct(s.weight)}</strong></div></div>
      <p>${s.reasons.map(esc).join('；')}。NG近月 ${num(s.ng_price)}，20日年化波动 ${pct(s.ng_hv)}。</p>
      <p class="sub">参考价为最新收盘价，不是订单或保证成交价。次日开盘重算仓位；止损=实际入场价−2×前日ATR14。已有持仓按信号退出或最长持有10个交易日；退出当天不重新入场。</p>
      ${['in_sample','out_of_sample'].map((k,i)=>`<section class="ngq-result"><h3>${i?'留出检验（2024起，非前瞻验证）':'样本内（2017–2023）'}</h3>${metricTable(data[k])}<p class="sub">净值曲线（对数纵轴）：<span style="color:#f5c76a">策略</span> / <span class="ngq-up">BOIL</span> / <span class="ngq-down">KOLD</span></p>${curveSvg(data[k].curve)}</section>`).join('')}
      <details><summary>数据来源与局限</summary><ul>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>来源：${['BOIL','KOLD','NG=F'].map(s=>`<a target="_blank" rel="noopener noreferrer" href="https://finance.yahoo.com/quote/${encodeURIComponent(s)}/history/">Yahoo ${esc(s)}</a>`).join(' · ')} · <a target="_blank" rel="noopener noreferrer" href="https://www.proshares.com/our-etfs/leveraged-and-inverse/boil">ProShares BOIL</a> · <a target="_blank" rel="noopener noreferrer" href="https://www.proshares.com/our-etfs/leveraged-and-inverse/kold">ProShares KOLD</a></p></details>`;
  }
  let loading=false, loadedAt=0;
  async function load() {
    const el=document.getElementById('ng-quant-content');
    if (!el || loading || Date.now()-loadedAt<60000) return;
    loading=true;
    try {
      const res=await fetch(root.STATIC_MODE?'./api/ng_boil_kold.json':'/api/ng-boil-kold',{signal:AbortSignal.timeout(15000)});
      if (!res.ok) throw new Error('HTTP '+res.status);
      const data=await res.json();
      // Static snapshots must also disable stale actionable signals client-side.
      if (data.asof_session && Date.now()-new Date(data.asof_session+'T00:00:00+09:00').getTime()>6*86400000) {
        data.stale=true;
        data.signal={action:'空仓',symbol:'CASH',entry_reference:null,stop_reference:null,weight:0,reasons:['快照过期，当前信号无数据']};
      }
      render(data,el);
      loadedAt=Date.now();
    } catch (e) {el.textContent='无数据：'+e.message+'。重新进入美气NG可重试。';}
    finally {loading=false;}
  }
  if (typeof document!=='undefined') document.addEventListener('click',e=>{if(e.target.closest('[data-view="ng"]')) load();});
  if (typeof module!=='undefined') module.exports={pct,num,curveSvg,metricTable};
})(typeof window!=='undefined'?window:globalThis);
