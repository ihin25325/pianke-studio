'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const titles={todo:'待制作',working:'处理中',review:'待验收成片',done:'已归档',attention:'需要处理',all:'全部商品'};
const taskLabels={queued:'等待处理',searching:'检索素材',downloading:'下载素材',analyzing:'分析画面 / 智能选片',analyzed:'选片分析完成',transcribing:'识别 / 翻译字幕',rendering:'生成视频',completed:'已完成',failed:'制作失败',needs_review:'待确认素材',links_ready:'链接已收集',downloaded:'素材已下载',skipped:'配件跳过 · 未制作'};
const state={view:'todo',page:1,q:'',batch:'',selected:new Set(),data:null,import:null,detail:null,loading:false};
let toastTimer,searchTimer,linkSearchTimer;
async function api(path,data){
  const response=await fetch(path,data===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const value=await response.json();if(!response.ok||value.error)throw Error(value.error||'请求失败，请稍后重试。');return value;
}
function toast(message,error=false){$('toast').textContent=message;$('toast').className='toast'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),6500);}
function show(id){if(!$(id).open)$(id).showModal();}
function date(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(+d)?'—':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
function safeLink(value){try{const u=new URL(value,location.origin);return (u.origin===location.origin&&u.pathname.startsWith('/media/'))||u.protocol==='https:'?esc(u.href):'#';}catch{return '#';}}
function statusHTML(p){return `<span class="status ${esc(p.status)}${p.skipped?' skipped':''}">${esc(p.status_label)}</span>${p.output_url&&!p.skipped?`<span class="approval-chip ${p.approval?.decision==='approved'?'ok':''}">${p.approval?.decision==='approved'?'已验收':p.approval?.decision==='rework'?'需返工':'待验收'}</span>`:''}`;}
function updateSelection(){
  $('bulk').classList.toggle('hidden',!state.selected.size);$('selected-count').textContent=state.selected.size;
  const rows=state.data?.items||[];const selectable=rows.filter(p=>p.status!=='working');
  const count=selectable.filter(p=>state.selected.has(p.id)).length;
  $('select-page').checked=!!selectable.length&&count===selectable.length;$('select-page').indeterminate=count>0&&count<selectable.length;
  document.querySelectorAll('[data-select]').forEach(input=>input.checked=state.selected.has(input.dataset.select));
}
function renderBoard(data){
  window.Downloads?.counts(data.download_count);
  document.querySelectorAll('[data-count]').forEach(el=>el.textContent=(data.counts[el.dataset.count]||0).toLocaleString());
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('selected',el.dataset.view===state.view));
  $('view-title').textContent=titles[state.view]||'商品清单';$('result-count').textContent=`${data.total.toLocaleString()} 个商品`;
  $('worker-status').textContent=data.paused?'队列已暂停，新任务暂不执行':data.worker_busy?'正在处理，完成后自动继续':'顺序处理，避免电脑卡顿';
  $('done-summary').textContent=`${data.counts.generated||0} 个有成片 · ${data.counts.skipped||0} 个免制作`;
  $('pause').textContent=data.paused?'继续队列':'暂停队列';
  const batchHTML='<option value="">全部批次</option>'+data.batches.map(b=>`<option value="${b.id}">${esc(b.filename)} · ${b.count} 件</option>`).join('');
  if(state.batchHTML!==batchHTML){$('batch').innerHTML=batchHTML;state.batchHTML=batchHTML;}$('batch').value=state.batch;
  const rowsHTML=data.items.length?data.items.map(p=>{
    const main=p.skipped&&p.status==='done'?`<button class="primary" data-action="detail" data-id="${p.id}">查看标记</button><button class="quiet" data-action="restore-search" data-id="${p.id}">仍需制作</button>`:p.status==='done'?`<button class="primary" data-action="detail" data-id="${p.id}">查看成片</button><button class="quiet" data-action="redo" data-id="${p.id}">重做</button>`:
      p.status==='working'?`<button class="quiet" data-action="detail" data-id="${p.id}">查看进度</button>`:
      ['failed','needs_review'].includes(p.status)?`<button class="primary" data-action="detail" data-id="${p.id}">去处理</button>`:
      `<button class="primary" data-action="render" data-id="${p.id}">开始制作</button><button class="quiet" data-action="detail" data-id="${p.id}">详情</button>`;
    return `<tr><td class="check"><input type="checkbox" data-select="${p.id}" aria-label="选择 ${esc(p.title)}" ${p.status==='working'?'disabled':''} ${state.selected.has(p.id)?'checked':''}></td><td><button class="product-title" data-action="detail" data-id="${p.id}" title="${esc(p.title)}">${esc(p.title)}</button><div class="product-id">ID ${esc(p.product_id||'未关联')} ${p.version_count?' · '+p.version_count+' 个成片版本':''}</div></td><td class="source-cell"><span class="source-pill ${p.source_url&&!p.skipped?'found':''}">${p.skipped?'无需检索':p.source_url?'已关联链接':'待检索'}</span></td><td>${statusHTML(p)}${p.status==='working'?`<div class="progress"><span style="width:${Math.min(100,Math.max(0,Number(p.progress)||0))}%"></span></div>`:''}<div class="row-note" title="${esc(p.message)}">${esc(!p.skipped&&p.manual_done?'手动标记 · 非自动成片':p.message)}</div></td><td class="time-cell"><span class="date">${esc(date(p.updated_at))}</span></td><td><div class="row-actions">${main}</div></td></tr>`;
  }).join(''):`<tr><td colspan="6" class="empty"><strong>${state.q?'没有匹配的商品':state.view==='todo'?'这里是你的下一批制作清单':state.view==='done'?'完成的视频会归档在这里':state.view==='working'?'当前没有制作任务':'当前没有需要处理的商品'}</strong>${state.view==='todo'&&!state.q?'导入 Excel 或粘贴商品，整理后勾选开始制作。<br><button class="primary" data-action="import">导入商品清单</button>':'试试切换商品状态，或调整搜索条件。'}</td></tr>`;
  const rowsKey=JSON.stringify([state.view,state.q,data.items]);
  if(state.rowsKey!==rowsKey){$('rows').innerHTML=rowsHTML;state.rowsKey=rowsKey;}
  $('page-number').textContent=`${data.page} / ${data.pages}`;$('prev').disabled=data.page<=1;$('next').disabled=data.page>=data.pages;
  $('pagination-info').textContent=data.total?`第 ${(data.page-1)*25+1}–${Math.min(data.page*25,data.total)} 个，共 ${data.total} 个商品`:'每页 25 个商品';
  $('connection').textContent=`本机已连接 · ${data.paused?'队列已暂停':'自动保存'}`;updateSelection();
}
async function refresh(){
  if(state.loading){state.refreshAgain=true;return;}state.loading=true;
  try{const params=new URLSearchParams({view:state.view,page:state.page,q:state.q,batch:state.batch});const data=await api('/api/board?'+params);state.data=data;state.page=data.page;renderBoard(data);
    if(location.hash.startsWith('#task-')){const pid=data.task_map[location.hash.slice(6)];if(pid){history.replaceState(null,'',location.pathname);await openDetail(pid);}}
  }catch(error){$('connection').textContent='连接中断 · 请双击 项目目录中的「启动工作台.bat」';if(!state.data)$('rows').innerHTML=`<tr><td colspan="6" class="empty"><strong>暂时没有连上本机服务</strong>${esc(error.message)}<br>请重新启动工作台后刷新。</td></tr>`;}finally{state.loading=false;if(state.refreshAgain){state.refreshAgain=false;refresh();}}
}
async function changeView(view){state.view=view;state.page=1;state.selected.clear();await refresh();}
async function jobs(ids,job){
  if(job==='render'&&window.StudioUI)return StudioUI.enqueue(ids,'render');
  const result=await api('/api/board/jobs',{ids,job});const errors=result.errors||[];
  const skipped=result.skipped?.length||0;
  toast(result.added?`已加入 ${result.added} 个${job==='collect'?'链接检索':job==='download'?'素材下载':'制作'}任务。${skipped?'另有 '+skipped+' 个配件免检索，已归已完成。':''}${errors.length?'另有 '+errors.length+' 个未加入：'+errors[0].message:''}`:errors[0]?.message||(skipped?`${skipped} 个配件免检索，已在已完成中标注；没有启动任务。`:'没有新增任务。'),!result.added&&!skipped);
  state.selected.clear();await refresh();return result;
}
async function redo(pid,extra={}){const r=await api(`/api/products/${pid}/redo`,extra);toast(r.message||'重做已加入队列，旧成片保留。');await refresh();if(state.detail===pid)await openDetail(pid);}
async function openDetail(pid){
  const request=state.detailRequest=(state.detailRequest||0)+1;const data=await api('/api/products/'+pid);if(request!==state.detailRequest)return;window.Review?.close();state.detail=pid;const p=data.product;const working=p.status==='working';
  $('detail-title').textContent=p.title;$('detail-id').textContent='商品 ID '+(p.product_id||'未关联')+' · '+p.version_count+' 个可用成片版本';
  const history=data.history.map((t,i)=>`<div class="history"><div class="history-head"><span>${esc(t.job_type==='collect'?'素材检索':t.job_type==='download'?'素材下载':'成片版本')} ${data.history.length-i} · ${esc(taskLabels[t.status]||t.status)}</span><span class="date">${esc(date(t.created_at))}</span></div><details><summary>${esc(t.message||'查看记录')}</summary><p>任务 ${esc(t.id)}${t.redone_from?' · 重做自 '+esc(t.redone_from):''}</p></details><div class="history-links">${t.output_url?`<a href="${safeLink(t.output_url)}" target="_blank" rel="noopener">播放</a><button data-action="download-video" data-task="${t.id}">下载此版</button>`:''}${t.job_type==='render'||!['collect','download'].includes(t.job_type)?`<a href="/typography?task=${t.id}">编辑文字 / 字幕</a><button data-action="redo-version" data-id="${p.id}" data-task="${t.id}" ${working?'disabled':''}>用此版重做</button>`:''}${t.subtitle_url?`<a href="${safeLink(t.subtitle_url)}" download>字幕 SRT</a>`:''}${t.metadata_url?`<a href="${safeLink(t.metadata_url)}" target="_blank" rel="noopener">制作记录</a>`:''}</div></div>`).join('');
  $('detail-body').innerHTML=`<div class="detail-topline">${statusHTML(p)}<span>${esc(p.message)}</span><button class="quiet" data-action="detail" data-id="${p.id}">刷新进度</button></div>${p.output_url?`<div class="video-panel"><video controls preload="metadata" src="${safeLink(p.output_url)}"></video></div>`:''}<div class="detail-actions"><button class="primary" data-action="${p.task_count?'redo':'render'}" data-id="${p.id}" ${working?'disabled':''}>${p.task_count?'一键重做':'开始制作'}</button>${p.task_count?`<button data-action="redo-new" data-id="${p.id}" ${working?'disabled':''}>重新找素材并重做</button>`:''}${p.current_task_id||p.latest_task_id?`<a href="/typography?task=${p.current_task_id||p.latest_task_id}">编辑文字、字体与字幕</a>`:''}${p.output_url?`<button data-action="download-video" data-task="${p.current_task_id}">下载成片</button>`:''}</div>
  <h3 class="section-title">素材链接</h3><div class="source-edit"><input type="url" id="detail-source" aria-label="国内外素材链接" placeholder="粘贴对应商品的国内外视频链接或公开 MP4" value="${esc(p.source_url)}" ${working?'disabled':''}><button data-action="save-source" data-id="${p.id}" ${working?'disabled':''}>保存链接</button></div><p class="hint">保存链接不启动制作；之后点击制作或重做才使用。${p.existing_video?' 原表中的淘宝视频已保留作参考，不计为本机完成。':''}</p><div class="detail-actions"><button data-action="collect" data-id="${p.id}" ${working?'disabled':''}>自动抓取素材链接</button><button data-action="download-source" data-id="${p.id}" ${working||!p.source_url?'disabled':''}>仅下载素材</button>${p.source_url?`<a href="${safeLink(p.source_url)}" target="_blank" rel="noopener">打开素材原网页 ↗</a>`:''}${p.product_url?`<a href="${safeLink(p.product_url)}" target="_blank" rel="noopener">商品原页面 ↗</a>`:''}</div>
  ${data.candidates.length?`<details open class="review-candidates"><summary class="text-link">候选视频（${data.candidates.length}）· 可在本页预览再采用</summary>${data.candidates.map(c=>`<div class="candidate"><div><a href="${safeLink(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a><small>${esc(c.platform||'YouTube')} · ${esc(c.channel||'未知频道')} · 匹配分 ${Number(c.score)||0} · ${Number(c.duration)||'?'} 秒</small>${c.match_reason?`<small>${esc(c.match_reason)}</small>`:''}</div><div class="candidate-actions"><button data-action="preview-candidate" data-id="${p.id}" data-url="${esc(c.url)}" data-duration="${Number(c.duration)||0}">本页预览</button><button data-action="use-candidate" data-id="${p.id}" data-url="${esc(c.url)}" ${working?'disabled':''}>采用</button></div></div>`).join('')}</details>`:''}
  <h3 class="section-title">制作历史 <span class="hint">旧版文件与设置完整保留</span></h3>${history||'<p class="hint">尚未开始制作。选好素材后即可制作第一版。</p>'}<div class="manual-actions"><span>仅管理待办，不会新增或删除视频文件</span><button class="quiet" data-action="mark" data-state="${p.status==='done'?'todo':'done'}" data-id="${p.id}" ${working?'disabled':''}>${p.status==='done'?'移回待办':'手动标记完成'}</button></div>`;
  const itemSettings=`<details class="item-skip-settings" ${p.skipped?'open':''}><summary>单独定义此商品是否免检索</summary><select id="item-skip-mode" aria-label="单品免检索定义"><option value="auto" ${p.skip_mode==='auto'?'selected':''}>按全局配件规则判断</option><option value="skip" ${p.skip_mode==='skip'?'selected':''}>此商品免检索，归已完成</option><option value="search" ${p.skip_mode==='search'?'selected':''}>此商品仍需制作，忽略免检索规则</option></select><button data-action="save-item-skip" data-id="${p.id}" ${working?'disabled':''}>保存单品定义</button><p class="hint">修改单品定义不会自动开始检索或制作；恢复按规则判断可撤销本商品的手动定义。</p></details>`;
  $('detail-body').insertAdjacentHTML('afterbegin',(p.skipped?`<div class="skip-notice">配件跳过，已归入已完成。${esc(p.message)}${p.output_url?' 之前生成的成片仍保留。':''}</div>`:'')+itemSettings);
  const report=data.search_report;
  const reportHTML=report?`<details class="search-report"><summary>最近检索：${report.attempts.length} 轮 · ${report.unique_candidates} 个去重候选</summary><p class="hint">检索名称：${esc(report.name)}${report.requires_variant?' · 需要确认具体型号':''}</p>${report.attempts.map(a=>`<p>${esc(a.engine||"YouTube")} · ${esc(a.query)}<small>${a.error?'连接 / 解析失败：'+esc(a.error):`${a.count} 条${a.cached?' · 使用本地缓存':''}`}</small></p>`).join('')}</details>`:'';
  if(!p.skipped)$('detail-body').insertAdjacentHTML('afterbegin',`<section class="search-name-editor"><label for="detail-search-name">检索名称（品牌＋具体型号）</label><div class="source-edit"><input id="detail-search-name" maxlength="160" value="${esc(p.search_name||'')}" placeholder="留空自动判断，例如：miniDSP 2X4HD" ${working?'disabled':''}><button data-action="save-search-name" data-id="${p.id}" ${working?'disabled':''}>保存检索名称</button></div><p class="hint">只影响找素材，不改商品标题。多型号商品请填实际要做的款式；保存后点下方“重新检索素材链接”。按所选国内外来源检索，不调用大模型。<a href="/sources">设置来源范围 ↗</a></p>${reportHTML}</section>`);
  const collectButton=$('detail-body').querySelector('[data-action="collect"]');if(collectButton)collectButton.textContent='重新检索素材链接';
  if(p.skipped){const blocked=new Set(['render','redo','redo-new','redo-version','collect','download-source','use-candidate']);$('detail-body').querySelectorAll('[data-action]').forEach(button=>{if(blocked.has(button.dataset.action))button.disabled=true;});}
  show('detail-dialog');
  if(window.Review){$('detail-body').insertAdjacentHTML('afterbegin',Review.render(data));$('detail-body').querySelectorAll('[data-action="redo"],[data-action="render"]').forEach(button=>button.hidden=true);Review.mount(data,openDetail,toast);}
  if(window.StudioUI)$('detail-body').insertAdjacentHTML('afterbegin',StudioUI.detail(data));
}
function importStatus(text,error=false){$('import-status').textContent=text;$('import-status').classList.toggle('error',error);}
function importPayload(){return {token:state.import.token,sheet:$('import-sheet').value,rule:$('import-rule').value,include_hidden:$('import-hidden').checked,mapping:Object.fromEntries([...document.querySelectorAll('[data-column]')].map(s=>[s.dataset.column,Number(s.value)]))};}
const fieldNames={id:'商品 ID',title:'商品名称',product_url:'商品页面链接',existing_video:'已有视频链接（仅参考）',source_url:'国内外素材链接',need:'是否需要制作'};
function showImport(data){
  state.import=data;$('import-preview').classList.remove('hidden');$('import-sheet').innerHTML=data.sheets.map(s=>`<option>${esc(s)}</option>`).join('');$('import-sheet').value=data.sheet;$('import-rule').value=data.rule;
  $('import-hidden').checked=!!data.include_hidden;
  $('column-mapping').innerHTML=Object.entries(fieldNames).map(([key,label])=>`<label>${label}<select data-column="${key}"><option value="-1">不使用此列</option>${data.headers.map((h,i)=>`<option value="${i}" ${data.mapping[key]===i?'selected':''}>${i+1}. ${esc(h||'未命名列')}</option>`).join('')}</select></label>`).join('');
  const s=data.summary;$('import-summary').innerHTML=[['本次整理',s.selected],['新增商品',s.new],['关联已有商品',s.existing],['其中已完成 / 跳过',s.completed]].map(([label,n])=>`<div><span>${label}</span><b>${n.toLocaleString()}</b></div>`).join('');
  $('import-samples').innerHTML=data.preview.map(p=>`<tr><td>${esc(p.product_id||'—')}</td><td>${esc(p.title)}</td><td>${esc(p.status)}</td></tr>`).join('');
  $('hidden-rows-note').classList.toggle('hidden',!s.hidden_rows);$('hidden-rows-note').textContent=data.include_hidden?`已包含 ${s.hidden_rows} 条隐藏行；当前读取 ${s.total_rows} 行。`:`Excel 共 ${s.source_rows} 行商品，已跳过 ${s.hidden_excluded} 条隐藏行；当前读取 ${s.total_rows} 条可见商品。`;
  $('import-warnings').textContent=`共读取 ${s.total_rows} 行；其中 ${s.skip_count||0} 个配件免检索归已完成；跳过隐藏行 ${s.hidden_excluded||0} 行；重复 ${s.duplicates} 行；规则排除 ${s.excluded} 行；无效 ${s.invalid_count} 行。`+(s.invalid_count?' '+s.invalid.slice(0,3).map(r=>`第 ${r.row} 行：${r.reason}`).join(' '):'');
  $('commit-import').disabled=!s.selected;$('commit-import').textContent=`确认整理 ${s.selected} 个商品`;importStatus(`已读取 ${data.filename}。请核对预览后确认。`);
}
async function upload(file){
  if(!file)return;const request=state.importRequest=(state.importRequest||0)+1;$('commit-import').disabled=true;importStatus('正在读取表格并匹配商品…');
  try{if(file.size>8*1024*1024)throw Error('文件超过 8 MB，请拆分后导入。');const r=await fetch('/api/imports/preview',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Upload-Filename':encodeURIComponent(file.name)},body:file});const data=await r.json();if(!r.ok)throw Error(data.error||'导入失败');if(request===state.importRequest)showImport(data);}catch(e){if(request===state.importRequest)importStatus(e.message,true);}
}
async function analyzeImport(resetMapping=false){if(!state.import)return;const request=state.importRequest=(state.importRequest||0)+1;$('commit-import').disabled=true;importStatus('正在更新预览…');try{const payload=importPayload();if(resetMapping)delete payload.mapping;const data=await api('/api/imports/analyze',payload);if(request===state.importRequest)showImport(data);}catch(e){if(request===state.importRequest)importStatus(e.message,true);}}
async function serverImport(path){const request=state.importRequest=(state.importRequest||0)+1;$('commit-import').disabled=true;importStatus('正在分析表格的可见行…');try{const data=await api(path,{});if(request===state.importRequest)showImport(data);}catch(e){if(request===state.importRequest)importStatus(e.message,true);}}
async function loadLinkProducts(){const data=await api('/api/board?'+new URLSearchParams({view:'all',q:$('link-product-search').value,page:1}));$('link-product').innerHTML='<option value="">请选择商品（可先搜索）</option>'+data.items.map(p=>`<option value="${p.id}">${esc(p.title)}</option>`).join('');}
const ruleWords=text=>text.split(/[,，、;；\r\n]+/).map(s=>s.trim()).filter(Boolean);
function readSkipRules(){
  const lines=$('skip-rules-text').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  const groups=lines.map((line,index)=>{const parts=line.split(/[|｜]/);if(parts.length>2)throw Error(`第 ${index+1} 组只能使用一个竖线分隔分类和关键词。`);return {label:parts.length===2?parts[0].trim():'自定义配件',keywords:ruleWords(parts.at(-1))};});
  return {enabled:$('skip-rules-enabled').checked,groups,exceptions:ruleWords($('skip-exceptions').value)};
}
function fillSkipRules(rules){$('skip-rules-enabled').checked=rules.enabled;$('skip-rules-text').value=rules.groups.map(g=>g.label+' | '+g.keywords.join(', ')).join('\n');$('skip-exceptions').value=rules.exceptions.join(', ');}
function updateRulesButton(){$('save-skip-rules').disabled=!state.rulesFresh||!state.rulesData?.can_apply||!!state.rulesSaving;}
function dirtyRules(){state.rulesRequest=(state.rulesRequest||0)+1;state.rulesFresh=false;updateRulesButton();$('skip-rules-status').textContent='规则已修改，请先预览命中商品，再保存。';$('skip-rules-status').classList.remove('error');}
function showRulesPreview(data){state.rulesData=data;state.rulesFresh=true;$('skip-rules-status').classList.remove('error');$('skip-rules-status').textContent=`当前 ${data.total} 个商品，命中 ${data.hit_count} 个；新增跳过 ${data.newly_skipped} 个，解除跳过 ${data.released} 个。${data.message}`;$('skip-rules-hits').innerHTML=data.hits.length?data.hits.map(p=>`<tr><td>${esc(p.title)}</td><td>${esc(p.label)}<br><span class="hint">${esc(p.keyword)}</span></td></tr>`).join(''):'<tr><td colspan="2">没有商品命中当前规则。</td></tr>';updateRulesButton();}
async function previewSkipRules(){const request=state.rulesRequest=(state.rulesRequest||0)+1;state.rulesFresh=false;updateRulesButton();$('skip-rules-status').textContent='正在本机匹配关键词…';try{const data=await api('/api/skip-rules/preview',{rules:readSkipRules()});if(request===state.rulesRequest)showRulesPreview(data);}catch(e){if(request===state.rulesRequest){$('skip-rules-status').textContent=e.message;$('skip-rules-status').classList.add('error');}throw e;}}
function updateResetButton(){ $('confirm-reset').disabled=!state.reset?.can_clear||state.resetSubmitting||$('reset-confirmation').value.trim()!=='清空所有清单'; }
function clearLocalBoard(){
  state.importRequest=(state.importRequest||0)+1;
  state.selected.clear();state.view='todo';state.page=1;state.q='';state.batch='';state.import=null;state.detail=null;state.rowsKey=null;
  clearTimeout(searchTimer);clearTimeout(linkSearchTimer);$('search').value='';$('batch').value='';$('paste-text').value='';$('links-text').value='';$('link-product-search').value='';
  $('link-product').innerHTML='';$('extracted-links').innerHTML='';$('link-binding').classList.add('hidden');
  $('import-preview').classList.add('hidden');$('commit-import').disabled=true;$('excel-file').value='';importStatus('');
  document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
  if(location.hash.startsWith('#task-'))history.replaceState(null,'',location.pathname);
}
async function action(name,button){
  const pid=button?.dataset.id;
  if(name==='downloads'){await Downloads.open(toast);return;}
  if(name==='downloads-refresh'){await Downloads.refresh();return;}
  if(name==='download-prev'||name==='download-next'){await Downloads.move(name==='download-prev'?-1:1);return;}
  if(name==='bulk-download'){if(state.selected.size)await Downloads.prepare([...state.selected],toast);return;}
  if(name==='download-video'){await Downloads.video(button.dataset.task,toast);return;}
  if(name==='storage'){
    const data=await api('/api/storage');state.storage=data;
    $('storage-output-dir').value=data.output_dir;$('storage-current-dir').textContent=data.output_dir;
    $('save-storage').disabled=!data.can_save;$('storage-status').classList.remove('error');
    $('storage-status').textContent=data.can_save?(data.exists?'修改后点“校验并保存”，重启后仍生效。':'当前目录尚未创建，保存时会自动建立并检查写入权限。'):'正在处理任务，请等本条结束后重新打开此窗口。';
    show('storage-dialog');return;
  }
  if(name==='default-storage'){$('storage-output-dir').value=state.storage.default_dir;$('storage-status').textContent='已填入默认目录，请点“校验并保存”后生效。';$('storage-status').classList.remove('error');return;}
  if(name==='save-storage'){
    $('storage-status').textContent='正在检查目录并保存…';$('storage-status').classList.remove('error');
    try{const r=await api('/api/storage',{output_dir:$('storage-output-dir').value});$('storage-current-dir').textContent=r.output_dir;$('storage-output-dir').value=r.output_dir;state.storage.output_dir=r.output_dir;$('storage-status').textContent=r.message;toast(r.message);}
    catch(error){$('storage-status').textContent=error.message;$('storage-status').classList.add('error');throw error;}return;
  }
  if(name==='skip-rules'){const data=await api('/api/skip-rules');state.rulesSaving=false;fillSkipRules(data.rules);showRulesPreview(data);show('skip-rules-dialog');return;}
  if(name==='preview-skip-rules'){await previewSkipRules();return;}
  if(name==='default-skip-rules'){fillSkipRules(state.rulesData.defaults);dirtyRules();return;}
  if(name==='save-skip-rules'){
    if(!state.rulesFresh||!state.rulesData?.can_apply)return;
    state.rulesSaving=true;updateRulesButton();
    try{const r=await api('/api/skip-rules',{rules:readSkipRules()});$('skip-rules-dialog').close();toast(r.message);state.selected.clear();await refresh();if(state.detail)await openDetail(state.detail);}
    catch(e){$('skip-rules-status').textContent=e.message;$('skip-rules-status').classList.add('error');throw e;}
    finally{state.rulesSaving=false;updateRulesButton();}return;
  }
  if(name==='restore-search'||name==='save-item-skip'){
    const r=await api(`/api/products/${pid}/skip`,{mode:name==='restore-search'?'search':$('item-skip-mode').value});toast(r.message);await refresh();if(state.detail===pid)await openDetail(pid);return;
  }
  if(name==='reset'){
    state.reset=await api('/api/board/reset-preview');state.resetSubmitting=false;$('reset-confirmation').value='';
    const c=state.reset.counts;$('reset-counts').textContent=`全部 ${c.products} 个商品 · ${c.tasks} 条制作记录 · ${c.imports} 个导入批次`;
    $('reset-status').textContent=state.reset.reason||'确认后先备份，再清空。此操作不会删除视频文件。';$('reset-status').classList.remove('error');
    updateResetButton();show('reset-dialog');return;
  }
  if(name==='confirm-reset'){
    if(!state.reset?.can_clear||$('reset-confirmation').value.trim()!=='清空所有清单')return;
    state.resetSubmitting=true;updateResetButton();$('reset-status').textContent='正在备份并清空记录…';
    try{const r=await api('/api/board/reset',{confirmation:$('reset-confirmation').value.trim(),revision:state.reset.revision});clearLocalBoard();toast(r.message);await refresh();}
    catch(e){state.reset.can_clear=false;$('reset-status').textContent=e.message+' 请关闭后重新打开清空窗口。';$('reset-status').classList.add('error');throw e;}
    finally{state.resetSubmitting=false;updateResetButton();}return;
  }
  if(name==='import'){show('import-dialog');return;}
  if(name==='paste'){show('paste-dialog');return;}
  if(name==='settings'){
    const s=state.data.settings;$('setting-start').value=Review.stamp(s.clip_start_seconds??0);$('setting-end').value=Review.stamp(s.clip_end_seconds===undefined?s.max_duration_seconds:s.clip_end_seconds);$('setting-scale').value=s.text_scale;$('setting-subtitles').checked=s.subtitles_enabled;$('setting-target').value=s.subtitle_target;validateTemplate();
    $('models-ready').textContent=`本地语音识别：${state.data.speech_ready?'已就绪':'未安装'} · 中文翻译：${state.data.translation_ready?'已就绪':'未安装'}`;show('settings-dialog');return;
  }
  if(name==='links'){show('links-dialog');return;}
  if(name==='prev'||name==='next'){state.page+=name==='next'?1:-1;await refresh();return;}
  if(name==='clear-selection'){state.selected.clear();updateSelection();return;}
  if(name==='pause'){await api('/api/control',{action:state.data.paused?'resume':'pause'});await refresh();return;}
  if(name==='save-paste'){const r=await api('/api/board/paste',{text:$('paste-text').value});toast(r.message);$('paste-dialog').close();$('paste-text').value='';await changeView('todo');return;}
  if(name==='detail'){await openDetail(pid);return;}
  if(name==='render'||name==='collect'||name==='download-source'){await jobs([pid],name==='collect'?'collect':name==='download-source'?'download':'render');if(state.detail===pid)await openDetail(pid);return;}
  if(name==='bulk-render'||name==='bulk-collect'){if(!state.selected.size)return;await jobs([...state.selected],name==='bulk-render'?'render':'collect');return;}
  if(name==='redo'){await redo(pid);return;}
  if(name==='redo-version'){await redo(pid,{source_task_id:button.dataset.task});return;}
  if(name==='redo-new'){if(!confirm('重新检索素材并生成新版？旧视频保留。新检索结果也可能选中相同视频；需要指定不同素材时请先保存新链接。'))return;await redo(pid,{mode:'new_source'});return;}
  if(name==='preview-candidate'){await Review.preview(button.dataset.url,Number(button.dataset.duration)||0);return;}
  if(name==='save-source'||name==='use-candidate'){const r=await api(`/api/products/${pid}/source`,{url:name==='save-source'?$('detail-source').value:button.dataset.url});toast(name==='use-candidate'?'已采用；调整起止时间后，点底部“保存时段并生成新版”即可。':r.message);await openDetail(pid);await refresh();return;}
  if(name==='save-search-name'){const r=await api(`/api/products/${pid}/search-name`,{name:$('detail-search-name').value});toast(r.message);await openDetail(pid);await refresh();return;}
  if(name==='mark'){if(button.dataset.state==='done'&&!confirm('手动标记完成只调整待办状态，不代表已生成视频。继续吗？'))return;const r=await api(`/api/products/${pid}/mark`,{state:button.dataset.state});toast(r.message);await openDetail(pid);await refresh();return;}
  if(name==='existing-excel'){await serverImport('/api/imports/existing');return;}
  if(name==='recent-excel'){await serverImport('/api/imports/recent');return;}
  if(name==='analyze-import'){await analyzeImport();return;}
  if(name==='commit-import'){const r=await api('/api/imports/commit',importPayload());toast(r.message);$('import-dialog').close();state.batch=r.batch_id;$('batch').value=r.batch_id;await changeView('todo');return;}
  if(name==='save-settings'){if(!validateTemplate())return;const r=await api('/api/settings',{clip_start_seconds:$('setting-start').value,clip_end_seconds:$('setting-end').value,text_scale:Number($('setting-scale').value),subtitles:{enabled:$('setting-subtitles').checked,target:$('setting-target').value,language:'auto'}});toast(r.message);$('settings-dialog').close();await refresh();return;}
  if(name==='extract-links'){const r=await api('/api/links/extract',{text:$('links-text').value});$('extracted-links').innerHTML=r.links.length?r.links.map((url,i)=>`<label><input type="radio" name="extracted" value="${esc(url)}" ${i===0?'checked':''}><a href="${safeLink(url)}" target="_blank" rel="noopener">${esc(url)}</a></label>`).join(''):'没有找到有效的公开 HTTPS 视频页面或分享链接。';$('link-binding').classList.toggle('hidden',!r.links.length);if(r.links.length)await loadLinkProducts();toast(`整理出 ${r.count} 个不同的视频链接。`);return;}
  if(name==='bind-extracted'){const id=$('link-product').value,url=document.querySelector('[name=extracted]:checked')?.value;if(!id||!url)throw Error('请先选中素材链接和对应商品。');const r=await api(`/api/products/${id}/source`,{url});toast(r.message);await refresh();}
}
function validateTemplate(){
  try{const start=Review.parse($('setting-start').value),end=Review.parse($('setting-end').value,true),length=end===null?90:Math.round((end-start)*1000)/1000;
    if(start>21600||end!==null&&end>21600||length<.1||length>90)throw Error('结束须晚于起始，片段长度为 0.1–90 秒；时间点不能超过 6 小时。');
    $('setting-range-summary').textContent=`批量默认：${Review.stamp(start)} → ${end===null?'自动结束':Review.stamp(end)} · 最多 ${length} 秒`;$('setting-range-summary').classList.remove('error');$('save-settings').disabled=false;return true;
  }catch(e){$('setting-range-summary').textContent=e.message;$('setting-range-summary').classList.add('error');$('save-settings').disabled=true;return false;}
}
for(const id of ['setting-start','setting-end'])$(id).addEventListener('input',validateTemplate);
document.addEventListener('click',async event=>{
  const close=event.target.closest('[data-close]');if(close){close.closest('dialog').close();return;}
  const view=event.target.closest('[data-view]');if(view){await changeView(view.dataset.view);return;}
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
  button.disabled=true;try{await action(button.dataset.action,button);}catch(error){toast(error.message,true);}finally{if(button.id==='confirm-reset')updateResetButton();else if(button.id==='save-skip-rules')updateRulesButton();else if(button.isConnected)button.disabled=false;}
});
document.addEventListener('change',event=>{
  const input=event.target;if(input.dataset.select){input.checked?state.selected.add(input.dataset.select):state.selected.delete(input.dataset.select);if(state.selected.size>100){state.selected.delete(input.dataset.select);toast('每批最多选择 100 个商品。',true);}updateSelection();}
  if(input.dataset.column){$('commit-import').disabled=true;importStatus('列匹配已改变，请点击“更新预览”。');}
});
$('select-page').addEventListener('change',event=>{for(const p of state.data.items){if(p.status==='working')continue;if(event.target.checked&&state.selected.size<100)state.selected.add(p.id);else if(!event.target.checked)state.selected.delete(p.id);}updateSelection();});
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=$('search').value;state.page=1;refresh();},250);});
$('reset-confirmation').addEventListener('input',updateResetButton);
for(const id of ['skip-rules-text','skip-exceptions'])$(id).addEventListener('input',dirtyRules);
$('skip-rules-enabled').addEventListener('change',dirtyRules);
$('batch').addEventListener('change',()=>{state.batch=$('batch').value;state.page=1;state.selected.clear();refresh();});
$('excel-file').addEventListener('change',event=>upload(event.target.files[0]));
$('import-rule').addEventListener('change',()=>analyzeImport());$('import-sheet').addEventListener('change',()=>analyzeImport(true));
$('import-hidden').addEventListener('change',()=>analyzeImport());
$('link-product-search').addEventListener('input',()=>{clearTimeout(linkSearchTimer);linkSearchTimer=setTimeout(()=>loadLinkProducts().catch(e=>toast(e.message,true)),250);});
for(const type of ['dragenter','dragover'])$('dropzone').addEventListener(type,e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
for(const type of ['dragleave','drop'])$('dropzone').addEventListener(type,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');if(type==='drop')upload(e.dataTransfer.files[0]);});
$('detail-dialog').addEventListener('close',()=>{state.detail=null;state.detailRequest=(state.detailRequest||0)+1;window.Review?.close();$('detail-body').querySelectorAll('video').forEach(video=>video.pause());});
window.addEventListener('hashchange',()=>{refresh();if(location.hash==='#storage')action('storage').catch(e=>toast(e.message,true));});refresh().then(()=>{if(location.hash==='#storage')action('storage').catch(e=>toast(e.message,true));});setInterval(()=>{if(!document.hidden)refresh();},3000);
