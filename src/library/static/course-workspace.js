const documentWorkspaceConfig=window.documentWorkspaceConfig||{apiBase:"/api/courses",kind:"course"};
/* One persistent Markdown document behind all three modes. */
let documentEditor=null, documentSession=null, workspaceCourse=null, workspaceDocument=null;
let documentMode='reading', documentLoadToken=0, comparison=null;
const docNode=selector=>document.querySelector(selector);
const docRequest=(path,options)=>request(`/${workspaceCourse}${path}`,options);
const draftKey=(course,path)=>`videoshelf:draft:${course}:${path}`;

function documentStatus(kind,text){
  if(docNode('#document-status').textContent!==text)docNode('#document-status').textContent=text;
  docNode('#document-status').dataset.state=kind;
  docNode('#compare-server').hidden=kind!=='conflict';
}
function createDocumentSession(doc){
  return new CourseEditor.DocumentSession({key:draftKey(workspaceCourse,doc.path),markdown:doc.markdown,revision:doc.revision,
    save:async(markdown,revision)=>{
      const result=await docRequest(`/document?path=${encodeURIComponent(doc.path)}`,{method:'PUT',body:JSON.stringify({markdown,revision})});
      workspaceDocument={...workspaceDocument,...result};docNode('#document-title').textContent=result.title;docNode('#compare-draft').hidden=!result.has_new_draft;return result;
    },onStatus:documentStatus});
}
async function saveOpenDocument(){if(documentSession)await documentSession.flush();}

