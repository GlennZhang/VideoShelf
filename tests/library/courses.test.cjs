const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(confirmResult, reject=false) {
  const nodes = {}, calls = [];
  const context = vm.createContext({
    document: {querySelector(selector) {return nodes[selector] ||= {innerHTML:'old preview',hidden:false,addEventListener(name, fn){this[name]=fn;}};}},
    confirm(text){assert.match(text,/原视频和已下载的文件不会删除/);return confirmResult;},
    fetch: async(url,options)=>{calls.push({url,options});return {ok:!reject,json:async()=>reject?{detail:'课程正在处理中'}:url==='/api/courses'?[]:{deleted:true}};},
  });
  const source=fs.readFileSync(path.join(__dirname,'../../src/library/static/courses.js'),'utf8');
  vm.runInContext(source.slice(0,source.lastIndexOf('refresh().catch')),context);
  vm.runInContext("selectedCourse='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';selectedLesson='001'",context);
  const button={id:'delete-course',dataset:{title:'测试课程'},disabled:false,closest(){return null;}};
  return {context,nodes,calls,button,click:()=>nodes['#course-detail'].click({target:button})};
}

test('confirmed delete clears selected course and tutorial preview',async()=>{
  const {context,nodes,calls,click}=setup(true);await click();
  assert.equal(calls[0].options.method,'DELETE');
  assert.equal(vm.runInContext('selectedCourse',context),null);
  assert.equal(nodes['#lesson-preview'].innerHTML,'');
  assert.equal(nodes['#lesson-preview'].hidden,true);
  assert.match(nodes['#course-message'].textContent,/原视频保留/);
});
test('cancelled confirmation leaves course untouched',async()=>{
  const {context,calls,click}=setup(false);await click();
  assert.equal(calls.length,0);
  assert.equal(vm.runInContext('selectedLesson',context),'001');
});
test('rejected delete retains selected course and allows retry',async()=>{
  const {context,nodes,button,click}=setup(true,true);await click();
  assert.notEqual(vm.runInContext('selectedCourse',context),null);
  assert.equal(button.disabled,false);
  assert.match(nodes['#course-message'].textContent,/正在处理中/);
});
