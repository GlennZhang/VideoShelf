import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DocumentSession} from './document-session.js';
function storage(){const map=new Map();return {getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};}
function setup(save,store=storage()){
  const statuses=[];
  const session=new DocumentSession({key:'doc',markdown:'原文',revision:'r0',save,storage:store,delay:60000,onStatus:(...args)=>statuses.push(args)});
  return {session,statuses,store};
}
test('typing during a save serializes the next revision',async()=>{
  const calls=[];let release;
  const {session,store}=setup((text,rev)=>{calls.push([text,rev]);return calls.length===1?new Promise(resolve=>release=resolve):Promise.resolve({revision:'r2'});});
  session.changed('第一版');const pending=session.flush();await Promise.resolve();
  session.changed('第二版');release({revision:'r1'});await pending;
  assert.deepEqual(calls,[['第一版','r0'],['第二版','r1']]);assert.equal(session.dirty,false);assert.equal(store.getItem('doc'),null);session.dispose();
});
test('failed save keeps a recoverable local draft',async()=>{
  const {session,store,statuses}=setup(()=>Promise.reject(new Error('offline')));
  session.changed('我的笔记');await assert.rejects(session.flush(),/offline/);
  assert.equal(session.dirty,true);assert.equal(JSON.parse(store.getItem('doc')).markdown,'我的笔记');assert.equal(statuses.at(-1)[0],'error');session.dispose();
  const recovered=setup(()=>Promise.resolve({revision:'r1'}),store);
  assert.equal(recovered.session.text,'我的笔记');assert.equal(recovered.statuses[0][0],'recovered');await recovered.session.flush();recovered.session.dispose();
});
test('conflict never overwrites until the compared revision is explicitly chosen',async()=>{
  const calls=[];let conflict=true;
  const {session}=setup((text,rev)=>{calls.push(rev);if(conflict)throw Object.assign(new Error('新版本'),{status:409});return {revision:'r3'};});
  session.changed('我的修改');await assert.rejects(session.flush());await assert.rejects(session.flush());assert.deepEqual(calls,['r0']);
  conflict=false;session.useComparedRevision('r2');await session.flush();assert.deepEqual(calls,['r0','r2']);session.dispose();
});
test('recovered stale draft does not autosave over newer server content',async()=>{
  const store=storage();store.setItem('doc',JSON.stringify({markdown:'旧页面的草稿',revision:'old'}));
  let saves=0;const {session,statuses}=setup(()=>{saves++;},store);
  assert.equal(session.text,'旧页面的草稿');assert.equal(statuses[0][0],'conflict');await assert.rejects(session.flush());assert.equal(saves,0);session.dispose();
});
test('saving an empty document is not treated as no change',async()=>{
  let saved;const {session}=setup(text=>{saved=text;return {revision:'empty'};});session.changed('');await session.flush();assert.equal(saved,'');assert.equal(session.dirty,false);session.dispose();
});

test('refreshing an unresolved recovered conflict cannot make it silently savable',async()=>{
  const store=storage();store.setItem('doc',JSON.stringify({markdown:'冲突草稿',revision:'old'}));
  const first=setup(()=>{throw new Error('must not save');},store);first.session.dispose();
  assert.equal(JSON.parse(store.getItem('doc')).revision,'old');
  const second=setup(()=>{throw new Error('must not save');},store);await assert.rejects(second.session.flush(),/版本不同/);second.session.dispose();
});
