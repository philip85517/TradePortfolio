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
