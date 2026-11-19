"use strict";
window.documentWorkspaceConfig={apiBase:'/api/dramas',kind:'drama'};
const el=selector=>document.querySelector(selector);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusText={queued:'排队中',running:'处理中',done:'已完成',failed:'失败',retry:'等待重试'};
let selectedDrama=null,selectedRecord=null,polling=false,detailSignature='';
async function request(path,options={}){
  const response=await fetch('/api/dramas'+path,{headers:{'Content-Type':'application/json'},...options});
  const data=response.headers.get('content-type')?.includes('application/json')?await response.json():null;
  if(!response.ok){const error=new Error(typeof data?.detail==='string'?data.detail:'请求失败，请检查输入');error.status=response.status;throw error;}
  return data;
}
function message(text,error=false){el('#drama-message').textContent=text;el('#drama-message').className=error?'course-error':'';}
function frameUrl(id,file){return `/api/dramas/${id}/frames/${file.split('/').map(encodeURIComponent).join('/')}`;}
async function refresh(){
  const items=await request('');
  if(!items.length){
    el('#drama-list').innerHTML='<p>还没有短剧。填写本机视频路径或从 B 站下载页面导入，开始第一次拆解。</p>';
    return;
  }
  const groups=new Map();
  const standalone=[];
  for(const it of items){
    const grp=it.group_title?it.group_title.trim():'';
    if(grp){
      if(!groups.has(grp))groups.set(grp,[]);
      groups.get(grp).push(it);
    }else{
      standalone.push(it);
    }
  }

  const renderChoice=item=>`<button class="drama-choice" data-drama="${item.id}" aria-pressed="${item.id===selectedDrama}"><strong>${escapeText(item.title)}</strong><span>${statusText[item.status]||item.status}${item.shots?.length?` · ${item.shots.length} 个镜头`:''}${item.has_report?' · 逆向报告就绪':''}</span></button>`;

  let listHtml='';
  for(const [grpName,grpItems] of groups.entries()){
    const hasSelected=grpItems.some(it=>it.id===selectedDrama);
    const doneCount=grpItems.filter(it=>it.status==='done').length;
    listHtml+=`<details class="drama-group" ${hasSelected?'open':'open'}>
      <summary><span>📁 ${escapeText(grpName)}</span><span class="drama-group-count">${doneCount}/${grpItems.length}完成</span></summary>
      <div class="drama-group-items">${grpItems.map(renderChoice).join('')}</div>
    </details>`;
  }
  if(standalone.length){
    if(groups.size>0)listHtml+=`<div style="margin:12px 0 6px;font-size:12px;font-weight:600;opacity:0.7;">单片短剧</div>`;
    listHtml+=standalone.map(renderChoice).join('');
  }

  if(el('#drama-list').innerHTML!==listHtml)el('#drama-list').innerHTML=listHtml;
  if(selectedDrama)await showDrama(selectedDrama);
}
async function showDrama(id){
  const item=await request('/'+id);if(selectedDrama!==id)return;
  selectedRecord=item;
  const active=item.tasks.find(t=>['queued','running','retry'].includes(t.status))||(item.status==='running'?{status:'running'}:null);
  const signature=JSON.stringify([id,item.status,item.stage,item.progress,item.error,item.path,item.source_available,item.shots?.length,item.has_report,active?.status]);
  if(signature===detailSignature)return;detailSignature=signature;
  const oldPlayer=el('#drama-player'),oldTime=oldPlayer?.currentTime||0,wasPlaying=oldPlayer&&!oldPlayer.paused;
  const sameVideo=oldPlayer?.dataset.drama===id;
  const canRead=item.documents_ready||item.status==='done';
  const hasReport=item.has_report||item.status==='done';

  const reportButtons=hasReport?`
    <a href="/api/dramas/${id}/report" target="_blank" style="display:inline-flex;align-items:center;background:#2b6ef2;color:#fff;text-decoration:none;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:600;">在新标签页打开完整报告 ↗</a>
    <a href="/api/dramas/${id}/file/pages/script.html" target="_blank" style="display:inline-flex;align-items:center;border:1px solid var(--line,#ddd);text-decoration:none;padding:6px 12px;border-radius:6px;font-size:13px;color:inherit;">查看剧本 ↗</a>
    <a href="/api/dramas/${id}/file/pages/storyboard.html" target="_blank" style="display:inline-flex;align-items:center;border:1px solid var(--line,#ddd);text-decoration:none;padding:6px 12px;border-radius:6px;font-size:13px;color:inherit;">查看分镜 ↗</a>
  `:'';

  el('#drama-detail').innerHTML=`<div class="drama-title"><h2>${escapeText(item.title)}${item.group_title?` <small style="font-size:13px;opacity:0.75;font-weight:normal;">[${escapeText(item.group_title)}]</small>`:''}</h2><p>${active?statusText[active.status]:statusText[item.status]}${item.duration?` · ${item.duration.toFixed(1)} 秒`:''}</p></div><p class="drama-stage" role="status">${active?.status==='queued'?'等待前面的任务完成':escapeText(item.stage)} · ${Math.round(item.progress*100)}%</p><progress value="${item.progress}" max="1" aria-label="拆解进度"></progress>${item.error?`<p class="course-error">${escapeText(item.error)}</p>`:''}<div class="drama-actions">${reportButtons}${canRead?'<button id="open-drama-docs">阅读与编辑拆解</button>':''}${!active?`<button id="${item.status==='done'?'regenerate-drama':'retry-drama'}">${item.status==='done'?'重新分析':'继续 / 重试'}</button>`:''}${canRead&&!active?`<a href="/api/dramas/${id}/download">导出 Markdown ZIP</a>`:''}<button id="relink-drama" ${active?'disabled':''}>重新关联原视频</button><button id="delete-drama" ${active?.status==='running'?'disabled':''}>删除短剧</button></div><details style="margin:12px 0;"><summary style="cursor:pointer;font-size:13px;">查看原视频与文件路径</summary><p class="drama-source-path" style="margin-top:8px;">原视频：${escapeText(item.path)}</p>${item.source_available===false?'<p class="course-error" role="alert">原视频已移动或不可用，请点击“重新关联原视频”填写新路径。拆解文档和截图仍可查看。</p>':''}<video ${item.source_available===false?'hidden':''} id="drama-player" data-drama="${id}" controls preload="metadata" src="/api/dramas/${id}/source" style="max-height:280px;"></video></details>${hasReport?`<div class="drama-report-header" style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;margin-bottom:8px;"><h3 style="margin:0;font-size:15px;font-weight:600;">📊 短剧逆向分析报告</h3><a href="/api/dramas/${id}/report" target="_blank" style="font-size:13px;color:#2b6ef2;text-decoration:none;">新标签页全屏打开 ↗</a></div><div class="drama-report-container"><iframe class="drama-report-iframe" src="/api/dramas/${id}/report" title="短剧逆向分析报告"></iframe></div>`:''}${item.shots?.length&&item.shots[0]?.frames?`<div class="shot-heading"><h3>分镜联系表</h3><p>点截图定位视频；长镜头的分析分段会单独标记。</p></div><div class="shot-list">${item.shots.map(shot=>`<article class="shot-row"><div class="shot-label"><h4>${shot.id} · ${escapeText(shot.analysis?.title||'等待分析')}</h4><span>${shot.start.toFixed(2)}–${shot.end.toFixed(2)} 秒${shot.subdivision?` · 原镜头 ${shot.take} / 分段 ${shot.part}`:''}</span>${canRead?`<button data-shot-document="${shot.id}">打开镜头拆解</button>`:''}</div><div class="shot-frames">${(shot.frames||[]).map(frame=>`<button data-time="${frame.time}" aria-label="定位到 ${frame.time.toFixed(2)} 秒"><img src="${frameUrl(id,frame.file)}" alt="镜头 ${shot.id}，${frame.time.toFixed(2)} 秒" loading="lazy"><span>${frame.time.toFixed(2)} 秒</span></button>`).join('')}</div>${shot.analysis?`<p>${escapeText(shot.analysis.action)}</p>`:''}</article>`).join('')}</div>`:''}`;
  if(sameVideo&&oldTime){const player=el('#drama-player');player?.addEventListener('loadedmetadata',()=>{player.currentTime=oldTime;if(wasPlaying)player.play().catch(()=>{});},{once:true});}
}
function settingValue(){
  return {
    backend:el('#drama-backend').value,
    model:el('#agy-model').value.trim(),
    https_proxy:el('#agy-proxy').value.trim(),
    timeout_s:Number(el('#agy-timeout').value),
    rea_vlm_base_url:el('#rea-vlm-base-url')?.value.trim()||'http://127.0.0.1:3425/v1',
    rea_vlm_api_key:el('#rea-vlm-api-key')?.value.trim()||'magpie',
    rea_vlm_model:el('#rea-vlm-model')?.value.trim()||'auto/glm-5.3-flash',
  };
}
async function settingsRequest(options={}){
  const response=await fetch('/api/drama-settings',{headers:{'Content-Type':'application/json'},...options});const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'请检查模型、代理地址和超时时间。');return data;
}
function updateBackend(){
  const b=el('#drama-backend').value;
  el('#rea-fields').hidden=b!=='rea_video'&&b!=='rea_toon';
  el('#agy-fields').hidden=b!=='agy';
  el('#compatible-info').hidden=b!=='compatible';
}
el('#drama-backend').addEventListener('change',updateBackend);
el('#save-drama-settings').addEventListener('click',async event=>{event.target.disabled=true;try{await settingsRequest({method:'PUT',body:JSON.stringify(settingValue())});message('分析设置已保存，下一条开始处理的短剧生效。');}catch(error){message(error.message,true);}finally{event.target.disabled=false;}});
el('#drama-import').addEventListener('submit',async event=>{
  event.preventDefault();const button=el('#import-drama');button.disabled=true;message('正在检查视频并加入队列…');
  try{await settingsRequest({method:'PUT',body:JSON.stringify(settingValue())});const item=await request('',{method:'POST',body:JSON.stringify({path:el('#drama-path').value.trim()})});selectedDrama=item.id;detailSignature='';await refresh();message(`已加入 ${item.imported?.length||1} 条短剧，将按镜头缓存分析结果。${item.errors?.length?'有 '+item.errors.length+' 个文件导入失败：'+item.errors.map(e=>e.path+'：'+e.message).join('；'):''}`,Boolean(item.errors?.length));}
  catch(error){message(error.message,true);}finally{button.disabled=false;}
});
el('#drama-list').addEventListener('click',async event=>{
  const button=event.target.closest('[data-drama]');if(!button)return;
  selectedDrama=button.dataset.drama;detailSignature='';
  try{
    await refresh();
    if(window.innerWidth <= 700){
      el('#drama-detail')?.scrollIntoView({behavior:'smooth',block:'start'});
    }
  }catch(error){message(error.message,true);}
});
el('#drama-detail').addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
    if(button.dataset.time){if(selectedRecord.source_available===false){message('请先重新关联原视频，再定位播放。',true);return;}const player=el('#drama-player');player.currentTime=Number(button.dataset.time);player.play().catch(()=>{});player.scrollIntoView({block:'nearest'});return;}
    if(button.dataset.shotDocument){
      const sid=button.dataset.shotDocument;
      const num=parseInt(sid.replace(/\D/g,'')||'1',10);
      const candidates=[`shots/${sid}.md`,`shots/${String(num).padStart(3,'0')}.md`,`shots/${String(num).padStart(2,'0')}.md`,'README.md'];
      let opened=false;
      for(const path of candidates){
        try{await openDocumentWorkspace(selectedDrama,path);opened=true;break;}catch(_){}
      }
      if(!opened)message('镜头拆解文档暂不可用',true);
      return;
    }
    if(button.id==='open-drama-docs'){
      try{await openDocumentWorkspace(selectedDrama,'README.md');}
      catch(_){await openDocumentWorkspace(selectedDrama,'剧本.md');}
      return;
    }
    if(button.id==='relink-drama'){const dialog=el('#relink-drama-dialog');dialog.dataset.drama=selectedDrama;el('#relink-drama-path').value=selectedRecord.path||'';el('#relink-drama-error').textContent='';dialog.showModal();el('#relink-drama-path').focus();return;}
    if(button.id==='delete-drama'){el('#delete-drama-title').textContent=selectedRecord.title;el('#delete-drama-error').textContent='';el('#delete-drama-dialog').dataset.drama=selectedDrama;el('#delete-drama-dialog').showModal();return;}
    if(['retry-drama','regenerate-drama'].includes(button.id)){
      button.disabled=true;await settingsRequest({method:'PUT',body:JSON.stringify(settingValue())});await request(`/${selectedDrama}/retry${button.id==='regenerate-drama'?'?regenerate=true':''}`,{method:'POST'});detailSignature='';await refresh();message(button.id==='regenerate-drama'?'已排队重新分析，人工正文保留，新稿可比较采用。':'已排队续跑，已完成的镜头和文字稿将复用。');
    }
  }catch(error){button.disabled=false;message(error.message,true);}
});
el('#cancel-relink-drama').addEventListener('click',()=>el('#relink-drama-dialog').close());
el('#relink-drama-form').addEventListener('submit',async event=>{
  event.preventDefault();const dialog=el('#relink-drama-dialog'),button=el('#confirm-relink-drama');button.disabled=true;el('#relink-drama-error').textContent='正在核对视频内容…';
  try{await request(`/${dialog.dataset.drama}/source`,{method:'PUT',body:JSON.stringify({path:el('#relink-drama-path').value.trim()})});dialog.close();detailSignature='';await refresh();message('原视频已重新关联，拆解、文字稿和人工编辑均已保留，无需重新分析。');}
  catch(error){el('#relink-drama-error').textContent=error.message;}finally{button.disabled=false;}
});
el('#cancel-delete-drama').addEventListener('click',()=>el('#delete-drama-dialog').close());
el('#confirm-delete-drama').addEventListener('click',async event=>{
  const dialog=el('#delete-drama-dialog'),id=dialog.dataset.drama;event.target.disabled=true;
  try{await request('/'+id,{method:'DELETE'});dialog.close();if(selectedDrama===id){selectedDrama=null;selectedRecord=null;detailSignature='';el('#drama-detail').innerHTML='<h2>短剧已删除</h2><p>原视频保留，可以选择其他短剧或重新导入。</p>';}await refresh();message('拆解资料已删除，原视频保留。');}
  catch(error){el('#delete-drama-error').textContent=error.message;}finally{event.target.disabled=false;}
});
settingsRequest().then(settings=>{
  el('#drama-backend').value=settings.backend||'rea_video';
  el('#agy-model').value=settings.model||'gemini-3.8-flash-high';
  el('#agy-proxy').value=settings.https_proxy||'http://127.0.0.1:7887';
  el('#agy-timeout').value=settings.timeout_s||600;
  if(el('#rea-vlm-base-url'))el('#rea-vlm-base-url').value=settings.rea_vlm_base_url||'http://127.0.0.1:3425/v1';
  if(el('#rea-vlm-api-key'))el('#rea-vlm-api-key').value=settings.rea_vlm_api_key||'magpie';
  if(el('#rea-vlm-model'))el('#rea-vlm-model').value=settings.rea_vlm_model||'auto/glm-5.3-flash';
  updateBackend();
}).catch(error=>message(error.message,true));
refresh().catch(error=>message(error.message,true));
setInterval(async()=>{if(polling||document.hidden||document.body.classList.contains('editor-open'))return;polling=true;try{await refresh();}catch(error){message(error.message,true);}finally{polling=false;}},4000);
