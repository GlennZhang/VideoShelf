/** Save ordering and recoverable drafts, independent of the editor DOM. */
export class DocumentSession {
  constructor({key, markdown, revision, save, onStatus, storage = localStorage, delay = 900}) {
    Object.assign(this, {key, text:markdown, savedText:markdown, revision, save, onStatus, storage, delay});
    this.timer=null;this.inflight=null;this.blocked=null;
    let draft;
    try { draft=JSON.parse(storage.getItem(key) || 'null'); } catch { draft=null; }
    if(draft && typeof draft.markdown==='string' && typeof draft.revision==='string' && draft.markdown!==markdown) {
      this.text=draft.markdown;
      if(draft.revision!==revision){this.revision=draft.revision;this.blocked=Object.assign(new Error('草稿与服务器版本不同，请先比较。'),{status:409});}
      this.onStatus(this.blocked?'conflict':'recovered',this.blocked?.message || '已恢复未保存草稿');
      if(!this.blocked)this.timer=setTimeout(()=>this.flush().catch(()=>{}),this.delay);
    } else this.onStatus('saved','已保存');
  }
  get dirty(){return this.text!==this.savedText;}
  backup(){
    try {
      if(this.dirty) this.storage.setItem(this.key,JSON.stringify({markdown:this.text,revision:this.revision}));
      else this.storage.removeItem(this.key);
    } catch { this.onStatus('backup-error','本机草稿备份失败，请保存或下载当前内容。'); }
  }
  changed(text){
    this.text=text;this.backup();clearTimeout(this.timer);
    if(this.blocked){this.onStatus('conflict',this.blocked.message);return;}
    this.onStatus(this.dirty?'modified':'saved',this.dirty?'尚未保存':'已保存');
    if(this.dirty) this.timer=setTimeout(()=>this.flush().catch(()=>{}),this.delay);
  }
  async flush(){
    clearTimeout(this.timer);
    if(this.blocked) throw this.blocked;
    while(this.dirty || this.inflight){
      if(this.inflight){await this.inflight;continue;}
      const text=this.text, expected=this.revision;
      this.onStatus('saving','保存中…');
      const pending=Promise.resolve().then(()=>this.save(text,expected)).then(result=>{
        this.revision=result.revision;this.savedText=text;this.backup();
        this.onStatus(this.dirty?'modified':'saved',this.dirty?'正在保存后续修改…':'已保存');
      }).catch(error=>{
        if(error.status===409)this.blocked=error;
        this.backup();this.onStatus(error.status===409?'conflict':'error',(error.status===409?'保存冲突，当前草稿仍保留。':'保存失败，草稿仍保留。')+(error.message || ''));throw error;
      });
      this.inflight=pending;
      try {await pending;} finally {if(this.inflight===pending)this.inflight=null;}
    }
  }
  useComparedRevision(revision){this.revision=revision;this.blocked=null;this.backup();}
  dispose(){clearTimeout(this.timer);this.backup();}
}
