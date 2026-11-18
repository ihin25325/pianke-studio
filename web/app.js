"use strict";

const $ = (id) => document.getElementById(id);
const statusNames = {queued:"等待中",searching:"检索素材",downloading:"下载素材",transcribing:"识别语音",rendering:"正在剪辑",completed:"已完成",needs_review:"待确认",failed:"未完成"};
const activeStatuses = new Set(["searching","downloading","transcribing","rendering"]);
const cards = new Map();
let currentState = null;
let pollTimer = null;
let polling = false;
let requestBusy = false;
let submittingBatch = false;
let firstLoad = true;
let refreshSequence = 0;
let settingsEdited = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(message) {
  $("global-alert-text").textContent = message;
  $("global-alert").hidden = false;
}

function safeLink(value, localOnly = false) {
  if (!value) return null;
  try {
    const url = new URL(value, location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (localOnly && url.origin !== location.origin) return null;
    return url.href;
  } catch (_) { return null; }
}

async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {Accept:"application/json"} : {"Content-Type":"application/json",Accept:"application/json"},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache:"no-store"
    });
    let payload;
    try { payload = await response.json(); }
    catch (_) { throw new Error("本地服务没有返回可读取的结果，请查看运行窗口。"); }
    if (!payload || typeof payload !== "object") throw new Error("本机服务返回的数据格式不正确，请查看运行窗口。");
    if (!response.ok || payload.ok === false) throw new Error(payload.error || payload.message || `请求未完成（${response.status}）`);
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("本机服务暂时没有回应，请稍后重试。");
    if (error instanceof TypeError) throw new Error("无法连接本机服务，请保持启动窗口开启。");
    throw error;
  } finally { clearTimeout(timeout); }
}

function formatDuration(value) {
  if (!Number.isFinite(Number(value)) || Number(value) <= 0) return "";
  const duration = Math.round(Number(value));
  return `${Math.floor(duration / 60)}:${String(duration % 60).padStart(2, "0")}`;
}

function productLines(text) {
  return text.trim().split(/\r?\n/).filter(line => line.trim() && !line.startsWith("宝贝id\t")).map(line => line.trim());
}

function productCount() {
  const count = productLines($("products").value).length;
  $("line-count").textContent = `${count} / 100`;
  $("line-count").classList.toggle("over-limit", count > 100);
  return count;
}

function sizeLabel(value) { return value >= 1.2 ? "更大文字" : value >= 1.05 ? "稍大文字" : "适中文字"; }
function updateSettingsLabel() {
  const label = sizeLabel(Number($("text-scale").value));
  $("text-scale-label").textContent = label.replace("文字", "");
  $("settings-summary").textContent = `${$("duration").value || 90} 秒以内 · ${label}`;
}

function makeField(id, label, type, placeholder) {
  const wrap = element("div");
  const labelNode = element("label", "", label);
  labelNode.htmlFor = id;
  const input = element(type === "textarea" ? "textarea" : "input");
  input.id = id;
  if (type !== "textarea") input.type = type;
  input.placeholder = placeholder || "";
  wrap.append(labelNode, input);
  return {wrap,input};
}

