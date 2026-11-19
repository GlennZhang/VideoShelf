"use strict";
const purposes={text:{title:'课程整理',description:'从文字稿提取知识点、步骤和提示词。'},vision:{title:'截图识别',description:'核对示例截图与屏幕提示词，请选择支持图片输入的模型。'},asr:{title:'语音转写',description:'将视频音频转成文字，可以继续使用本地模型。'}};
const escapeModelText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const settingsNode=selector=>document.querySelector(selector);
let loadedProfiles=null;
async function settingsRequest(path='',options={}){
  const response=await fetch('/api/model-settings'+path,{headers:{'Content-Type':'application/json'},...options});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'设置格式不正确，请检查服务地址与模型名。');
  return data;
}
function profileValue(purpose){
  const root=settingsNode(`[data-profile="${purpose}"]`),value={};
  for(const name of ['provider','base_url','model','api_key','api_key_env'])value[name]=root.querySelector(`[name="${name}"]`).value.trim();
  return value;
}
function profileMarkup(purpose,profile){
  const info=purposes[purpose];
  return `<section class="model-profile" data-profile="${purpose}"><h2>${info.title}</h2><p>${info.description}</p><div class="profile-fields"><label>服务方式<select name="provider" aria-label="${info.title}服务方式">${[['omlx','本地 oMLX'],['litellm','LiteLLM'],['openai-compatible','OpenAI 兼容接口']].map(([value,label])=>`<option value="${value}" ${profile.provider===value?'selected':''}>${label}</option>`).join('')}</select></label><label>服务地址<input name="base_url" type="url" required value="${escapeModelText(profile.base_url)}" placeholder="http://localhost:4000/v1" aria-label="${info.title}服务地址"></label><label class="model-name">模型名称<input name="model" required value="${escapeModelText(profile.model)}" list="models-${purpose}" aria-label="${info.title}模型名称"><datalist id="models-${purpose}"></datalist><small>可读取服务的模型列表，也可以直接填写网关配置的模型名。</small></label><label>API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="${profile.has_api_key?'当前服务已配置密钥':'可选，按服务要求填写'}" aria-label="${info.title}API Key"><small>同一服务留空保留原密钥；更换服务需填写新密钥或环境变量。密钥不会回显。</small></label><label>密钥环境变量（可选）<input name="api_key_env" value="${escapeModelText(profile.api_key_env)}" placeholder="LITELLM_API_KEY" aria-label="${info.title}密钥环境变量"><small>填写后优先使用服务进程中的环境变量。</small></label></div><div class="profile-connection"><button type="button" data-discover="${purpose}">读取模型 / 测试连接</button><p data-connection-status role="status" aria-live="polite"></p></div></section>`;
}
async function loadSettings(){
  loadedProfiles=await settingsRequest();
  settingsNode('#model-profiles').innerHTML=Object.keys(purposes).map(p=>profileMarkup(p,loadedProfiles[p])).join('');
}
settingsNode('#model-profiles').addEventListener('click',async event=>{
  const button=event.target.closest('[data-discover]');if(!button)return;
  const purpose=button.dataset.discover,root=button.closest('[data-profile]'),status=root.querySelector('[data-connection-status]');
  button.disabled=true;status.className='';status.textContent='正在连接服务…';
  try{
    const result=await settingsRequest(`/models/${purpose}`,{method:'POST',body:JSON.stringify(profileValue(purpose))});
    root.querySelector('datalist').innerHTML=result.models.map(model=>`<option value="${escapeModelText(model)}"></option>`).join('');
    status.textContent=`连接成功，已读取 ${result.models.length} 个模型。可在模型名称输入框中选择。`;
  }catch(error){status.className='settings-error';status.textContent=error.message;}
  finally{button.disabled=false;}
});
settingsNode('#model-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=settingsNode('#save-models'),status=settingsNode('#model-save-status');button.disabled=true;status.className='';status.textContent='保存中…';
  try{
    const profiles=Object.fromEntries(Object.keys(purposes).map(p=>[p,profileValue(p)]));
    loadedProfiles=await settingsRequest('',{method:'PUT',body:JSON.stringify(profiles)});
    for(const p of Object.keys(purposes)){
      const key=settingsNode(`[data-profile="${p}"] [name="api_key"]`);key.value='';key.placeholder=loadedProfiles[p].has_api_key?'当前服务已配置密钥':'可选，按服务要求填写';
    }
    status.textContent='已保存，下一节课使用新设置。';
  }catch(error){status.className='settings-error';status.textContent=error.message;}
  finally{button.disabled=false;}
});
loadSettings().catch(error=>{settingsNode('#model-profiles').textContent='读取失败：'+error.message;settingsNode('#save-models').disabled=true;});
