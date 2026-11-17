'use strict';
(()=>{
 const el=id=>document.getElementById(id),escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let data=null;
 function message(text,error=false){el('status').textContent=text;el('status').classList.toggle('error',error);}
 async function api(path,value){const response=await fetch(path,value===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const result=await response.json();if(!response.ok)throw Error(result.error||'操作失败');return result;}
 async function run(button,action){button.disabled=true;try{await action();}catch(e){message(e.message,true);}finally{button.disabled=false;}}
 function products(){const filter=el('product-filter').value.toLowerCase();el('product').innerHTML=data.products.filter(p=>p.title.toLowerCase().includes(filter)).map(p=>`<option value="${p.id}">${escape(p.title)}</option>`).join('');}
 el('product-filter').addEventListener('input',products);
 el('save').addEventListener('click',e=>run(e.currentTarget,async()=>message((await api('/api/sources/settings',{scope:el('scope').value})).message)));
 el('probe').addEventListener('click',e=>run(e.currentTarget,async()=>{el('result').hidden=true;message('正在读取公开视频元信息，最长约 65 秒；不会下载或调用模型…');const result=await api('/api/sources/probe',{url:el('video-url').value.trim()});const v=result.video;if(!v)throw Error('没有找到可用视频');el('video-url').value=v.url;el('result').innerHTML=`<strong>${escape(v.title||'未提供标题')}</strong>${escape(v.platform)} · ${v.duration?Number(v.duration).toFixed(1)+' 秒':'时长将在下载后校验'}<br>已发现视频，尚未下载。授权与实际下载需进一步核对。`;el('result').hidden=false;message(result.message);}));
 async function bind(download){const id=el('product').value;if(!id)throw Error('请先在工作台整理商品，再选择要绑定的商品');await api('/api/products/'+id+'/source',{url:el('video-url').value.trim()});if(download){const result=await api('/api/board/jobs',{ids:[id],job:'download'});if(!result.added)throw Error(result.errors?.[0]?.message||'未加入下载任务，请回工作台查看状态');message('已加入原片下载队列。回工作台查看进度，下载完成后可本页播放和粗剪。');}else message('链接已保存，不下载、不制作。可回工作台打开商品详情。');}
 el('bind').addEventListener('click',e=>run(e.currentTarget,()=>bind(false)));el('download').addEventListener('click',e=>run(e.currentTarget,()=>bind(true)));
 api('/api/sources').then(result=>{data=result;el('scope').innerHTML=Object.entries(data.scopes).map(([key,label])=>`<option value="${key}">${escape(label)}</option>`).join('');el('scope').value=data.scope;el('platforms').innerHTML=[...new Set(data.platforms.map(p=>p.name))].map(name=>`<span>${escape(name)}</span>`).join('');products();message(data.note);}).catch(e=>message(e.message,true));
})();
