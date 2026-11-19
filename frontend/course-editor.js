import {EditorView,basicSetup} from 'codemirror';
import {EditorState,StateField,StateEffect,Compartment,Prec} from '@codemirror/state';
import {Decoration,WidgetType,keymap} from '@codemirror/view';
import {markdown} from '@codemirror/lang-markdown';
import {undo,redo} from '@codemirror/commands';
import {marked} from 'marked';
import DOMPurify from 'dompurify';
export {DocumentSession} from './document-session.js';

const modeEffect=StateEffect.define();
const composingEffect=StateEffect.define();
const modeField=StateField.define({create:()=> 'reading',update:(value,tr)=>tr.effects.find(e=>e.is(modeEffect))?.value || value});
const composingField=StateField.define({create:()=>false,update:(value,tr)=>tr.effects.find(e=>e.is(composingEffect))?.value ?? value});

export function renderMarkdown(text, {courseId,path,onNavigate,apiBase}={}) {
  const element=document.createElement('div');element.className='md-rendered';
  element.innerHTML=DOMPurify.sanitize(marked.parse(text,{gfm:true}),{USE_PROFILES:{html:true},FORBID_TAGS:['style','form','input','iframe','video','audio'],FORBID_ATTR:['style']});
  const base=new URL(path || 'README.md','https://document.local/');
  for(const image of element.querySelectorAll('img')) {
    if(!URL.canParse(image.getAttribute('src') || '',base)){image.removeAttribute('src');continue;}
    const url=new URL(image.getAttribute('src') || '',base);
    if(url.origin==='https://document.local' && url.pathname.startsWith('/assets/') && courseId) {
      image.src=`${apiBase || `/api/courses/${courseId}`}/document-assets/${url.pathname.slice(8).split('/').map(encodeURIComponent).join('/')}`;
    }
    image.loading='lazy';
  }
  for(const link of element.querySelectorAll('a')) {
    const href=link.getAttribute('href') || '';
    if(href.startsWith('#'))continue;
    if(!URL.canParse(href,base)){link.removeAttribute('href');continue;}
    const url=new URL(href,base);
    if(url.origin==='https://document.local' && url.pathname.endsWith('.md')) {
      const target=decodeURIComponent(url.pathname.slice(1));
      link.href='#';link.dataset.document=target;
      link.addEventListener('click',event=>{event.preventDefault();onNavigate?.(target);});
    } else if(url.protocol==='https:' || url.protocol==='http:') {
      link.target='_blank';link.rel='noopener noreferrer';
    }
  }
  return element;
}

class MarkdownBlock extends WidgetType {
  constructor(raw,from,to,context){super();Object.assign(this,{raw,from,to,context});}
  eq(other){return this.raw===other.raw && this.from===other.from && this.to===other.to && this.context===other.context;}
  updateDOM(element,view,previous){
    if(this.raw!==previous.raw || this.context!==previous.context)return false;
    element.dataset.from=this.from;element.dataset.to=this.to;return true;
  }
  toDOM(view){
    const element=renderMarkdown(this.raw,this.context);element.classList.add('md-block');
    element.dataset.from=this.from;element.dataset.to=this.to;
    for(const image of element.querySelectorAll('img'))image.addEventListener('load',()=>view.requestMeasure());
    element.addEventListener('mousedown',event=>{
      if(view.state.field(modeField)!=='live' || event.button!==0 || event.target.closest('a,button,summary'))return;
      event.preventDefault();
      const prefix=this.raw.match(/^(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)/)?.[0].length || 0;
      view.dispatch({selection:{anchor:Math.min(Number(element.dataset.from)+prefix,Number(element.dataset.to))},userEvent:'select.pointer'});view.focus();
    });
    return element;
  }
}

function decorations(state,context){
  const mode=state.field(modeField);
  if(mode!=='live')return Decoration.none;
  const text=state.doc.toString(),selection=state.selection;
  const ranges=[];let from=0;
  for(const token of marked.lexer(text,{gfm:true})) {
    const to=Math.min(from+token.raw.length,text.length);
    const selected=selection.ranges.some(r=>r.from<=to && r.to>=from);
    if(token.type!=='space' && to>from && (mode==='reading' || !selected)) {
      ranges.push(Decoration.replace({widget:new MarkdownBlock(token.raw,from,to,context),block:true}).range(from,to));
    }
    from=to;
  }
  for(let number=1;number<=state.doc.lines;number++){
    const line=state.doc.line(number);
    if(line.length===0 && (mode==='reading' || !selection.ranges.some(r=>r.from<=line.to && r.to>=line.from)))ranges.push(Decoration.line({class:'md-spacer'}).range(line.from));
  }
  return Decoration.set(ranges,true);
}

