'use strict';
const el = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const statusText = { queued: '排队中', running: '处理中', done: '已完成', failed: '失败', pending: '待下载' };

let resolvedItems = [];
let selectedTask = null;
let polling = false;

async function api(path, options = {}) {
  const response = await fetch('/api/downloads' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try { detail = (await response.json()).detail || detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return response.json();
}

function message(text, error = false) {
  el('#download-message').textContent = text;
  el('#download-message').className = error ? 'course-error' : '';
}

function renderResolved() {
  el('#item-list').innerHTML = resolvedItems.map((item, position) => `
    <label class="item-row"><input type="checkbox" class="item-check" data-position="${position}" checked>
      <span class="item-index">${position + 1}</span>
      <span class="item-title">${escapeText(item.title)}</span>
      <small>${item.part ? `P${item.part} · ` : ''}${escapeText(item.bvid)}</small>
    </label>`).join('');
}

el('#resolve-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = el('#resolve-btn');
  button.disabled = true;
  try {
    const result = await api('/resolve', { method: 'POST', body: JSON.stringify({ url: el('#bili-url').value.trim() }) });
    resolvedItems = result.items;
    el('#resolve-title').textContent = `${result.title || '解析结果'}（${result.items.length} 条，${result.kind === 'season' ? '合集' : result.kind === 'multi_part' ? '多 P 视频' : '单视频'}）`;
    el('#resolve-result').hidden = false;
    renderResolved();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
});

el('#select-all').addEventListener('change', event => {
  for (const box of document.querySelectorAll('.item-check')) box.checked = event.target.checked;
});

el('#start-download').addEventListener('click', async () => {
  const picks = [...document.querySelectorAll('.item-check:checked')].map(box => resolvedItems[Number(box.dataset.position)]);
  if (!picks.length) { message('请至少勾选一个条目。', true); return; }
  const targetDir = el('#target-dir').value.trim();
  if (!targetDir) { message('请填写下载目录。', true); return; }
  try {
    const created = await api('', { method: 'POST', body: JSON.stringify({ items: picks, target_dir: targetDir, title: el('#resolve-title').textContent.split('（')[0] }) });
    selectedTask = created.id;
    message(`已开始下载 ${picks.length} 个条目。`);
    await refreshAll();
  } catch (error) {
    message(error.message, true);
  }
});

el('#save-dl-settings').addEventListener('click', async event => {
  event.target.disabled = true;
  try {
    const response = await fetch('/api/download-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cookie_source: el('#dl-cookie-source').value,
        browser: el('#dl-browser').value,
        sessdata: el('#dl-sessdata').value.trim(),
        quality: el('#dl-quality').value,
        download_dir: el('#dl-dir').value.trim(),
      }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || '保存失败');
    message('下载设置已保存，下次下载生效。');
  } catch (error) {
    message(error.message, true);
  } finally {
    event.target.disabled = false;
  }
});

function badge(item) {
  const label = statusText[item.status] || item.status;
  const extra = item.status === 'running' && item.stage ? ` · ${escapeText(item.stage)} ${Math.round((item.progress || 0) * 100)}%` : '';
  return `<span class="course-status ${item.status}">${label}</span>${extra}`;
}

function renderList(tasks) {
  const node = el('#download-list');
  if (!tasks.length) { node.replaceChildren(); node.textContent = '还没有下载任务。'; return; }
  node.innerHTML = tasks.map(task => `
    <button type="button" class="course-item ${task.id === selectedTask ? 'selected' : ''}" data-task="${task.id}">
      <strong>${escapeText(task.title || task.items[0]?.title || '下载任务')}</strong>
      ${badge(task)}
      <small>${task.items.filter(i => i.status === 'done').length}/${task.items.length} 已完成${task.error ? ' · ' + escapeText(task.error.slice(0, 80)) : ''}</small>
    </button>`).join('');
}

async function refreshDetail() {
  const task = await api('/' + selectedTask);
  const detail = el('#download-detail');
  const doneItems = task.items.filter(it => it.status === 'done' && it.file);
  detail.innerHTML = `<h2>${escapeText(task.title || '下载任务')}</h2>
    <p>${badge(task)} <small>目录：${escapeText(task.target_dir)}</small></p>
    <div class="dialog-actions">
      ${task.status === 'failed' ? '<button type="button" id="dl-retry">重试（跳过已完成）</button>' : ''}
      ${doneItems.length > 0 ? `<button type="button" id="dl-drama-all" class="primary">整组一键拆解（${doneItems.length}集）</button>` : ''}
      ${task.status !== 'running' && task.status !== 'queued' ? '<button type="button" id="dl-delete">删除任务（保留视频）</button>' : ''}
    </div>
    <div class="item-grid">${task.items.map(item => `
      <div class="item-row static">
        <span class="item-index">${item.index}</span>
        <span class="item-title">${escapeText(item.title)}</span>
        <span class="course-status ${item.status}">${statusText[item.status] || item.status}</span>
        ${item.status === 'done' ? `
          <button type="button" class="summarize-btn" data-file="${escapeText(item.file)}">生成总结</button>
          <button type="button" class="drama-btn" data-file="${escapeText(item.file)}" data-title="${escapeText(item.title)}">短剧拆解</button>
        ` : ''}
      </div>`).join('')}</div>`;
  const retry = detail.querySelector('#dl-retry');
  if (retry) retry.addEventListener('click', async () => {
    try { await api('/' + selectedTask + '/retry', { method: 'POST' }); message('已重新排队，已完成的条目会跳过。'); refreshAll(); }
    catch (error) { message(error.message, true); }
  });
  const dramaAll = detail.querySelector('#dl-drama-all');
  if (dramaAll) dramaAll.addEventListener('click', async () => {
    dramaAll.disabled = true;
    try {
      const response = await fetch('/api/dramas/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          group_title: task.title || 'B站合集',
          group_id: task.id,
          items: doneItems.map(it => ({ path: it.file, title: it.title })),
        }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || '批量创建失败');
      message(`已将 ${doneItems.length} 集加入短剧拆解队列，请到「短剧拆解」页查看！`);
    } catch (error) {
      message(error.message, true);
    } finally {
      dramaAll.disabled = false;
    }
  });
  const remove = detail.querySelector('#dl-delete');
  if (remove) remove.addEventListener('click', async () => {
    try { await api('/' + selectedTask, { method: 'DELETE' }); selectedTask = null; detail.innerHTML = '<h2>选择一个任务</h2><p>下载完成后，可对单个条目生成学习笔记。</p>'; await refreshAll(); }
    catch (error) { message(error.message, true); }
  });
  for (const button of detail.querySelectorAll('.summarize-btn')) {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const response = await fetch('/api/summaries', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: button.dataset.file, include_frames: true }),
        });
        if (!response.ok) throw new Error((await response.json()).detail || '创建失败');
        message('总结任务已创建，到「视频总结」页查看进度。');
      } catch (error) {
        message(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  }
  for (const button of detail.querySelectorAll('.drama-btn')) {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const response = await fetch('/api/dramas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: button.dataset.file,
            group_title: task.title || '',
            group_id: task.id || '',
          }),
        });
        if (!response.ok) throw new Error((await response.json()).detail || '创建失败');
        message('短剧拆解任务已创建，到「短剧拆解」页查看进度。');
      } catch (error) {
        message(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  }
}

