'use strict';
window.Downloads=(()=>{
  const el=id=>document.getElementById(id),escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let page=1,pages=1,loading=false,request=0,notify=()=>{},searchTimer,jobsKey,recordsKey;
  async function call(path,data){const r=await fetch(path,data===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),v=await r.json();if(!r.ok||v.error)throw Error(v.error||'下载请求失败');return v;}
  function counts(value){document.querySelectorAll('[data-download-count]').forEach(e=>e.textContent=value||0);}
  function time(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';}
  async function refresh(){
    const nonce=++request;loading=true;
    try{const data=await call('/api/exports?'+new URLSearchParams({page,q:el('export-record-search').value}));if(nonce!==request)return;page=data.page;pages=data.pages;counts(data.count);
      const jobs=data.batches.map(j=>`<article class="export-job ${escape(j.status)}"><div class="export-job-head"><strong>${escape(j.filename)} · ${j.count} 条</strong>${j.download_url?`<a href="${escape(j.download_url)}" download>${j.sent_at?'再次下载 ZIP':'下载 ZIP'}</a>`:''}</div><p>${escape(j.status==='missing'?'压缩包已移走，请重新勾选打包。':j.message)}</p>${j.status==='building'?`<progress value="${Number(j.progress)||0}" max="100" aria-label="打包进度"></progress> <small>${Number(j.progress)||0}%</small>`:''}${j.local_file?`<small>本机备份：${escape(j.local_file)}</small>`:''}${j.sent_at?`<small>已发送下载：${escape(time(j.sent_at))}</small>`:''}${j.skipped?.length?`<details><summary>已跳过 ${j.skipped.length} 个无成片商品</summary>${j.skipped.map(s=>`<p>${escape(s.message)} · ${escape(s.id)}</p>`).join('')}</details>`:''}</article>`).join('')||'<p class="hint">还没有批量下载包。先在商品清单勾选已有成片的商品。</p>';
      // Keep focused download anchors stable during polling/native save dialogs.
      const nextJobsKey=JSON.stringify(data.batches);if(jobsKey!==nextJobsKey){el('export-jobs').innerHTML=jobs;jobsKey=nextJobsKey;}
      el('export-record-count').textContent=`${data.total} 个版本`;el('export-notice').textContent=data.notice;
      const rows=data.items.map(r=>`<tr><td><strong>${escape(r.filename)}</strong><small>${escape(r.title)}<br>版本 ${escape(r.task_id)} · ${r.count} 次下载${r.available?'':' · 原文件已移走'}</small></td><td>${escape(time(r.downloaded_at))}</td><td><button data-action="download-video" data-task="${escape(r.task_id)}" ${r.available?'':'disabled'}>再次下载</button></td></tr>`).join('')||'<tr><td colspan="3" class="empty">暂无下载记录。仅播放或打包不会加入清单。</td></tr>';
      const nextRecordsKey=JSON.stringify(data.items);if(recordsKey!==nextRecordsKey){el('export-records').innerHTML=rows;recordsKey=nextRecordsKey;}
      el('export-page').textContent=`${page} / ${pages}`;document.querySelector('[data-action="download-prev"]').disabled=page<=1;document.querySelector('[data-action="download-next"]').disabled=page>=pages;
    }catch(e){el('export-status').textContent=e.message;}finally{if(nonce===request)loading=false;}
  }
  async function open(toast){if(toast)notify=toast;if(!el('downloads-dialog').open)el('downloads-dialog').showModal();el('export-status').textContent='';await refresh();}
  async function prepare(ids,toast){await open(toast);el('export-status').textContent='正在核对成片并准备打包…';try{const r=await call('/api/exports/batches',{ids});el('export-status').textContent=r.message;notify(r.message);await refresh();}catch(e){el('export-status').textContent=e.message;throw e;}}
  async function video(task,toast){if(toast)notify=toast;const r=await call('/api/exports/video',{task_id:task});const a=document.createElement('a');a.href=r.download_url;a.download=r.filename;document.body.append(a);a.click();a.remove();notify('已请求下载：'+r.filename+'。请核对浏览器下载结果。');}
  function move(delta){page=Math.max(1,Math.min(pages,page+delta));return refresh();}
  document.addEventListener('DOMContentLoaded',()=>{el('export-record-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;refresh();},250);});setInterval(()=>{if(el('downloads-dialog').open&&!document.hidden&&!loading)refresh();},1500);});
  return {open,refresh,prepare,video,counts,move};
})();