export function createMarkdownEditor({parent,text,context,onChange,onSave}) {
  const editable=new Compartment();
  const reader=document.createElement('div');reader.className='document-reading';
  const surface=document.createElement('div');surface.className='document-editing';surface.hidden=true;
  parent.replaceChildren(reader,surface);
  let view;
  function paintReading(){
    const article=document.createElement('article');article.className='document-reading-content';let from=0;
    for(const token of marked.lexer(view.state.doc.toString(),{gfm:true})){
      if(token.type!=='space'){
        const block=renderMarkdown(token.raw,context);block.classList.add('reading-block');block.dataset.from=from;article.append(block);
      }
      from+=token.raw.length;
    }
    if(!view.state.doc.length){const empty=document.createElement('p');empty.className='empty-document';empty.textContent='空文档。切换到实时编辑或源码模式开始书写。';article.append(empty);}
    reader.replaceChildren(article);
  }
  const rendered=StateField.define({
    create:state=>decorations(state,context),
    update:(value,tr)=>{
      if(tr.state.field(composingField))return value.map(tr.changes);
      return tr.docChanged || tr.selection || tr.effects.some(e=>e.is(modeEffect)||e.is(composingEffect)) ? decorations(tr.state,context) : value;
    },
    provide:field=>EditorView.decorations.from(field),
  });
  view=new EditorView({parent:surface,state:EditorState.create({doc:text,extensions:[
    basicSetup,markdown(),EditorView.lineWrapping,EditorView.contentAttributes.of({"aria-label":"Markdown 正文"}),modeField,composingField,rendered,
    editable.of([EditorState.readOnly.of(true),EditorView.editable.of(false)]),
    Prec.highest(keymap.of([{key:'Mod-s',run:()=>{onSave();return true;}}])),
    EditorView.updateListener.of(update=>{
      if(update.docChanged && update.state.field(modeField)==='reading')paintReading();
      if(update.docChanged && !update.view.compositionStarted)onChange(update.state.doc.toString());
      if(update.transactions.some(tr=>tr.effects.some(e=>e.is(composingEffect)&&e.value===false)))onChange(update.state.doc.toString());
    }),
    EditorView.domEventHandlers({
      compositionstart:(_,view)=>{view.dispatch({effects:composingEffect.of(true)});},
      compositionend:(_,view)=>{queueMicrotask(()=>{if(!view.destroyed)view.dispatch({effects:composingEffect.of(false)});});},
    }),
    EditorView.theme({
      '&':{height:'100%',fontSize:'16px',backgroundColor:'transparent'},
      '.cm-scroller':{overflow:'auto',fontFamily:'inherit'},
      '.cm-content':{padding:'24px 32px 80px',maxWidth:'900px',margin:'0 auto',minHeight:'100%',boxSizing:'border-box',width:'100%'},
      '.cm-line':{lineHeight:'1.8'},
      '&.cm-focused':{outline:'none'},
      '.cm-gutters':{backgroundColor:'transparent',border:'none'},
    }),
  ]})});
  parent.dataset.mode='reading';paintReading();
  return {
    value:()=>view.state.doc.toString(),
    setValue:text=>view.dispatch({changes:{from:0,to:view.state.doc.length,insert:text},userEvent:'input'}),
    setMode:mode=>{
      const previous=view.state.field(modeField);
      const visible=previous==='reading'?Array.from(reader.querySelectorAll('.reading-block')).find(e=>e.getBoundingClientRect().bottom>=reader.getBoundingClientRect().top+8):null;
      const anchor=previous==='reading'?Number(visible?.dataset.from || 0):view.lineBlockAtHeight(view.scrollDOM.scrollTop).from;
      parent.dataset.mode=mode;reader.hidden=mode!=='reading';surface.hidden=mode==='reading';
      view.dispatch({effects:[modeEffect.of(mode),editable.reconfigure([EditorState.readOnly.of(mode==='reading'),EditorView.editable.of(mode!=='reading')])]});
      if(mode==='reading'){
        paintReading();const blocks=Array.from(reader.querySelectorAll('.reading-block'));const block=blocks.filter(e=>Number(e.dataset.from)<=anchor).at(-1);
        if(block)reader.scrollTop=block.offsetTop-12;
      } else {view.requestMeasure();view.dispatch({effects:EditorView.scrollIntoView(anchor,{y:'start'})});view.focus();}
    },
    undo:()=>undo(view),redo:()=>redo(view),
    selection:()=>view.state.selection.main,
    select:(anchor,head=anchor)=>view.dispatch({selection:{anchor,head}}),
    insert:(before,after='')=>{const s=view.state.selection.main;view.dispatch({changes:{from:s.from,to:s.to,insert:before+view.state.sliceDoc(s.from,s.to)+after},selection:{anchor:s.from+before.length,head:s.to+before.length},userEvent:'input'});view.focus();},
    destroy:()=>view.destroy(),
  };
}