async function openDocumentWorkspace(courseId,path){
  const token=++documentLoadToken;
  try {
    await saveOpenDocument();
    const [doc,list]=await Promise.all([request(`/${courseId}/document?path=${encodeURIComponent(path)}`),request(`/${courseId}/documents`)]);
    if(token!==documentLoadToken)return;
    documentSession?.dispose();documentEditor?.destroy();
    workspaceCourse=courseId;workspaceDocument=doc;
    docNode('#document-workspace').hidden=false;document.body.classList.add('editor-open');
    docNode('#document-title').textContent=doc.title;docNode('#document-path').textContent=doc.path;
    const documentButton=item=>`<button data-document="${escapeText(item.path)}" aria-current="${item.path===path?'page':'false'}" title="${escapeText(item.path)}"><span>${escapeText(item.title)}</span></button>`;
    const groups=[[documentWorkspaceConfig.kind==='drama'?'拆解文档':'课程目录',list.filter(d=>!d.path.includes('/'))],['逐镜拆解',list.filter(d=>d.path.startsWith('shots/'))],['单课教程',list.filter(d=>d.path.startsWith('lessons/'))],['专题教程',list.filter(d=>d.path.startsWith('topics/'))],['提示词库',list.filter(d=>d.path.startsWith('prompts/'))]];
    const raw=list.filter(d=>d.path.startsWith('sources/')||d.path.startsWith('transcripts/'));
    docNode('#document-nav').innerHTML=groups.filter(([,items])=>items.length).map(([title,items])=>`<h3>${title}</h3>${items.map(documentButton).join('')}`).join('')+(raw.length?`<details ${/^(sources|transcripts)\//.test(path)?'open':''}><summary>原始资料</summary>${raw.map(documentButton).join('')}</details>`:'');
    docNode('#document-picker').innerHTML=list.map(item=>`<option value="${escapeText(item.path)}">${escapeText(item.title)}</option>`).join('');docNode('#document-picker').value=path;
    docNode('#document-host').innerHTML='';
    documentSession=createDocumentSession(doc);
    documentEditor=CourseEditor.createMarkdownEditor({parent:docNode('#document-host'),text:documentSession.text,
      context:{courseId,path,apiBase:`${documentWorkspaceConfig.apiBase}/${courseId}`,onNavigate:target=>openDocumentWorkspace(courseId,target)},
      onChange:text=>documentSession.changed(text),onSave:()=>saveOpenDocument().catch(error=>message(error.message,true))});
    setDocumentMode(documentMode);
    docNode('#compare-draft').hidden=!doc.has_new_draft;
    docNode('#document-workspace').classList.remove('with-source');
    docNode('#document-video').pause();docNode('#document-video').removeAttribute('src');
    docNode('#source-list').innerHTML='';docNode('#review-items').innerHTML='';docNode('#review-toggle').hidden=true;docNode('#review-section').hidden=true;
    const lesson=(documentWorkspaceConfig.kind==='drama'?/^shots\/(\d+)\.md$/:/^lessons\/(\d+)\.md$/).exec(path);
    docNode('#source-toggle').hidden=!lesson;
    if(lesson) {
      request(`/${courseId}/${documentWorkspaceConfig.kind==='drama'?'shots':'lessons'}/${lesson[1]}/diagnostics`).then(data=>{
        if(token!==documentLoadToken)return;
        docNode('#source-precision').textContent=data.precision;
        docNode('#document-video').dataset.source=documentWorkspaceConfig.kind==='drama'?`/api/dramas/${courseId}/source`:`/api/courses/${courseId}/lessons/${lesson[1]}/source`;
        docNode('#source-list').innerHTML=data.sources.map(s=>`<details><summary>${escapeText(s.title)}</summary><button data-source-time="${s.start}">${s.start.toFixed(1)}–${s.end.toFixed(1)} 秒</button><p>${escapeText(s.evidence)}</p></details>`).join('');
        docNode('#review-items').innerHTML=data.reviews.map(r=>`<li><strong>${escapeText(r.title)}</strong><p>${escapeText(r.message)}</p><button data-source-time="${r.start}">定位视频</button></li>`).join('');
        docNode('#review-section').hidden=!data.reviews.length;docNode('#review-toggle').hidden=!data.reviews.length;docNode('#review-toggle').textContent=`核对事项（${data.reviews.length}）`;
        docNode('#recognition-details').textContent=JSON.stringify(data.raw,null,2);
      }).catch(error=>{if(token===documentLoadToken)docNode('#source-list').textContent='来源暂不可用：'+error.message;});
    }
  } catch(error){message(error.message,true);documentStatus(error.status===409?'conflict':'error',error.message);}
}
function setDocumentMode(mode){
  documentMode=mode;documentEditor?.setMode(mode);
  for(const button of document.querySelectorAll('[data-document-mode]'))button.setAttribute('aria-pressed',String(button.dataset.documentMode===mode));
  docNode('#document-format').hidden=mode==='reading';
}
async function closeDocumentWorkspace(){
  await saveOpenDocument();++documentLoadToken;
  documentSession?.dispose();documentSession=null;documentEditor?.destroy();documentEditor=null;
  docNode('#document-video').pause();docNode('#document-video').removeAttribute('src');
  docNode('#document-workspace').hidden=true;document.body.classList.remove('editor-open');
}
function showSource(){
  docNode('#document-workspace').classList.add('with-source');
  const video=docNode('#document-video');if(!video.getAttribute('src') && video.dataset.source)video.src=video.dataset.source;
}
async function compareDocument(kind){
  if(kind==='draft')await saveOpenDocument();
  const right=await docRequest(`/document?path=${encodeURIComponent(workspaceDocument.path)}${kind==='draft'?'&draft=true':''}`);
  comparison={kind,right};
  docNode('#comparison-title').textContent=kind==='draft'?'比较 AI 新稿':'比较保存冲突';
  docNode('#comparison-description').textContent=kind==='draft'?'AI 新稿尚未覆盖你的正文。采用后可在编辑器撤销。':'服务器上已有另一版本。当前草稿仍保留，请比较后决定。';
  docNode('#comparison-left').replaceChildren(CourseEditor.renderMarkdown(documentSession.text,{courseId:workspaceCourse,path:workspaceDocument.path,apiBase:`${documentWorkspaceConfig.apiBase}/${workspaceCourse}`}));
  docNode('#comparison-right').replaceChildren(CourseEditor.renderMarkdown(right.markdown,{courseId:workspaceCourse,path:workspaceDocument.path,apiBase:`${documentWorkspaceConfig.apiBase}/${workspaceCourse}`}));
  docNode('#comparison-right-source').textContent=right.markdown;
  docNode('#comparison-ours').hidden=kind!=='conflict';
  docNode('#comparison-adopt').textContent=kind==='draft'?'采用 AI 新稿':'采用服务器版本';
  docNode('#document-comparison').showModal();docNode('#comparison-close').focus();
}
function acceptDocument(doc){
  documentSession.dispose();localStorage.removeItem(draftKey(workspaceCourse,doc.path));
  workspaceDocument=doc;docNode('#document-title').textContent=doc.title;documentSession=createDocumentSession(doc);
  documentEditor.setValue(doc.markdown);docNode('#compare-draft').hidden=doc.has_new_draft===false;
}

