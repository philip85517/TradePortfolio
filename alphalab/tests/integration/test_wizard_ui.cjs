const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const portfolioReviewUI = require('../../research/static/portfolio-review.js');
function helpers() { const context = { module: { exports: {} } }; vm.runInNewContext(fs.existsSync('alphalab/research/static/wizard.js') ? fs.readFileSync('alphalab/research/static/wizard.js', 'utf8') : '', context); return context.module.exports; }
function submissionStorage() {
 const values=new Map();
 return {getItem:key=>values.get(key) ?? null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
}

class WizardNode {
 constructor(id='') { this.id=id; this.value=''; this.textContent=''; this.innerHTML=''; this.hidden=false; this.disabled=false; this.checked=false; this.dataset={}; this.open=false; this.listeners={}; this.classList={toggle(){},add(){},remove(){}}; }
 addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
 querySelector() { return new WizardNode(); }
 querySelectorAll() { return []; }
 focus() {}
}

class ReviewNode extends WizardNode {
 constructor(documentObject) { super('results'); this.ownerDocument=documentObject; this.regions=new Map(); }
 querySelector(selector) {
   const region=selector.match(/^\[data-review-region="([^"]+)"\]$/)?.[1];
   if (region) { if (!this.regions.has(region)) this.regions.set(region,new WizardNode()); return this.regions.get(region); }
   if (selector==='[data-review-chart-note]') { if (!this.regions.has('chart-note')) this.regions.set('chart-note',new WizardNode()); return this.regions.get('chart-note'); }
   return null;
 }
 querySelectorAll() { return []; }
}

function wizardDocument() {
 const documentObject={};
 const ids=['error','saveStatus','retrySave','loadServer','home','editor','steps','step1','step2','step3','step4','step5','scopeForm','market','startDate','endDate','selectionMode','ruleVersion','topN','manualScope','ruleScope','symbols','delistingPolicy','portfolioName','initialCash','weighting','minHoldings','commission','slippage','maxSingle','maxIndustry','weights','customWeights','preview','backData','toConfirm','taskConnection','refreshTask','readiness','exploreChoice','explorationHelp','acceptExploratory','applyExploratory','prepareTask','editScope','check','prepare','cancelPrepare','toPortfolio','dataGate','confirmation','backPortfolio','run','runTask','retryRun','recoverTask','copyDraft','returnConfig'];
 const nodes=new Map(ids.map((id)=>[id,new WizardNode(id)]));
 const results=new ReviewNode(documentObject); nodes.set('results',results);
 documentObject.getElementById=(id)=>{ if(!nodes.has(id)) nodes.set(id,new WizardNode(id)); return nodes.get(id); };
 documentObject.querySelector=()=>new WizardNode();
 documentObject.querySelectorAll=()=>[];
 documentObject.createElement=()=>new WizardNode();
 documentObject.body=new WizardNode('body');
 documentObject._nodes=nodes;
 return documentObject;
}

function reviewResult(runId='run-1', portfolioId='strategy') {
 return {run_id:runId,portfolio_id:portfolioId,name:'测试组合',initial_cash:1000,horizons:[706],summary:{horizon:706,status:'COMPLETE',total_return:0.1,profit_loss:100,ending_equity:1100,initial_cash:1000},nav:[{date:'2025-01-02',equity:1000,unit_nav:1},{date:'2025-01-03',equity:1100,unit_nav:1.1}],events:[],scope:{requested_start_date:'2025-01-01',requested_end_date:'2025-01-03',actual_date_range:['2025-01-02','2025-01-03']},capabilities:{daily_nav:true}};
}

function lifecycleContext(fetchImpl) {
 const documentObject=wizardDocument();
 const storage=submissionStorage();
 const draft={id:'draft-1',revision:1,scope:{market:'a_share',start_date:'2025-01-01',end_date:'2025-12-03',selection_mode:'manual',symbols:['000001'],rule_version:'fixed_v0',top_n:10,quality_mode:'strict'},portfolio:{name:'测试组合',initial_cash:100000,weighting:'equal',weights:{},commission_rate:0.0003,slippage_rate:0.001,max_single_weight:1,max_industry_weight:1,min_holdings:1},readiness:{status:'READY',dates:{signal_date:'2025-01-01',entry_date:'2025-01-02',exit_date:'2025-12-03',horizon:706},coverage:[],issues:[],repair_plan:{executable_count:0}},preview:{holdings:[]},task_id:'task-1'};
 const runResult=reviewResult();
 const reviewOptions=[];
 const controllers=[];
 const windowObject={addEventListener(){},PortfolioReviewUI:{create(container,options){
   const controller=portfolioReviewUI.create(container,{...options,charts:null});
   reviewOptions.push(options); controllers.push(controller); return controller;
 }}};
 const context={document:documentObject,window:windowObject,location:{search:'?draft=draft-1'},history:{replaceState(){}},localStorage:storage,fetch:fetchImpl,URLSearchParams,URL,Blob,AbortController,crypto:{randomUUID(){return 'request-id'}},setInterval(){return 1},setTimeout(){return 1},clearTimeout(){},console,Promise,Date,Math,JSON,Number,String,Object,Array,Set,Map,encodeURIComponent,decodeURIComponent};
 context.globalThis=context;
 return {context,documentObject,draft,runResult,reviewOptions,controllers};
}
test('run submission follows frozen data after recheck without reusing failed legacy request',()=>{
 const h=helpers(),storage=submissionStorage();let sequence=0;
 const createKey=()=>`request-${++sequence}`;
 const draft={id:'draft',revision:7,readiness:{data_identity:'before-repair',requirement_id:'scope-a'}};
 storage.setItem('alphalab.wizard.submit.draft.7','failed-old-request');
 const old=h.submissionKey(draft,storage,createKey);
 assert.notEqual(old,'failed-old-request');
 assert.equal(h.submissionKey(draft,storage,createKey),old);
 draft.readiness={data_identity:'after-repair',requirement_id:'scope-a'};
 const repaired=h.submissionKey(draft,storage,createKey);
 assert.notEqual(repaired,old);
 assert.equal(h.submissionKey(draft,storage,createKey),repaired);
 // Reloading the script preserves idempotency for this exact frozen data.
 assert.equal(helpers().submissionKey(JSON.parse(JSON.stringify(draft)),storage,createKey),repaired);
 draft.readiness={data_identity:'after-repair',requirement_id:'scope-b'};
 assert.notEqual(h.submissionKey(draft,storage,createKey),repaired);
});
test('retry replaces only current readiness submission and leaves other frozen identities idempotent',()=>{
 const h=helpers(),storage=submissionStorage();let sequence=0;
 const createKey=()=>`request-${++sequence}`;
 const previous={id:'draft',revision:7,readiness:{data_identity:'old',requirement_id:'scope'}};
 const current={...previous,readiness:{data_identity:'new',requirement_id:'scope'}};
 const old=h.submissionKey(previous,storage,createKey),first=h.submissionKey(current,storage,createKey);
 h.clearSubmissionKey(current,storage);
 const retried=h.submissionKey(current,storage,createKey);
 assert.notEqual(retried,first);
 assert.equal(h.submissionKey(current,storage,createKey),retried);
 assert.equal(h.submissionKey(previous,storage,createKey),old);
 assert.notEqual(h.submissionKey({...current,revision:8},storage,createKey),retried);
 assert.notEqual(h.submissionKey({...current,id:'another'},storage,createKey),retried);
});
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

test('original wizard renders observable review states, drops stale fetches, and retries the same frozen identity', async () => {
 const pending=[]; const comparisonPending=[]; const calls=[]; let portfolioCount=0;
 const draft={id:'draft-1',revision:1,scope:{market:'a_share',start_date:'2025-01-01',end_date:'2025-12-03',selection_mode:'manual',symbols:['000001'],rule_version:'fixed_v0',top_n:10,quality_mode:'strict'},portfolio:{name:'测试组合',initial_cash:100000,weighting:'equal',weights:{},commission_rate:0.0003,slippage_rate:0.001,max_single_weight:1,max_industry_weight:1,min_holdings:1},readiness:{status:'READY',dates:{signal_date:'2025-01-01',entry_date:'2025-01-02',exit_date:'2025-12-03',horizon:706},coverage:[],issues:[],repair_plan:{executable_count:0}},preview:{holdings:[]},task_id:'task-1'};
 const result={run_id:'run-1',portfolio_id:'strategy',name:'测试组合',initial_cash:100000,horizons:[706],summary:{horizon:706,status:'COMPLETE'}};
 const detail=(portfolioId)=>({run_id:'run-1',portfolio_id:portfolioId,name:'测试组合',initial_cash:1000,horizons:[706],summary:{horizon:706,status:'COMPLETE',total_return:0.1,profit_loss:100,ending_equity:1100,initial_cash:1000},nav:[{date:'2025-01-02',equity:1000,unit_nav:1},{date:'2025-01-03',equity:1100,unit_nav:1.1}],events:[],scope:{requested_start_date:'2025-01-01',requested_end_date:'2025-01-03',actual_date_range:['2025-01-02','2025-01-03']},capabilities:{daily_nav:true}});
 const response=(value,status=200)=>({ok:status>=200 && status<300,status,json:async()=>value});
 const fetchImpl=async (url)=>{
   calls.push(url);
   if(url==='/api/wizard/drafts/draft-1') return response({draft:JSON.parse(JSON.stringify(draft))});
   if(url==='/api/wizard/tasks/task-1') return response({task:{id:'task-1',kind:'run',status:'SUCCEEDED',result}});
   if(url.startsWith('/research/review/run-1/api/portfolio')) { portfolioCount+=1; return new Promise((resolve,reject)=>pending.push({resolve,reject,index:portfolioCount})); }
   if(url.startsWith('/research/review/run-1/api/index-comparisons')) return new Promise(resolve=>comparisonPending.push({resolve,url}));
   if(url==='/research/review/run-1/api/summary') return response({spec:{wizard_metadata:{delisting_events:[]}}});
   throw new Error(`unexpected fetch ${url}`);
 };
 const harness=lifecycleContext(fetchImpl);
 harness.draft=draft; harness.runResult=result;
 vm.runInNewContext(fs.readFileSync('alphalab/research/static/wizard.js','utf8'),harness.context);
 const flush=async()=>{for(let i=0;i<4;i++) await new Promise((resolve)=>setImmediate(resolve));};
 await flush();
 assert.equal(portfolioCount,1);
 assert.equal(harness.reviewOptions[0].portfolioId,'strategy');
 assert.match(harness.documentObject._nodes.get('results').regions.get('state').innerHTML,/正在重新读取冻结结果/);

 harness.reviewOptions[0].onSelectionChange({runId:'run-1',portfolioId:'alternate',horizon:'706'});
 await flush();
 assert.equal(portfolioCount,2);
 assert.equal(harness.reviewOptions[1].portfolioId,'alternate');
 pending[0].resolve(response(detail('strategy')));
 pending[1].resolve(response(detail('alternate')));
 await flush();
 assert.equal(harness.controllers[0].getModel().data.nav.length,0,'stale owner must not receive late detail');
 assert.equal(harness.controllers[1].getModel().data.nav.length,2);
 assert.match(harness.documentObject._nodes.get('results').regions.get('chart').innerHTML,/组合日终净值回退图/);

 harness.reviewOptions[1].onRetry({runId:'run-1',portfolioId:'alternate',horizon:'706'});
 await flush();
 assert.equal(portfolioCount,3,'same identity retry must issue a new request');
 pending[2].resolve(response({error:'temporary failure'},503));
 await flush();
 assert.equal(harness.controllers[2].getState().status,'fallback');
 assert.match(harness.documentObject._nodes.get('results').regions.get('state').innerHTML,/详细投影暂不可用/);
 harness.reviewOptions[2].onRetry({runId:'run-1',portfolioId:'alternate',horizon:'706'});
 await flush();
 assert.equal(portfolioCount,4);
 pending[3].resolve(response(detail('alternate')));
 await flush();
 assert.equal(harness.controllers[3].getState().status,'ready');
 assert.equal(harness.controllers[3].getModel().portfolioId,'alternate');
 assert.equal(calls.filter((url)=>url.includes('/api/summary')).length,2);
 assert.equal(comparisonPending.length,2);
 assert.match(comparisonPending[1].url,/portfolio_id=alternate&horizon=706/);
 comparisonPending[0].resolve(response({series:[{id:'stale',status:'ready',rows:[]}]}));
 comparisonPending[1].resolve(response({series:[{id:'hsi',name:'恒生指数',status:'ready',rows:[{date:'2025-01-02',unit_nav:1},{date:'2025-01-03',unit_nav:1.1}]}]}));
 await flush();
 assert.equal(harness.controllers[1].getState().comparisons,null,'late comparison must not reach destroyed owner');
 assert.equal(harness.controllers[3].getState().comparisons.series[0].id,'hsi');
 assert.equal(harness.controllers[3].getState().status,'ready');
 harness.reviewOptions[3].onReloadComparisons();
 await flush();
 assert.equal(portfolioCount,4,'retry indices must not reload portfolio or rerun research');
 comparisonPending[2].resolve(response({error:'index provider unavailable'},503));
 await flush();
 assert.equal(harness.controllers[3].getState().comparisonStatus,'error');
 assert.equal(harness.controllers[3].getState().status,'ready');
});

test('replacement policy exposes opt-in and an auditable transaction table', () => {
 const html=fs.readFileSync('alphalab/research/static/wizard.html','utf8');
 assert.match(html,/announcement-replace-v1/);
 const h=helpers();const rendered=h.executionHtml([{date:'2025-06-13',symbol:'002336',action:'SELL',shares:500,price:0.4,net_proceeds:199,source_url:'https://example.com/a'}]);
 assert.match(rendered,/002336/);assert.match(rendered,/2025-06-13/);assert.match(rendered,/卖出/);
});
