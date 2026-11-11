const assert=require('node:assert/strict');
const view=require('../static/ng_strategy_lab2_view.js');
const data=require('../docs/api/ng_strategy_lab2.json');
assert.equal(data.round,2);assert.equal(data.candidates.length,6);assert.equal(data.multiple_tests,12);
assert.equal(view.pct(null),'无数据');assert.ok(view.curve([]).includes('无数据'));
const target={innerHTML:''};view.render(data,target);
assert.ok(target.innerHTML.includes('两轮共12个固定候选'));
assert.ok(target.innerHTML.includes('同区间UNG买入持有参照'));
assert.ok(target.innerHTML.includes('执行审计'));
for(const c of data.candidates){
 assert.ok(target.innerHTML.includes(c.name));assert.ok(!view.curve(c.periods.holdout.curve).includes('NaN'));
 assert.ok(view.paper(c,true).includes('纸面状态禁用'));
 if(c.passed)assert.ok(view.card(c,false).includes('nglab-pass'));
 else assert.ok(view.card(c,false).includes(c.fail_reasons[0]));
}
const copy=structuredClone(data);copy.candidates[0].name='<script>alert(1)</script>';
view.render(copy,target);assert.ok(!target.innerHTML.includes('<script>alert(1)</script>'));assert.ok(target.innerHTML.includes('&lt;script&gt;'));
view.render({status:'no_data'},target);assert.ok(target.textContent.includes('无数据'));
console.log('ng_strategy_lab2 view tests passed');
