(function(root){
 'use strict';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const pct=x=>Number.isFinite(x)?(100*x).toFixed(2)+'%':'无数据';
 const num=x=>Number.isFinite(x)?x.toFixed(3):'无数据';
 const cls=x=>x>0?'ngq-up':x<0?'ngq-down':'';
 function row(label,period){const m=period.metrics;return `<tr><td>${esc(label)}</td><td>${esc(period.start)}—${esc(period.end)}</td><td class="${cls(m.total_return)}">${pct(m.total_return)}</td><td class="${cls(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td><td>${m.trades}</td><td>${m.round_trips}</td></tr>`;}
 function curve(points){
  const vals=points.map(p=>p.equity),lo=Math.min(.95,...vals),hi=Math.max(1.05,...vals),y=v=>210-175*(v-lo)/(hi-lo);
  return `<svg viewBox="0 0 780 245" role="img" aria-label="留出期净值，线性纵轴"><title>2024起留出净值，起点1；黄色是实验策略，不是预测。</title>${[lo,(lo+hi)/2,hi].map(v=>`<text x="3" y="${y(v)}" fill="#b8c7dd" font-size="11">${v.toFixed(2)}</text>`).join('')}<line x1="55" x2="745" y1="${y(1)}" y2="${y(1)}" stroke="#65748b" stroke-dasharray="4"/><polyline fill="none" stroke="#f5c76a" stroke-width="2" points="${points.map((p,i)=>`${55+690*i/Math.max(1,points.length-1)},${y(p.equity)}`).join(' ')}"/><text x="55" y="237" fill="#b8c7dd" font-size="11">${esc(points[0].date)}</text><text x="650" y="237" fill="#b8c7dd" font-size="11">${esc(points.at(-1).date)}</text></svg>`;
 }
 function modelSignal(c,stale){
  if(stale)return '<p class="ngq-warning">无数据：价格归档超过5日，纸面信号已禁用；历史研究结果保留。</p>';
  const positions=c.model_state.positions||[];
  const state=positions.length?positions.map(p=>`${p.weight<0?'卖空':'做多'}${esc(p.symbol)} ${pct(Math.abs(p.weight))}（参考价${p.reference_price.toFixed(2)} USD）`).join('；'):'空仓';
  return `<p><strong>最近完整日线的纸面模拟状态：</strong>${state}。</p><p class="sub">${c.passed?'通过门槛仍不是实盘建议。':'未通过验证，仅供观察，禁止解读为下单建议。'}以下为条件性规则目标，不是预测：${esc(c.signal.action)}；以归档末日为基点的下次计划日（可能已过去，非今日建议） ${esc(c.signal.reference_session)}，假日需核验。${esc(c.signal.scope)}</p>`;
 }
 function card(c,stale){
  return `<article class="ngq-put-card ${c.passed?'nglab-pass':''}"><h3>${esc(c.name)} · ${c.passed?'通过预注册门槛（非保证盈利）':'未通过'}</h3><p>${esc(c.rules)}</p><p class="ngq-warning">${c.passed?'仍需真正前瞻纸面验证及可执行性审查。':c.fail_reasons.map(esc).join('；')}</p><div class="table-wrap"><table class="ngq-table"><thead><tr><th>区间</th><th>交易日范围</th><th>累计收益</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>成交笔数*</th><th>完整平仓次数</th></tr></thead><tbody>${row('样本内',c.periods.in_sample)}${row('验证',c.periods.validation)}${row('留出（一次评估）',c.periods.holdout)}</tbody></table></div><p class="sub">跨两轮12重Bonferroni式IID近似p=${num(Number.isFinite(c.periods.holdout.metrics.bonferroni_p_iid)?Math.min(1,2*c.periods.holdout.metrics.bonferroni_p_iid):null)}；不是严格显著性或盈利证明。${c.short?'借券15%压力情景：留出CAGR '+pct(c.borrow15_holdout.cagr)+'，回撤 '+pct(c.borrow15_holdout.max_drawdown)+'，Sharpe '+num(c.borrow15_holdout.sharpe)+'。可借券与真实费率：无数据。':'本策略不直接卖空，不计借券费；KOLD仍有每日重置和展期风险。'}</p>${modelSignal(c,stale)}<div class="ngq-result">${curve(c.periods.holdout.curve)}</div></article>`;
 }
 function render(d,el){
  if(d.status!=='ok'){el.textContent='无数据：策略实验室尚无冻结研究快照。';return;}
  el.innerHTML=`<p class="ngq-warning"><strong>${d.pass_count?d.pass_count+'/6个通过预注册门槛（非保证盈利）':'无通过验证的策略（0/6）'}</strong>。不因看过留出结果再调参，不隐藏失败。${d.pass_count?'先做真正前瞻纸面测试，不直接实盘。':'不能把本轮结果宣传为已找到稳定盈利系统。'}</p><p class="sub">研究冻结 ${esc(d.updated_at)}（JST）；最近完整共同美股交易日 ${esc(d.asof_session)}。${esc(d.methodology)} 本模块不自动刷新历史实验或行情，超过5日禁用信号。</p><div class="table-wrap"><table class="ngq-table"><thead><tr><th>系统</th><th>样本内CAGR</th><th>验证CAGR</th><th>留出CAGR</th><th>留出回撤</th><th>留出Sharpe</th><th>结论</th></tr></thead><tbody>${d.candidates.map(c=>`<tr><td>${esc(c.name)}</td><td class="${cls(c.periods.in_sample.metrics.cagr)}">${pct(c.periods.in_sample.metrics.cagr)}</td><td class="${cls(c.periods.validation.metrics.cagr)}">${pct(c.periods.validation.metrics.cagr)}</td><td class="${cls(c.periods.holdout.metrics.cagr)}">${pct(c.periods.holdout.metrics.cagr)}</td><td class="ngq-down">${pct(c.periods.holdout.metrics.max_drawdown)}</td><td>${num(c.periods.holdout.metrics.sharpe)}</td><td>${c.passed?'通过门槛':'未通过'}</td></tr>`).join('')}</tbody></table></div>${d.candidates.map(c=>card(c,d.stale)).join('')}<p class="sub">* 成交笔数是单腿实际换仓委托计数（含加减仓与期末平仓），不等于完整往返次数。所有收益扣单边10bp换仓费；空头按实际日历天收10%年化借券费，额外展示15%压力情景；现金收益0。短仓存在无限损失、追保、停牌、召回和无券风险。净值归零按破产记录，不保证有限责任可执行。</p><p class="ngq-warning">已看过此前市场走势，所谓留出不是完全未知数据。本轮六候选、跨两轮12次多重检验、样本依赖/肥尾和NG换月跳空都可能制造偶然收益。EIA历史发布时间及近次月曲线在本轮归档中无数据，已在看结果前排除，没有使用伪造代理。</p>`;
 }
 let loading=false,last=0;
 async function load(){
  const el=document.getElementById('ng-strategy-lab-content');if(!el||loading||Date.now()-last<60000)return;loading=true;
  try{
   const r=await fetch(root.STATIC_MODE?'./api/ng_strategy_lab.json':'/api/ng-strategy-lab',{signal:AbortSignal.timeout(15000)});
   if(!r.ok)throw new Error('HTTP '+r.status);
   const d=await r.json();
   if(d.asof_session&&Date.now()-new Date(d.asof_session+'T00:00:00+09:00').getTime()>6*86400000)d.stale=true;
   render(d,el);last=Date.now();
  }catch(e){el.textContent='无数据：'+e.message+'。重新进入美气NG可重试。';}finally{loading=false;}
 }
 if(typeof document!=='undefined')document.addEventListener('click',e=>{if(e.target.closest('[data-view="ng"]'))load();});
 if(typeof module!=='undefined')module.exports={pct,curve,row,card,modelSignal,render};
})(typeof window!=='undefined'?window:globalThis);
