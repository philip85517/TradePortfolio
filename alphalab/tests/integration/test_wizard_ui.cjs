const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function helpers() { const context = { module: { exports: {} } }; vm.runInNewContext(fs.existsSync('alphalab/research/static/wizard.js') ? fs.readFileSync('alphalab/research/static/wizard.js', 'utf8') : '', context); return context.module.exports; }
test('scope validates reversed dates and duplicate selections before checking data', () => {
 const h=helpers(); assert.equal(typeof h.validateScope,'function');
 assert.match(h.validateScope({start_date:'2025-01-01',end_date:'2021-01-01'}), /结束/);
 assert.match(h.validateScope({start_date:'2021-01-01',end_date:'2025-01-01',selection_mode:'manual',symbols:['000001','000001']}), /重复/);
});
test('response ownership rejects changed inputs, switched draft and older revision', () => {
 const h=helpers(); assert.equal(typeof h.acceptResponse,'function');
 const current={id:'a',revision:3};
 assert.equal(h.acceptResponse(current,{id:'a',revision:4},2,3),false);
 assert.equal(h.acceptResponse(current,{id:'b',revision:4},2,2),false);
 assert.equal(h.acceptResponse(current,{id:'a',revision:2},2,2),false);
 assert.equal(h.acceptResponse(current,{id:'a',revision:4},2,2),true);
});
test('data coverage is visible for backend arrays and dates explain test calendar', () => {
 const h=helpers(); assert.equal(typeof h.coverageHtml,'function');
 const html=h.coverageHtml([{symbol:'000001',name:'平安银行',status:'MISSING',required_sessions:12,available_sessions:10,missing_dates:['2021-03-02','2021-03-03'],adjustment:'hfq'}]);
 assert.match(html,/平安银行/); assert.match(html,/10 \/ 12/); assert.match(html,/2021-03-02/); assert.match(html,/需补齐/); assert.doesNotMatch(html,/<details/);
 assert.equal(h.calendarLabel('injected'),'测试日历（仅用于验收）');
});
test('completion gates avoid redundant preparation and premature results', () => {
 const h=helpers(); assert.equal(typeof h.gates,'function');
 const ready=h.gates({readiness:{status:'READY'}},null,false,false);
 assert.equal(ready.canPrepare,false); assert.equal(ready.canViewResult,false); assert.equal(ready.canConfigure,true);
 const pending=h.gates({readiness:{status:'BLOCKED'}},null,false,false);
 assert.equal(pending.canConfigure,false); assert.equal(pending.canPrepare,true);
 const running=h.gates({readiness:{status:'READY'},preview:{}},{kind:'run',status:'RUNNING'},false,false);
 assert.equal(running.canViewResult,true); assert.equal(running.canPrepare,false);
});
test('initial capital permits the default amount and positive cent amounts in native form validation', () => {
 const html=fs.readFileSync('alphalab/research/static/wizard.html','utf8');
 const input=html.match(/<input\b[^>]*id="initialCash"[^>]*>/)[0];
 const min=Number(input.match(/min="([^"]+)"/)[1]);
 const step=Number(input.match(/step="([^"]+)"/)[1]);
 for(const amount of [100000,100000.01,1.01]) {
   const increments=(amount-min)/step;
   assert.ok(amount >= min && Math.abs(increments-Math.round(increments))<1e-6, `${amount} must pass native min/step constraints`);
 }
});
test('unfinished creation list excludes successful runs but keeps failures and preparations', () => {
 const h=helpers(); assert.equal(typeof h.unfinishedDrafts,'function');
 const rows=[{id:'done',task_kind:'run',task_status:'SUCCEEDED'},{id:'failure',task_kind:'run',task_status:'FAILED'},{id:'prepared',task_kind:'prepare',task_status:'SUCCEEDED'}];
 assert.equal(h.unfinishedDrafts(rows).map(x=>x.id).join(','),'failure,prepared');
});
test('rule selection never sends hidden manual codes while manual selection retains typed codes', () => {
 const h=helpers(); assert.equal(typeof h.selectionSymbols,'function');
 assert.equal(h.selectionSymbols('rule','000001, 600000').length,0);
 assert.equal(h.selectionSymbols('manual','000001, 600000').join(','),'000001,600000');
});
