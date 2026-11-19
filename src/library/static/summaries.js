'use strict';
const el = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const statusText = { queued: '排队中', running: '处理中', done: '已完成', failed: '失败', retry: '等待重试' };

let selectedSummary = null;
let polling = false;

async function api(path, options = {}) {
  const response = await fetch('/api/summaries' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { message = (await response.json()).detail || message; } catch { /* keep default */ }
    throw new Error(message);
  }
  return response.json();
}

function message(text, error = false) {
  el('#summary-message').textContent = text;
  el('#summary-message').className = error ? 'course-error' : '';
}

function badge(item) {
  const label = statusText[item.status] || item.status;
  const extra = item.status === 'running' && item.stage ? ` · ${escapeText(item.stage)} ${Math.round((item.progress || 0) * 100)}%` : '';
  return `<span class="course-status ${item.status}">${label}</span>${extra}`;
}

function renderList(items) {
  const node = el('#summary-list');
  if (!items.length) { node.replaceChildren(); node.textContent = '还没有总结，提交一个视频试试。'; return; }
  node.innerHTML = items.map(item => `
    <button type="button" class="course-item ${item.id === selectedSummary ? 'selected' : ''}" data-summary="${item.id}">
      <strong>${escapeText(item.title)}</strong>
      ${badge(item)}
      ${item.error ? `<small class="course-error">${escapeText(item.error.slice(0, 120))}</small>` : ''}
    </button>`).join('');
}

function renderMarkdown(text, summaryId) {
  const node = CourseEditor.renderMarkdown(text, { path: 'notes.md' });
  for (const image of node.querySelectorAll('img')) {
    const src = image.getAttribute('src') || '';
    if (src.startsWith('frames/')) {
      image.src = `/api/summaries/${summaryId}/frames/${encodeURIComponent(src.slice('frames/'.length))}`;
    }
    image.loading = 'lazy';
  }
  return node;
}

async function refreshDetail() {
  const item = await api('/' + selectedSummary);
  const detail = el('#summary-detail');
  if (item.status !== 'done') {
    detail.innerHTML = `<h2>${escapeText(item.title)}</h2><p>${badge(item)}</p>${item.error ? `<p class="course-error">${escapeText(item.error)}</p>` : ''}
      <div class="dialog-actions">
        ${item.status === 'failed' ? '<button type="button" id="summary-retry">重试</button>' : ''}
        ${item.status === 'failed' || item.status === 'done' ? '<button type="button" id="summary-delete">删除</button>' : ''}
      </div>`;
    const retry = detail.querySelector('#summary-retry');
    if (retry) retry.addEventListener('click', async () => {
      try { await api('/' + selectedSummary + '/retry', { method: 'POST' }); message('已重新排队。'); refreshAll(); }
      catch (error) { message(error.message, true); }
    });
    const remove = detail.querySelector('#summary-delete');
    if (remove) remove.addEventListener('click', () => {
      el('#delete-summary-title').textContent = item.title;
      el('#delete-summary-dialog').showModal();
    });
    return;
  }
  const active = detail.dataset.tab || 'notes';
  detail.innerHTML = `<h2>${escapeText(item.title)}</h2>
    <div class="dialog-actions summary-toolbar">
      <div class="document-modes" role="group" aria-label="切换产物">
        <button data-summary-tab="notes" aria-pressed="${active === 'notes'}">学习笔记</button>
        <button data-summary-tab="transcript" aria-pressed="${active === 'transcript'}">优化稿</button>
      </div>
      <a id="summary-download" href="/api/summaries/${item.id}/download?file=${active}" download>下载本页</a>
      <button type="button" id="summary-delete">删除</button>
    </div>
    <div id="summary-body" class="md-rendered"></div>`;
  const body = detail.querySelector('#summary-body');
  body.replaceChildren(renderMarkdown(active === 'notes' ? item.notes : item.transcript_polished, item.id));
  for (const tab of detail.querySelectorAll('[data-summary-tab]')) {
    tab.addEventListener('click', () => { detail.dataset.tab = tab.dataset.summaryTab; refreshDetail(); });
  }
  detail.querySelector('#summary-delete').addEventListener('click', () => {
    el('#delete-summary-title').textContent = item.title;
    el('#delete-summary-dialog').showModal();
  });
}

async function refreshAll() {
  const items = await api('');
  renderList(items);
  if (selectedSummary && items.some(item => item.id === selectedSummary)) await refreshDetail();
  else if (selectedSummary) { selectedSummary = null; }
}

el('#summary-import').addEventListener('submit', async event => {
  event.preventDefault();
  const button = el('#create-summary');
  button.disabled = true;
  try {
    const item = await api('', { method: 'POST', body: JSON.stringify({ path: el('#summary-path').value.trim(), include_frames: el('#summary-frames').checked }) });
    selectedSummary = item.id;
    message('已加入队列，开始处理。');
    el('#summary-path').value = '';
    await refreshAll();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
});

el('#summary-list').addEventListener('click', event => {
  const button = event.target.closest('[data-summary]');
  if (!button) return;
  selectedSummary = button.dataset.summary;
  refreshAll().catch(error => message(error.message, true));
});

el('#cancel-delete-summary').addEventListener('click', () => el('#delete-summary-dialog').close());
el('#confirm-delete-summary').addEventListener('click', async event => {
  event.target.disabled = true;
  try {
    await api('/' + selectedSummary, { method: 'DELETE' });
    el('#delete-summary-dialog').close();
    selectedSummary = null;
    el('#summary-detail').innerHTML = '<h2>选择一条总结</h2><p>提交视频后，这里会显示处理进度、学习笔记和优化稿。</p>';
    await refreshAll();
  } catch (error) {
    el('#delete-summary-error').textContent = error.message;
  } finally {
    event.target.disabled = false;
  }
});

setInterval(async () => {
  if (polling || document.hidden) return;
  polling = true;
  try { await refreshAll(); } catch { /* next tick retries */ } finally { polling = false; }
}, 4000);

refreshAll().catch(error => message(error.message, true));
