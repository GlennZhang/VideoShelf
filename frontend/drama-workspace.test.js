import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {runInContext} from 'node:vm';

const staticPath=new URL('../src/library/static/',import.meta.url);
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
function page(){
  const dom=new JSDOM(readFileSync(new URL('dramas.html',staticPath),'utf8'),{url:'http://127.0.0.1/',runScripts:'outside-only',pretendToBeVisual:true});
  const win=dom.window,calls=[];
  win.setInterval=()=>0;
  win.HTMLMediaElement.prototype.pause=()=>{};win.HTMLMediaElement.prototype.play=()=>Promise.resolve();
  win.HTMLDialogElement.prototype.showModal=function(){this.open=true;};win.HTMLDialogElement.prototype.close=function(){this.open=false;};
  win.Range.prototype.getClientRects=()=>[];win.Range.prototype.getBoundingClientRect=()=>new win.DOMRect();
  win.HTMLElement.prototype.scrollIntoView=()=>{};
  const item={id:'a'.repeat(32),title:'测试短剧',status:'done',stage:'完成',progress:1,duration:2,error:'',documents_ready:true,tasks:[],shots:[{id:'001',take:1,part:1,start:0,end:2,subdivision:false,frames:[{time:0,file:'cache/frames/001.jpg'}],analysis:{title:'抬头',action:'人物抬头'}}]};
  const doc={path:'shots/001.md',title:'镜头拆解',markdown:'# 镜头拆解\n\n![截图](../assets/frame.jpg)',revision:'first',has_new_draft:false};
  win.fetch=async(url,options={})=>{
    calls.push({url,options});let result;
    if(url==='/api/drama-settings')result={backend:'agy',model:'gemini-test',https_proxy:'http://127.0.0.1:7887',timeout_s:600};
    else if(url==='/api/dramas')result=[item];
    else if(url.endsWith('/documents'))result=[{path:doc.path,title:doc.title}];
    else if(url.includes('/document?'))result=doc;
    else if(url.endsWith('/diagnostics'))result={precision:'块级时间',sources:[{title:'镜头001',start:0,end:2,evidence:'原始帧'}],reviews:[],raw:{}};
    else if(options.method==='DELETE')result={deleted:true};
    else result=item;
    return {ok:true,headers:{get:()=> 'application/json'},json:async()=>result};
  };
  for(const filename of ['course-editor.bundle.js','dramas.js','course-workspace.js'])runInContext(readFileSync(new URL(filename,staticPath),'utf8'),dom.getInternalVMContext());
  return {dom,win,calls,item};
}

test('drama workspace reuses reading/live/source modes, routes assets, source and saves correctly',async()=>{
  const {dom,win,calls,item}=page();
  try{
    await tick();await win.openDocumentWorkspace(item.id,'shots/001.md');await tick();
    assert.equal(win.document.querySelector('#document-workspace').hidden,false);
    assert.match(win.document.querySelector('#document-host img').src,new RegExp('/api/dramas/'+item.id+'/document-assets/frame.jpg$'));
    win.document.querySelector('[data-document-mode="source"]').click();
    assert.equal(win.document.querySelector('.cm-content').getAttribute('contenteditable'),'true');
    win.document.querySelector('[data-insert="heading"]').click();
    win.document.querySelector('#document-save').click();await tick();
    assert.ok(calls.some(c=>c.options.method==='PUT'&&c.url.startsWith('/api/dramas/'+item.id+'/document?')));
    win.document.querySelector('[data-document-mode="live"]').click();
    assert.equal(win.document.querySelector('[data-document-mode="live"]').getAttribute('aria-pressed'),'true');
    win.document.querySelector('#source-toggle').click();
    assert.equal(win.document.querySelector('#document-video').getAttribute('src'),'/api/dramas/'+item.id+'/source');
    assert.ok(calls.some(c=>c.url.endsWith('/shots/001/diagnostics')));
  }finally{dom.window.close();}
});

test('delete dialog can be cancelled without deleting the selected drama',async()=>{
  const {dom,win,calls}=page();
  try{
    await tick();win.document.querySelector('[data-drama]').click();await tick();
    win.document.querySelector('#delete-drama').click();await tick();
    assert.equal(win.document.querySelector('#delete-drama-dialog').open,true);
    win.document.querySelector('#cancel-delete-drama').click();
    assert.equal(win.document.querySelector('#delete-drama-dialog').open,false);
    assert.ok(!calls.some(c=>c.options.method==='DELETE'));
  }finally{dom.window.close();}
});

test('relink dialog submits new path and refreshes source without requesting analysis',async()=>{
  const {dom,win,calls,item}=page();
  try{
    await tick();win.document.querySelector('[data-drama]').click();await tick();
    win.document.querySelector('#relink-drama').click();
    assert.equal(win.document.querySelector('#relink-drama-dialog').open,true);
    win.document.querySelector('#relink-drama-path').value='/new/place/drama.mp4';
    win.document.querySelector('#relink-drama-form').dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await tick();
    const submission=calls.find(c=>c.options.method==='PUT'&&c.url===`/api/dramas/${item.id}/source`);
    assert.equal(JSON.parse(submission.options.body).path,'/new/place/drama.mp4');
    assert.equal(win.document.querySelector('#relink-drama-dialog').open,false);
    assert.match(win.document.querySelector('#drama-message').textContent,/无需重新分析/);
    assert.ok(!calls.some(c=>c.url.includes('/retry')));
  }finally{dom.window.close();}
});
