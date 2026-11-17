'use strict';
(() => {
  const roles={hook:'开头钩子',context:'铺垫',example:'示例／展开',takeaway:'收束／行动'};
  const patterns={question:'提问 · 留一个知识缺口',contrast:'反差 · 从熟悉说法到新方法',action:'动作 · 先做再解释',story:'情境 · 先进入一件事'};
  const kinds={shot:'镜头变化',caption:'画面文字',speech:'台词',transition:'转场',pause:'停顿',emphasis:'音量突出',pace:'语速变化'};
  const dims={hook:'钩子',pace:'节奏',captions:'字幕',transitions:'转场',audio:'声音节奏'};
  const cards=new Map(), selections=new Map();
  let current='', pollTimer, dirty=false, rendered=null, sourceSignature='', draft=null, importing=false;
  const endpoint=(id,action='analysis')=>'/api/assets/'+encodeURIComponent(id)+'/'+action;
  const sec=t=>Number(t).toFixed(2)+'s';
  const on=(id,event,fn)=>$(id).addEventListener(event,fn);
  const guarded=fn=>async e=>{errorAt('#analysis-error');try{await fn(e);}catch(err){errorAt('#analysis-error',err.message);}};
  function view(production){
    $('#analysis-view').hidden=production;$('#production-view').hidden=!production;
    $('#view-analysis').setAttribute('aria-pressed',String(!production));$('#view-production').setAttribute('aria-pressed',String(production));
    document.querySelectorAll('video,audio').forEach(m=>m.pause());
  }
  on('#view-analysis','click',()=>view(false));on('#view-production','click',()=>view(true));
  function resetSelection(id){
    clearTimeout(pollTimer);$('#structure-player')?.pause();
    current=id;rendered=null;$('#structure-card').hidden=true;
    $('#analysis-retry').disabled=!id;$('#analysis-asset').value=id;
  }
  function choose(id){
    if(dirty){notice('还有未保存的结构修改，请先保存。');$('#analysis-asset').value=current;return;}
    resetSelection(id);
    if(id)poll(id);
  }
  async function loadAssets(next){
    const options=state.assets.filter(a=>kindOf(a)==='video'&&a.file);
    const signature=JSON.stringify(options.map(a=>[a.id,a.title]));
    if(signature!==sourceSignature){
      sourceSignature=signature;
      $('#analysis-asset').replaceChildren(Object.assign(make('option','','选择视频'),{value:''}),...options.map(a=>Object.assign(make('option','',a.title),{value:a.id})));
      $('#analysis-asset').value=current;
      for(const asset of options){
        const result=await call(endpoint(asset.id));
        if(result.card)cards.set(asset.id,result.card);
      }
      renderSources();
    }
    const previous=$('#structure-workflow').value;
    $('#structure-workflow').replaceChildren(Object.assign(make('option','','选择已保存的结构流程'),{value:''}),...state.skills.filter(s=>s.structure).map(s=>Object.assign(make('option','',s.title),{value:s.id})));
    if(state.skills.some(s=>s.id===previous))$('#structure-workflow').value=previous;
    const c=next?.capabilities;
    if(c)$('#analysis-dependencies').textContent=`本机能力：画面分析${c.ffmpeg?'可用':'需 FFmpeg'} · 字幕 OCR${c.ocr?'可用':'未配置，可手填'} · 台词转写${c.transcription?'可用，需回听核对':'未配置，可回听手填'}。`;
  }
  document.addEventListener('workbench-state',e=>loadAssets(e.detail).catch(err=>errorAt('#analysis-error',err.message)));
  on('#analysis-asset','change',e=>choose(e.target.value));
  async function poll(id){
    try{
      const result=await call(endpoint(id));if(current!==id)return;
      const active=['running','cancelling'].includes(result.status);
      $('#analysis-progress').hidden=false;
      $('#analysis-message').textContent=result.message;
      $('#analysis-percent').textContent=active?`${result.progress}% · 阶段进度`:(result.elapsed_seconds?`处理 ${result.elapsed_seconds} 秒`:'');
      $('#analysis-meter').value=result.progress||0;$('#analysis-meter').hidden=!active;
      $('#analysis-cancel').hidden=!active;$('#analysis-cancel').disabled=result.status==='cancelling';
      $('#analysis-retry').disabled=active;$('#analysis-retry').textContent=result.card?'重新分析（保留旧版备份）':'开始分析／重试';
      if(result.card&&!active&&!dirty){
        cards.set(id,result.card);
        const signature=JSON.stringify(result.card);
        if(signature!==rendered){renderCard(id,result.card);rendered=signature;renderSources();}
      }
      if(active)pollTimer=setTimeout(()=>poll(id),650);
    }catch(err){errorAt('#analysis-error',err.message);$('#analysis-retry').disabled=false;}
  }
  async function start(){
    if(!current)return;
    if(dirty)throw Error('请先保存结构修改，再重新分析。');
    clearTimeout(pollTimer);$('#analysis-retry').disabled=true;
    try{await call(endpoint(current),{method:'POST',body:{language:$('#analysis-language').value}});await poll(current);}
    catch(err){$('#analysis-retry').disabled=false;throw err;}
  }
  on('#analysis-retry','click',guarded(start));
  on('#analysis-cancel','click',guarded(async()=>{await call(endpoint(current,'cancel'),{method:'POST',body:{}});clearTimeout(pollTimer);await poll(current);}));
  async function importFile(file){
    if(!file||importing)return;
    if(dirty)throw Error('请先保存当前结构修改。');
    if(!/\.(mp4|m4v|mov|webm)$/i.test(file.name)||file.size>=128*1024*1024-4096)throw Error('请选择小于 128 MB 的 MP4、MOV 或 WebM 视频。');
    importing=true;$('#structure-file').disabled=true;$('#structure-drop').classList.add('busy');
    const body=new FormData();body.set('file',file);body.set('category','待整理');body.set('title',file.name.replace(/\.[^.]+$/,''));
    try{
      const asset=await call('/api/assets',{method:'POST',body});resetSelection(asset.id);await refresh();
      $('#analysis-asset').value=current;
      await start();
    }finally{importing=false;$('#structure-file').disabled=false;$('#structure-file').value='';$('#structure-drop').classList.remove('busy');}
  }
  on('#structure-file','change',guarded(e=>importFile(e.target.files[0])));
  for(const event of ['dragenter','dragover'])on('#structure-drop',event,e=>{e.preventDefault();e.currentTarget.classList.add('dragging');});
  for(const event of ['dragleave','drop'])on('#structure-drop',event,e=>{e.preventDefault();e.currentTarget.classList.remove('dragging');});
  on('#structure-drop','drop',guarded(e=>importFile(e.dataTransfer.files[0])));
  function seek(t){const player=$('#structure-player');if(player){player.currentTime=t;player.play().catch(()=>{});}}
  function renderCard(id,card){
    const container=$('#structure-card');container.hidden=false;
    const title=state.assets.find(a=>a.id===id)?.title||'视频';
    const player=make('video','structure-player');player.id='structure-player';player.src=libraryEndpoint(id);player.controls=true;player.preload='metadata';player.playsInline=true;
    const frames=make('div','structure-frames');
    for(const frame of card.frames){
      const image=make('img');image.src='/media/analysis/'+encodeURIComponent(id)+'/'+frame.file;image.alt=sec(frame.time)+' 的真实抽帧';image.loading='lazy';
      const item=button('',()=>seek(frame.time),'frame-button');item.setAttribute('aria-label','回看 '+sec(frame.time));item.append(image,make('span','',sec(frame.time)));frames.append(item);
    }
    const timeline=make('div','evidence-timeline');
    for(const [kind,label] of Object.entries(kinds)){
      const items=card.events.filter(e=>e.kind===kind),track=make('div','evidence-track');
      for(const e of items){
        const marker=button('',()=>seek(e.start),'evidence-marker');marker.title=`${sec(e.start)} · ${e.text}`;
        marker.setAttribute('aria-label',label+' '+sec(e.start));marker.style.left=(e.start/card.analyzed_duration*98)+'%';
        marker.style.width=Math.max(.6,(e.end-e.start)/card.analyzed_duration*98)+'%';track.append(marker);
      }
      if(!items.length)track.append(make('span','help','未检出／不可用'));
      timeline.append(add(make('div','evidence-lane'),make('span','',label),track));
    }
    const info=make('div','structure-evidence');
    const summary=add(make('div','section-head'),make('h2','',title+' · 结构卡'),make('span','help',`${sec(card.analyzed_duration)} / ${sec(card.duration)}`));
    const capability=make('p','help',card.scope_note+' '+card.transcript.message);
    const details=make('details','evidence-details');details.append(make('summary','','查看检测记录、字幕与台词（点击时间回看）'));
    const list=make('div','evidence-list');
    for(const e of card.events){
      list.append(add(make('div','evidence-row'),button(sec(e.start),()=>seek(e.start),'minor'),make('strong','',kinds[e.kind]),add(make('div'),make('p','',e.text),make('p','help',e.method))));
    }
    details.append(list);
    info.append(summary,capability,timeline,frames,details,make('p','help',card.method));
    for(const warning of card.warnings)info.append(make('p','error',warning));
    const editor=make('form','structure-editor');editor.id='structure-editor';
    const hookPattern=make('select');hookPattern.id='structure-hook-pattern';
    for(const [value,label] of Object.entries(patterns))hookPattern.append(Object.assign(make('option','',label),{value}));
    hookPattern.value=card.hook_pattern||'question';
    editor.append(labeled('开场借鉴什么手法（候选，可改）',hookPattern));
    editor.append(add(make('div','section-head'),make('h3','','回看后修改结构'),make('span','help','左侧记录证据；右侧是待验证的解释。区段时间可编辑。')));
    for(const [index,beat] of card.beats.entries()){
      const row=make('div','beat-row');row.dataset.index=index;
      const role=make('select');role.name='role';role.setAttribute('aria-label','区段 '+(index+1)+' 用途');
      for(const [value,label] of Object.entries(roles))role.append(Object.assign(make('option','',label),{value}));role.value=beat.role;
      const start=make('input'),end=make('input');for(const [node,name,value] of [[start,'start',beat.start],[end,'end',beat.end]]){node.type='number';node.name=name;node.min=0;node.max=card.analyzed_duration;node.step='.001';node.value=value;node.required=true;}
      const observation=make('textarea'),interpretation=make('textarea');observation.name='observation';interpretation.name='interpretation';
      for(const [node,text] of [[observation,beat.observation],[interpretation,beat.interpretation]]){node.rows=3;node.maxLength=4000;node.value=text;}
      row.append(add(make('div','beat-time'),role,add(make('div','time-inputs'),labeled('起点（秒）',start),labeled('终点（秒）',end)),button('回看这一段',()=>seek(Number(start.value)),'minor')),
        labeled('观察到了什么 · 可纠错',observation),labeled('可能为什么有效 · 推测',interpretation));editor.append(row);
    }
    const save=make('button','btn','保存结构修改');save.type='submit';save.id='structure-save';
    const status=make('span','help',card.edited?'已有人工作业，自动证据仍保留。':'自动记录尚未核对。');status.id='structure-edit-status';
    editor.append(add(make('div','editor-actions'),save,status));
    editor.addEventListener('input',()=>{dirty=true;status.textContent='有未保存的修改';});
    editor.addEventListener('submit',guarded(async e=>{
      e.preventDefault();save.disabled=true;
      const beats=[...editor.querySelectorAll('.beat-row')].map(row=>Object.fromEntries(['start','end','role','observation','interpretation'].map(key=>[key,['start','end'].includes(key)?Number(row.querySelector(`[name=${key}]`).value):row.querySelector(`[name=${key}]`).value])));
      try{const result=await call(endpoint(id,'structure'),{method:'POST',body:{beats,hook_pattern:hookPattern.value}});cards.set(id,result);dirty=false;status.textContent='结构已保存，下一步选择复用维度。';renderSources();notice('结构修改已保存。');}
      finally{save.disabled=false;}
    }));
    container.replaceChildren(add(make('div','structure-overview'),player,info),editor);
  }
  function renderSources(){
    const area=$('#reuse-sources');area.replaceChildren();
    if(!cards.size){area.append(make('p','empty','完成分析后，这里会出现可选择的结构卡。'));return;}
    for(const [id,card] of cards){
      const item=make('div','reuse-source');item.append(make('strong','',state.assets.find(a=>a.id===id)?.title||'视频结构'));
      const choices=make('div','dims');
      for(const [dimension,label] of Object.entries(dims)){
        const input=make('input');input.type='checkbox';input.checked=(selections.get(id)||[]).includes(dimension);
        input.disabled=dimension==='audio'&&card.audio.status!=='ok';input.setAttribute('aria-label',(state.assets.find(a=>a.id===id)?.title||'视频')+' · 借鉴'+label);
        input.addEventListener('change',()=>{let selected=selections.get(id)||[];selections.set(id,input.checked?[...selected,dimension]:selected.filter(x=>x!==dimension));});
        choices.append(add(make('label','dim'),input,make('span','',label)));
      }
      item.append(choices);area.append(item);
    }
  }
  on('#structure-save-workflow','click',guarded(async()=>{
    if(dirty)throw Error('先保存结构修改，流程才会采用最新的区段。');
    const selected=[...selections].filter(([,dimensions])=>dimensions.length).map(([asset_id,dimensions])=>({asset_id,dimensions}));
    const result=await call('/api/structure-workflows',{method:'POST',body:{title:$('#structure-workflow-title').value,selections:selected,base_skill:$('#structure-pencil').checked?'pencil-drawing':''}});
    await refresh();$('#structure-workflow').value=result.id;notice('流程已保存。填一个新主题，生成自己的脚本。');
  }));
  on('#structure-draft','click',guarded(async()=>{
    const workflow_id=$('#structure-workflow').value;
    draft=await call('/api/structure-drafts',{method:'POST',body:{workflow_id,topic:$('#structure-topic').value,points:$('#structure-points').value.split('\n').map(x=>x.trim()).filter(Boolean)}});
    const result=$('#structure-draft-result');result.hidden=false;
    const text=make('textarea');text.id='structure-draft-script';text.rows=6;text.value=draft.storyboard.map(row=>`[${row.start.toFixed(2)}–${row.end.toFixed(2)}s] ${row.line}`).join('\n');
    const apply=button('带到制作单，先做样片',()=>{
      $('#title').value=draft.topic;$('#script').value=text.value+'\n\n分镜画面（按镜头顺序）：\n'+draft.storyboard.map((row,index)=>`${index+1}. [${row.start.toFixed(2)}–${row.end.toFixed(2)}s] ${row.visual}`).join('\n');$('#skill').value=workflow_id;skillDescription();view(true);notice('脚本与流程已带入。制作前请核对，并指定自己的本集音轨。');
    },'btn primary');
    const table=make('div','draft-storyboard');
    for(const row of draft.storyboard){
      const visual=make('textarea');visual.rows=3;visual.value=row.visual;visual.setAttribute('aria-label',sec(row.start)+' 分镜画面');
      visual.addEventListener('input',()=>{row.visual=visual.value;});
      table.append(add(make('div'),make('strong','',`${sec(row.start)}–${sec(row.end)} · ${roles[row.role]}`),labeled('具体画面动作 · 可编辑',visual),make('p','help',row.review)));
    }
    result.replaceChildren(make('h3','','新主题脚本与分镜'),make('p','help',draft.method+' '+draft.timing_note),make('p','help',draft.storyboard.some(r=>r.needs_visual)?'部分画面还不具体：可按「动作｜台词」补充要点，再生成；也可直接编辑下方分镜。':'画面动作来自你输入的新内容；原片素材不会自动剪入。'),labeled('可编辑脚本',text),table,make('p','help',draft.warning),apply);
    result.scrollIntoView({block:'nearest'});
  }));
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  if(state.token)loadAssets().catch(()=>{});
})();
