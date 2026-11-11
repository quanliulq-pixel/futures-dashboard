(function(root){
 'use strict';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const pct=x=>Number.isFinite(x)?(x*100).toFixed(2)+'%':'无数据';
 const num=x=>Number.isFinite(x)?x.toFixed(3):'无数据';
 const cls=x=>x>0?'ngq-up':x<0?'ngq-down':'';
 function row(label,p){const m=p.metrics;return `<tr><td>${esc(label)}</td><td>${esc(p.start)}—${esc(p.end)}</td><td class="${cls(m.total_return)}">${pct(m.total_return)}</td><td class="${cls(m.cagr)}">${pct(m.cagr)}</td><td class="ngq-down">${pct(m.max_drawdown)}</td><td>${num(m.sharpe)}</td><td>${m.trades} / ${m.round_trips}</td><td>${Object.hasOwn(m,'input_coverage')?pct(m.input_coverage):'不适用'}</td></tr>`;}
 function table(rows){return `<div class="table-wrap"><table class="ngq-table"><thead><tr><th>区间</th><th>美国交易日</th><th>累计收益</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>订单/平仓</th><th>输入覆盖*</th></tr></thead><tbody>${rows}</tbody></table></div>`;}
 function curve(points){
  if(!Array.isArray(points)||!points.length)return '<p>无数据</p>';
  const vals=points.map(p=>p.equity),lo=Math.min(.95,...vals),hi=Math.max(1.05,...vals),y=v=>205-170*(v-lo)/(hi-lo);
  return `<svg viewBox="0 0 780 240" role="img" aria-label="第二轮留出净值，起点1"><title>冻结历史实验净值，不是未来预测</title>${[lo,1,hi].map(v=>`<text x="2" y="${y(v)}" fill="#b8c7dd" font-size="11">${v.toFixed(2)}</text>`).join('')}<line x1="55" x2="745" y1="${y(1)}" y2="${y(1)}" stroke="#65748b" stroke-dasharray="4"/><polyline fill="none" stroke="#f5c76a" stroke-width="2" points="${points.map((p,i)=>`${55+690*i/Math.max(1,points.length-1)},${y(p.equity)}`).join(' ')}"/><text x="55" y="230" fill="#b8c7dd" font-size="11">${esc(points[0].date)}</text><text x="650" y="230" fill="#b8c7dd" font-size="11">${esc(points.at(-1).date)}</text></svg>`;
 }
 function paper(c,stale){
  if(stale)return '<p class="ngq-warning">无数据：冻结归档超过5日，纸面状态禁用；不是实时信号。</p>';
  const s=c.model_state||{},positions=s.positions||[];
  const state=positions.length?positions.map(p=>`${p.weight>0?'做多':'卖空'}${esc(p.symbol)} ${pct(Math.abs(p.weight))}（参考价${Number.isFinite(p.reference_price)?p.reference_price.toFixed(2):'无数据'} USD）`).join('；'):'空仓';
  const inputs=['s','w','c'].map((k,i)=>`${['库存','天气','COT'][i]}参考期 ${esc((s.input_dates||{})[k]||'无数据')} / 保守可用日 ${esc((s.available_dates||{})[k]||'无数据')}`).join('；');
  return `<p><strong>截至归档末日的纸面持仓：</strong>${state}。${esc(s.reason)}；非今日下单建议。</p><p class="sub">${inputs}。${c.passed?'仅通过历史门槛，仍需未来纸面验证。':'未通过：仅供研究观察。'}</p>`;
 }
 function card(c,stale){return `<article class="ngq-put-card ${c.passed?'nglab-pass':''}"><h4>${esc(c.name)} · ${c.passed?'通过预注册门槛（非保证盈利）':'未通过'}</h4><p>${esc(c.rules)}</p><p class="ngq-warning">${c.passed?'历史通过不等于未来盈利。':c.fail_reasons.map(esc).join('；')}</p>${table(row('样本内',c.periods.in_sample)+row('验证',c.periods.validation)+row('留出·冻结结果',c.periods.holdout))}<p class="sub">两轮12重Bonferroni式IID近似p=${num(c.periods.holdout.metrics.bonferroni_p_iid)}，非严格显著性。留出累计换仓费/初始净值 ${pct(c.periods.holdout.metrics.cost_paid)}；借券费/初始净值 ${pct(c.periods.holdout.metrics.borrow_paid)}。</p>${paper(c,stale)}<div class="ngq-result">${curve(c.periods.holdout.curve)}</div></article>`;}
 function render(d,el){
  if(d.status!=='ok'){el.textContent='无数据：第二轮基本面实验尚无冻结快照。';return;}
  el.innerHTML=`<p class="ngq-warning"><strong>${d.pass_count?d.pass_count+'/6个通过第二轮历史门槛（非保证盈利）':'第二轮无通过验证的策略（0/6）'}</strong>。两轮共${d.multiple_tests}个固定候选，保留所有失败；不因留出表现而改参数。</p><p class="sub">冻结 ${esc(d.updated_at)}（JST）；最后完整美国交易日 ${esc(d.asof_session)}。${esc(d.methodology)} 此面板不抓行情、不重新回测；超过5日禁用纸面状态。</p><p class="ngq-warning">执行审计：${esc(d.execution_note)}</p><h4>同区间UNG买入持有参照（100%仓位，亦扣10bp双边费用）</h4>${table(row('样本内参照',d.benchmark.in_sample)+row('验证参照',d.benchmark.validation)+row('留出参照',d.benchmark.holdout))}${d.candidates.map(c=>card(c,d.stale)).join('')}<p class="sub">* 输入覆盖=该系统所需报告/特征有效、未过期且不在隔离窗的交易日占比；缺失时现金，现金年数仍计入收益。订单含周度再平衡和期末平仓，不等于完整往返。周内固定股数，仅缺失/过期/隔离即退出；每周首日开盘目标仓位min(50%,10%/前日HV60)。正常库存周四/COT周五日程后首个真实交易日，再额外等2个交易日；天气观测先等一周，再等真实开盘及2日。不是首发事件抢跑回测。</p><p class="ngq-warning">2013-10-01—11-15、2018-12-22—2019-03-15、2025年第四季度保守现金隔离；这些窗口不代表每份EIA都实际停发。季度/年度与日期均是美国业务日期。年度基准、COT分类和NOAA权重有后修订风险，不能称严格点时数据库。</p><ul class="sub">${d.caveats.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
 }
 let loading=false,last=0;
 async function load(){
  const el=document.getElementById('ng-strategy-lab2-content');if(!el||loading||Date.now()-last<60000)return;loading=true;
  try{const r=await fetch(root.STATIC_MODE?'./api/ng_strategy_lab2.json':'/api/ng-strategy-lab2',{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();if(d.asof_session&&Date.now()-new Date(d.asof_session+'T00:00:00+09:00').getTime()>6*86400000)d.stale=true;render(d,el);last=Date.now();}
  catch(e){el.textContent='无数据：'+e.message;}finally{loading=false;}
 }
 if(typeof document!=='undefined')document.addEventListener('click',e=>{if(e.target.closest('[data-view="ng"]'))load();});
 if(typeof module!=='undefined')module.exports={pct,row,curve,paper,card,render};
})(typeof window!=='undefined'?window:globalThis);
