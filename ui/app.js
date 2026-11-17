'use strict';
const $ = selector => document.querySelector(selector);
const make = (tag, cls, txt) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (txt !== undefined && txt !== null) node.textContent = String(txt);
  return node;
};
const add = (node,...items) => {node.append(...items.filter(Boolean));return node;};
const button = (label, action, cls='btn', disabled=false) => {
  const b=make('button',cls,label); b.type='button'; b.disabled=disabled;
  b.addEventListener('click', action);return b;
};
const labeled=(label, field)=>add(make('label','',label),field);
const field=(tag,name,placeholder,max)=> {
  const n=make(tag); n.name=name;if(placeholder)n.placeholder=placeholder;if(max)n.maxLength=max;return n;
};
const state = {assets:[],skills:[],jobs:[],token:'',filter:'全部',refs:new Map(),current:null,openedAsset:null};
const filters=['全部','自制','收藏参考','待整理'];
const dimensions=['钩子','画法','分镜','节奏','配音','字幕'];
const libraryEndpoint=id=>'/media/asset/'+encodeURIComponent(id);
const jobEndpoint=(id, phase)=>'/media/job/'+encodeURIComponent(id)+'/'+phase;
function safeLink(raw){
  try {const u=new URL(raw);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}
}
function safeMedia(raw){
  try{const u=new URL(raw,location.origin);if(u.origin===location.origin&&u.pathname.startsWith('/media/'))return u.href;}catch{}
  return null;
}
function notice(label){
  const box=$('#notice');box.textContent=label;box.hidden=false;
  clearTimeout(notice.timer);notice.timer=setTimeout(()=>{box.hidden=true;},3500);
}
function errorAt(where,message=''){
  const el=$(where);el.hidden=!message;el.textContent=message;
}
async function call(url, {method='GET',body}={}){
  const headers={Accept:'application/json'};
  if(method!=='GET') headers['X-Workbench-Token']=state.token;
  if(body!==undefined && !(body instanceof FormData)){headers['Content-Type']='application/json';body=JSON.stringify(body);}
  let reply;
  try{reply=await fetch(url,{method,headers,body,credentials:'same-origin',cache:'no-store'});}
  catch{throw Error('连不上本地工作台。检查启动窗口是否仍在运行，再刷新。');}
  let payload;
  try{payload=await reply.json();}catch{throw Error('本地服务返回了无法读取的结果（'+reply.status+'）。');}
  if(!reply.ok)throw Error(payload.error||payload.message||'请求未完成（'+reply.status+'）。');
  return payload;
}
function kindOf(a){
  const k=String(a.kind||'').toLowerCase(); const path=String(a.file||'').toLowerCase();
  if(k.includes('image')||/\.(png|jpg|jpeg|webp|gif)$/.test(path))return 'image';
  if(k.includes('audio')||/\.(wav|mp3|m4a|aac|flac|ogg)$/.test(path))return 'audio';
  if(k.includes('video')||/\.(mp4|mov|webm|m4v)$/.test(path))return 'video';
  return 'link';
}
const kindLabel={video:'视频',image:'图片',audio:'音频',link:'来源链接'};
function media(a, thumbnail=false){
  const kind=kindOf(a); if(kind==='link'||!a.file)return null;
  const el=make(kind==='image'?'img':kind,thumbnail?'':('preview-'+kind));
  el.src=libraryEndpoint(a.id)+(thumbnail&&kind==='video'?'#t=0.8':'');
  if(kind==='image'){el.alt=thumbnail?'':a.title;el.loading='lazy';}
  else{
    el.preload=thumbnail?'metadata':'auto';el.controls=!thumbnail;
    if(kind==='video'){el.playsInline=true;}
    if(thumbnail){el.muted=true;el.tabIndex=-1;el.setAttribute('aria-hidden','true');}
  }
  return el;
}
function genericThumb(a){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 28 28');svg.setAttribute('width','24');svg.setAttribute('height','24');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');
  path.setAttribute('d',kindOf(a)==='audio'?'M5 11v6m5-9v12m4-15v18m4-12v6m5-4v2':kindOf(a)==='link'?'M12 16l4-4m-6 7l-2 2a3.5 3.5 0 0 1-5-5l5-5a3.5 3.5 0 0 1 5 0m2 6a3.5 3.5 0 0 0 5 0l5-5a3.5 3.5 0 0 0-5-5l-2 2':'M5 7h18v14H5zM5 17l6-5 5 4 3-2 4 4');path.setAttribute('stroke','currentColor');path.setAttribute('fill','none');path.setAttribute('stroke-width','1.4');path.setAttribute('stroke-linecap','round');svg.append(path);return svg;
}
function setReference(a,enabled){
  if(enabled){if(!state.refs.has(a.id))state.refs.set(a.id,{id:a.id,dimensions:[],segment:'',notes:''});}
  else state.refs.delete(a.id);
  renderLibrary();renderReferences();
}
function renderLibrary(){
  $('#filters').replaceChildren(...filters.map(f=>{
    const el=button(f,()=>{state.filter=f;renderLibrary();},'filter'+(f===state.filter?' active':''));
    el.setAttribute('aria-pressed',f===state.filter?'true':'false');return el;
  }));
  const q=$('#search').value.toLocaleLowerCase().trim();
  const assets=state.assets.filter(a=>(state.filter==='全部'||a.category===state.filter)&&
    (!q||[a.title,a.id,a.notes].join(' ').toLocaleLowerCase().includes(q)));
  $('#asset-count').textContent=assets.length+' 条素材';
  $('#assets').replaceChildren(...(assets.length?assets.map(a=>{
    const chosen=state.refs.has(a.id), article=make('article','asset'+(chosen?' selected':''));
    const thumb=button('',()=>openAsset(a),'asset-thumb');thumb.setAttribute('aria-label','预览 '+a.title);
    const still=kindOf(a)==='audio'?null:media(a,true);
    thumb.append(genericThumb(a));
    if(still){
      still.hidden=true;
      const onFrame=()=>{still.hidden=false;thumb.querySelector('svg')?.setAttribute('hidden','');};
      still.addEventListener(still.tagName==='IMG'?'load':'loadeddata',onFrame,{once:true});
      thumb.append(still);
      if(still.complete&&still.naturalWidth)onFrame();
    }
    const name=button(a.title,()=>openAsset(a),'asset-name');
    const meta=add(make('div','metadata'),make('span','technical',a.id),make('span','',a.category||kindLabel[kindOf(a)]));
    const check=make('input');check.type='checkbox';check.checked=chosen;check.setAttribute('aria-label','选作参考 '+a.title);
    check.addEventListener('change',()=>setReference(a,check.checked));
    return add(article,thumb,add(make('div'),name,meta),add(make('label','asset-check'),check));
  }):[make('p','empty',state.assets.length?'没有符合条件的素材。':'素材库还没有条目。用「加入素材」导入文件或登记链接。')]));
}
function renderSkill(){
  const select=$('#skill'),prev=select.value;
  select.replaceChildren(...[Object.assign(make('option','','选择制作流程'),{value:''}),...state.skills.map(s=>Object.assign(make('option','',s.title||s.id),{value:s.id}))]);
  if(state.skills.some(s=>s.id===prev))select.value=prev;
  else if(state.skills.length===1)select.value=state.skills[0].id;
  skillDescription();
}
function skillCurrent(){return state.skills.find(s=>s.id===$('#skill').value);}
function ownVoice(a){return a.category==='自制'&&Boolean(a.file)&&kindOf(a)==='audio';}
function needsVoice(s=skillCurrent()){return Boolean(s&&(s.requires_voice||s.id==='pencil-drawing'));}
function skillDescription(){
  const s=skillCurrent();
  const el=$('#skill-description');
  const intro=make('p','help',s?(s.description||'沿用此流程，按下方选择读取参考。'):'先选流程，再决定素材中的哪些要素能参考。');
  el.replaceChildren(intro);
  if(s){
    const fallback=s.id==='pencil-drawing'?'笔触逐步画出人物和动作，先用中文说清情境，再写出西语例句并朗读。混合语句保持同一声线，不分别拼接。先确认有声片头和第一幕；通过后锁定画面和声音再做全片。':'';
    const details=Array.isArray(s.details)?s.details.map(x=>String(x)).join('\n'):String(s.details||fallback);
    if(details.trim()){
      const detail=make('details','workflow-details');
      detail.append(make('summary','','查看流程细节'),make('div','workflow-copy',details));
      el.append(detail);
    }
  }
  updateVoiceGate();
}
function renderVoices(preferred){
  const select=$('#voice-asset'),prev=preferred||select.value;
  const options=state.assets.filter(ownVoice);
  select.replaceChildren(...[Object.assign(make('option','','请选择本集自制音轨'),{value:''}),...options.map(a=>Object.assign(make('option','',a.title),{value:a.id}))]);
  if(options.some(a=>a.id===prev))select.value=prev;
  updateVoiceGate();
}
function updateVoiceGate(){
  const selected=skillCurrent(),required=needsVoice(selected);
  const voice=$('#voice-asset').value,valid=state.assets.some(a=>a.id===voice&&ownVoice(a));
  $('#voice-required').hidden=!required;
  $('#voice-asset').setAttribute('aria-required',required?'true':'false');
  $('#voice-help').textContent=required&&!valid
    ? '这个流程不替换原音色。先导入本集自制音轨并在这里选择；素材勾选的「配音」只用作风格参考。'
    : '本集音轨会用于成片。参考素材的「配音」只借鉴语气和节奏，不会把原声剪进成片。';
  $('#voice-help').classList.toggle('voice-block',required&&!valid);
  const player=$('#voice-monitor'),source=valid?libraryEndpoint(voice):'';
  if(source&&player.getAttribute('src')!==source)player.src=source;
  if(!source&&player.hasAttribute('src')){player.pause();player.removeAttribute('src');player.load();}
  player.hidden=!valid;
  const general=!state.token||!selected||state.submitting;
  $('#start-job').disabled=general||(required&&!valid);
  $('#start-storyboard').disabled=general;
}
function renderReferences(){
  $('#reference-total').textContent='已选 '+state.refs.size+' 条';
  if(!state.refs.size){$('#references').replaceChildren(make('div','empty','在左侧勾选参考素材，再指定要借鉴的要素。也可以只用所选流程。'));return;}
  $('#references').replaceChildren(...Array.from(state.refs.values(),r=>{
    const a=state.assets.find(i=>i.id===r.id);
    const dims=make('div','dims');
    for(const d of dimensions){
      const input=make('input');input.type='checkbox';input.checked=r.dimensions.includes(d);
      input.setAttribute('aria-label',(a?.title||r.id)+' · '+d);
      input.addEventListener('change',()=>r.dimensions=input.checked?[...new Set([...r.dimensions,d])]:r.dimensions.filter(x=>x!==d));
      dims.append(add(make('label','dim'),input,make('span','',d)));
    }
    const segment=field('input','segment','例 00:00–00:10',100);segment.value=r.segment;
    segment.addEventListener('input',()=>r.segment=segment.value);
    const notes=field('input','notes','例：只学停顿，不换声音',1800);notes.value=r.notes;
    notes.addEventListener('input',()=>r.notes=notes.value);
    const actions=add(make('div'),a?button('参考卡',()=>openAsset(a),'minor'):null,
      make('span','',' '),button('移除',()=>setReference(a||r,false),'minor'));
    return add(make('section','ref'),add(make('div','ref-title'),make('strong','',a?.title||r.id),actions),dims,
      add(make('div','ref-fields'),labeled('参考时段（可选）',segment),labeled('要保留／不要照搬（可选）',notes)));
  }));
}
function normalizeJob(job){
  const phase=String(job.phase||'sample').toLowerCase();
  const status=String(job.status||'').toLowerCase();
  const isFinal=['final','full'].includes(phase)||status.includes('final')||status.includes('full');
  const isStoryboard=phase==='storyboard';
  const sampleUrl=safeMedia(job.sample_url) || ((job.sample||job.sample_path||['awaiting_review','review','sample_ready','preview_ready','sample_complete'].includes(status)||(!isFinal&&['done','completed','succeeded','success','ready'].includes(status)))?jobEndpoint(job.id,'sample'):null);
  const failed=['failed','error','cancelled','canceled','blocked','interrupted'].some(x=>status.includes(x));
  const finalUrl=safeMedia(job.final_url) || ((job.final||job.final_path||(isFinal&&['done','completed','succeeded','success','ready','final_ready','final_complete'].includes(status)))?jobEndpoint(job.id,'final'):null);
  const review=!isStoryboard&&!isFinal&&!failed&&Boolean(sampleUrl)&&!['running','queued','starting'].includes(status);
  const label=failed?'需要处理':isStoryboard?(sampleUrl?'无声分镜可预览':'制作无声分镜中'):finalUrl?'全片可预览':review?'待审样片':isFinal?'制作全片中':status==='queued'?'等待启动':status?'制作样片中':'等待任务';
  return {failed,label,sampleUrl,finalUrl,review,isFinal,isStoryboard,retryFinal:failed&&isFinal&&Boolean(sampleUrl)};
}
let jobSignature='';
function renderJobs(force=false){
  const j=state.jobs.find(x=>x.id===state.current)||state.jobs[0];
  if(j)state.current=j.id;
  const signature=JSON.stringify([j?.id,state.jobs.map(x=>[x.id,x.phase,x.status,x.error,x.sample_url,x.final_url,x.message,x.refs])]);
  if(!force&&signature===jobSignature)return;jobSignature=signature;
  if(!j){
    $('#job-content').replaceChildren(make('p','empty','还没有制作任务。填好左侧制作单后，点「做有声样片」。'));
    $('#job-history').replaceChildren();return;
  }
  const status=normalizeJob(j);
  const content=add(make('div','job-feature'),make('span','state'+(status.failed?' bad':''),status.label),make('h3','',j.title||'未命名任务'));
  const found=state.skills.find(s=>s.id===j.skill);
  if(j.skill)content.append(make('p','help',found?.title||j.skill));
  if(j.error)content.append(make('p','error job-error',j.error));
  else if(j.message)content.append(make('p','help wrap',j.message));
  for(const [caption,src] of (status.finalUrl? [['全片',status.finalUrl]]:status.sampleUrl?[[status.isStoryboard?'无声分镜预览':'有声样片',status.sampleUrl]]:[])){
    const video=make('video','job-video'); video.src=src;video.controls=true;video.playsInline=true;video.preload='metadata';video.setAttribute('aria-label',caption);
    video.addEventListener('error',()=>{if(!content.querySelector('.playback-error'))content.append(make('p','error playback-error','浏览器暂未读到可播视频。刷新进度，或检查任务日志。'));});
    content.append(video);
  }
  if(status.isStoryboard&&status.sampleUrl&&!status.failed)content.append(make('p','help','这份是无声分镜，不是有声样片。补齐本集音轨后，可从制作单启动有声样片。'));
  if(status.review)appendAudioComparison(content,j);
  if(status.review||status.retryFinal){
    const approve=button(status.retryFinal?'重试制作全片':'通过，做全片',async()=>{
      approve.disabled=true;
      try{await call('/api/jobs/'+encodeURIComponent(j.id)+'/approve',{method:'POST',body:{}});await refresh();notice('已提交全片制作。');}
      catch(e){content.append(make('p','error',e.message));approve.disabled=false;}
    },'btn primary');content.append(approve);
  } else if(!status.finalUrl&&!status.failed&&(!status.isStoryboard||!status.sampleUrl)){
    content.append(make('p','help',status.isStoryboard?'不使用音轨，分镜完成后在这里播放。':status.isFinal?'全片完成后将在这里出现。':'有声样片完成后将在这里出现。'));
  }
  $('#job-content').replaceChildren(content);
  $('#job-history').replaceChildren(...state.jobs.filter(x=>x.id!==j.id).map(other=>button('',()=>{
    state.current=other.id;renderJobs(true);
  },'job-row')).map((row,i)=>{
    const other=state.jobs.filter(x=>x.id!==j.id)[i];
    return add(row,make('span','job-name',other.title||'未命名任务'),make('span','state',normalizeJob(other).label));
  }));
}
let lastAssetSig='',lastSkillSig='',refreshing;
async function refresh(){
  if(refreshing)return refreshing;
  refreshing=(async()=>{
    const next=await call('/api/state');
    state.token=next.token||state.token;state.jobs=Array.isArray(next.jobs)?next.jobs:[];
    const ass=JSON.stringify(next.assets),ss=JSON.stringify(next.skills);
    if(ass!==lastAssetSig){lastAssetSig=ass;state.assets=Array.isArray(next.assets)?next.assets:[];renderLibrary();renderReferences();renderVoices();}
    if(ss!==lastSkillSig){lastSkillSig=ss;state.skills=Array.isArray(next.skills)?next.skills:[];renderSkill();}
    updateVoiceGate();renderJobs();
    document.dispatchEvent(new CustomEvent('workbench-state',{detail:next}));
  })();
  try{await refreshing;}finally{refreshing=null;}
}
function addCardFields(a,wrapper){
  const cards=make('section','cards');
  cards.append(make('h3','','参考卡'));
  const entries=Array.isArray(a.cards)?a.cards:[];
  if(!entries.length)cards.append(make('p','help','尚未记录。可写清具体时间、看到了什么、怎么借鉴。'));
  for(const c of entries){
    const dl=make('dl');
    for(const [name,val] of [['时段',c.segment],['要素',c.dimension],['观察',c.observation],['借鉴方式',c.application],['核验情况',c.verification]]){
      if(val)dl.append(make('dt','',name),make('dd','',val));
    }
    cards.append(dl);
  }
  const detail=make('details');
  detail.append(make('summary','','添加一张参考卡'));
  const form=make('form','card-form');
  const segment=field('input','segment','例 00:00–00:03',100);segment.required=true;
  const dimension=make('select');dimension.name='dimension';
  for(const type of dimensions)dimension.append(Object.assign(make('option','',type),{value:type}));
  const observation=field('textarea','observation','例：人物先挥手，西语稍后写出',1800);observation.required=true;observation.rows=2;
  const application=field('textarea','application','例：借鉴动作和出字顺序，不照描人物',1800);application.required=true;application.rows=2;
  const verification=make('select');verification.name='verification';
  for(const v of ['手动记录','未验证'])verification.append(Object.assign(make('option','',v),{value:v}));
  const problem=make('p','error');problem.hidden=true;problem.setAttribute('role','alert');
  const submit=make('button','btn','保存参考卡');submit.type='submit';
  add(form,labeled('时间段',segment),labeled('要素',dimension),labeled('观察到了什么',observation),labeled('做视频时怎么借鉴',application),labeled('核验情况',verification),problem,submit);
  form.addEventListener('submit',async e=>{
    e.preventDefault();submit.disabled=true;problem.hidden=true;
    try{
      await call('/api/assets/'+encodeURIComponent(a.id)+'/cards',{method:'POST',body:Object.fromEntries(new FormData(form))});
      await refresh();
      const latest=state.assets.find(x=>x.id===a.id)||a;
      renderAssetDetail(latest);notice('参考卡已保存。');
    }catch(err){problem.textContent=err.message;problem.hidden=false;}finally{submit.disabled=false;}
  });
  add(cards,add(detail,form));
  wrapper.append(cards);
}
const audioResults=new Map();
const asTime=s=>Number.isFinite(Number(s))?Number(s).toFixed(1)+' 秒':'—';
const speechLabels={auto:'自动识别',zh:'中文',es:'西语'};
function speechLanguage(){
  const el=make('select');el.name='language';
  for(const [value,text] of Object.entries(speechLabels))el.append(Object.assign(make('option','',text),{value}));
  return el;
}
const mixedSpeechTip='中西语混说：先用自动识别；如果一段漏字或串语种，再填这一段的起止时刻，选对应语言重跑。';
function showAudio(result,slot){
  const src=safeMedia(result.audio_url);const player=src?make('audio','preview-audio'):null;
  if(player){player.controls=true;player.preload='metadata';player.src=src;player.setAttribute('aria-label','提取的原始参考音频');}
  const transcript=result.transcript||{};
  const reading=make('div','audio-readout');
  reading.append(make('p','help','范围：'+asTime(result.segment_start_sec)+' – '+asTime(result.segment_end_sec)+(result.default_segment_capped?'（仅分析开头片段）':'')));
  const stats=[];
  if(Number.isFinite(result.rms_dbfs))stats.push('平均电平 '+result.rms_dbfs+' dBFS');
  if(Number.isFinite(result.peak_dbfs))stats.push('峰值 '+result.peak_dbfs+' dBFS');
  if(Array.isArray(result.pauses))stats.push('检测到 '+result.pauses.length+' 处低声或停顿');
  if(Array.isArray(result.pitch_estimates)&&result.pitch_estimates.length)stats.push('音高估计 '+result.pitch_estimates.length+' 点');
  if(stats.length)reading.append(make('p','help',stats.join(' · ')));
  const metrics=make('details','audio-measurements');
  metrics.append(make('summary','','查看带时间的停顿和音高'));
  function rows(label,items,textOf){
    if(!Array.isArray(items)||!items.length)return null;
    const listing=make('div','metric-list');listing.append(make('strong','',label));
    for(const item of items)listing.append(make('p','technical',textOf(item)));
    return listing;
  }
  const pauses=rows('停顿 / 低声（原片时间）',result.pauses,
    p=>asTime(p.start_sec)+' – '+asTime(p.end_sec));
  const pitch=rows('音高估计（原片时间）',result.pitch_estimates,
    p=>asTime(p.time_sec)+' · '+Number(p.hz).toFixed(1)+' Hz');
  if(pauses||pitch)reading.append(add(metrics,add(make('div','measurement-grid'),pauses,pitch)));
  if(result.method_note)reading.append(make('p','help wrap',result.method_note));
  const detail=make('details');detail.open=true;
  detail.append(make('summary','','逐段转写'+(speechLabels[transcript.language]?' · '+speechLabels[transcript.language]:'')));
  if(transcript.status==='ready'&&Array.isArray(transcript.segments)){
    const list=make('ul','transcript');
    for(const t of transcript.segments)list.append(make('li','',asTime(t.start_sec)+' – '+asTime(t.end_sec)+'  '+t.text));
    detail.append(list);
  }
  if(Array.isArray(transcript.unrecognized)&&transcript.unrecognized.length){
    const list=make('ul','transcript');
    for(const t of transcript.unrecognized)list.append(make('li','',asTime(t.start_sec)+' – '+asTime(t.end_sec)+'  未识别'));
    detail.append(list);
  }
  detail.append(make('p','help wrap',transcript.message||'未取得转写。可以先播放提取的原音；机器指标不代替试听。'));
  slot.replaceChildren(...[player,reading,detail].filter(Boolean));
}
function addAudioFields(a,wrapper){
  const k=kindOf(a);
  if(k==='image')return;
  const box=make('section','cards audio-section');box.append(make('h3','','音频参考'));
  if(!a.file){
    box.append(make('p','help','只有来源链接。请把有权使用的本地视频或单独音频加入素材库，再来提取试听。'));
    wrapper.append(box);return;
  }
  const form=make('form','audio-form');
  const segment=field('input','audio-segment','可选 00:03–00:09',100);
  const language=speechLanguage();
  const extract=make('button','btn','提取并分析音频');extract.type='submit';
  const tools=add(make('div','audio-tools'),labeled('分析时段（可选）',segment),labeled('转写语言',language),extract);
  const slot=make('div','audio-result');slot.setAttribute('aria-live','polite');
  form.addEventListener('submit',async e=>{
    e.preventDefault();extract.disabled=true;slot.replaceChildren(make('p','help','正在从原文件提取音频并分析…'));
    try{
      const result=await call('/api/assets/'+encodeURIComponent(a.id)+'/audio',{method:'POST',body:{segment:segment.value.trim(),language:language.value}});
      audioResults.set(a.id,result);showAudio(result,slot);
    }catch(err){slot.replaceChildren(make('p','error',err.message));}finally{extract.disabled=false;}
  });
  wrapper.append(add(box,add(form,tools),make('p','help wrap',mixedSpeechTip),slot));
  if(audioResults.has(a.id))showAudio(audioResults.get(a.id),slot);
}
function appendAudioComparison(content,job){
  const refs=Array.isArray(job.refs)?job.refs:[];
  const picks=refs.filter(r=>r.dimensions?.includes('配音')).map(r=>[r,state.assets.find(a=>a.id===r.id)]);
  if(!picks.length)return;
  const section=make('details','cards comparison');
  section.append(make('summary','','对照参考配音（'+picks.length+'）'));
  for(const [r,a] of picks){
    const item=make('div','job-line');
    item.append(make('p','help wrap',(a?.title||r.id)+(r.segment?' · '+r.segment:'')));
    if(!a?.file||!['video','audio'].includes(kindOf(a))){
      item.append(make('p','help','没有可提取的本地音轨。请导入对应视频或音频。'));
    }else{
      const slot=make('div','audio-result');
      const language=speechLanguage();
      const run=button('提取试听这段',async()=>{
        run.disabled=true;slot.replaceChildren(make('p','help','正在提取参考音轨…'));
        try{
          const output=await call('/api/assets/'+encodeURIComponent(a.id)+'/audio',{method:'POST',body:{segment:r.segment==='全片'?'':(r.segment||''),language:language.value}});
          showAudio(output,slot);
        }catch(err){slot.replaceChildren(make('p','error',err.message));}
        finally{run.disabled=false;}
      },'btn compact');
      item.append(add(make('div','audio-tools'),labeled('转写语言',language),run),slot);
    }
    section.append(item);
  }
  content.append(section);
}
function renderAssetDetail(a){
  $('#preview-title').textContent=a.title;
  const wrapper=make('div');
  const file=media(a);
  const link=safeLink(a.url);
  if(file)wrapper.append(file);
  if(link){
    const anchor=make('a','btn link-button','在新标签页打开来源');
    anchor.href=link;anchor.target='_blank';anchor.rel='noopener noreferrer';wrapper.append(anchor);
  }
  if(!file&&!link)wrapper.append(make('p','empty','没有可播放的文件或有效的来源链接。'));
  if(a.review)wrapper.append(make('p','help wrap',a.review));
  if(a.notes)wrapper.append(make('p','help wrap',a.notes));
  const has=state.refs.has(a.id);
  const choice=button(has?'移出制作单':'勾选作参考',()=>{
    setReference(a,!state.refs.has(a.id));choice.textContent=state.refs.has(a.id)?'移出制作单':'勾选作参考';
  },'btn');
  wrapper.append(add(make('div','preview-action'),choice,make('span','help',a.category||kindLabel[kindOf(a)])));
  addAudioFields(a,wrapper); addCardFields(a,wrapper);
  $('#preview-content').replaceChildren(wrapper);
}
function openAsset(a){
  state.openedAsset=a.id;
  renderAssetDetail(a);const dialog=$('#preview-dialog');if(!dialog.open)dialog.showModal();
}
$('#preview-dialog').addEventListener('close',()=>{$('#preview-content').replaceChildren();state.openedAsset=null;});
for(const close of document.querySelectorAll('[data-close]'))close.addEventListener('click',()=>document.getElementById(close.dataset.close).close());
let importingVoice=false;
function openImport(voice=false){
  importingVoice=voice;
  const f=$('#import-form');
  f.elements.category.value=voice?'自制':'待整理';
  f.elements.file.accept=voice?'audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg': 'video/*,image/*,audio/*,.mp4,.mov,.webm,.png,.jpg,.jpeg,.webp,.wav,.mp3,.m4a';
  f.elements.url.closest('label').hidden=voice;
  if(voice)f.elements.url.value='';
  $('#import-title').textContent=voice?'导入本集音轨':'加入素材';
  errorAt('#import-error');$('#import-dialog').showModal();
}
$('#import-open').addEventListener('click',()=>openImport());
$('#voice-import').addEventListener('click',()=>openImport(true));
$('#voice-asset').addEventListener('change',updateVoiceGate);
$('#workflow-open').addEventListener('click',()=>{errorAt('#workflow-error');$('#workflow-dialog').showModal();});
$('#workflow-form').addEventListener('submit',async e=>{
  e.preventDefault();errorAt('#workflow-error');
  const f=e.currentTarget,body=Object.fromEntries(new FormData(f));
  if(Object.values(body).some(x=>!String(x).trim())){errorAt('#workflow-error','请填写名称、一句话介绍和执行步骤。');return;}
  const submit=f.querySelector('[type="submit"]');submit.disabled=true;
  try{
    const result=await call('/api/workflows',{method:'POST',body});
    await refresh();
    const id=result.id||result.workflow?.id||result.skill?.id;
    if(id&&state.skills.some(s=>s.id===id)){$('#skill').value=id;skillDescription();}
    f.reset();$('#workflow-dialog').close();notice('工作流已保存。');
  }catch(err){errorAt('#workflow-error',err.message);}finally{submit.disabled=false;}
});
$('#import-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const form=e.currentTarget, data=new FormData(form), file=data.get('file');
  const hasFile=file instanceof File&&file.size>0;
  const entered=String(data.get('url')||'').trim(),url=safeLink(entered);
  errorAt('#import-error');
  if(importingVoice){
    if(!hasFile||!(/\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(file.name))){errorAt('#import-error','请选择本地音频文件，例如 WAV、MP3 或 M4A。');return;}
    data.set('category','自制');
  }
  if(!hasFile&&!url){errorAt('#import-error','请选择本地文件，或输入一个有效的 HTTPS／HTTP 来源链接。');return;}
  if(entered&&!url){errorAt('#import-error','来源链接无效，或含账号和密码。请使用正常的 HTTPS／HTTP 链接。');return;}
  const submit=form.querySelector('[type="submit"]');submit.disabled=true;
  try{
    let body;
    if(hasFile){if(url)data.set('url',url);else data.delete('url');body=data;}
    else{data.delete('file');data.set('url',url);body=Object.fromEntries(data);}
    const saved=await call('/api/assets',{method:'POST',body});
    form.reset();$('#import-dialog').close();state.filter='全部';await refresh();
    if(importingVoice)renderVoices(saved.id||saved.asset?.id);
    notice(importingVoice?'本集音轨已导入。':'素材已加入。');
  }catch(err){errorAt('#import-error',err.message);}finally{submit.disabled=false;}
});
$('#job-form').addEventListener('submit',async e=>{
  e.preventDefault();errorAt('#form-error');
  const mode=e.submitter?.id==='start-storyboard'?'storyboard':'sample';
  const chosen=skillCurrent(),voice_asset_id=mode==='sample'?$('#voice-asset').value:'';
  if(mode==='sample'&&needsVoice(chosen)&&!state.assets.some(a=>a.id===voice_asset_id&&ownVoice(a))){
    errorAt('#form-error','请先选择本集自制音轨，参考素材不能代替它。');return;
  }
  if(state.submitting)return;
  const refs=Array.from(state.refs.values()).map(({id,dimensions,segment,notes})=>({id,dimensions,segment:segment.trim(),notes:notes.trim()}));
  const incomplete=refs.find(ref=>!ref.dimensions.length);
  if(incomplete){
    const name=state.assets.find(a=>a.id===incomplete.id)?.title||incomplete.id;
    errorAt('#form-error','请给「'+name+'」选至少一个要借鉴的要素，或把它移出制作单。');return;
  }
  const form=new FormData(e.currentTarget),title=String(form.get('title')||'').trim(),script=String(form.get('script')||'').trim(),skill=form.get('skill');
  if(!title||!script||!skill){errorAt('#form-error','请填标题、脚本，并选一个制作流程。');return;}
  state.submitting=true;updateVoiceGate();
  try{
    const result=await call('/api/jobs',{method:'POST',body:{title,script,skill,refs,mode,...(voice_asset_id?{voice_asset_id}:{})}});
    state.current=result.id||result.job?.id||null;await refresh();notice(mode==='storyboard'?'无声分镜任务已提交。':'样片任务已提交。');
    if(matchMedia('(max-width: 850px)').matches)$('#production').scrollIntoView({block:'start'});
  }catch(err){errorAt('#form-error',err.message);}finally{state.submitting=false;updateVoiceGate();}
});
$('#search').addEventListener('input',renderLibrary);
$('#skill').addEventListener('change',skillDescription);
$('#refresh').addEventListener('click',async()=>{try{await refresh();notice('已刷新。');}catch(e){notice(e.message);}});
renderReferences();
refresh().catch(e=>{
  $('#asset-count').textContent='未连接服务';$('#assets').replaceChildren(make('p','error',e.message));
  $('#job-content').replaceChildren(make('p','empty','服务恢复后，点击「刷新」。'));
});
setInterval(()=>refresh().catch(()=>{}),4000);