function createCard(task) {
  const card = element("article", "task-card");
  card.id='task-'+task.id;
  card.setAttribute("aria-labelledby", `task-title-${task.id}`);
  const heading = element("div", "task-heading");
  const titleArea = element("div");
  const title = element("h3", "task-title"); title.id = `task-title-${task.id}`;
  const identity = element("p", "task-identity");
  const badge = element("span", "task-badge");
  titleArea.append(title, identity); heading.append(titleArea,badge);
  const message = element("p", "task-message");
  const progress = element("div", "task-progress");
  progress.setAttribute("role", "progressbar"); progress.setAttribute("aria-label", "剪辑进度"); progress.setAttribute("aria-valuemin", "0"); progress.setAttribute("aria-valuemax", "100");
  const fill = element("div", "task-progress-fill"); progress.append(fill);

  const detail = element("details", "task-detail");
  const summary = element("summary", "", "查看素材与简介");
  const description = element("div", "task-description");
  const descriptionSource = element("p", "source-caption");
  const selected = element("a", "selected-source", "查看已选素材 ↗"); selected.target="_blank"; selected.rel="noopener noreferrer";
  const sourceHeading = element("p", "source-heading", "检索到的候选素材");
  const candidates = element("ul", "source-list");
  detail.append(summary,description,descriptionSource,selected,sourceHeading,candidates);

  const review = element("form", "review-form"); review.hidden = true;
  const reviewIntro = element("p", "review-intro", "补充商品名与视频链接，即可继续。下方只需一句话说明是什么商品。");
  const reviewTitle = makeField(`review-title-${task.id}`, "商品标题", "text", "填写准确的商品名称或型号");
  reviewTitle.input.maxLength = 300;
  reviewTitle.input.required = true;
  const reviewUrl = makeField(`review-url-${task.id}`, "国内外视频链接", "url", "https://视频页面或公开直链"); reviewUrl.input.required = true;
  const reviewDescription = makeField(`review-description-${task.id}`, "商品类别 / 简介（可选）", "textarea", "例如：录音电容麦克风、音频接口、MIDI键盘。默认不写卖点。");
  reviewDescription.input.maxLength = 1800;
  const reviewError = element("p", "inline-error"); reviewError.hidden = true; reviewError.setAttribute("role", "alert");
  const reviewSubmit = element("button", "review-submit", "确认素材，继续剪辑 →"); reviewSubmit.type="submit";
  review.append(reviewIntro,reviewTitle.wrap,reviewUrl.wrap,reviewDescription.wrap,reviewError,reviewSubmit);

  const actions = element("div", "task-actions");
  const download = element("a", "download-link", "下载成片 ↓"); download.setAttribute("download", "");
  const metadata = element("a", "metadata-link", "制作记录"); metadata.target="_blank"; metadata.rel="noopener noreferrer";
  const subtitles=element("a","metadata-link","字幕 SRT ↓");subtitles.setAttribute("download","");
  const previewToggle = element("button", "preview-toggle", "展开预览"); previewToggle.type = "button"; previewToggle.setAttribute("aria-expanded", "false");
  const retry = element("button", "retry-button", "重新尝试"); retry.type="button";
  const edit=element('a','editor-link','编辑文字 / 字体');edit.href='/typography?task='+encodeURIComponent(task.id);
  actions.append(download,metadata,subtitles,retry,edit,previewToggle);
  const videoWrap = element("div", "video-wrap"); videoWrap.hidden = true;
  const video = element("video"); video.controls = true; video.preload = "none"; video.playsInline = true;
  videoWrap.append(video);
  const actionError = element("p", "inline-error task-action-error"); actionError.hidden=true; actionError.setAttribute("role","alert");
  card.append(heading,message,progress,detail,review,actions,actionError,videoWrap);

  const state = {card,title,identity,badge,message,progress,fill,detail,description,descriptionSource,selected,sourceHeading,candidates,review,reviewTitle,reviewUrl,reviewDescription,reviewError,reviewSubmit,actions,download,metadata,subtitles,previewToggle,retry,edit,videoWrap,video,actionError,task,reviewInitialized:false,candidateKey:"",outputUrl:null,lastStatus:null,signature:""};

  previewToggle.addEventListener("click", () => {
    const open = videoWrap.hidden;
    videoWrap.hidden = !open;
    previewToggle.textContent = open ? "收起预览" : "展开预览";
    previewToggle.setAttribute("aria-expanded", String(open));
    if (open && state.outputUrl && !video.getAttribute("src")) video.src = state.outputUrl;
    if (!open) video.pause();
  });
  retry.addEventListener("click", async () => {
    if(retry.disabled)return;
    retry.disabled = true;
    actionError.hidden=true;
    try { await api(`/api/tasks/${encodeURIComponent(task.id)}/retry`, {}); await refresh(); }
    catch(error) { actionError.textContent=error.message;actionError.hidden=false; }
    finally { retry.disabled = false; }
  });
  review.addEventListener("submit", async event => {
    event.preventDefault();
    if(reviewSubmit.disabled)return;
    reviewError.hidden = true;
    reviewSubmit.disabled = true; reviewSubmit.textContent="正在提交…";
    for(const field of [reviewTitle,reviewUrl,reviewDescription])field.input.disabled=true;
    try {
      const payload={url:reviewUrl.input.value.trim(),title:reviewTitle.input.value.trim()};
      if(reviewDescription.input.value.trim()!==String(state.originalReviewDescription || "").trim())payload.description=reviewDescription.input.value.trim();
      await api(`/api/tasks/${encodeURIComponent(task.id)}/source`, payload);
      await refresh();
    } catch(error) { reviewError.textContent=error.message; reviewError.hidden=false; }
    finally { for(const field of [reviewTitle,reviewUrl,reviewDescription])field.input.disabled=false;reviewSubmit.disabled=false; reviewSubmit.textContent="确认素材，继续剪辑 →"; }
  });
  return state;
}

