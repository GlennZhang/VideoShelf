"use strict";
const el = (selector) => document.querySelector(selector);
const escapeText = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusText = {imported:'待处理',pending:'待处理',queued:'排队中',running:'处理中',done:'已完成',failed:'失败',retry:'等待重试'};
let selectedCourse = null;
let selectedLesson = null;
let polling = false;
async function request(path, options = {}) {
  const response = await fetch('/api/courses' + path, {headers:{'Content-Type':'application/json'}, ...options});
  if (!response.ok) { const data = await response.json(); const error=new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入');error.status=response.status;throw error; }
  return response.json();
}
function message(text, error=false) { el('#course-message').textContent=text;el('#course-message').className=error?'course-error':''; }
async function refresh() {
  const courses = await request('');
  const listHtml = courses.length ? courses.map(c => `<button class="course-choice" data-course="${c.id}" aria-pressed="${c.id===selectedCourse}">${escapeText(c.title)}<br>${c.lessons.length} 课 · ${statusText[c.status] || escapeText(c.status)}</button>`).join('') : '<p>还没有课程，先导入一个本机课程文件夹。</p>';
  if (el('#course-list').innerHTML !== listHtml) el('#course-list').innerHTML = listHtml;
  if (selectedCourse) await showCourse(selectedCourse);
}
async function showCourse(id) {
  const course = await request('/' + id);
  if (selectedCourse !== id) return;
  const tasks = course.tasks || [];
  const active = tasks.find(t => ['running','queued','retry'].includes(t.status)) || (course.status === 'running' ? {status:'running'} : null);
  const task = active || tasks[0];
  const detailHtml = `<h2>${escapeText(course.title)}</h2><p>${course.lessons.filter(l=>l.status==='done').length} / ${course.lessons.length} 课已完成 · ${statusText[active?.status || course.status]}</p>${task ? `<p>${escapeText(task.stage || statusText[task.status])} · ${task.progress === undefined ? '正在处理' : Math.round(task.progress*100)+'%'}</p>${task.error && !active ? `<p class="course-error">${escapeText(task.error)}</p>`:''}`:''}<div class="course-actions"><button id="open-course-docs">课程文档</button><button id="retry-course" ${active?'disabled':''}>继续整理 / 重试</button><button id="regenerate-course" ${active?'disabled':''} title="复用转写生成 AI 新稿，保留人工正文">重新整理 AI 新稿</button>${!active?`<a href="/api/courses/${id}/download">下载 Markdown 与图片 ZIP</a>`:''}<button id="delete-course" class="course-error" data-title="${escapeText(course.title)}" ${(course.status==='running' || active?.status==='running')?'disabled title="课程处理完成后才能删除"':''}>删除课程</button></div><table><thead><tr><th>课程</th><th>状态</th><th>教程</th></tr></thead><tbody>${course.lessons.map(l=>`<tr><td>${escapeText(l.title)}${l.error?`<p class="course-error">${escapeText(l.error)}</p>`:''}</td><td>${statusText[l.status]}</td><td>${l.status==='done'?`<button data-lesson="${l.id}">查看教程</button>`:'—'}</td></tr>`).join('')}</tbody></table>`;
  if (el('#course-detail').innerHTML !== detailHtml) el('#course-detail').innerHTML = detailHtml;
}
async function showLesson(id) {
  await openDocumentWorkspace(selectedCourse,`lessons/${id}.md`);
}
el('#course-import').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;message('正在导入课程…');
  try { const course=await request('',{method:'POST',body:JSON.stringify({path:el('#course-path').value.trim()})});selectedCourse=course.id;selectedLesson=null;el('#lesson-preview').hidden=true;await refresh();message('课程已加入后台队列，可查看逐课进度。'); }
  catch(error){message(error.message,true);}finally{button.disabled=false;}
});
el('#course-list').addEventListener('click',async event=>{
  const button=event.target.closest('[data-course]');if(!button)return;selectedCourse=button.dataset.course;selectedLesson=null;el('#lesson-preview').hidden=true;
  try{await refresh();}catch(error){message(error.message,true);}
});
el('#course-detail').addEventListener('click',async event=>{
  try {
    const lesson=event.target.closest('[data-lesson]');if(lesson){selectedLesson=lesson.dataset.lesson;await showLesson(selectedLesson);}
    if(event.target.id==='open-course-docs')await openDocumentWorkspace(selectedCourse,'README.md');
    if(event.target.id==='delete-course'){
      const id=selectedCourse;
      if(!confirm(`删除课程“${event.target.dataset.title}”？将删除课程记录、已编辑的 Markdown 正文、生成的教程、截图和缓存，并移除待处理任务。原视频和已下载的文件不会删除。此操作不可撤销。`))return;
      event.target.disabled=true;
      try { await request(`/${id}`,{method:'DELETE'}); }
      catch(error){event.target.disabled=false;throw error;}
      if(selectedCourse===id){selectedCourse=null;selectedLesson=null;el('#lesson-preview').hidden=true;el('#lesson-preview').innerHTML='';el('#course-detail').innerHTML='<h2>选择一套课程</h2><p>课程已删除，可以选择其他课程或重新导入。</p>';}
      await refresh();message('课程及生成资料已删除，原视频保留。');
    }
    if(event.target.id==='regenerate-course'){event.target.disabled=true;await request(`/${selectedCourse}/regenerate`,{method:'POST'});message('已开始重新整理。现有正文会保留，新稿完成后可比较采用。');await showCourse(selectedCourse);}
    if(event.target.id==='retry-course'){event.target.disabled=true;await request(`/${selectedCourse}/retry`,{method:'POST'});message('已加入队列，将复用已有缓存。');await showCourse(selectedCourse);}
  }catch(error){message(error.message,true);}
});
el('#lesson-preview').addEventListener('click', event=>{
  const button=event.target.closest('[data-time]');if(!button)return;
  const player=el('#course-player');player.currentTime=Number(button.dataset.time);player.play().catch(error=>message('请在播放器中开始播放：'+error.message,true));player.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});
});
refresh().catch(error=>message(error.message,true));
setInterval(async()=>{if(polling||document.hidden)return;polling=true;try{await refresh();}catch(error){message(error.message,true);}finally{polling=false;}},4000);
