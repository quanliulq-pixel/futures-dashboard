(function(root){
 'use strict';
 const pct=x=>Number.isFinite(x)?(100*x).toFixed(2)+'%':'无数据';
 const num=x=>Number.isFinite(x)?x.toFixed(2):'无数据';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const color=x=>x>0?'ngq-up':x<0?'ngq-down':'';
 const names={UNG:'UNG看涨信用价差',BOIL:'BOIL看涨信用价差',combined:'UNG+BOIL等风险组合'};
 function table(variants,key){
  return `<div class="table-wrap"><table class="ngq-table"><thead><tr><th>模型组合</th><th>累计</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>胜率</th><th>交易数</th><th>最差单笔/风险准备金</th><th>最差单笔/期初账户</th></tr></thead><tbody>${Object.entries(variants).map(([name,segments])=>{const m=segments[key].metrics;return `<tr><td>${esc(names[name]||name)}</td><td class="${color(m.total_return)}">${pct(m.total_return)}</td><td class="${color(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td><td>${pct(m.win_rate)}</td><td>${m.trade_count}</td><td class="ngq-down">${pct(m.worst_trade)}</td><td class="ngq-down">${pct(m.worst_trade_account)}</td></tr>`;}).join('')}</tbody></table></div>`;
 }
 function curve(variants,key){
  const entries=Object.entries(variants),values=entries.flatMap(([,s])=>s[key].curve.map(x=>x.strategy));
  if(!values.length)return '无数据';
  const lo=Math.min(1,...values),hi=Math.max(1.001,...values),y=v=>205-(v-lo)/(hi-lo)*170,colors=['#8ab4f8','#f5c76a','#c084fc'];
  return `<svg viewBox="0 0 780 235" role="img" aria-label="卖Call价差模型净值，线性纵轴，起点1"><title>蓝色UNG、黄色BOIL、紫色组合；线性纵轴，起点1，非真实期权收益。</title>${[lo,(lo+hi)/2,hi].map(v=>`<text x="2" y="${y(v)}" fill="#b8c7dd" font-size="11">${v.toFixed(3)}</text>`).join('')}${entries.map(([name,s],i)=>`<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${s[key].curve.map((p,j)=>`${55+690*j/Math.max(s[key].curve.length-1,1)},${y(p.strategy)}`).join(' ')}"/>`).join('')}<text x="55" y="231" fill="#b8c7dd" font-size="11">${esc(entries[0][1][key].start)}</text><text x="640" y="231" fill="#b8c7dd" font-size="11">${esc(entries[0][1][key].end)}</text></svg>`;
 }
 function liveCard(x){
  return `<article class="ngq-put-card"><h3>${esc(x.symbol)} · ${esc(x.action)}（不裸卖）</h3><p>纸面模型：${esc(x.paper_signal)} · ${esc(x.reason)}</p><p class="sub">信号基准 ${esc(x.asof_session)} · 标的收盘 ${num(x.spot)} USD / HV20 ${pct(x.hv)}</p><p>${esc(x.chain_status)}</p><p>真实短腿/保护腿行权价、到期、Bid/Ask、IV、Delta、净权利金及整数合约资金：无数据。</p><p class="ngq-warning">${esc(x.gate)}</p></article>`;
 }
 function stress(variants,key){
  return `<div class="table-wrap"><table class="ngq-table"><thead><tr><th>组合 / 固定压力情景</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th></tr></thead><tbody>${Object.entries(variants).flatMap(([name,s])=>Object.entries(s[key].stress).map(([scenario,m])=>`<tr><td>${esc(names[name])} / ${scenario==='iv_hv_1_0'?'IV/HV=1.0':'费用×2'}</td><td class="${color(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td></tr>`)).join('')}</tbody></table></div>`;
 }
 function tails(variants,key){
  return Object.entries(variants).map(([name,s])=>`<h4>${esc(names[name])} · 2022尖峰 / 冬季压力</h4><div class="table-wrap"><table class="ngq-table"><thead><tr><th>实际覆盖区间</th><th>盯市收益</th><th>区间回撤</th><th>区间平仓笔数</th><th>最差单笔/风险准备金</th></tr></thead><tbody>${s[key].tail_windows.map(t=>`<tr><td>${esc(t.label)} (${esc(t.start)}—${esc(t.end)})</td><td class="${color(t.total_return)}">${pct(t.total_return)}</td><td class="ngq-down">${pct(t.max_drawdown)}</td><td>${t.closed_trades}</td><td>${pct(t.worst_trade)}</td></tr>`).join('')}</tbody></table></div><h4>最差5笔（按风险准备金收益排序）</h4><div class="table-wrap"><table class="ngq-table"><thead><tr><th>标的 / 入场—退出</th><th>原因</th><th>单笔/风险准备金</th><th>单笔/期初账户</th></tr></thead><tbody>${s[key].worst_trades.map(t=>`<tr><td>${esc(t.symbol)} ${esc(t.entry)}—${esc(t.exit)}</td><td>${esc(t.reason)}</td><td class="ngq-down">${pct(t.risk_return)}</td><td class="ngq-down">${pct(t.pnl)}</td></tr>`).join('')}</tbody></table></div>`).join('');
 }
 function render(d,el){
  if(d.status!=='ok'){el.textContent='无数据：卖Call模型快照尚未生成。';return;}
  el.innerHTML=`<p class="ngq-warning"><strong>${esc(d.label)}</strong>。不是实际期权历史收益。</p><p class="sub">研究生成 ${esc(d.updated_at)}（JST），冻结归档；最近共同完整交易日 ${esc(d.asof_session)}，不是今天。</p>${d.live.map(liveCard).join('')}<p class="ngq-warning">${esc(d.verdict)}</p>${['in_sample','out_of_sample'].map((key,i)=>`<section class="ngq-result"><h3>${i?'2024起事后留出':'2017–2023样本内'}</h3><p>${esc(d.variants.combined[key].start)} — ${esc(d.variants.combined[key].end)}</p>${table(d.variants,key)}<p class="sub">净值：蓝色UNG / 黄色BOIL / 紫色组合，线性纵轴。独立组合每笔风险0.5%，双标的合计1%，非等额权利金回报；未加入无限风险裸卖对照。</p>${curve(d.variants,key)}<details><summary>固定压力检验（IV假设与双倍费用）</summary>${stress(d.variants,key)}</details><details><summary>2022暴涨、冬季逼空与最差交易</summary><p class="sub">无交易或零损失不证明能承受未来逼空；冬季表显示实际覆盖日期。区间收益含盯市，最差单笔仅区间内平仓交易。</p>${tails(d.variants,key)}</details></section>`).join('')}<details><summary>完整固定规则、模型成本与真实风险</summary><ul>${d.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`;
 }
 let loading=false,last=0;
 async function load(){
  const el=document.getElementById('ng-call-quant-content');
  if(!el||loading||Date.now()-last<60000)return;
  loading=true;
  try{
   const response=await fetch(root.STATIC_MODE?'./api/ng_sell_call.json':'/api/ng-sell-call',{signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const d=await response.json();
   if(d.asof_session&&Date.now()-new Date(d.asof_session+'T00:00:00+09:00').getTime()>=6*86400000){
    (d.live||[]).forEach(x=>{x.paper_signal='空仓';x.reason='无数据：日线超过5个日历日，禁用纸面信号';});
   }
   render(d,el);last=Date.now();
  }catch(e){el.textContent='无数据：'+e.message+'。重新进入美气NG可重试。';}finally{loading=false;}
 }
 if(typeof document!=='undefined')document.addEventListener('click',e=>{if(e.target.closest('[data-view="ng"]'))load();});
 if(typeof module!=='undefined')module.exports={pct,table,curve,liveCard,render,stress,tails};
})(typeof window!=='undefined'?window:globalThis);