function updateCard(view, task) {
  view.task = task;
  const status = task.status || "queued";
  if(view.lastStatus!==status)view.actionError.hidden=true;
  view.title.textContent = task.title || task.input || "商品视频";
  view.title.title = view.title.textContent;
  const identityParts = [task.brand,task.model].filter(Boolean);
  if (task.product_id) identityParts.push(`商品 ${task.product_id}`);
  view.identity.textContent=identityParts.join(" · "); view.identity.hidden=!identityParts.length;
  view.badge.textContent=statusNames[status] || status;
  view.badge.className=`task-badge${activeStatuses.has(status) ? " is-active" : status === "completed" ? " is-completed" : status === "needs_review" ? " is-review" : status === "failed" ? " is-failed" : ""}`;
  view.message.textContent=(status==='transcribing'?task.transcription?.message:null) || task.message || (status === "queued" ? "已加入队列，轮到这个商品后会自动开始。" : "");
  view.message.hidden=!view.message.textContent;
  const progress = Math.max(0,Math.min(100,Number(task.progress) || 0));
  view.progress.hidden=!activeStatuses.has(status);
  view.progress.setAttribute("aria-valuenow", String(progress));
  view.fill.style.width=`${progress}%`;
  view.description.textContent=task.description || "暂未整理商品简介。";
  view.descriptionSource.textContent=task.description_source ? `简介来源：${task.description_source}` : "";
  view.descriptionSource.hidden=!task.description_source;
  const selectedUrl=safeLink(task.selected_url);
  view.selected.hidden=!selectedUrl;
  if(selectedUrl) view.selected.href=selectedUrl;
  const candidates=Array.isArray(task.candidates) ? task.candidates : [];
  view.sourceHeading.hidden=!candidates.length;
  const candidateKey=JSON.stringify([status,candidates]);
  if(view.candidateKey!==candidateKey) {
    view.candidateKey=candidateKey;
    view.candidates.replaceChildren();
    candidates.forEach(candidate => {
      const li=element("li", "source-item");
      const link=element("a", "source-link", candidate.title || "视频素材");
      const href=safeLink(candidate.url);
      if(href) { link.href=href; link.target="_blank"; link.rel="noopener noreferrer"; }
      const caption=element("p", "source-caption", [candidate.channel,formatDuration(candidate.duration)].filter(Boolean).join(" · "));
      li.append(link,caption);
      if(status==="needs_review" && href) {
        const pick=element("button", "source-pick", "使用这个素材"); pick.type="button";
        pick.addEventListener("click", () => {view.reviewUrl.input.value=href;view.reviewUrl.input.focus();});
        li.append(pick);
      }
      view.candidates.append(li);
    });
  }
  view.detail.hidden=!(task.description || selectedUrl || candidates.length);
  view.review.hidden=status!=="needs_review";
  if(status==="needs_review" && !view.reviewInitialized) {
    view.reviewInitialized=true;
    view.reviewTitle.input.value=task.title || "";
    view.reviewUrl.input.value=task.selected_url || "";
    view.reviewDescription.input.value=task.description || "";
    view.originalReviewDescription=task.description || "";
    if(candidates.length) view.detail.open=true;
  }
  view.retry.hidden=status!=="failed";
  view.edit.hidden=!(status==='completed' || status==='failed');
  const outputUrl=safeLink(task.output_url,true);
  const completed=status==="completed" && Boolean(outputUrl);
  view.download.hidden=!completed;
  view.previewToggle.hidden=!completed;
  const metadataUrl=safeLink(task.metadata_url,true);
  view.metadata.hidden=!(completed && metadataUrl);
  const subtitleUrl=safeLink(task.subtitle_url,true);view.subtitles.hidden=!(completed && subtitleUrl);if(subtitleUrl)view.subtitles.href=subtitleUrl;
  if(completed) {
    view.download.href=outputUrl;
    view.video.setAttribute("aria-label", `${task.title || "商品"}成片预览`);
    if(view.outputUrl!==outputUrl){view.video.pause();view.video.removeAttribute("src");view.outputUrl=outputUrl;if(!view.videoWrap.hidden)view.video.src=outputUrl;}
  } else {
    view.video.pause(); view.videoWrap.hidden=true;
    view.previewToggle.textContent="展开预览";view.previewToggle.setAttribute("aria-expanded","false");
    if(view.video.hasAttribute("src")){view.video.removeAttribute("src");view.video.load();}
    view.outputUrl=null;
  }
  if(metadataUrl) view.metadata.href=metadataUrl;
  view.actions.hidden=!(completed || status==="failed");
  if(view.lastStatus!==null && view.lastStatus!==status && ["completed","needs_review","failed"].includes(status)) $("queue-announcement").textContent=`${task.title || "商品"}：${statusNames[status]}`;
  view.lastStatus=status;
}

