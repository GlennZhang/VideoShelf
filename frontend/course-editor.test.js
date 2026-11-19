import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><body><div id="root"></div></body>',{pretendToBeVisual:true,url:'http://127.0.0.1/'});
for(const name of ['window','document','Window','Node','HTMLElement','MutationObserver','DOMRect'])globalThis[name]=name==='window'?dom.window:name==='document'?dom.window.document:dom.window[name];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame=dom.window.requestAnimationFrame.bind(dom.window);globalThis.cancelAnimationFrame=dom.window.cancelAnimationFrame.bind(dom.window);
dom.window.Range.prototype.getClientRects=()=>[];dom.window.Range.prototype.getBoundingClientRect=()=>new dom.window.DOMRect();
const {renderMarkdown,createMarkdownEditor}=await import('./course-editor.js');

test('reading renders headings, lists, tables, code and local images',()=>{
  const raw='# 标题\n\n- 要点\n\n| 名称 | 参数 |\n| --- | --- |\n| 强度 | 0.8 |\n\n```text\n提示词\n```\n\n![示例](../assets/sample.jpg)';
  const rendered=renderMarkdown(raw,{courseId:'abc',path:'lessons/001.md'});
  assert.equal(rendered.querySelector('h1').textContent,'标题');assert.equal(rendered.querySelector('li').textContent,'要点');assert.match(rendered.querySelector('table').textContent,/0.8/);assert.match(rendered.querySelector('code').textContent,/提示词/);assert.equal(rendered.querySelector('img').getAttribute('src'),'/api/courses/abc/document-assets/sample.jpg');
});
test('Markdown rendering removes executable HTML and unsafe link schemes',()=>{
  const rendered=renderMarkdown('<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n[危险](javascript:alert(1))');
  assert.equal(rendered.querySelector('script'),null);assert.equal(rendered.querySelector('img').hasAttribute('onerror'),false);assert.ok(!rendered.innerHTML.includes('href="javascript:'));
});
test('local document links navigate without modifying Markdown source',()=>{
  let destination;const raw='[提示词库](../prompts/README.md)';
  const rendered=renderMarkdown(raw,{path:'lessons/001.md',onNavigate:p=>destination=p});rendered.querySelector('a').click();assert.equal(destination,'prompts/README.md');assert.equal(raw,'[提示词库](../prompts/README.md)');
});
test('mode switches preserve content, selection and undo history',()=>{
  const parent=document.querySelector('#root'),raw='# 标题\n\n中文正文\n\n![图](../assets/a.jpg)';
  const changes=[];const editor=createMarkdownEditor({parent,text:raw,context:{path:'lessons/001.md',courseId:'abc'},onChange:value=>changes.push(value),onSave:()=>{}});
  assert.equal(parent.querySelector('.cm-content').getAttribute('contenteditable'),'false');assert.ok(parent.querySelector('.reading-block'));
  editor.setMode('live');assert.equal(parent.querySelector('.cm-content').getAttribute('contenteditable'),'true');editor.setValue(raw+'\n\n我的学习笔记');editor.select(8);editor.setMode('source');assert.equal(editor.selection().anchor,8);assert.equal(parent.querySelector('.md-block'),null);editor.setMode('reading');editor.setMode('live');assert.match(editor.value(),/我的学习笔记/);assert.equal(editor.selection().anchor,8);editor.undo();assert.equal(editor.value(),raw);assert.ok(changes.length);editor.destroy();
});

test('Chinese composition keeps the editable DOM stable and commits after composition',async()=>{
  const parent=document.querySelector('#root'),changes=[];
  const editor=createMarkdownEditor({parent,text:'中文原稿',context:{path:'lessons/001.md'},onChange:value=>changes.push(value),onSave:()=>{}});
  editor.setMode('live');const content=parent.querySelector('.cm-content');
  content.dispatchEvent(new dom.window.CompositionEvent('compositionstart',{bubbles:true,data:''}));
  editor.setValue('中文输入完成');
  assert.equal(parent.querySelector('.cm-content'),content);
  assert.equal(changes.length,0);
  content.dispatchEvent(new dom.window.CompositionEvent('compositionend',{bubbles:true,data:'中文输入完成'}));
  await Promise.resolve();assert.equal(changes.at(-1),'中文输入完成');editor.destroy();
});


test('drama document images and links use their own API without changing course defaults',()=>{
  let destination;
  const rendered=renderMarkdown('![镜头](../assets/frame.jpg)\n\n[角色卡](../characters.md)',{courseId:'drama1',apiBase:'/api/dramas/drama1',path:'shots/001.md',onNavigate:p=>destination=p});
  assert.equal(rendered.querySelector('img').getAttribute('src'),'/api/dramas/drama1/document-assets/frame.jpg');
  rendered.querySelector('a').click();assert.equal(destination,'characters.md');
});
