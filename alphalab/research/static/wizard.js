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
      canPrepare:!ready && !pending && !running && (value.readiness?.repair_plan?.executable_count == null || value.readiness.repair_plan.executable_count > 0), canViewResult:currentTask?.kind === 'run'};
  }
  function diagnosticLabel(value) { return ({identity:'身份',prices:'价格与复权',coverage:'覆盖',warmup:'预热',signal:'信号',entry:'建仓',holding:'持有',exit:'退出',data_missing:'数据缺失',data_conflict:'数据冲突',market_event:'已确认市场事件',status_unknown:'状态待核实',source_capability:'来源能力不足'}[value] || value || '未标注'); }
  function coverageHtml(rows, page=0, issues=[]) {
    if (!Array.isArray(rows) || !rows.length) return '<p class="muted">尚未得到逐股覆盖检查，请先处理上方数据问题。</p>';
    return '<h3>历史数据覆盖与缺口</h3><div class="coverage-list">'+rows.slice(page*25,page*25+25).map(row=>{
      const resolutions=new Set(issues.filter(i=>i.symbol===row.symbol && !['info','warning'].includes(i.severity)).map(i=>i.resolution));
      const state=resolutions.has('unsupported')?'UNSUPPORTED':resolutions.has('verify')?'NEEDS_VERIFICATION':issues.some(i=>i.symbol===row.symbol && i.code==='ADJUSTMENT_STANDARDIZATION_REQUIRED')?'NEEDS_ADJUSTMENT':row.status;
      const status={READY:'已就绪',NEEDS_ADJUSTMENT:'待统一后复权',MISSING:'需补齐',INVALID:'需修复数据',NEEDS_VERIFICATION:'需核实交易状态',UNSUPPORTED:'当前能力不支持'}[state] || '待检查';
      const adjustment={hfq:'后复权',qfq:'前复权',raw:'不复权',none:'不复权',mixed:'复权口径不一致'}[row.adjustment] || row.adjustment;
      const missing=row.missing_dates || [];
      return `<article class="notice ${state==='READY'?'success':'danger'}"><strong>${esc(row.symbol)} ${esc(row.name)} · ${esc(status)}</strong><p>已覆盖 ${esc(row.available_sessions ?? 0)} / ${esc(row.required_sessions ?? '待确定')} 个所需交易日${adjustment?' · '+esc(adjustment):''}</p>${missing.length?`<p>缺少 ${missing.length} 个交易日：${missing.map(esc).join('、')}</p>`:''}</article>`;
    }).join('')+'</div>';
  }
  function repairLabel(r) {
    if (r?.status==='READY') return '数据已就绪，无需修复';
    const plan=r?.repair_plan;
    if (plan?.executable_count == null) return '补齐所需数据 / 重试';
    const n=plan.executable_count, kinds=new Set((plan.actions || []).map(a=>['verify','verify_status'].includes(a.kind)?'verify':['download','bars','entry','calendar','universe'].includes(a.kind)?'download':a.kind));
    if (!n) return '当前来源无法自动解决剩余问题';
    return `${kinds.size===1 && kinds.has('verify')?'核实交易状态':kinds.size===1 && kinds.has('download')?'修复可处理项':'处理可自动处理项'}（${n} 项）`;
  }
  function filterIssues(issues, filters={}) {
    return issues.filter(i=>(!filters.symbol || String(i.symbol || '').includes(filters.symbol)) && (!filters.category || i.category===filters.category) && (!filters.phase || i.phase===filters.phase));
  }
  function blockers(r) { return (r?.issues || []).filter(i=>!['info','warning'].includes(i.severity)); }
  function researchRestrictionsOnly(r) {
    const rows=blockers(r);
    return r?.repair_plan?.executable_count===0 && rows.length>0 && rows.every(i=>i.code==='PIT_UNAVAILABLE' || i.resolution==='unsupported');
  }
  function readinessHeadline(r) {
    if(r?.status==='READY') return '当前研究已通过检查，可以配置组合';
    if(researchRestrictionsOnly(r)) return r.repair_summary?.resolved>0 && r.repair_summary.resolved===r.repair_summary.attempted ? '数据修复完成，研究条件尚未满足' : '当前没有待补行情，研究条件尚未满足';
    return '当前研究还不能开始，请处理以下检查项';
  }
  function readinessGate(r) {
    if(r?.status==='READY') return '当前范围已通过所选质量模式的检查。';
    if(!r) return '请先完成当前研究范围的数据检查。';
    return '暂不能配置组合：'+blockers(r).map(i=>i.code==='PIT_UNAVAILABLE'?'需选择研究模式或补充历史资料':i.code==='DELISTED'?`${i.symbol} 退市清算暂不支持`:(i.message || i.code)).join('；')+'。';
  }
  function explorationHelp(r) {
    const others=blockers(r).filter(i=>i.code!=='PIT_UNAVAILABLE');
    return '探索研究接受历史身份或行业资料不完整的限制，结果会明确标记为探索研究。'+(others.length ? '切换后仍不能开始回测：'+others.map(i=>i.code==='DELISTED'?`${i.symbol} 的退市清算暂不支持`:(i.message || i.code)).join('；')+'。' : '切换后将重新检查，检查通过后才能继续配置。');
  }
  function issuesHtml(issues, filters={}) {
    const filtered=filterIssues(issues,filters);
    const group=i=>['info','warning'].includes(i.severity)?i.severity:i.code==='PIT_UNAVAILABLE'?'mode':i.resolution==='unsupported'?'capability':['download','verify'].includes(i.resolution)?i.resolution:'source';
    return [['mode','研究模式选择'],['capability','系统能力限制'],['download','可自动处理'],['verify','需核实交易状态'],['source','需补充来源证据'],['warning','提示（不阻断）'],['info','说明（不阻断）']].map(([key,label])=>{
      const rows=filtered.filter(i=>group(i)===key), blocking=!['info','warning'].includes(key);
      if (!rows.length) return '';
      return `<details class="issue-group ${blocking?'danger':key}" ${blocking?'open':''}><summary>${label} · ${rows.length} ${blocking?'项':'条'}</summary>${rows.map(i=>{
        const title=i.code==='PIT_UNAVAILABLE'?'缺少完整的历史身份／行业资料':i.code==='DELISTED'?`${i.symbol} 在持有期间退市，系统暂不支持清算`:[i.symbol,i.message || i.code].filter(Boolean).join(' · ');
        const action=i.code==='PIT_UNAVAILABLE'?'当前资料无法完整还原当时的上市身份或所属行业。可补充带真实生效区间的历史资料，或在下方接受限制并切换探索研究。':i.code==='DELISTED'?'重复补数无法解决，探索模式也不能绕过。需要增加退市处理能力；修改日期或股票范围会改变本次研究。':i.action || '';
        return `<details ${blocking?'open':''}><summary>${esc(title)}</summary><p>${esc(action)}</p>${i.code==='SOURCE_ADJUSTMENT_UNAVAILABLE'?`<button type="button" data-retry-source="${esc(i.symbol)}">重新核实来源</button><p>向当前来源重新请求此股票的数据，验证响应后更新检查结果。</p>`:''}<details><summary>技术详情</summary><p>阶段：${esc(diagnosticLabel(i.phase))} · 类型：${esc(diagnosticLabel(i.category || i.code))}</p>${i.date_ranges?.length?'<p>相关日期：'+i.date_ranges.map(d=>esc(d.start)+' → '+esc(d.end)).join('、')+'</p>':''}<pre>${esc(JSON.stringify(i.evidence || {},null,2))}</pre></details></details>`;
      }).join('')}</details>`;
    }).join('') || '<p class="muted">没有符合筛选条件的检查项。</p>';
  }
  function attemptSummary(t) {
    const summary=t?.result?.repair_summary;
    if (summary?.resolved>0 && summary.resolved===summary.attempted) return `本轮 ${summary.resolved} 项修复全部完成，没有失败项。数据已保存，无需重复补数。`;
    if (summary) return `来源不支持 ${summary.unsupported ?? (t?.result?.repair_attempts || []).filter(a=>a.status==='unsupported').length} 项 · 本轮已处理 ${summary.resolved || 0} 项 · 未变化 ${summary.unchanged || 0} 项 · 请求失败 ${summary.failed || 0} 项。已验证的缓存与草稿保留。`;
    const attempts=t?.result?.repair_attempts || t?.result?.attempts || t?.progress?.attempts || [];
    if (!Array.isArray(attempts) || !attempts.length) return ['PARTIAL','FAILED','CANCELLED','INTERRUPTED'].includes(t?.status)?'已验证的缓存与草稿保留；重新检查后继续处理剩余问题。':'';
    const count=(...states)=>attempts.filter(a=>states.includes(a.outcome || a.status)).length;
    return `本轮已处理 ${count('resolved','repaired','saved','published')} 项 · 未变化 ${count('unchanged','unresolved')} 项 · 来源不支持 ${count('unsupported')} 项 · 请求失败 ${count('failed')} 项。已验证的缓存与草稿保留。`;
  }
  function taskTiming(t, now=Date.now()) {
    const running=['QUEUED','RUNNING','CANCELLING'].includes(t.status);
    const seconds=(a,b)=>Math.max(0,Math.floor((a-Date.parse(b))/1000)) || 0;
    const heartbeatAge=seconds(now,t.heartbeat_at || t.updated_at);
    const progressAge=seconds(now,t.progress_at || t.updated_at);
    return {elapsed:seconds(running?now:Date.parse(t.updated_at),t.created_at),heartbeatAge,progressAge,stale:running && (heartbeatAge>10 || progressAge>15)};
  }
  function adjustmentHtml(summary) {
    if (!summary) return '';
    const labels={qfq:'前复权',hfq:'后复权',none:'未复权',mixed:'单股混用',unknown:'口径未知'};
    return '<section class="notice"><h3>复权口径与来源</h3>'+ (summary.target ? `<p><strong>统一目标：后复权 · 待统一 ${esc((summary.remaining_symbols || []).length)} 只</strong></p>` : '') + '<p>'+ Object.entries(summary.stock_counts || {}).map(([key,n])=>`${esc(labels[key] || key)}：${esc(n)} 只`).join(' · ')+`</p><p>单股混用：${esc((summary.mixed_symbols || []).length)} 只 · 未复权／未知：${esc((summary.unknown_symbols || []).length)} 只</p><p>${esc(summary.execution_basis)}</p><p>${esc(summary.limitation)}</p><details><summary>查看数据来源</summary>`+Object.entries(summary.source_stock_counts || {}).map(([key,n])=>`<p>${esc(key)}：${esc(n)} 只</p>`).join('')+'</details></section>';
  }
  function pendingReadinessHtml(t) {
    if (t?.kind !== 'prepare' || !['QUEUED','RUNNING','CANCELLING'].includes(t.status)) return '';
    const summary=t.configuration?.readiness?.adjustment_summary;
    return '<div class="notice"><strong>'+ (summary?.target==='hfq' && summary.remaining_symbols?.length ? '正在统一后复权数据' : '正在准备历史数据') + '</strong><p>完成后自动重新校验。以下为本轮开始时的统计，不代表当前已就绪。</p></div>'+adjustmentHtml(summary);
  }
  function liquidationHtml(summary) {
    if (summary?.liquidation_status !== 'OPEN_POSITION') return '';
    const values=[['已实现盈亏',summary.realized_profit_loss],['未实现盈亏',summary.unrealized_profit_loss],['未平仓估值',summary.unrealized_holdings_value],['期末现金',summary.realized_cash]];
    return '<div class="notice warning"><strong>结束日停牌，仍有未平仓持仓</strong><p>期末权益及总收益包含未实现估值，不代表全部卖出；停牌持仓未收取卖出费用。</p>'+values.map(([label,value])=>`<p>${label}：${esc(value == null ? '—' : Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2}))}</p>`).join('')+'</div>';
  }
  function taskForDraft(currentTask, value) { return currentTask?.id === value.task_id ? currentTask : null; }
  function selectionSymbols(mode, text) { return mode==='manual' ? text.split(/[\s,，;；]+/).filter(Boolean) : []; }
  function unfinishedDrafts(rows) { return rows.filter(row=>!(row.task_kind==='run' && row.task_status==='SUCCEEDED')); }
  if (typeof module !== 'undefined') module.exports = {validateScope, acceptResponse, coverageHtml, calendarLabel, gates, unfinishedDrafts, selectionSymbols, repairLabel, filterIssues, issuesHtml, attemptSummary, diagnosticLabel, taskForDraft, taskTiming, liquidationHtml, adjustmentHtml, pendingReadinessHtml, readinessHeadline, readinessGate, explorationHelp};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const fmt = v => v == null ? '—' : typeof v === 'number' ? v.toLocaleString('zh-CN',{maximumFractionDigits:2}) : String(v);
  const active = t => t && ['QUEUED','RUNNING','CANCELLING'].includes(t.status);
  const names = ['选择范围','准备数据','配置组合','确认运行','查看结果'];
  const defaults = {scope:{market:'a_share',start_date:'2021-03-01',end_date:'2025-06-30',selection_mode:'manual',symbols:[],rule_version:'fixed_v0',top_n:10,quality_mode:'strict'},portfolio:{name:'我的股票组合',initial_cash:100000,weighting:'equal',weights:{},commission_rate:0.0003,slippage_rate:0.001,max_single_weight:1,max_industry_weight:1,min_holdings:1}};
  let statusUnavailable=false, coveragePage=0, issueFilters={}, readinessRendered=undefined;
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
      draft.scope=readScope(); draft.readiness=null; draft.task_id=null;
      task=taskForDraft(task,draft); clearTimeout(pollTimer); statusUnavailable=false;
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
        if (acceptResponse(draft,result.draft,sentEpoch,epoch)) { draft=result.draft; task=taskForDraft(task,draft); if (!task) clearTimeout(pollTimer); dirty=false; localStorage.removeItem(localKey(owner)); }
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
    if (!r) { $('readiness').innerHTML=pendingReadinessHtml(task) || '<div class="notice">尚未检查，或研究范围已修改。请重新检查数据。</div>'; return; }
    const ready=r.status==='READY';
    let html=`<div class="notice ${ready?'success':'danger'}"><strong>${esc(readinessHeadline(r))}</strong></div>`;
    html+=adjustmentHtml(r.adjustment_summary);
    const dates=r.dates || {};
    html+=metrics([['请求开始',draft.scope.start_date],['请求结束',draft.scope.end_date],['数据质量',draft.scope.quality_mode==='strict'?'正式研究':'探索研究'],...Object.entries(dates).map(([k,v])=>[({entry_date:'实际建仓日',exit_date:'实际结束日',end_date:'实际结束日',signal_date:'规则信号日',warmup_start:'预热起点',warmup_start_date:'预热起点',requested_start_date:'请求开始日',requested_end_date:'请求结束日',warmup_sessions:'所需预热交易日',calendar_source:'交易日历来源',horizon:'持有交易日数'}[k] || k),k==='calendar_source'?calendarLabel(v):v])]);
    html+=metrics([['已就绪股票（只）',(r.coverage || []).filter(x=>x.status==='READY').length],['待核实问题（条）',(r.issues || []).filter(x=>!['info','warning'].includes(x.severity) && x.resolution==='verify').length],['可执行动作（项）',r.repair_plan?.executable_count ?? '待确定'],['需选择／不支持（条）',(r.issues || []).filter(x=>!['info','warning'].includes(x.severity) && ['user','unsupported'].includes(x.resolution)).length]]);
    if (r.repair_plan?.executable_count===0 && !ready) html+='<div class="notice">当前没有可自动补数的任务。请按下方说明处理研究条件；重复补数不会消除这些限制。</div>';
    const options=(key)=>[...new Set((r.issues || []).map(i=>i[key]).filter(Boolean))].map(v=>`<option value="${esc(v)}" ${issueFilters[key]===v?'selected':''}>${esc(diagnosticLabel(v))}</option>`).join('');
    if (r.issues?.length) html+=`<div class="form-grid"><label>股票筛选<input id="issueSymbol" value="${esc(issueFilters.symbol || '')}" placeholder="六位代码或部分代码"></label><label>问题类型<select id="issueCategory"><option value="">全部类型</option>${options('category')}</select></label><label>研究阶段<select id="issuePhase"><option value="">全部阶段</option>${options('phase')}</select></label></div><div id="issueGroups">${issuesHtml(r.issues,issueFilters)}</div>`;
    if (r.repair_plan?.actions?.length) html+=details(r.repair_plan.actions,'查看本轮自动处理动作');
    if (r.warnings?.length) html+='<div class="notice">'+r.warnings.map(x=>esc(x.message || x)).join('<br>')+'</div>';
    const pages=Math.max(1,Math.ceil((r.coverage || []).length/25)); coveragePage=Math.min(coveragePage,pages-1);
    html+='<details id="coverageDetails"><summary>查看数据覆盖（'+(r.coverage || []).length+' 只股票）</summary>'+coverageHtml(r.coverage,coveragePage,r.issues || [])+`<div class="actions"><button type="button" data-page="${coveragePage-1}" ${coveragePage===0?'disabled':''}>上一页</button><span>第 ${coveragePage+1} / ${pages} 页 · 每页 25 只股票</span><button type="button" data-page="${coveragePage+1}" ${coveragePage+1>=pages?'disabled':''}>下一页</button><button type="button" id="exportDiagnostics">导出原始诊断</button></div></details>`;
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
    const labels={QUEUED:'等待执行',RUNNING:'正在执行',SUCCEEDED:'已完成',PARTIAL:'部分完成，仍有研究阻断',FAILED:'执行失败',CANCELLED:'已取消',INTERRUPTED:'服务已重启，任务中断',CANCELLING:'正在取消'};
    const {elapsed,heartbeatAge,progressAge,stale}=taskTiming(t);
    return `<div class="notice ${['FAILED','PARTIAL','INTERRUPTED'].includes(t.status)?'danger':''}"><strong>${esc(t.status==='PARTIAL' && researchRestrictionsOnly(t.result)?readinessHeadline(t.result):(labels[t.status] || t.status))}</strong><p>${esc(t.status==='PARTIAL' && researchRestrictionsOnly(t.result)?'修复记录已保存；剩余研究条件见上方说明。':t.stage === '仍有数据缺口，请处理后继续' ? '仍有研究阻断，请查看对应处理方式' : (t.stage || '等待后台处理'))}</p><small>已用时 ${Number.isFinite(elapsed)?elapsed:0} 秒 · ${active(t)?`最近心跳 ${heartbeatAge} 秒前 · 最近进度 ${progressAge} 秒前`:'状态已保存'}</small>${stale?'<p class="progress-warning">进度暂不可确认；可刷新状态或取消任务。心跳仅表示服务仍在响应。</p>':''}<p>${esc(attemptSummary(t))}</p>${t.error?`<p>${esc(t.error.message || t.error)}</p>`:''}</div>`;
  }
  function render() {
    if (!draft) return;
    const permissions=gates(draft,task,dirty,busy || statusUnavailable), {ready,running}=permissions, preview=ready && Boolean(draft.preview);
    $('steps').innerHTML=names.map((n,i)=>`<button type="button" data-step="${i+1}" ${i+1===step?'aria-current="step"':''} ${(i===2&&!ready)||(i===3&&!preview)||(i===4&&!permissions.canViewResult)?'disabled':''}>${i+1}. ${n}<small>${i===1?(ready?'可继续':'需检查'):i===2?(!ready?'等待数据就绪':preview?'预览已完成':'待配置'):i===3?(!preview?'等待预览':'可提交'):i===4?(task?.kind==='run'?'已创建任务':'等待运行'): '范围在先'}</small></button>`).join('');
    for(let i=1;i<=5;i++) $('step'+i).hidden=i!==step;
    $('toPortfolio').disabled=!permissions.canConfigure; $('toConfirm').disabled=!preview || busy || running;
    $('dataGate').textContent=readinessGate(draft.readiness);
    $('check').disabled=busy || running;
    $('prepare').disabled=!permissions.canPrepare;
    $('prepare').hidden=draft.readiness?.repair_plan?.executable_count===0;
    $('prepare').textContent=repairLabel(draft.readiness);
    if (statusUnavailable) { $('prepare').disabled=true; $('check').disabled=true; $('run').disabled=true; }
    $('refreshTask').hidden=!(statusUnavailable || running); $('taskConnection').hidden=!statusUnavailable;
    $('exploreChoice').hidden=draft.scope.quality_mode==='exploratory' || !(draft.readiness?.issues || []).some(i=>!['info','warning'].includes(i.severity) && i.code==='PIT_UNAVAILABLE');
    $('explorationHelp').textContent=explorationHelp(draft.readiness);
    $('applyExploratory').disabled=busy || running || statusUnavailable || !$('acceptExploratory').checked;
    $('run').disabled=busy || running || statusUnavailable || !preview;
    $('cancelPrepare').textContent=task?.kind==='check'?'取消检查':task?.kind==='retry_source'?'取消来源核实':'取消数据准备';
    $('cancelPrepare').hidden=!(['check','prepare','retry_source'].includes(task?.kind) && running);
    $('prepareTask').innerHTML=['check','prepare','retry_source'].includes(task?.kind)?renderTask(task)+(task.result?.repair_attempts?.length?details(task.result.repair_attempts,'本轮处理记录与未解决原因'):''):'';
    $('runTask').innerHTML=task?.kind==='run'?renderTask(task):'<p>尚未启动运行，请先完成配置与确认。</p>';
    $('retryRun').hidden=!(task?.kind==='run' && ['FAILED','CANCELLED','INTERRUPTED'].includes(task.status));
    $('recoverTask').hidden=!(task?.kind==='run' && ['FAILED','CANCELLED','INTERRUPTED'].includes(task.status));
    if (readinessRendered!==draft.readiness) { readinessRendered=draft.readiness; coveragePage=0; renderReadiness(); } renderPreview();
    $('confirmation').innerHTML=metrics([['组合名称',draft.portfolio.name],['初始本金',draft.portfolio.initial_cash],['研究区间',draft.scope.start_date+' → '+draft.scope.end_date],['持有方式','买入并持有至结束日'],['范围',draft.scope.selection_mode==='manual' ? draft.scope.symbols.join(', ') : 'fixed_v0 · '+draft.scope.top_n+' 只'],['数据质量',draft.scope.quality_mode==='strict'?'正式研究':'探索研究'],['佣金',draft.portfolio.commission_rate*100+'%'],['滑点',draft.portfolio.slippage_rate*100+'%'],['权重方式',({equal:'等权',score:'规则评分',custom:'自定义'}[draft.portfolio.weighting])],['最低持仓数',draft.portfolio.min_holdings],['单股权重上限',draft.portfolio.max_single_weight==null?'不限制':draft.portfolio.max_single_weight*100+'%'],['行业权重上限',draft.portfolio.max_industry_weight==null?'不限制':draft.portfolio.max_industry_weight*100+'%']])+(draft.scope.quality_mode==='exploratory'?'<div class="notice danger">本次结果为探索研究，保留数据检查所列历史身份与行业快照限制。</div>':'')+metrics(Object.entries(draft.readiness?.dates || {}).filter(([key])=>['signal_date','entry_date','exit_date','warmup_start_date','horizon','calendar_source'].includes(key)).map(([key,value])=>[({signal_date:'规则信号日',entry_date:'实际建仓日',exit_date:'实际结束日',warmup_start_date:'预热起点',horizon:'持有交易日数',calendar_source:'交易日历来源'}[key]),key==='calendar_source'?calendarLabel(value):value]));
    const result=task?.kind==='run' && task.status==='SUCCEEDED' ? task.result : null;
    $('results').innerHTML=result?.run_id?`<div class="notice success"><h3>运行已保存</h3><p>冻结配置及结果可从最近运行重新打开。</p><a href="/research/review/${encodeURIComponent(result.run_id)}/">打开净值、收益、回撤与持仓审阅 →</a></div>`+metrics([['总收益率',result.summary?.total_return == null ? '—' : (result.summary.total_return*100).toFixed(2)+'%'],['绝对盈亏',result.summary?.profit_loss],['期末权益',result.summary?.ending_equity]])+liquidationHtml(result.summary)+details(result.summary || {},'运行摘要'):'';
    document.querySelectorAll('#scopeForm input,#scopeForm select,#scopeForm textarea,#portfolioForm input,#portfolioForm select,#portfolioForm textarea').forEach(el=>el.disabled=running || busy || statusUnavailable);
    syncChoices();
  }
  function go(n) { step=n; render(); $('step'+n).querySelector('h2').focus(); }
  async function command(kind, extra={}) {
    if (busy || statusUnavailable || active(task)) return;
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
      const p=await api('/drafts/'+encodeURIComponent(id)+'/'+kind,'POST',{revision:draft.revision,...extra,...(kind==='run'?{idempotency_key:requestKey}:{}),...(kind==='prepare' && draft.readiness?.repair_plan?.plan_id?{plan_id:draft.readiness.repair_plan.plan_id}:{})});
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
      statusUnavailable=false; clearError(); render();
      if(active(task)) pollTimer=setTimeout(poll,1500);
    } catch(e) { if(draft?.id===owner && task?.id===id) { statusUnavailable=true; render(); error(new Error('状态暂不可获取，已保留当前草稿。可重新获取状态。'+e.message)); pollTimer=setTimeout(poll,4000); } }
  }
  async function openDraft(id) {
    const request=++opening; clearError(); const p=await api('/drafts/'+encodeURIComponent(id));
    if(request!==opening)return;
    epoch++; draft=p.draft; task=null; statusUnavailable=false; readinessRendered=undefined; dirty=false; requestKey=null;
    fill();
    const saved=localStorage.getItem(localKey(id));
    if(saved) { try { const b=JSON.parse(saved); draft.scope=b.scope; draft.portfolio=b.portfolio; draft.readiness=null; draft.preview=null; dirty=true; fill(); if(b.weightsText!=null)$('weights').value=b.weightsText; $('saveStatus').textContent='已恢复本机未保存输入，请重试保存'; $('retrySave').hidden=false; } catch {} }
    else {$('saveStatus').textContent='已恢复服务端草稿'; $('retrySave').hidden=true;}
    history.replaceState(null,'','/wizard?draft='+encodeURIComponent(id)); $('home').hidden=true; $('editor').hidden=false;
    step=draft.preview?4:draft.readiness?.status==='READY'?3:draft.readiness?2:1;
    if(draft.task_id) { task={id:draft.task_id,status:'QUEUED',kind:'prepare'}; statusUnavailable=true; step=2; await poll(); if(request!==opening)return; if(task.kind==='run')step=5; }
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
  setInterval(()=>{if(active(task)){ const panel=task.kind==='run'?'runTask':'prepareTask'; $(panel).innerHTML=renderTask(task); }},1000);
  $('scopeForm').addEventListener('submit',e=>{e.preventDefault();command('check').catch(error);});
  $('portfolioForm').addEventListener('submit',e=>{e.preventDefault();command('preview').catch(error);});
  for(const [id,n] of Object.entries({editScope:1,toPortfolio:3,backData:2,toConfirm:4,backPortfolio:3,returnConfig:2})) action(id,()=>go(n));
  action('refreshTask',poll);
  $('acceptExploratory').addEventListener('change',render);
  action('applyExploratory',async()=>{if(!$('acceptExploratory').checked)return; document.querySelector('[name=quality][value=exploratory]').checked=true; $('acceptExploratory').checked=false; changed(true); await command('check');});
  $('readiness').addEventListener('change',e=>{const key={issueSymbol:'symbol',issueCategory:'category',issuePhase:'phase'}[e.target.id]; if(key){issueFilters[key]=e.target.value; $('issueGroups').innerHTML=issuesHtml(draft.readiness?.issues || [],issueFilters);}});
  $('readiness').addEventListener('click',e=>{const retry=e.target.closest('[data-retry-source]'); if(retry){command('retry_source',{symbol:retry.dataset.retrySource}).catch(error); return;} const b=e.target.closest('[data-page]'); if(b && !b.disabled){coveragePage=Number(b.dataset.page); renderReadiness(); $('coverageDetails').open=true; $('readiness').querySelector('[data-page]')?.focus();} if(e.target.id==='exportDiagnostics'){const url=URL.createObjectURL(new Blob([JSON.stringify(draft.readiness,null,2)],{type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download='data-diagnostics.json'; a.click(); URL.revokeObjectURL(url);}});
  action('check',()=>command('check')); action('prepare',()=>command('prepare')); action('run',()=>command('run'));
  action('retryRun',()=>{requestKey=null;localStorage.removeItem('alphalab.wizard.submit.'+draft.id+'.'+draft.revision);return command('run');});
  action('recoverTask',async()=>{const p=await api('/drafts','POST',{source_task_id:task.id});await openDraft(p.draft.id);});
  action('cancelPrepare',async()=>{ const p=await api('/tasks/'+encodeURIComponent(task.id)+'/cancel','POST',{}); task=p.task; render(); poll(); });
  window.addEventListener('beforeunload',e=>{if(dirty){remember();e.preventDefault();e.returnValue='';}});
  const initial=new URLSearchParams(location.search).get('draft'); (initial?openDraft(initial):home()).catch(error);
})();