function render(state) {
  currentState=state;
  if(state.typography && $('type-summary')){
    const names={'Source Han Sans CN':'思源黑体','Source Han Serif CN':'思源宋体','LXGW WenKai':'霞鹜文楷'};
    $('type-summary').textContent=(names[state.typography.title_font] || state.typography.title_font)+' 标题 · '+(names[state.typography.description_font] || state.typography.description_font)+' 类别';
  }
  const tasks=Array.isArray(state.tasks) ? state.tasks : [];
  const completed=tasks.filter(task => task.status==="completed").length;
  const review=tasks.filter(task => task.status==="needs_review").length;
  $("count-all").textContent=tasks.length;
  $("count-completed").textContent=completed;
  $("count-review").textContent=review;
  $("busy-label").textContent=state.paused ? "已暂停" : state.worker_busy ? "正在处理" : tasks.length && completed===tasks.length ? "全部完成" : "准备就绪";
  $("pause-banner").hidden=!state.paused;
  $("toggle-queue").textContent=state.paused ? "恢复队列" : "暂停队列";
  $("toggle-queue").disabled=requestBusy;
  $("empty-state").hidden=tasks.length>0;
  if(state.storage_root) $("storage-label").textContent=`本机工作目录 · ${state.storage_root}`;
  if(firstLoad && state.settings && !settingsEdited) {
    const duration=Number(state.settings.max_duration_seconds);
    const scale=Number(state.settings.text_scale);
    if(duration>=10 && duration<=90) $("duration").value=String(duration);
    if(scale>=.9 && scale<=1.25) $("text-scale").value=String(scale);
    $("subtitles-enabled").checked=Boolean(state.settings.subtitles_enabled);$("speech-language").value=state.settings.subtitle_language || "auto";
    $("subtitle-target").value=state.settings.subtitle_target || "zh";
    updateSettingsLabel();
  }
  firstLoad=false;
  $("speech-ready").textContent=state.speech_ready?(state.translation_ready?"本地识别＋中文翻译已就绪 · 无 API 费用。中文原声直接识别；专业词请校对。":"本地识别已就绪；中文翻译需双击 项目目录中的「安装中文翻译.bat」。"):"模型尚未安装：请双击 项目目录中的「安装语音字幕.bat」。";
  const present=new Set();
  tasks.forEach(task => {
    present.add(String(task.id));
    let view=cards.get(String(task.id));
    if(!view){view=createCard(task);cards.set(String(task.id),view);$("task-list").append(view.card);}
    const signature=JSON.stringify(task);
    if(view.signature!==signature){view.signature=signature;updateCard(view,task);}
  });
  for(const [id,view] of cards) {if(!present.has(id)){view.video.pause();view.card.remove();cards.delete(id);}}
  $("queue-filter").hidden=tasks.length<=6 && $("task-filter").value==="all";
  applyTaskFilter();
  if(location.hash.startsWith('#task-') && !render.didScroll){const target=document.getElementById(location.hash.slice(1));if(target){target.scrollIntoView({block:'center'});render.didScroll=true;}}
}

