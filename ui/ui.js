'use strict';
const $ = id => document.getElementById(id);
const COLORS = ['#208a70', '#6c92b1', '#d3aa65', '#9a8db0', '#a5bdb4', '#c47f74'];
const SHARED = '__SHARED_UNATTRIBUTED__';
const FIELDS = ['input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens'];
const state = {token:'', today:'', sources:[], local:null, data:null, view:null, busy:false, limit:20, request:0};
const number = value => Number(value || 0).toLocaleString('zh-CN');
const displayName = name => name === SHARED ? '设备归属不明' : name;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const percent = value => Number(value || 0).toFixed(2) + '%';
function compact(value) {return value >= 1e8 ? (value/1e8).toFixed(1)+'亿' : value >= 1e4 ? (value/1e4).toFixed(1)+'万' : number(Math.round(value));}
function message(text, error=false) {$('message').textContent=text; $('message').classList.toggle('error',error); $('message').hidden=!text;}
function busy(value) {
  state.busy=value;
  for(const id of ['refresh-button','import-button','apply-settings','start-date','end-date','machine-filter','machine-name','codex-home']) $(id).disabled=value;
  document.querySelectorAll('[data-range],.remove-source').forEach(button=>button.disabled=value);
  $('export-local').disabled=value || !state.local;
}
async function api(path, body) {
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Usage-Token':state.token},body:JSON.stringify(body)});
  const data=await response.json(); if(!response.ok) throw new Error(data.error || '操作失败，请重试。'); return data;
}
function saveFile(name, text, type='text/plain;charset=utf-8') {
  const url=URL.createObjectURL(new Blob([text],{type})); const link=document.createElement('a'); link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function csvDownload(name, columns, rows) {
  const quote=value=>'"'+String(value ?? '').replace(/"/g,'""')+'"';
  // Prevent a computer name from becoming an Excel formula.
  const safe=value=>typeof value==='string' && /^[=+@\-\t\r]/.test(value) ? "'"+value : value;
  const text=[columns.map(column=>quote(column[1])).join(','),...rows.map(row=>columns.map(column=>quote(safe(row[column[0]]))).join(','))].join('\r\n');
  saveFile(name,'\ufeff'+text,'text/csv;charset=utf-8');
}
function setRange(value, rerun=true) {
  let end=state.today, start='';
  if(value!=='all') {const date=new Date(end+'T00:00:00Z');date.setUTCDate(date.getUTCDate()-(value==='today'?0:Number(value)-1));start=date.toISOString().slice(0,10);}
  $('start-date').value=start;$('end-date').value=value==='all'?'':end;
  document.querySelectorAll('[data-range]').forEach(button=>button.classList.toggle('selected',button.dataset.range===value));
  if(rerun) analyzeSources();
}
async function analyzeSources(candidate=state.sources) {
  if(new TextEncoder().encode(JSON.stringify(candidate.map(({name,text})=>({name,text})))).length>60*1024*1024) throw new Error('已加载数据超过 60 MB，请减少文件数量后重试。');
  const request=++state.request;
  const result=await api('/api/analyze',{files:candidate.map(({name,text})=>({name,text})),from:$('start-date').value,to:$('end-date').value});
  if(request!==state.request) return false;
  state.sources=candidate;state.data=result;state.limit=20;
  const selected=$('machine-filter').value;
  $('machine-filter').replaceChildren(new Option('全部电脑',''));
  const names=new Set([...result.exported_machines,...result.summary.machines.map(row=>row.machine)]);
  for(const name of [...names].sort()) $('machine-filter').add(new Option(displayName(name),name));
  if(names.has(selected)) $('machine-filter').value=selected;
  render();renderSources();renderDiagnostics();
  $('updated-at').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
  return true;
}
async function guarded(action) {
  if(state.busy) return;
  busy(true);
  try {await action();} catch(error) {message(error.message || '操作失败，请重试。',true);} finally {busy(false);}
}
async function scanLocal() {
  await guarded(async()=>{
    message('正在读取本机日志，计算去重后的用量…');
    const local=await api('/api/scan',{machine:$('machine-name').value,codex_home:$('codex-home').value});
    const candidate=[{...local,local:true},...state.sources.filter(source=>!source.local)];
    if(await analyzeSources(candidate)) {
      state.local=local;$('sidebar-machine').textContent=local.machine;
      message('已读取本机 '+number(local.files_scanned)+' 份日志。可继续导入其他电脑的数据。');
    }
  });
}
async function importFiles(files) {
  if(!files.length || state.busy) return;
  await guarded(async()=>{
    message('正在验证并合并导入文件…');
    if(files.some(file=>!file.name.endsWith('.usage.jsonl'))) throw new Error('请选择本工具导出的 .usage.jsonl 文件。');
    if(files.reduce((sum,file)=>sum+file.size,0)>48*1024*1024) throw new Error('本次导入超过 48 MB，请减少文件数量。');
    const incoming=await Promise.all(files.map(async file=>({name:file.name,text:await file.text(),local:false})));
    const candidate=[...state.sources];
    for(const source of incoming) if(!candidate.some(existing=>existing.text===source.text)) candidate.push(source);
    if(candidate.length>100) throw new Error('当前最多可合并 100 份文件。');
    if(await analyzeSources(candidate)) message('导入完成，已按事件去重并更新电脑占比。');
  });
  $('file-input').value='';
}
function buildView() {
  const selected=$('machine-filter').value;
  const daily=state.data.daily.filter(row=>!selected || row.machine===selected).map(row=>({...row}));
  const byMachine=new Map(),byDate=new Map();
  for(const row of daily) {
    if(!byMachine.has(row.machine)) byMachine.set(row.machine,{machine:row.machine,...Object.fromEntries(FIELDS.map(key=>[key,0])),event_count:0});
    const machine=byMachine.get(row.machine);
    for(const field of [...FIELDS,'event_count']) machine[field]+=row[field];
    byDate.set(row.date,(byDate.get(row.date)||0)+row.total_tokens);
  }
  const machines=[...byMachine.values()].sort((a,b)=>b.total_tokens-a.total_tokens);
  const total=machines.reduce((sum,row)=>sum+row.total_tokens,0);
  for(const row of machines) row.share_percent=total?row.total_tokens/total*100:0;
  for(const row of daily) row.day_share_percent=byDate.get(row.date)?row.total_tokens/byDate.get(row.date)*100:0;
  return {machines,total,daily:daily.sort((a,b)=>b.date.localeCompare(a.date)||a.machine.localeCompare(b.machine)),byDate};
}
function colorFor(machine) {
  const names=state.data.summary.machines.map(row=>row.machine);
  return machine===SHARED?'#a5aaa8':COLORS[Math.max(0,names.indexOf(machine))%COLORS.length];
}
function render() {
  if(!state.data) return;
  const view=state.view=buildView();
  $('total-tokens').textContent=number(view.total);
  $('input-tokens').textContent=number(view.machines.reduce((sum,row)=>sum+row.input_tokens,0));
  $('output-tokens').textContent=number(view.machines.reduce((sum,row)=>sum+row.output_tokens,0));
  const selected=$('machine-filter').value;
  $('device-count').textContent=selected?(selected===SHARED?'0':'1'):number(state.data.exported_machines.length);
  $('side-device-count').textContent=number(state.data.exported_machines.length);
  $('event-count').textContent=number(view.machines.reduce((sum,row)=>sum+row.event_count,0))+' 次用量事件 · 已去重';
  const from=$('start-date').value || state.data.available_from;
  const to=$('end-date').value || state.data.available_to;
  $('period-label').textContent=from&&to ? from+' — '+to : '当前范围暂无记录';
  renderTrend(view);renderShare(view);renderTable();
  for(const id of ['download-summary','download-daily']) $(id).disabled=!view.daily.length;
}
function renderTrend(view) {
  const chart=$('trend-chart');
  if(!view.byDate.size) {chart.innerHTML='<div class="empty-chart">当前日期范围暂无用量<small>调整日期，或导入更多电脑的数据</small></div>';return;}
  const keys=[...view.byDate.keys()].sort();
  const start=new Date(keys[0]+'T00:00:00Z'),end=new Date(keys[keys.length-1]+'T00:00:00Z');
  const days=Math.round((end-start)/86400000)+1;
  const step=Math.max(1,Math.ceil(days/90));
  const points=[];
  for(let index=0;index<days;index+=step) {
    let total=0; const first=new Date(+start+index*86400000).toISOString().slice(0,10);
    for(let j=index;j<Math.min(index+step,days);j++) total+=view.byDate.get(new Date(+start+j*86400000).toISOString().slice(0,10))||0;
    points.push({date:first,total});
  }
  if(points.length===1) {points.unshift({date:points[0].date,total:0,synthetic:true});}
  $('trend-description').textContent='按北京时间统计 · '+(step>1?'每 '+step+' 天合计':'输入与输出合计');
  const width=650,height=245,left=57,right=20,top=13,bottom=34;
  const plotWidth=width-left-right,plotHeight=height-top-bottom;
  const max=Math.max(...points.map(point=>point.total),1)*1.15;
  const coords=points.map((point,index)=>[left+index*plotWidth/(points.length-1),top+plotHeight-point.total/max*plotHeight]);
  const line=coords.map(([x,y],index)=>(index?'L':'M')+x.toFixed(2)+','+y.toFixed(2)).join(' ');
  let grid='';
  for(let index=0;index<=4;index++) {const y=top+plotHeight*index/4;grid+='<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="#edf0f1" stroke-dasharray="3 5"/><text x="'+(left-10)+'" y="'+(y+4)+'" text-anchor="end">'+compact(max*(1-index/4))+'</text>';}
  const labelIndices=new Set([0,points.length-1]);for(let i=1;i<5;i++)labelIndices.add(Math.round(i*(points.length-1)/5));
  let labels='';for(const index of labelIndices){if(points[index].synthetic)continue;labels+='<text x="'+coords[index][0]+'" y="'+(height-8)+'" text-anchor="middle">'+points[index].date.slice(5).replace('-','/')+'</text>';}
  const circles=points.map((point,index)=>point.synthetic?'':'<circle cx="'+coords[index][0]+'" cy="'+coords[index][1]+'" r="'+(points.length<20?3:2)+'" fill="#208a70" stroke="white" stroke-width="1.5"><title>'+point.date+(step>1?' 起 '+step+' 天':'')+'：'+number(point.total)+' token</title></circle>').join('');
  chart.innerHTML='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="每日总 token 用量趋势"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#208a70" stop-opacity=".19"/><stop offset="100%" stop-color="#208a70" stop-opacity=".01"/></linearGradient></defs>'+grid+'<path d="'+line+' L'+coords[coords.length-1][0]+','+(top+plotHeight)+' L'+left+','+(top+plotHeight)+' Z" fill="url(#area)"/><path class="data-line" d="'+line+'" fill="none" stroke="#208a70" stroke-width="2.5" stroke-linejoin="round"/>'+circles+labels+'</svg>';
}
function renderShare(view) {
  if(!view.total) {$('share-chart').innerHTML='<div class="empty-chart">暂无占比数据</div>';$('share-legend').replaceChildren();return;}
  const circumference=2*Math.PI*62;let offset=0;
  const circles=view.machines.map(row=>{const length=row.total_tokens/view.total*circumference;const result='<circle cx="88" cy="88" r="62" fill="none" stroke="'+colorFor(row.machine)+'" stroke-width="16" stroke-dasharray="'+length+' '+(circumference-length)+'" stroke-dashoffset="'+(-offset)+'"><title>'+escapeHTML(displayName(row.machine))+'：'+percent(row.share_percent)+'</title></circle>';offset+=length;return result;}).join('');
  $('share-chart').innerHTML='<svg viewBox="0 0 176 176" role="img" aria-label="电脑用量占比环形图"><circle cx="88" cy="88" r="62" fill="none" stroke="#edf2ef" stroke-width="16"/>'+circles+'</svg><div class="donut-label"><strong>'+compact(view.total)+'</strong><small>总 token</small></div>';
  $('share-legend').innerHTML=view.machines.map(row=>'<div class="legend-row"><i class="color-dot" style="background:'+colorFor(row.machine)+'"></i><span class="name" title="'+escapeHTML(displayName(row.machine))+'">'+escapeHTML(displayName(row.machine))+'</span><span class="share">'+percent(row.share_percent)+'</span></div>').join('');
}
function renderTable() {
  const rows=state.view.daily;
  $('daily-table').innerHTML=rows.length ? rows.slice(0,state.limit).map(row=>'<tr><td>'+row.date+'</td><td><span class="table-machine"><i class="color-dot" style="background:'+colorFor(row.machine)+'"></i>'+escapeHTML(displayName(row.machine))+'</span></td><td>'+number(row.input_tokens)+'</td><td>'+number(row.cached_input_tokens)+'</td><td>'+number(row.output_tokens)+'</td><td>'+number(row.total_tokens)+'</td><td>'+percent(row.day_share_percent)+'</td></tr>').join('') : '<tr><td colspan="7" class="table-empty">暂无数据，请调整日期或导入统计文件。</td></tr>';
  $('table-count').textContent='显示 '+number(Math.min(state.limit,rows.length))+' / '+number(rows.length)+' 条记录';
  $('show-more').hidden=state.limit>=rows.length;
  $('table-description').textContent='按日期倒序排列 · 默认展示最近 20 条记录';
}
function renderSources() {
  $('sources-list').replaceChildren();
  state.sources.forEach((source,index)=>{
    const info=state.data.sources[index];const item=document.createElement('div');item.className='source-item';
    const range=info.from||info.to?' · '+(info.from||'起始')+' 至 '+(info.to||'最新'):'';
    item.innerHTML='<span class="computer-icon">▰</span><div class="source-info"><strong>'+escapeHTML(displayName(info.machine))+'</strong><small>'+escapeHTML(source.name+range)+'</small></div>'+(source.local?'<span class="badge">本机</span>':'');
    if(!source.local){const button=document.createElement('button');button.className='remove-source';button.textContent='×';button.setAttribute('aria-label','移除 '+info.machine);button.addEventListener('click',()=>guarded(async()=>{if(await analyzeSources(state.sources.filter((_,i)=>i!==index)))message('已移除此导入文件。');}));item.append(button);}
    $('sources-list').append(item);
  });
}
const DIAGNOSTICS={
  duplicate_event_removed:'已去除重复事件',shared_event_unattributed:'跨电脑重复事件，设备归属不明',
  incomplete_trailing_line:'日志尾行尚未写完，下次读取再统计',missing_cumulative_counter_skipped:'缺少累计计数，已跳过',
  missing_session_id_skipped:'缺少会话标识，已跳过',initial_history_gap_only_last_counted:'初始历史有缺口，仅计可确认的单次用量',
  initial_cumulative_assigned_to_first_timestamp:'首个累计值归于首条事件日期，日期可能不精确',
  counter_reset_without_last_skipped:'计数重置且缺少单次值，已跳过',counter_reset_used_last:'计数重置，按单次用量统计',
  history_gap_only_last_counted:'历史存在缺口，仅计可确认的单次用量',invalid_token_or_metadata_record:'存在无效统计记录，已跳过'
};
function renderDiagnostics() {
  const counts=state.data.diagnostics.counts;const entries=Object.entries(counts);
  $('diagnostic-count').textContent=entries.length;
  $('diagnostics-list').innerHTML=entries.length?entries.map(([key,count])=>{const source=key.startsWith('source_warning:');const clean=key.replace('source_warning:','');return '<div class="diagnostic-row"><strong>'+escapeHTML(DIAGNOSTICS[clean]||'存在需要检查的统计记录')+'</strong>'+number(count)+(source?' 份文件含此情况':' 条')+'</div>';}).join(''):'<div class="diagnostic-row">未发现统计异常。</div>';
}
$('refresh-button').addEventListener('click',scanLocal);$('apply-settings').addEventListener('click',scanLocal);
$('import-button').addEventListener('click',()=>$('file-input').click());$('file-input').addEventListener('change',event=>importFiles([...event.target.files]));
$('drop-zone').addEventListener('click',()=>$('file-input').click());
$('drop-zone').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();$('file-input').click();}});
for(const kind of ['dragenter','dragover'])$('drop-zone').addEventListener(kind,event=>{event.preventDefault();$('drop-zone').classList.add('drag-over');});
for(const kind of ['dragleave','drop'])$('drop-zone').addEventListener(kind,event=>{event.preventDefault();$('drop-zone').classList.remove('drag-over');if(kind==='drop')importFiles([...event.dataTransfer.files]);});
document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>guarded(async()=>{setRange(button.dataset.range,false);await analyzeSources();message('');})));
for(const id of ['start-date','end-date'])$(id).addEventListener('change',()=>guarded(async()=>{document.querySelectorAll('[data-range]').forEach(button=>button.classList.remove('selected'));await analyzeSources();message('');}));
$('machine-filter').addEventListener('change',()=>{state.limit=20;render();});
$('show-more').addEventListener('click',()=>{state.limit+=50;renderTable();});
$('export-local').addEventListener('click',()=>{if(state.local)saveFile(state.local.machine.replace(/[^\w\u4e00-\u9fff.-]/g,'_')+'.usage.jsonl',state.local.text);});
$('download-summary').addEventListener('click',()=>{if(!state.view)return;csvDownload('电脑用量占比.csv',[['machine','电脑名称'],['input_tokens','输入 token'],['cached_input_tokens','缓存输入 token'],['output_tokens','输出 token'],['reasoning_output_tokens','推理输出 token'],['total_tokens','总 token'],['share_percent','期间占比（%）']],state.view.machines.map(row=>({...row,machine:displayName(row.machine),share_percent:row.share_percent.toFixed(6)})));});
$('download-daily').addEventListener('click',()=>{if(!state.view)return;csvDownload('每日用量明细.csv',[['date','日期'],['machine','电脑名称'],['input_tokens','输入 token'],['cached_input_tokens','缓存输入 token'],['output_tokens','输出 token'],['total_tokens','总 token'],['day_share_percent','当天占比（%）']],state.view.daily.map(row=>({...row,machine:displayName(row.machine),day_share_percent:row.day_share_percent.toFixed(6)})));});
document.querySelectorAll('.nav-link').forEach(link=>link.addEventListener('click',()=>{document.querySelectorAll('.nav-link').forEach(item=>item.classList.toggle('active',item===link));}));
async function init() {
  try {
    const response=await fetch('/api/config');if(!response.ok)throw new Error('本机服务不可用，请重新启动程序。');
    const config=await response.json();state.token=config.token;state.today=config.today;
    $('machine-name').value=config.machine;$('codex-home').value=config.codex_home;$('sidebar-machine').textContent=config.machine;
    setRange('30',false);await scanLocal();
  }catch(error){message(error.message,true);}
}
init();
