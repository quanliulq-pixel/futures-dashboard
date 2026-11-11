(function(root){
 'use strict';
 const pct=x=>Number.isFinite(x)?(100*x).toFixed(2)+'%':'无数据';
 const num=x=>Number.isFinite(x)?x.toFixed(2):'无数据';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const color=x=>x>0?'ngq-up':x<0?'ngq-down':'';
 function table(d){
  const names={strategy:'BOIL+UNG现金担保卖Put（模型）',BOIL:'100%持有BOIL',UNG:'100%持有UNG'};
  return `<p>${esc(d.start)} — ${esc(d.end)}</p><div class="table-wrap"><table class="ngq-table"><thead><tr><th>组合</th><th>累计</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>胜率</th><th>交易数</th><th>模型到期指派*</th><th>最差交易/担保金</th></tr></thead><tbody>${Object.entries(d.metrics).map(([k,m])=>`<tr><td>${names[k]}</td><td class="${color(m.total_return)}">${pct(m.total_return)}</td><td class="${color(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td><td>${pct(m.win_rate)}</td><td>${m.trade_count}</td><td>${k==='strategy'?m.assignment_count:'不适用'}</td><td>${pct(m.worst_trade)}</td></tr>`).join('')}</tbody></table></div>`;
 }
 function curve(points){
  const keys=['strategy','BOIL','UNG'],colors=['#f5c76a','#f87171','#8ab4f8'];
  const logs=points.flatMap(p=>keys.map(k=>Math.log(Math.max(p[k],1e-12))));
  const lo=Math.min(0,...logs), hi=Math.max(.01,...logs), y=v=>210-(v-lo)/(hi-lo)*175;
  return `<svg viewBox="0 0 780 245" role="img" aria-label="模型卖Put、BOIL、UNG净值对比，对数纵轴"><title>黄色模型卖Put、红色BOIL、蓝色UNG。对数纵轴。</title>${[lo,(lo+hi)/2,hi].map(v=>`<text x="3" y="${y(v)}" fill="#b8c7dd" font-size="11">${Math.exp(v).toPrecision(2)}</text>`).join('')}${keys.map((k,i)=>`<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${points.map((p,j)=>`${55+690*j/Math.max(points.length-1,1)},${y(Math.log(Math.max(p[k],1e-12)))}`).join(' ')}"/>`).join('')}<text x="55" y="237" fill="#b8c7dd" font-size="11">${esc(points[0].date)}</text><text x="650" y="237" fill="#b8c7dd" font-size="11">${esc(points.at(-1).date)}</text></svg>`;
 }
 function liveCard(x){
  const q=x.quote;
  return `<article class="ngq-put-card"><h3>${esc(x.symbol)} · ${esc(x.action)}</h3><p>${esc(x.reason)}</p><p class="sub">${esc(x.chain_status)} · 更新 ${esc(x.updated_at)}（JST）</p><p>标的最近完整日线 ${num(x.spot)} USD / HV20 ${pct(x.hv)}</p>${q?`<div class="table-wrap"><table class="ngq-table"><thead><tr><th>真实合约（非下单建议）</th><th>到期</th><th>行权价</th><th>Bid / Ask</th><th>IV / Δ*</th><th>权利金/担保金</th><th>盈亏平衡</th><th>1张担保金USD</th></tr></thead><tbody><tr><td>${esc(q.contract)}</td><td>${esc(q.expiry)} (${q.dte}天)</td><td>${num(q.strike)}</td><td>${num(q.bid)} / ${num(q.ask)}</td><td>${pct(q.iv)} / ${num(q.delta)}</td><td>${pct(q.premium_yield)}</td><td>${num(q.breakeven)}</td><td>${num(q.collateral_per_contract)}</td></tr></tbody></table></div><p class="sub">最近交易 ${esc(q.last_trade_at)}（JST），不是报价时标。Δ为真实IV计算的欧式近似；收益率按bid计算、未扣费。资金比例上限 ${pct(q.collateral_weight)}；账户不足以100股整数合约现金担保时不卖。</p>`:'<p>候选行权价 / 到期 / Bid / Ask / IV / Δ / 收益率 / 盈亏平衡：无数据（没有满足规则的真实链候选，不用模型报价冒充）。</p>'}${(x.chain_sample||[]).length?`<details><summary>真实链示例（未通过全套筛选，不是推荐）</summary><ul>${x.chain_sample.map(r=>`<li>到期 ${esc(r.expiry)}，Put ${num(r.strike)}，bid/ask ${num(r.bid)} / ${num(r.ask)}，IV ${pct(r.iv)}，Δ约 ${num(r.delta)}</li>`).join('')}</ul><p class="sub">基于最近完整收盘价计算Δ，不是实时标的价格；未核验报价时标、事件日历，不可据此下单。</p></details>`:''}<p class="ngq-warning">${esc(x.event_calendar)}。事件未核验，默认不卖；请人工核验并重新获取报价。</p></article>`;
 }
 function render(d,el){
  if(d.status!=='ok'){el.textContent='无数据：卖Put日线与模型快照尚未生成。';return;}
  el.innerHTML=`<p class="ngq-warning"><strong>${esc(d.label)}</strong>。历史IV、历史挂牌期权与美式提前指派无数据，以下不是历史可成交收益。</p><p class="sub">更新 ${esc(d.updated_at)}（JST） · 最近完整美股交易日 ${esc(d.asof_session)} · 非实时信号</p>${(d.data_quality||[]).map(x=>`<p class="sub">${esc(x)}</p>`).join('')}${(d.live||[]).map(liveCard).join('')}${d.in_sample.metrics.strategy.total_return<0&&d.out_of_sample.metrics.strategy.total_return<0?'<p class="ngq-warning">此模型在样本内与留出检验均亏损，尚未验证盈利能力。</p>':''}${['in_sample','out_of_sample'].map((k,i)=>`<section class="ngq-result"><h3>${i?'2024起留出检验（事后）':'2017–2023样本内'} · 模型定价回测</h3>${table(d[k])}<p class="sub">净值（对数纵轴）：黄色模型卖Put / 红色BOIL / 蓝色UNG。担保金总额≤20%，持有对照100%，敞口不同。</p>${curve(d[k].curve)}</section>`).join('')}<p class="ngq-warning">* 模型到期指派不含美式提前行权；0次不能解释为真实无指派。最差交易收益按该笔担保金计算，不是期权权利金回报。</p><details><summary>模型假设、费用、数据与执行局限</summary><ul>${d.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>数据：Yahoo Finance真实标的历史与当前期权链；期权回测为独立模型，不是Yahoo历史期权成交。</p></details>`;
 }
 let loading=false,last=0;
 async function load(){
  const el=document.getElementById('ng-put-quant-content');
  if(!el||loading||Date.now()-last<60000)return;
  loading=true;
  try{
   const response=await fetch(root.STATIC_MODE?'./api/ng_sell_put.json':'/api/ng-sell-put',{signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const d=await response.json();
   if(d.updated_at&&Date.now()-new Date(d.updated_at).getTime()>86400000){
    (d.live||[]).forEach(x=>{x.action='不卖';x.quote=null;x.chain_sample=[];x.chain_status='无数据：期权链快照超过24小时';});
   }
   render(d,el);last=Date.now();
  }catch(e){el.textContent='无数据：'+e.message+'。重新进入美气NG可重试。';}finally{loading=false;}
 }
 if(typeof document!=='undefined')document.addEventListener('click',e=>{if(e.target.closest('[data-view="ng"]'))load();});
 if(typeof module!=='undefined')module.exports={pct,table,curve,liveCard};
})(typeof window!=='undefined'?window:globalThis);