async function refreshAll() {
  const tasks = await api('');
  renderList(tasks);
  if (selectedTask && tasks.some(task => task.id === selectedTask)) await refreshDetail();
}

el('#download-list').addEventListener('click', event => {
  const button = event.target.closest('[data-task]');
  if (!button) return;
  selectedTask = button.dataset.task;
  refreshAll().catch(error => message(error.message, true));
});

function updateCookieFields() {
  const source = el('#dl-cookie-source').value;
  el('#dl-sessdata').hidden = source !== 'manual';
  el('#dl-sessdata-label').hidden = source !== 'manual';
  el('#dl-browser').disabled = source !== 'browser';
}

el('#dl-cookie-source').addEventListener('change', updateCookieFields);

async function loadSettings() {
  try {
    const response = await fetch('/api/download-settings');
    const settings = await response.json();
    el('#dl-cookie-source').value = settings.cookie_source || 'browser';
    el('#dl-browser').value = settings.browser || 'chrome';
    el('#dl-quality').value = settings.quality || '自动（登录后最高）';
    el('#dl-dir').value = settings.download_dir || '';
    updateCookieFields();
    if (settings.download_dir && !el('#target-dir').value) el('#target-dir').value = settings.download_dir;
  } catch { /* 设置加载失败不阻塞页面 */ }
}

// ---- 扫码登录 ----
let qrTimer = null;

async function pollQr(key) {
  try {
    const response = await fetch('/api/downloads/qr-login/poll?key=' + encodeURIComponent(key));
    const result = await response.json();
    if (result.status === 'success') {
      el('#qr-status').textContent = '登录成功，SESSDATA 已自动保存。';
      setTimeout(() => el('#qr-dialog').close(), 1200);
      clearInterval(qrTimer);
      await loadSettings();
      return;
    }
    if (result.status === 'expired') {
      el('#qr-status').textContent = '二维码已过期。';
      el('#qr-refresh').hidden = false;
      clearInterval(qrTimer);
      return;
    }
    el('#qr-status').textContent = result.status === 'scanned' ? '已扫码，请在手机上确认…' : '等待扫码…';
  } catch { /* 下一轮重试 */ }
}

async function startQrLogin() {
  el('#qr-refresh').hidden = true;
  el('#qr-status').textContent = '正在获取二维码…';
  try {
    const response = await fetch('/api/downloads/qr-login/start', { method: 'POST' });
    if (!response.ok) throw new Error((await response.json()).detail || '获取二维码失败');
    const started = await response.json();
    el('#qr-image').replaceChildren();
    const image = document.createElement('img');
    image.src = started.svg;
    image.alt = 'B 站登录二维码';
    el('#qr-image').append(image);
    el('#qr-dialog').showModal();
    el('#qr-status').textContent = '等待扫码…';
    clearInterval(qrTimer);
    qrTimer = setInterval(() => pollQr(started.qrcode_key), 2500);
  } catch (error) {
    el('#qr-status').textContent = error.message;
  }
}

el('#qr-login-btn').addEventListener('click', startQrLogin);
el('#qr-close').addEventListener('click', () => { clearInterval(qrTimer); el('#qr-dialog').close(); });
el('#qr-refresh').addEventListener('click', startQrLogin);

setInterval(async () => {
  if (polling || document.hidden) return;
  polling = true;
  try { await refreshAll(); } catch { /* 下一轮重试 */ } finally { polling = false; }
}, 4000);

loadSettings();
refreshAll().catch(error => message(error.message, true));
