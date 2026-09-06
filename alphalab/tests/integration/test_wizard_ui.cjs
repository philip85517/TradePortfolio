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
test('repair gates honor server actions and keep legacy compatibility',()=>{
 const h=helpers();
 assert.equal(h.gates({readiness:{status:'BLOCKED',repair_plan:{executable_count:0}}},null,false,false).canPrepare,false);
 assert.equal(h.gates({readiness:{status:'BLOCKED',repair_plan:{executable_count:2}}},null,false,false).canPrepare,true);
 assert.match(h.repairLabel({repair_plan:{executable_count:2,actions:[{kind:'verify'}]}}),/核实交易状态.*2/);
 assert.match(h.repairLabel({repair_plan:{executable_count:1,actions:[{kind:'download'}]}}),/修复可处理项.*1/);
});
test('large report groups and filters evidence without losing raw diagnostics',()=>{
 const h=helpers(); const issues=Array.from({length:306},(_,i)=>({symbol:String(i%154).padStart(6,'0'),resolution:i%2?'download':'verify',phase:'entry',code:'MISSING',message:'<unsafe>',date_ranges:[{start:'2021-03-02',end:'2021-03-03'}],evidence:{source:'fixture'}}));
 assert.equal(h.filterIssues(issues,{symbol:'000001'}).length,2);
 const html=h.issuesHtml(issues,{}); assert.match(html,/可自动处理/); assert.match(html,/需核实/); assert.match(html,/2021-03-02/); assert.doesNotMatch(html,/<unsafe>/);
 const rows=Array.from({length:154},(_,i)=>({symbol:String(i).padStart(6,'0'),status:'READY'}));
 assert.equal((h.coverageHtml(rows).match(/<article/g)||[]).length,25);
 assert.match(h.coverageHtml(rows,6),/000153/);
 assert.match(h.attemptSummary({status:'PARTIAL',result:{attempts:[{outcome:'repaired'},{outcome:'unchanged'},{outcome:'failed'}]}}),/已处理 1.*未变化 1.*请求失败 1/);
});

test('backend repair kinds and persisted summaries are presented accurately',()=>{
 const h=helpers();
 assert.match(h.repairLabel({repair_plan:{executable_count:3,actions:[{kind:'verify_status'}]}}),/核实交易状态.*3/);
 assert.match(h.repairLabel({repair_plan:{executable_count:2,actions:[{kind:'bars'},{kind:'entry'}]}}),/修复可处理项.*2/);
 assert.match(h.attemptSummary({result:{repair_summary:{resolved:2,unchanged:3,failed:1}}}),/已处理 2.*未变化 3.*请求失败 1/);
 assert.match(h.attemptSummary({result:{repair_attempts:[{status:'resolved'},{status:'unresolved'}]}}),/已处理 1.*未变化 1/);
});
test('issue labels and coverage distinguish unverified status from repairable data',()=>{
 const h=helpers();
 assert.equal(h.diagnosticLabel('source_capability'),'来源能力不足');
 assert.equal(h.diagnosticLabel('holding'),'持有');
 const issues=[{symbol:'000001',resolution:'verify',phase:'holding',category:'status_unknown'}];
 assert.match(h.issuesHtml(issues),/阶段：持有 · 类型：状态待核实/);
 const html=h.coverageHtml([{symbol:'000001',status:'INVALID'}],0,issues);
 assert.match(html,/需核实交易状态/); assert.doesNotMatch(html,/需修复数据/);
 assert.match(h.coverageHtml([{symbol:'000001',status:'INVALID'}],0,[{symbol:'000001',resolution:'unsupported'}]),/当前能力不支持/);
});

test('scope invalidation detaches previous task summaries without mutating saved attempts',()=>{
 const h=helpers(), previous={id:'old',status:'PARTIAL',result:{repair_attempts:[{status:'resolved'}]}};
 assert.equal(h.taskForDraft(previous,{task_id:null}),null);
 assert.equal(h.taskForDraft(previous,{task_id:'new'}),null);
 assert.equal(h.taskForDraft(previous,{task_id:'old'}),previous);
 assert.equal(previous.result.repair_attempts[0].status,'resolved');
});
