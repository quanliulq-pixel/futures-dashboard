const assert=require('node:assert/strict');
const view=require('../static/ng_strategy_lab_view.js');
const data=require('../docs/api/ng_strategy_lab.json');
assert.equal(view.pct(null),'无数据');
const target={innerHTML:''};view.render(data,target);
assert.ok(target.innerHTML.includes('无通过验证的策略（0/6）'));
for(const c of data.candidates){
 assert.ok(target.innerHTML.includes(c.name));
 assert.ok(target.innerHTML.includes(c.fail_reasons[0]));
 assert.ok(!view.curve(c.periods.holdout.curve).includes('NaN'));
 assert.ok(view.modelSignal(c,true).includes('信号已禁用'));
}
assert.equal(data.candidates.length,6);
assert.ok(target.innerHTML.includes('不能把本轮结果宣传'));
const copy=structuredClone(data);copy.candidates[0].name='<script>alert(1)</script>';
view.render(copy,target);assert.ok(!target.innerHTML.includes('<script>alert(1)</script>'));
assert.ok(target.innerHTML.includes('&lt;script&gt;'));
console.log('ng_strategy_lab view tests passed');
