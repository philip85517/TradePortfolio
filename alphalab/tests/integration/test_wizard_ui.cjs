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

test('running elapsed time advances independently of progress and stale heartbeat stays explicit',()=>{
 const h=helpers(), task={status:'RUNNING',created_at:'2026-09-07T00:00:00Z',updated_at:'2026-09-07T00:00:01Z',heartbeat_at:'2026-09-07T00:00:05Z',progress_at:'2026-09-07T00:00:01Z'};
 assert.equal(h.taskTiming(task,Date.parse('2026-09-07T00:00:30Z')).elapsed,30);
 assert.equal(h.taskTiming(task,Date.parse('2026-09-07T00:00:30Z')).stale,true);
 assert.equal(h.taskTiming({...task,status:'SUCCEEDED'},Date.parse('2026-09-07T00:01:00Z')).elapsed,1);
});
test('supported market events are separated from blocking issues',()=>{
 const h=helpers(), issues=[{severity:'info',resolution:'unsupported',message:'确认停牌'},{severity:'warning',resolution:'user',message:'陈旧估值'}];
 const html=h.issuesHtml(issues);
 assert.match(html,/说明（不阻断）/); assert.match(html,/提示（不阻断）/);
 assert.doesNotMatch(html,/需要你处理|issue-group danger/);
 assert.doesNotMatch(h.coverageHtml([{symbol:'000001',status:'READY'}],0,[{symbol:'000001',severity:'info',resolution:'unsupported'}]),/当前能力不支持/);
});

test('unsupported source offers explicit symbol retry while ordinary issues do not',()=>{
 const h=helpers();
 const html=h.issuesHtml([{symbol:'302132',code:'SOURCE_ADJUSTMENT_UNAVAILABLE',resolution:'unsupported',message:'来源不支持'}]);
 assert.match(html,/重新核实来源/); assert.match(html,/data-retry-source="302132"/);
 assert.doesNotMatch(h.issuesHtml([{symbol:'000001',code:'TRADING_STATUS_UNKNOWN'}]),/data-retry-source/);
});

test('unliquidated wizard result separates realized cash and valuation', () => {
 const h=helpers();const html=h.liquidationHtml({liquidation_status:'OPEN_POSITION',realized_profit_loss:20,unrealized_profit_loss:30,unrealized_holdings_value:1000,realized_cash:2000});
 assert.match(html,/仍有未平仓/);assert.match(html,/已实现盈亏/);assert.match(html,/未实现盈亏/);assert.match(html,/不代表全部卖出/);
 assert.equal(h.liquidationHtml({liquidation_status:'LIQUIDATED'}),'');assert.equal(h.liquidationHtml({}), '');
});

test('adjustment overview distinguishes per-stock scale from mixed series and preserves source labels', () => {
 const h=helpers();const html=h.adjustmentHtml({stock_counts:{qfq:365,hfq:4540},mixed_symbols:[],unknown_symbols:[],source_stock_counts:{'source <cache>':4540},execution_basis:'未复权开盘价',limitation:'同股固定比例才尺度不变'});
 assert.match(html,/前复权.*365/);assert.match(html,/后复权.*4540/);assert.match(html,/单股混用.*0/);assert.match(html,/未复权开盘价/);assert.match(html,/&lt;cache&gt;/);
 assert.equal(h.adjustmentHtml(null),'');
});

test('uniform adjustment target and pending stock have explicit labels', () => {
 const h=helpers(); const summary=h.adjustmentHtml({target:'hfq',remaining_symbols:['000001'],stock_counts:{qfq:1}});
 assert.match(summary,/统一目标：后复权/);assert.match(summary,/待统一 1 只/);
 const html=h.coverageHtml([{symbol:'000001',status:'INVALID',adjustment:'qfq'}],0,[{symbol:'000001',code:'ADJUSTMENT_STANDARDIZATION_REQUIRED',resolution:'download',severity:'blocking'}]);
 assert.match(html,/待统一后复权/);
});

test('running uniform migration shows frozen target without claiming readiness', () => {
 const h=helpers();const task={kind:'prepare',status:'RUNNING',configuration:{readiness:{adjustment_summary:{target:'hfq',remaining_symbols:['000001'],stock_counts:{qfq:1}}}}};
 const html=h.pendingReadinessHtml(task);assert.match(html,/正在统一/);assert.match(html,/本轮开始时/);assert.match(html,/待统一 1 只/);assert.doesNotMatch(html,/可以配置组合/);
 assert.equal(h.pendingReadinessHtml({...task,status:'SUCCEEDED'}),'');assert.equal(h.pendingReadinessHtml({...task,kind:'run'}),'');
});

test('completed repairs distinguish research restrictions from missing data', () => {
 const h=helpers();const r={status:'BLOCKED',repair_plan:{executable_count:0},issues:[{code:'PIT_UNAVAILABLE',resolution:'user'},{code:'DELISTED',symbol:'002336',resolution:'unsupported'}],repair_summary:{attempted:365,resolved:365,failed:0,unsupported:0,unchanged:0}};
 assert.match(h.readinessHeadline(r),/数据修复完成/);
 assert.match(h.readinessGate(r),/002336/);
 assert.match(h.explorationHelp(r),/仍不能开始回测/);
 const html=h.issuesHtml(r.issues);
 assert.match(html,/研究模式选择/);assert.match(html,/系统能力限制/);assert.match(html,/重复补数无法解决/);assert.match(html,/<details><summary>技术详情/);
 assert.doesNotMatch(h.readinessHeadline({...r,issues:[...r.issues,{code:'MISSING_BARS',resolution:'download'}]}),/修复完成/);
 assert.match(h.readinessGate({...r,status:'READY',issues:[]}),/通过/);
 assert.doesNotMatch(h.explorationHelp({...r,issues:[r.issues[0]]}),/退市/);
});

test('preview request has an explicit waiting state instead of stale instructions',()=>{
 const h=helpers();assert.match(h.previewPendingHtml('preview'),/正在计算持仓预览/);
 assert.match(h.previewPendingHtml('preview'),/无需重复点击/);
 assert.equal(h.previewPendingHtml(null),'');assert.equal(h.previewPendingHtml('run'),'');
});

test('reopening a finished check restores preview instead of returning to data',()=>{
 const h=helpers(),d={preview:{status:'READY'},readiness:{status:'READY'}};
 assert.equal(h.restoredStep(d,{kind:'check',status:'SUCCEEDED'}),4);
 assert.equal(h.restoredStep(d,{kind:'check',status:'RUNNING'}),2);
 assert.equal(h.restoredStep(d,{kind:'run',status:'SUCCEEDED'}),5);
});