docNode('#document-nav').addEventListener('click',event=>{
  const item=event.target.closest('[data-document]');if(item)openDocumentWorkspace(workspaceCourse,item.dataset.document);
});
docNode('#document-tools').addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try {
    if(button.dataset.documentMode){setDocumentMode(button.dataset.documentMode);return;}
    if(button.id==='document-save')await saveOpenDocument();
    if(button.id==='document-close')await closeDocumentWorkspace();
    if(button.id==='document-undo')documentEditor.undo();
    if(button.id==='document-redo')documentEditor.redo();
    if(button.dataset.insert==='bold')documentEditor.insert('**','**');
    if(button.dataset.insert==='heading')documentEditor.insert('\n## ');
    if(button.dataset.insert==='list')documentEditor.insert('\n- ');
    if(button.dataset.insert==='code')documentEditor.insert('\n```text\n','\n```\n');
    if(button.dataset.insert==='table')documentEditor.insert('\n| 名称 | 内容 |\n| --- | --- |\n|  |  |\n');
    if(button.id==='compare-draft')await compareDocument('draft');
    if(button.id==='compare-server')await compareDocument('conflict');
    if(button.id==='source-toggle')showSource();
    if(button.id==='review-toggle'){showSource();docNode('#review-section').open=true;}
    if(button.id==='document-download'){
      await saveOpenDocument();const link=document.createElement('a');link.href=`${documentWorkspaceConfig.apiBase}/${workspaceCourse}/download`;link.download=documentWorkspaceConfig.kind==='drama'?'drama-breakdown.zip':'course-tutorials.zip';link.click();
    }
    if(button.id==='download-draft'){
      const url=URL.createObjectURL(new Blob([documentSession.text],{type:'text/markdown;charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download=workspaceDocument.path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  }catch(error){documentStatus(error.status===409?'conflict':'error',error.message);}
});
docNode('#document-source').addEventListener('click',event=>{
  const button=event.target.closest('[data-source-time]');
  if(event.target.id==='source-close'){docNode('#document-workspace').classList.remove('with-source');docNode('#document-video').pause();docNode('#source-toggle').focus();}
  if(button){showSource();const video=docNode('#document-video');video.currentTime=Number(button.dataset.sourceTime);video.play().catch(error=>message(error.message,true));}
});
docNode('#document-comparison').addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try {
    if(button.id==='comparison-close'){docNode('#document-comparison').close();return;}
    if(button.id==='comparison-ours'){
      documentSession.useComparedRevision(comparison.right.revision);await saveOpenDocument();
    }
    if(button.id==='comparison-adopt'){
      const doc=comparison.kind==='draft'?await docRequest(`/document/adopt?path=${encodeURIComponent(workspaceDocument.path)}`,{method:'POST',body:JSON.stringify({revision:documentSession.revision,draft_revision:comparison.right.revision})}):comparison.right;
      acceptDocument(doc);
    }
    docNode('#document-comparison').close();
  }catch(error){docNode('#comparison-description').textContent=error.message;}
});
window.addEventListener('beforeunload',event=>{if(documentSession?.dirty){documentSession.backup();event.preventDefault();event.returnValue='';}});

docNode('#document-picker').addEventListener('change',event=>openDocumentWorkspace(workspaceCourse,event.target.value));
window.addEventListener('keydown',event=>{if(!event.defaultPrevented && documentSession && (event.metaKey||event.ctrlKey) && event.key.toLowerCase()==='s'){event.preventDefault();saveOpenDocument().catch(error=>documentStatus(error.status===409?'conflict':'error',error.message));}});
