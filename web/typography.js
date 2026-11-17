"use strict";
const $=id=>document.getElementById(id);
let current=null,taskId=new URLSearchParams(location.search).get("task") || "",timer=null,sequence=0,dirty=false,busy=false,loading=false;
let speechBusy=false,speechTimer=null,speechErrors=0;
const sections=[["brand","顶部品牌"],["title","型号标题"],["description","商品类别"]];
const fields={};

async function api(path,body){
  const response=await fetch(path,{method:body===undefined?"GET":"POST",headers:body===undefined?{}:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
  let data;try{data=await response.json();}catch(_){throw new Error("本地服务未响应，请重新打开工作台。");}
  if(!response.ok)throw new Error(data.error || "操作未完成，请稍后再试。");
  return data;
}
function message(text,error=false){$("message").textContent=text;$("message").className="type-message"+(error?" is-error":"");$("message").hidden=false;}
function node(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;}
function values(){const result={subtitle_align:$("subtitle-align").value,subtitle_y:Number($("subtitle-y").value)};for(const [section] of sections){const f=fields[section];result[section+"_font"]=f.font.value;result[section+"_scale"]=Number(f.scale.value)/100;result[section+"_bold"]=f.bold.checked;result[section+"_color"]=f.color.value;result[section+"_offset"]=Number(f.offset.value);}return result;}
function textValues(){return {brand_title:$("brand-text").value.trim(),product_title:$("title-text").value.trim(),description:$("description-text").value.trim()};}
function subtitleValues(){return {enabled:$("subtitles-enabled").checked,language:$("speech-language").value,target:$("subtitle-target").value,srt:taskId?$("subtitle-srt").value:""};}
function targetLabel(){ $("recognize-speech").textContent=$("subtitle-target").value==="zh"?"识别并翻译成中文":"识别原语言字幕"; }
function hasText(){const value=textValues();return Boolean(value.brand_title && value.product_title);}
function labels(){for(const [section] of sections){const f=fields[section];f.scaleLabel.textContent=f.scale.value+"%";const offset=Number(f.offset.value);f.offsetLabel.textContent=offset===0?"原位置":(offset<0?"上移 ":"下移 ")+Math.abs(offset);}const y=$("subtitle-y").value;$("subtitle-preset").value=["994","780","590"].includes(y)?y:"custom";}
function changed(){if(loading)return;dirty=true;sequence++;$("save-state").textContent="尚未保存";$("preview-status").textContent="等待更新预览…";labels();$("result-link").hidden=true;clearTimeout(timer);timer=setTimeout(preview,650);}
function fillStyle(style){for(const [section] of sections){const f=fields[section];f.font.value=style[section+"_font"];f.scale.value=Math.round(style[section+"_scale"]*100);f.bold.checked=style[section+"_bold"];f.color.value=style[section+"_color"];f.offset.value=style[section+"_offset"];}$("subtitle-align").value=style.subtitle_align || "center";$("subtitle-y").value=style.subtitle_y ?? 994;$("subtitle-y-slider").value=$("subtitle-y").value;labels();}
function createControls(){
  for(const [section,label] of sections){
    const panel=node("div");panel.id="panel-"+section;panel.setAttribute("role","tabpanel");panel.setAttribute("aria-labelledby","tab-"+section);panel.hidden=section!=="title";
    const wrap=node("div");wrap.className="field";const fontLabel=node("label",label+"字体");fontLabel.htmlFor=section+"-font";const select=node("select");select.id=section+"-font";for(const choice of current.fonts){const option=node("option",choice.label);option.value=choice.value;select.append(option);}wrap.append(fontLabel,select);panel.append(wrap);
    function slider(suffix,title,min,max,step){const heading=node("label");heading.className="adjust-heading";heading.htmlFor=section+"-"+suffix;heading.append(node("span",title));const output=node("output");output.htmlFor=section+"-"+suffix;heading.append(output);const input=node("input");input.type="range";input.id=section+"-"+suffix;input.min=min;input.max=max;input.step=step;panel.append(heading,input);return [input,output];}
    const [scale,scaleLabel]=slider("scale",label+"大小",50,160,5);
    const ends=node("div");ends.className="adjust-ends";ends.append(node("span","更小"),node("span","更大"));panel.append(ends);
    const options=node("div");options.className="font-options";const boldLabel=node("label");boldLabel.className="bold-option";const bold=node("input");bold.type="checkbox";bold.id=section+"-bold";boldLabel.append(bold,node("span",label+"加粗"));const colorLabel=node("label");colorLabel.className="color-option";const color=node("input");color.type="color";color.id=section+"-color";colorLabel.append(node("span",label+"颜色"),color);options.append(boldLabel,colorLabel);panel.append(options);
    const [offset,offsetLabel]=slider("offset",label+"上下位置",-100,100,2);
    const positionEnds=node("div");positionEnds.className="adjust-ends";positionEnds.append(node("span","向上"),node("span","向下"));panel.append(positionEnds);
    for(const input of [select,scale,bold,color,offset])input.addEventListener("input",changed);
    fields[section]={font:select,scale,scaleLabel,bold,color,offset,offsetLabel};$("section-panels").append(panel);
  }
}
function switchTab(section){for(const [name] of sections){const selected=name===section;$("tab-"+name).setAttribute("aria-selected",String(selected));$("tab-"+name).tabIndex=selected?0:-1;$("panel-"+name).hidden=!selected;}}
async function preview(){
  clearTimeout(timer);const request=++sequence;
  if(!hasText()){$("preview-canvas").classList.remove("is-loading");$("preview-status").textContent="请填写品牌与标题";return;}
  $("preview-status").textContent="正在更新预览…";$("preview-canvas").classList.add("is-loading");
  try{
    const data=await api("/api/typography/preview",{task_id:taskId,style:values(),text:textValues(),subtitles:subtitleValues(),subtitle_sample:$("subtitle-sample").checked});
    if(request!==sequence)return;
    const image=$("preview-image");image.onload=()=>{if(request!==sequence)return;image.hidden=false;$("preview-placeholder").hidden=true;$("preview-canvas").classList.remove("is-loading");$("preview-status").textContent="预览已更新";};
    image.onerror=()=>{if(request===sequence){$("preview-status").textContent="预览画面未加载";$("preview-canvas").classList.remove("is-loading");}};
    image.src=data.url;
    $("layout-warning").textContent=(data.warnings || []).join(" ");$("layout-warning").hidden=!data.warnings?.length;
    $("preview-help").textContent=data.using_sample?"使用示例原片预览文字，修改后自动更新。":"使用这条视频的原始素材预览，导出采用相同排版。";
    if(data.speech_preview_kind==="sample")$("preview-help").textContent+=" 当前是位置示例，不会写入成片；实际加字幕需勾选语音字幕开关。";
    else if(data.speech_preview)$("preview-help").textContent+=" 当前展示第一句语音字幕。";
  }catch(error){if(request!==sequence)return;$("preview-canvas").classList.remove("is-loading");$("preview-status").textContent="预览未完成";message(error.message,true);}
}
function lock(value){busy=value;$("save-default").disabled=value;$("render-edited").disabled=value || speechBusy;}
function setSpeechBusy(value){speechBusy=value;$("recognize-speech").disabled=value || !current?.speech_ready;$("subtitle-srt").disabled=value;$("speech-language").disabled=value;$("subtitle-target").disabled=value;$("speech-progress").hidden=!value;$("render-edited").disabled=busy || value;}
async function pollSpeech(){
  clearTimeout(speechTimer);
  try{
    const data=await api("/api/tasks/"+encodeURIComponent(taskId)+"/transcript"),state=data.transcription;
    speechErrors=0;$("speech-status").textContent=state.message || "等待本机识别…";$("speech-progress").value=state.progress || 0;
    if(state.status==="ready"){
      $("subtitle-target").value=state.subtitle_target || "original";targetLabel();
      $("subtitle-srt").value=state.srt || "";$("subtitle-details").open=true;setSpeechBusy(false);changed();return;
    }
    if(state.status==="failed" || state.status==="none"){setSpeechBusy(false);return;}
  }catch(error){speechErrors++;$("speech-status").textContent=error.message;if(speechErrors>=5){setSpeechBusy(false);return;}}
  speechTimer=setTimeout(pollSpeech,2000);
}
async function load(){
  loading=true;
  try{
    current=await api("/api/typography"+(taskId?"?task="+encodeURIComponent(taskId):""));
    createControls();fillStyle(current.style);
    $("brand-text").value=current.text.brand_title;$("title-text").value=current.text.product_title;$("description-text").value=current.text.description;
    $("subtitles-enabled").checked=current.subtitles.enabled;$("speech-language").value=current.subtitles.language;$("subtitle-srt").value=current.subtitles.srt;
    $("subtitle-target").value=current.subtitles.target || "zh";targetLabel();
    $("recognize-speech").hidden=!taskId;$("subtitle-details").hidden=!taskId;
    $("speech-status").textContent=current.speech_ready?(current.translation_ready?"本地识别＋中文翻译已就绪 · 无 API key · 不按 API token 收费":"识别已就绪；中文翻译需双击 D 盘项目中的「安装中文翻译.bat」。"):"模型尚未安装：请双击 D 盘项目中的「安装语音字幕.bat」，完成后刷新。";
    setSpeechBusy(false);
    const target=$("editing-target");for(const task of current.tasks){const option=node("option",task.title+" · "+task.id.slice(-4));option.value=task.id;target.append(option);}target.value=taskId;
    $("mode-label").textContent=taskId?"编辑这条视频":"批量默认样式";$("copy-heading").textContent=taskId?"这条视频的文案":"示例文案";
    $("copy-note").textContent=taskId?"修改后生成新版，原成片保留。":"只用于预览，不会替换每个商品的名称。";
    $("render-edited").hidden=!taskId;$("save-state").textContent=taskId?"当前视频样式":"已保存的默认样式";
    $("save-help").textContent=taskId?"「生成新版」应用本条文案、排版、字幕及位置，旧成片不变；「保存为批量默认」保存样式、字幕位置与开关，不复制字幕内容。":"保存后，新商品自动使用这套排版、字幕位置与开关；旧成片不变，每个商品仍使用自己的名称和类别。";
    lock(false);loading=false;
    if(["queued","transcribing"].includes(current.task?.transcription?.status)){setSpeechBusy(true);pollSpeech();}
    await preview();
  }catch(error){loading=false;message(error.message,true);$("preview-status").textContent="未连接";}
}
for(const [section] of sections){$("tab-"+section).addEventListener("click",()=>switchTab(section));$("tab-"+section).addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();const i=sections.findIndex(item=>item[0]===section),next=sections[(i+(event.key==="ArrowRight"?1:2))%3][0];switchTab(next);$("tab-"+next).focus();});}
for(const id of ["brand-text","title-text","description-text"])$(id).addEventListener("input",changed);
for(const id of ["subtitles-enabled","speech-language","subtitle-srt"])$(id).addEventListener("input",changed);
$("subtitle-align").addEventListener("change",changed);
$("subtitle-sample").addEventListener("change",()=>{sequence++;clearTimeout(timer);preview();});
$("subtitle-preset").addEventListener("change",()=>{if($("subtitle-preset").value==="custom"){$("subtitle-y").focus();return;}$("subtitle-y").value=$("subtitle-preset").value;$("subtitle-y-slider").value=$("subtitle-y").value;changed();});
$("subtitle-y-slider").addEventListener("input",()=>{$("subtitle-y").value=$("subtitle-y-slider").value;changed();});
$("subtitle-y").addEventListener("input",()=>{if($("subtitle-y").validity.valid)$("subtitle-y-slider").value=$("subtitle-y").value;changed();});
$("subtitle-target").addEventListener("change",()=>{if(taskId){$("subtitle-srt").value="";$("speech-status").textContent="输出语言已切换。请重新识别，或直接生成新版自动处理；不会只把原语言字幕改个标签。";}targetLabel();changed();});
$("recognize-speech").addEventListener("click",async()=>{
  if(speechBusy || !taskId)return;setSpeechBusy(true);$("speech-status").textContent="正在排队…";
  try{await api("/api/tasks/"+encodeURIComponent(taskId)+"/transcribe",{language:$("speech-language").value,target:$("subtitle-target").value});$("subtitles-enabled").checked=true;pollSpeech();}
  catch(error){setSpeechBusy(false);$("speech-status").textContent=error.message;}
});
$("editing-target").addEventListener("change",()=>{const value=$("editing-target").value;location.href="/typography"+(value?"?task="+encodeURIComponent(value):"");});
$("reset-style").addEventListener("click",()=>{if(!current)return;fillStyle(current.defaults);changed();});
$("save-default").addEventListener("click",async()=>{
  if(busy || !current)return;lock(true);
  try{const data=await api("/api/typography",{style:values(),subtitles:{enabled:$("subtitles-enabled").checked,language:$("speech-language").value,target:$("subtitle-target").value}});dirty=false;$("save-state").textContent="已保存为默认样式";message(data.message);}
  catch(error){message(error.message,true);}finally{lock(false);}
});
$("render-edited").addEventListener("click",async()=>{
  if(busy || !taskId)return;if(!hasText()){message("请先填写品牌和标题。",true);return;}lock(true);
  try{const data=await api("/api/tasks/"+encodeURIComponent(taskId)+"/appearance",{style:values(),text:textValues(),subtitles:subtitleValues()});dirty=false;$("save-state").textContent="修改已保存";message(data.message);$("result-link").href="/#task-"+data.task_id;$("result-link").hidden=false;}
  catch(error){message(error.message,true);}finally{lock(false);}
});
load();