function applyTaskFilter() {
  const filter=$("task-filter").value;
  let visible=0;
  for(const view of cards.values()) {
    const status=view.task.status;
    const show=filter==="all" || (filter==="active" ? status==="queued" || activeStatuses.has(status) : status===filter);
    view.card.hidden=!show;
    if(show)visible++;
    else view.video.pause();
  }
  $("filter-count").textContent=`${visible} 个任务`;
  $("filter-empty").hidden=cards.size===0 || visible>0;
}

async function refresh() {
  const sequence=++refreshSequence;
  try {
    const state=await api("/api/state");
    if(sequence!==refreshSequence)return;
    $("connection").className="connection online";
    $("connection-text").textContent="本机已连接";
    render(state);
  } catch(error) {
    if(sequence!==refreshSequence)return;
    $("connection").className="connection offline";
    $("connection-text").textContent="本机暂未连接";
    if(firstLoad) $("busy-label").textContent="等待本地服务";
  }
}

async function poll() {
  clearTimeout(pollTimer);
  if (polling) return;
  polling = true;
  try { await refresh(); }
  finally { polling=false; pollTimer=setTimeout(poll,2000); }
}

$("products").addEventListener("input",productCount);
$("duration").addEventListener("input",()=>{settingsEdited=true;updateSettingsLabel();});
$("text-scale").addEventListener("input",()=>{settingsEdited=true;updateSettingsLabel();});
for(const id of ["subtitles-enabled","speech-language","subtitle-target"])$(id).addEventListener("input",()=>{settingsEdited=true;});
$("task-filter").addEventListener("change",applyTaskFilter);
$("dismiss-alert").addEventListener("click",()=>{$("global-alert").hidden=true;});
$("fill-demo").addEventListener("click",()=>{
  const demo="Neumann U87 Ai 电容麦克风";
  $("products").value=$("products").value.trim() ? `${$("products").value.trim()}\n${demo}` : demo;
  productCount(); $("products").focus();
});
$("batch-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(submittingBatch)return;
  const count=productCount();
  if(!count){$("products").focus();return;}
  if(count>100){showError("每批最多放入 100 行商品，请分批提交。");return;}
  const text=$("products").value.trim();
  submittingBatch=true;
  for(const id of ["products","duration","text-scale","fill-demo"])$(id).disabled=true;
  $("submit-batch").disabled=true;$("submit-label").textContent="正在加入队列…";$("global-alert").hidden=true;
  try {
    const result=await api("/api/tasks",{text,max_duration_seconds:Number($("duration").value),text_scale:Number($("text-scale").value),subtitles:{enabled:$("subtitles-enabled").checked,language:$("speech-language").value,target:$("subtitle-target").value}});
    const errors=Array.isArray(result.errors) ? result.errors : [];
    const added=Array.isArray(result.added) ? result.added.length : Number(result.added) || 0;
    if(errors.length) {
      const messages=errors.map(error=>typeof error==="string"?error:error.message || JSON.stringify(error));
      const rejected=new Set(messages.map(message=>Number((message.match(/^第\s*(\d+)\s*行/) || [])[1])).filter(Boolean));
      if(rejected.size===errors.length)$("products").value=productLines(text).filter((_,index)=>rejected.has(index+1)).join("\n");
      showError(`已添加 ${added} 个任务。${messages.join("；")}`);
    } else $("products").value="";
    productCount();
    $("queue-announcement").textContent=`已添加 ${added} 个任务。`;
    await refresh();
  } catch(error){showError(error.message);}
  finally{submittingBatch=false;for(const id of ["products","duration","text-scale","fill-demo","submit-batch"])$(id).disabled=false;$("submit-label").textContent="开始自动剪辑";}
});
$("toggle-queue").addEventListener("click",async()=>{
  if(!currentState || requestBusy)return;
  requestBusy=true;$("toggle-queue").disabled=true;
  try{await api("/api/control",{action:currentState.paused?"resume":"pause"});await refresh();}
  catch(error){showError(error.message);}
  finally{requestBusy=false;$("toggle-queue").disabled=false;}
});
document.addEventListener("visibilitychange",()=>{
  if(document.visibilityState==="visible"){clearTimeout(pollTimer);poll();}
});
productCount(); updateSettingsLabel(); poll();
