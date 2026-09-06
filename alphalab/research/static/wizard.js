(() => {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function validateScope(s) {
    if (!s.start_date || !s.end_date) return '请选择开始与结束日期。';
    if (s.start_date > s.end_date) return '结束日期不能早于开始日期。';
    if (s.start_date < '2021-01-01' || s.end_date > '2025-12-31') return '请选择 2021–2025 年内的日期。';
    if (s.selection_mode === 'manual') {
      if (!s.symbols?.length) return '请输入至少一个股票代码。';
      if (new Set(s.symbols).size !== s.symbols.length) return '股票代码重复，请删除重复项。';
      if (s.symbols.some(x => !/^\d{6}$/.test(x))) return '请输入六位 A 股股票代码。';
    }
    if (s.selection_mode === 'rule' && (!Number.isInteger(s.top_n) || s.top_n < 1)) return '目标股票数须为正整数。';
    return '';
  }
  function acceptResponse(current, incoming, requestedEpoch, currentEpoch) {
    return Boolean(current && incoming && current.id === incoming.id && incoming.revision >= current.revision && requestedEpoch === currentEpoch);
  }
  function calendarLabel(value) {
    return value === 'injected' ? '测试日历（仅用于验收）' : value === 'baostock.query_trade_dates' ? 'BaoStock 市场交易日历' : value;
  }
  function gates(value, currentTask, changed, pending) {
    const ready = !changed && value.readiness?.status === 'READY';
    const running = currentTask && ['QUEUED','RUNNING','CANCELLING'].includes(currentTask.status);
    return {ready, running, canConfigure:ready && !pending && !running,
      canPrepare:!ready && !pending && !running, canViewResult:currentTask?.kind === 'run'};
  }
  function coverageHtml(rows) {
    if (!Array.isArray(rows) || !rows.length) return '<p class="muted">尚未得到逐股覆盖检查，请先处理上方数据问题。</p>';
    return '<h3>历史数据覆盖与缺口</h3><div class="coverage-list">'+rows.map(row=>{
      const status={READY:'已就绪',MISSING:'需补齐',INVALID:'需修复数据'}[row.status] || '待检查';
      const adjustment={hfq:'后复权',qfq:'前复权',raw:'不复权',none:'不复权',mixed:'复权口径不一致'}[row.adjustment] || row.adjustment;
      const missing=row.missing_dates || [];
      return `<article class="notice ${row.status==='READY'?'success':'danger'}"><strong>${esc(row.symbol)} ${esc(row.name)} · ${esc(status)}</strong><p>已覆盖 ${esc(row.available_sessions ?? 0)} / ${esc(row.required_sessions ?? '待确定')} 个所需交易日${adjustment?' · '+esc(adjustment):''}</p>${missing.length?`<p>缺少 ${missing.length} 个交易日：${missing.map(esc).join('、')}</p>`:''}</article>`;
    }).join('')+'</div>';
  }
  function selectionSymbols(mode, text) { return mode==='manual' ? text.split(/[\s,，;；]+/).filter(Boolean) : []; }
  function unfinishedDrafts(rows) { return rows.filter(row=>!(row.task_kind==='run' && row.task_status==='SUCCEEDED')); }
  if (typeof module !== 'undefined') module.exports = {validateScope, acceptResponse, coverageHtml, calendarLabel, gates, unfinishedDrafts, selectionSymbols};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const fmt = v => v == null ? '—' : typeof v === 'number' ? v.toLocaleString('zh-CN',{maximumFractionDigits:2}) : String(v);
  const active = t => t && ['QUEUED','RUNNING','CANCELLING'].includes(t.status);
  const names = ['选择范围','准备数据','配置组合','确认运行','查看结果'];
  const defaults = {scope:{market:'a_share',start_date:'2021-03-01',end_date:'2025-06-30',selection_mode:'manual',symbols:[],rule_version:'fixed_v0',top_n:10,quality_mode:'strict'},portfolio:{name:'我的股票组合',initial_cash:100000,weighting:'equal',weights:{},commission_rate:0.0003,slippage_rate:0.001,max_single_weight:1,max_industry_weight:1,min_holdings:1}};
  let draft = null, task = null, step = 1, opening = 0, epoch = 0, dirty = false, busy = false, saveTimer, saving = null, pollTimer, requestKey = null;
  async function api(path, method='GET', body) {
    const r = await fetch('/api/wizard'+path,{method,headers:{Accept:'application/json',...(body ? {'Content-Type':'application/json'} : {})},body:body ? JSON.stringify(body) : undefined,cache:'no-store'});
    let p; try { p=await r.json(); } catch { throw new Error('服务未返回有效响应，请稍后重试。'); }
    if (!r.ok) { const e=new Error(p.error || `请求失败 (${r.status})`); Object.assign(e,{code:p.code,step:p.step}); throw e; }
    return p;
  }
  function error(e) { $('error').textContent=e.message || String(e); $('error').hidden=false; $('error').focus(); }
  function clearError() {$('error').hidden=true;}
  function localKey(id) { return 'alphalab.wizard.buffer.'+id; }
  function remember() { if (!draft) return; try { localStorage.setItem(localKey(draft.id),JSON.stringify({scope:draft.scope,portfolio:draft.portfolio,weightsText:$('weights').value,revision:draft.revision})); } catch { $('saveStatus').textContent='浏览器无法保存备份，请保持页面直到服务端保存成功。'; } }
  function readScope() { return {market:$('market').value,start_date:$('startDate').value,end_date:$('endDate').value,selection_mode:$('selectionMode').value,symbols:selectionSymbols($('selectionMode').value,$('symbols').value),rule_version:$('ruleVersion').value,top_n:Number($('topN').value),quality_mode:document.querySelector('[name=quality]:checked').value}; }
  function readPortfolio() {
    const weights={};
    for (const line of ($('weighting').value==='custom' ? $('weights').value : '').split(/[\n,，]+/).map(x=>x.trim()).filter(Boolean)) {
      const m=line.match(/^(\d{6})\s*[:：=]\s*([\d.]+)\s*%?$/);
      if (!m || Object.hasOwn(weights,m[1]) || !Number.isFinite(Number(m[2]))) throw new Error('自定义权重请按“000001: 50%”填写，每只股票只填写一次。');
      weights[m[1]]=Number(m[2])/100;
    }
    return {name:$('portfolioName').value,initial_cash:Number($('initialCash').value),weighting:$('weighting').value,weights,commission_rate:Number($('commission').value)/100,slippage_rate:Number($('slippage').value)/100,max_single_weight:$('maxSingle').value===''?null:Number($('maxSingle').value)/100,max_industry_weight:$('maxIndustry').value===''?null:Number($('maxIndustry').value)/100,min_holdings:Number($('minHoldings').value)};
  }
  function syncChoices() {
    const manual=$('selectionMode').value==='manual';
    $('manualScope').hidden=!manual; $('ruleScope').hidden=manual;
    $('weighting').querySelector('[value=score]').disabled=manual;
    $('weighting').querySelector('[value=custom]').disabled=!manual;
    $('customWeights').hidden=$('weighting').value!=='custom';
  }
  function fill() {
    const s={...defaults.scope,...draft.scope}, p={...defaults.portfolio,...draft.portfolio};
    for (const [id,key] of Object.entries({market:'market',startDate:'start_date',endDate:'end_date',selectionMode:'selection_mode',ruleVersion:'rule_version',topN:'top_n'})) $(id).value=s[key];
    $('symbols').value=(s.symbols || []).join(', ');
    document.querySelector(`[name=quality][value=${s.quality_mode==='exploratory' ? 'exploratory' : 'strict'}]`).checked=true;
    for (const [id,key] of Object.entries({portfolioName:'name',initialCash:'initial_cash',weighting:'weighting',minHoldings:'min_holdings'})) $(id).value=p[key];
    for (const [id,key] of Object.entries({commission:'commission_rate',slippage:'slippage_rate',maxSingle:'max_single_weight',maxIndustry:'max_industry_weight'})) $(id).value=p[key]==null?'':p[key]*100;
    $('weights').value=Object.entries(p.weights || {}).map(([s,w])=>`${s}: ${w*100}%`).join('\n');
    syncChoices();
  }
  function changed(scopeChanged) {
    if (!draft) return;
    epoch++; dirty=true; requestKey=null; clearError();
    if (scopeChanged) {
      draft.scope=readScope(); draft.readiness=null;
      if ((draft.scope.selection_mode==='manual' && $('weighting').value==='score') || (draft.scope.selection_mode==='rule' && $('weighting').value==='custom')) $('weighting').value='equal';
    }
    try { draft.portfolio=readPortfolio(); } catch { /* Keep incomplete weight text in browser backup until valid. */ }
    draft.preview=null; syncChoices(); remember();
    $('scopeValidation').textContent=validateScope(readScope());
    $('saveStatus').textContent='有未保存的更改…'; render();
    clearTimeout(saveTimer); saveTimer=setTimeout(()=>save().catch(error),650);
  }
  async function save() {
    clearTimeout(saveTimer);
    if (saving) { await saving; if (dirty) return save(); return; }
    if (!dirty || !draft) return;
    saving=(async()=>{
      while(dirty) {
        const owner=draft.id, sentEpoch=epoch;
        const portfolio=readPortfolio();
        const body={revision:draft.revision,scope:readScope(),portfolio};
        $('saveStatus').textContent='正在保存…'; $('retrySave').hidden=true;
        let result;
        try { result=await api('/drafts/'+encodeURIComponent(owner),'PATCH',body); }
        catch(e) {
          if (e.code==='REVISION_CONFLICT') {
            const fresh=await api('/drafts/'+encodeURIComponent(owner));
            if (draft?.id===owner) { draft.revision=fresh.draft.revision; $('loadServer').hidden=false; }
            e.message='此草稿已在其他页面更新。当前输入保留；点击“重试保存”以当前输入覆盖，或点击“放弃本机更改，读取服务端版本”。';
          }
          throw e;
        }
        if (draft?.id!==owner) return;
        if (acceptResponse(draft,result.draft,sentEpoch,epoch)) { draft=result.draft; dirty=false; localStorage.removeItem(localKey(owner)); }
        else { draft.revision=Math.max(draft.revision,result.draft.revision); remember(); }
      }
      $('saveStatus').textContent='已自动保存'; $('loadServer').hidden=true; render();
    })();
    try { await saving; } catch(e) { $('saveStatus').textContent='保存失败，输入仍保留'; $('retrySave').hidden=false; throw e; } finally { saving=null; }
  }
  function metrics(items) { return '<div class="metrics">'+items.map(([k,v])=>`<div class="metric"><span>${esc(k)}</span><strong>${esc(fmt(v))}</strong></div>`).join('')+'</div>'; }
  function details(value,label='检查详情') { return `<details><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`; }
  function renderReadiness() {
    const r=draft.readiness;
    if (!r) { $('readiness').innerHTML='<div class="notice">尚未检查，或研究范围已修改。请重新检查数据。</div>'; return; }
    const ready=r.status==='READY';
    let html=`<div class="notice ${ready?'success':'danger'}"><strong>${ready?'数据已就绪，可以配置组合':'数据尚未就绪，需先处理以下问题'}</strong></div>`;
    const dates=r.dates || {};
    html+=metrics([['请求开始',draft.scope.start_date],['请求结束',draft.scope.end_date],['数据质量',draft.scope.quality_mode==='strict'?'正式研究':'探索研究'],...Object.entries(dates).map(([k,v])=>[({entry_date:'实际建仓日',exit_date:'实际结束日',end_date:'实际结束日',signal_date:'规则信号日',warmup_start:'预热起点',warmup_start_date:'预热起点',requested_start_date:'请求开始日',requested_end_date:'请求结束日',warmup_sessions:'所需预热交易日',calendar_source:'交易日历来源',horizon:'持有交易日数'}[k] || k),k==='calendar_source'?calendarLabel(v):v])]);
    if (r.issues?.length) html+='<ul>'+r.issues.map(i=>`<li><strong>${esc(i.message || i.code || i)}</strong>${i.action ? `<p>${esc(i.action)}</p>`:''}</li>`).join('')+'</ul>';
    if (r.warnings?.length) html+='<div class="notice">'+r.warnings.map(x=>esc(x.message || x)).join('<br>')+'</div>';
    html+=coverageHtml(r.coverage);
    $('readiness').innerHTML=html;
  }
  function renderPreview() {
    const p=draft.preview; if(!p) { $('preview').innerHTML='<p class="muted">配置修改后需重新计算持仓预览。</p>'; return; }
    const holdings=p.holdings || p.portfolio?.holdings || [];
    let html='<h3>可执行持仓预览</h3>'+metrics([['初始本金',draft.portfolio.initial_cash],['实际持仓数',holdings.length],['剩余现金',p.cash_residual ?? p.cash ?? p.residual_cash ?? p.remaining_cash],['建仓成本',p.total_cost ?? ((p.commission != null || p.slippage != null) ? Number(p.commission || 0)+Number(p.slippage || 0) : p.transaction_cost)]]);
    if (holdings.length) html+='<div class="table-wrap"><table><thead><tr><th>代码 / 名称</th><th>目标权重</th><th>股数</th><th>建仓价格</th></tr></thead><tbody>'+holdings.map(h=>`<tr><td>${esc(h.symbol)} ${esc(h.name)}</td><td>${esc(fmt(Number(h.target_weight ?? h.weight ?? 0)*100))}%</td><td>${esc(fmt(h.shares ?? h.quantity))}</td><td>${esc(fmt(h.entry_price ?? h.price))}</td></tr>`).join('')+'</tbody></table></div>';
    if(p.diagnostics && Object.keys(p.diagnostics).length) html+='<div class="notice">'+Object.entries(p.diagnostics).map(([symbol,reason])=>esc(symbol+': '+reason)).join('<br>')+'</div>';
    $('preview').innerHTML=html+details(p,'完整成交与现金预览');
  }
  function renderTask(t) {
    if (!t) return '';
    const labels={QUEUED:'等待执行',RUNNING:'正在执行',SUCCEEDED:'已完成',PARTIAL:'部分完成，仍有数据缺口',FAILED:'执行失败',CANCELLED:'已取消',INTERRUPTED:'服务已重启，任务中断',CANCELLING:'正在取消'};
    const elapsed=Math.max(0,Math.round((Date.parse(t.updated_at || new Date().toISOString())-Date.parse(t.created_at))/1000));
    return `<div class="notice ${['FAILED','PARTIAL','INTERRUPTED'].includes(t.status)?'danger':''}"><strong>${esc(labels[t.status] || t.status)}</strong><p>${esc(t.stage || '等待后台处理')}</p><small>已用时 ${Number.isFinite(elapsed)?elapsed:0} 秒 · ${active(t)?'暂无可靠剩余时间估计':'状态已保存'}</small>${t.error?`<p>${esc(t.error.message || t.error)}</p>`:''}</div>`;
  }
  function render() {
    if (!draft) return;
    const permissions=gates(draft,task,dirty,busy), {ready,running}=permissions, preview=ready && Boolean(draft.preview);
    $('steps').innerHTML=names.map((n,i)=>`<button type="button" data-step="${i+1}" ${i+1===step?'aria-current="step"':''} ${(i===2&&!ready)||(i===3&&!preview)||(i===4&&!permissions.canViewResult)?'disabled':''}>${i+1}. ${n}<small>${i===1?(ready?'可继续':'需检查'):i===2?(!ready?'等待数据就绪':preview?'预览已完成':'待配置'):i===3?(!preview?'等待预览':'可提交'):i===4?(task?.kind==='run'?'已创建任务':'等待运行'): '范围在先'}</small></button>`).join('');
    for(let i=1;i<=5;i++) $('step'+i).hidden=i!==step;
    $('toPortfolio').disabled=!ready || busy || running; $('toConfirm').disabled=!preview || busy || running;
    $('dataGate').textContent=ready?'当前范围已通过所选质量模式的检查。':'历史数据全部通过当前模式检查后才能继续；补数部分成功时仍需处理剩余缺口。';
    $('check').disabled=busy || running;
    $('prepare').disabled=!permissions.canPrepare;
    $('prepare').textContent=ready?'数据已就绪，无需补数':'补齐所需数据 / 重试';
    $('run').disabled=busy || running || !preview;
    $('cancelPrepare').hidden=!(task?.kind==='prepare' && running);
    $('prepareTask').innerHTML=task?.kind==='prepare'?renderTask(task):'';
    $('runTask').innerHTML=task?.kind==='run'?renderTask(task):'<p>尚未启动运行，请先完成配置与确认。</p>';
    $('retryRun').hidden=!(task?.kind==='run' && ['FAILED','CANCELLED','INTERRUPTED'].includes(task.status));
    $('recoverTask').hidden=!(task?.kind==='run' && ['FAILED','CANCELLED','INTERRUPTED'].includes(task.status));
    renderReadiness(); renderPreview();
    $('confirmation').innerHTML=metrics([['组合名称',draft.portfolio.name],['初始本金',draft.portfolio.initial_cash],['研究区间',draft.scope.start_date+' → '+draft.scope.end_date],['持有方式','买入并持有至结束日'],['范围',draft.scope.selection_mode==='manual' ? draft.scope.symbols.join(', ') : 'fixed_v0 · '+draft.scope.top_n+' 只'],['数据质量',draft.scope.quality_mode==='strict'?'正式研究':'探索研究'],['佣金',draft.portfolio.commission_rate*100+'%'],['滑点',draft.portfolio.slippage_rate*100+'%'],['权重方式',({equal:'等权',score:'规则评分',custom:'自定义'}[draft.portfolio.weighting])],['最低持仓数',draft.portfolio.min_holdings],['单股权重上限',draft.portfolio.max_single_weight==null?'不限制':draft.portfolio.max_single_weight*100+'%'],['行业权重上限',draft.portfolio.max_industry_weight==null?'不限制':draft.portfolio.max_industry_weight*100+'%']])+(draft.scope.quality_mode==='exploratory'?'<div class="notice danger">本次结果为探索研究，保留数据检查所列历史身份与行业快照限制。</div>':'')+metrics(Object.entries(draft.readiness?.dates || {}).filter(([key])=>['signal_date','entry_date','exit_date','warmup_start_date','horizon','calendar_source'].includes(key)).map(([key,value])=>[({signal_date:'规则信号日',entry_date:'实际建仓日',exit_date:'实际结束日',warmup_start_date:'预热起点',horizon:'持有交易日数',calendar_source:'交易日历来源'}[key]),key==='calendar_source'?calendarLabel(value):value]));
    const result=task?.kind==='run' && task.status==='SUCCEEDED' ? task.result : null;
    $('results').innerHTML=result?.run_id?`<div class="notice success"><h3>运行已保存</h3><p>冻结配置及结果可从最近运行重新打开。</p><a href="/research/review/${encodeURIComponent(result.run_id)}/">打开净值、收益、回撤与持仓审阅 →</a></div>`+metrics([['总收益率',result.summary?.total_return == null ? '—' : (result.summary.total_return*100).toFixed(2)+'%'],['绝对盈亏',result.summary?.profit_loss],['期末权益',result.summary?.ending_equity]])+details(result.summary || {},'运行摘要'):'';
    document.querySelectorAll('#scopeForm input,#scopeForm select,#scopeForm textarea,#portfolioForm input,#portfolioForm select,#portfolioForm textarea').forEach(el=>el.disabled=running || busy);
    syncChoices();
  }
  function go(n) { step=n; render(); $('step'+n).querySelector('h2').focus(); }
  async function command(kind) {
    if (busy) return;
    clearError();
    const validation=validateScope(readScope()); if (validation) { go(1); throw new Error(validation); }
    await save(); if(dirty) return;
    busy=true; render();
    const sentEpoch=epoch, id=draft.id;
    try {
      if(kind==='run' && !requestKey) {
        const key='alphalab.wizard.submit.'+draft.id+'.'+draft.revision;
        requestKey=localStorage.getItem(key) || draft.id+':'+draft.revision+':'+(crypto.randomUUID ? crypto.randomUUID() : Date.now());
        localStorage.setItem(key,requestKey);
      }
      const p=await api('/drafts/'+encodeURIComponent(id)+'/'+kind,'POST',{revision:draft.revision,...(kind==='run'?{idempotency_key:requestKey}:{})});
      if (!acceptResponse(draft,p.draft,sentEpoch,epoch)) return;
      draft=p.draft; if (p.preview) draft.preview=p.preview;
      if(p.task) {task=p.task; poll();}
      go(kind==='run'?5:kind==='preview'?3:2);
    } catch(e) { if(e.step===2 || e.step==='data' || ['STALE_READINESS','DATA_CHANGED','DATA_NOT_READY'].includes(e.code)) { draft.readiness=null; draft.preview=null; go(2); } throw e; }
    finally { busy=false; render(); }
  }
  async function poll() {
    clearTimeout(pollTimer); if(!task) return;
    const id=task.id, owner=draft.id, sentEpoch=epoch;
    try {
      const p=await api('/tasks/'+encodeURIComponent(id));
      if (draft?.id!==owner || task?.id!==id) return;
      task=p.task;
      if(!active(task)) {
        const fresh=await api('/drafts/'+encodeURIComponent(owner));
        if(!dirty && acceptResponse(draft,fresh.draft,sentEpoch,epoch)) draft=fresh.draft;
      }
      render();
      if(active(task)) pollTimer=setTimeout(poll,1500);
    } catch(e) { if(draft?.id===owner) { error(new Error('读取任务进度失败，正在重连。'+e.message)); pollTimer=setTimeout(poll,4000); } }
  }
  async function openDraft(id) {
    const request=++opening; clearError(); const p=await api('/drafts/'+encodeURIComponent(id));
    if(request!==opening)return;
    epoch++; draft=p.draft; task=null; dirty=false; requestKey=null;
    fill();
    const saved=localStorage.getItem(localKey(id));
    if(saved) { try { const b=JSON.parse(saved); draft.scope=b.scope; draft.portfolio=b.portfolio; draft.readiness=null; draft.preview=null; dirty=true; fill(); if(b.weightsText!=null)$('weights').value=b.weightsText; $('saveStatus').textContent='已恢复本机未保存输入，请重试保存'; $('retrySave').hidden=false; } catch {} }
    else {$('saveStatus').textContent='已恢复服务端草稿'; $('retrySave').hidden=true;}
    history.replaceState(null,'','/wizard?draft='+encodeURIComponent(id)); $('home').hidden=true; $('editor').hidden=false;
    step=draft.preview?4:draft.readiness?.status==='READY'?3:draft.readiness?2:1;
    if(draft.task_id) { const response=await api('/tasks/'+encodeURIComponent(draft.task_id)); if(request!==opening)return; task=response.task; if(task.kind==='run')step=5; else if(active(task))step=2; poll(); }
    go(step);
  }
  async function home() {
    if(dirty) await save(); opening++; clearTimeout(pollTimer); draft=null; task=null; epoch++;
    $('home').hidden=false; $('editor').hidden=true; history.replaceState(null,'','/wizard');
    const [d,r]=await Promise.all([api('/drafts'),api('/runs')]);
    const unfinished=unfinishedDrafts(d.drafts);
    $('draftList').innerHTML=unfinished.length?unfinished.map(x=>`<div class="record"><div><strong>${esc(x.portfolio?.name || '未命名股票组合')}</strong><p>${esc(x.scope?.start_date || '未选日期')} → ${esc(x.scope?.end_date || '')}</p><small>${esc(x.updated_at || '')}</small></div><button data-draft="${esc(x.id)}" type="button">继续创建</button></div>`).join(''):'<p class="muted">暂无未完成创建。新建后将自动保存进度；已完成的模拟可从最近运行打开。</p>';
    $('runList').innerHTML=r.runs.length?r.runs.map(x=>`<div class="record"><div><strong>${esc(x.name || x.portfolio?.name || x.run_id)}</strong><p>${esc(x.created_at || '')}</p></div><div class="actions"><a href="/research/review/${encodeURIComponent(x.run_id)}/">打开结果 →</a><button type="button" data-copy-run="${esc(x.run_id)}" ${x.can_copy===false?'disabled':''}>复制配置</button>${x.can_copy===false?'<small class="muted">此历史运行未保存向导配置，请新建组合。</small>':''}</div></div>`).join(''):'<p class="muted">尚无已保存运行。完成模拟后将在这里显示。</p>';
  }
  async function create(copy=false) {
    await save(); const body=copy?{source_id:draft.id}:defaults;
    const p=await api('/drafts','POST',body); await openDraft(p.draft.id);
  }
  const action=(id,fn)=>$(id).addEventListener('click',()=>Promise.resolve().then(fn).catch(error));
  action('loadServer',async()=>{const id=draft.id;clearTimeout(saveTimer);dirty=false;localStorage.removeItem(localKey(id));$('loadServer').hidden=true;await openDraft(id);});
  action('newDraft',()=>create()); action('copyDraft',()=>create(true)); action('backHome',home); action('retrySave',save);
  $('draftList').addEventListener('click',e=>{ const b=e.target.closest('[data-draft]'); if(b)openDraft(b.dataset.draft).catch(error); });
  $('runList').addEventListener('click',e=>{const b=e.target.closest('[data-copy-run]');if(b)api('/drafts','POST',{source_run_id:b.dataset.copyRun}).then(p=>openDraft(p.draft.id)).catch(error);});
  $('steps').addEventListener('click',e=>{const b=e.target.closest('[data-step]');if(b && !b.disabled)go(Number(b.dataset.step));});
  $('scopeForm').addEventListener('input',()=>changed(true)); $('portfolioForm').addEventListener('input',()=>changed(false));
  $('scopeForm').addEventListener('submit',e=>{e.preventDefault();command('check').catch(error);});
  $('portfolioForm').addEventListener('submit',e=>{e.preventDefault();command('preview').catch(error);});
  for(const [id,n] of Object.entries({editScope:1,toPortfolio:3,backData:2,toConfirm:4,backPortfolio:3,returnConfig:2})) action(id,()=>go(n));
  action('check',()=>command('check')); action('prepare',()=>command('prepare')); action('run',()=>command('run'));
  action('retryRun',()=>{requestKey=null;localStorage.removeItem('alphalab.wizard.submit.'+draft.id+'.'+draft.revision);return command('run');});
  action('recoverTask',async()=>{const p=await api('/drafts','POST',{source_task_id:task.id});await openDraft(p.draft.id);});
  action('cancelPrepare',async()=>{ const p=await api('/tasks/'+encodeURIComponent(task.id)+'/cancel','POST',{}); task=p.task; render(); poll(); });
  window.addEventListener('beforeunload',e=>{if(dirty){remember();e.preventDefault();e.returnValue='';}});
  const initial=new URLSearchParams(location.search).get('draft'); (initial?openDraft(initial):home()).catch(error);
})();
