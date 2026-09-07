const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function load() {
 const nodes=new Map();
 function node(id){if(!nodes.has(id)) nodes.set(id,{hidden:false,innerHTML:'',value:'',dataset:{},classList:{toggle(){},add(){}},setAttribute(){},querySelectorAll(){return[];},addEventListener(e,fn){this[e]=fn;}});return nodes.get(id);}
 const document={getElementById:node,querySelector:node,querySelectorAll(){return[];},addEventListener(){}};
 const context={document,localStorage:{getItem(){return null;}},URLSearchParams,fetch:()=>new Promise(()=>{}),window:{}};
 vm.runInNewContext(fs.readFileSync('alphalab/research/static/app.js','utf8'),context);
 return node;
}
test('returning to selection immediately hides and clears future data before the network responds',()=>{
 const node=load();node('evaluationMode').click();
 node('evaluationSummary').hidden=false;node('performanceGrid').innerHTML='future returns';node('chart').innerHTML='future prices';
 node('selectionMode').click();
 assert.equal(node('evaluationSummary').hidden,true);
 assert.equal(node('performanceGrid').innerHTML,'');
 assert.equal(node('chart').innerHTML,'');
});
test('wizard review keeps requested interval distinct from signal date and labels manual inputs',()=>{
 const context={module:{exports:{}}};vm.runInNewContext(fs.readFileSync('alphalab/research/static/app.js','utf8'),context);
 const labels=context.module.exports.reviewLabels;assert.equal(typeof labels,'function');
 const view=labels({requested_date:'2021-02-26',rule_version:'manual_v1',spec:{wizard_metadata:{scope:{selection_mode:'manual',start_date:'2021-03-01',end_date:'2025-06-30'}}}});
 assert.equal(view.requestedDate,'2021-03-01 → 2025-06-30');assert.equal(view.rule,'手选股票');assert.equal(view.factorSource,'手选股票');
});

function reviewExports() {
 const context={module:{exports:{}}};vm.runInNewContext(fs.readFileSync('alphalab/research/static/app.js','utf8'),context);
 return context.module.exports;
}
test('open positions display valuation separately from realized cash and gains',()=>{
 const {performanceCardHtml,portfolioStatus}=reviewExports();
 const result={status:'COMPLETE',liquidation_status:'OPEN_POSITION',total_return:.2,profit_loss:200,
   realized_profit_loss:50,unrealized_profit_loss:150,realized_cash:500,unrealized_holdings_value:700,
   open_positions:{'<A>':10}};
 const html=performanceCardHtml(result,'20 日组合');
 assert.match(html,/估值收益/);assert.match(html,/尚未清算/);assert.match(html,/已实现盈亏 50.00/);
 assert.match(html,/未实现盈亏 150.00/);assert.match(html,/现金 500.00/);
 assert.match(html,/未实现持仓估值 700.00/);assert.match(html,/&lt;A&gt;/);
 assert.doesNotMatch(html,/已全部清算/);
 assert.equal(portfolioStatus([result]),'估值完成 · 尚未清算');
 assert.equal(portfolioStatus([{...result,liquidation_status:'LIQUIDATED'}]),'已全部清算');
 assert.equal(portfolioStatus([{status:'COMPLETE'}]),'已完成');
});
test('stale NAV valuation evidence names affected dates and symbols safely',()=>{
 const {staleValuationHtml}=reviewExports();
 const html=staleValuationHtml([{date:'2023-01-04',horizon:20,stale_symbols:'<A>,B',max_valuation_stale_days:3}]);
 assert.match(html,/历史价格估值/);assert.match(html,/2023-01-04/);assert.match(html,/3 天/);
 assert.match(html,/&lt;A&gt;,B/);assert.equal(staleValuationHtml([{stale_symbols:'',max_valuation_stale_days:0}]),'');
});
