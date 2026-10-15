/* VideoShelf 本地视频资料库 — vanilla SPA */
"use strict";

const S = {
  view: { type: "all", value: null },
  q: "",
  selected: new Set(),
  batchMode: false,
  current: null,       // detail video object
  tasksTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts) => {
  const resp = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);
  return resp.json();
};
const fmtDur = (s) => {
  if (!s) return "--:--";
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};

/* ---------- sidebar ---------- */

function attributedPainters(v) {
  const people = (v.people || []).filter((p) => p.kind === "painter");
  const manual = people.some((p) => p.source === "user");
  return people.filter((p) => p.is_attribution !== undefined ? p.is_attribution :
    (!manual || p.source === "user") && (p.role === "author" ||
      (p.role === "featured_painter" && ["绘画", "书法"].includes(v.category))));
}

async function sidebarPainters() {
  const people = await api("/people?kind=painter");
  // A running server may still use the API version without video_count.
  // Check actual membership so refreshing the page also fixes that case.
  return Promise.all(people.map(async (p) => {
    if (typeof p.video_count === "number") return p;
    for (let offset = 0; ; offset += 500) {
      const videos = await api(`/videos?artist_id=${p.id}&limit=500&offset=${offset}`);
      if (videos.items.some((v) => attributedPainters(v).some((a) => a.id === p.id))) {
        return { ...p, video_count: 1 };
      }
      if (videos.items.length < 500) return { ...p, video_count: 0 };
    }
  }));
}

async function refreshSidebar() {
  const [stats, folders, painters, collections] = await Promise.all([
    api("/stats"), api("/folders"), sidebarPainters(),
    api("/collections"),
  ]);
  $("#browse-total").textContent = stats.total;
  $("#browse-pending").textContent = stats.pending;
  $("#browse-favorites").textContent = stats.favorites ??
    (await api("/videos?favorite=true&limit=500")).total;
  $("#nav-folders").innerHTML = folders.map((f) => `
    <li data-view="folder" data-value="${f.id}" title="${f.path}">
      <span>${f.path.split("/").pop()}</span>
      <button class="rescan" data-rescan="${f.id}" title="重新扫描">⟳</button>
    </li>`).join("");
  $("#nav-categories").innerHTML = Object.entries(stats.categories)
    .map(([c, n]) => `<li data-view="category" data-value="${c}">
      <span>${c}</span><span class="count">${n}</span></li>`)
    .join("");
  const activePainters = painters.filter((p) => p.video_count > 0);
  if (S.view.type === "painter" && !activePainters.some((p) => String(p.id) === String(S.view.value))) {
    S.view = { type: "all", value: null };
  }
  $("#nav-painters").innerHTML = `
    <li data-view="unknown-artist"><span class="grow">未知画家</span><span class="count">${stats.unknown_artists ?? 0}</span></li>` + activePainters.slice(0, 30).map((p) => `
    <li data-view="painter" data-value="${p.id}">
      <span class="grow">${esc(p.canonical_name)}${p.confirmed_status === "pending" ? " ?" : ""}</span>
      <button class="rename" data-rename="${p.id}" title="改名">✎</button>
    </li>`).join("");
  $("#nav-collections").innerHTML = collections.map((c) => `
    <li data-view="collection" data-value="${c.id}">
      <span>${c.name}</span><span class="count">${c.video_count}</span></li>`)
    .join("");
  bindNav();
  document.querySelectorAll("#sidebar nav li[data-view]").forEach((li) => {
    li.classList.toggle("active", li.dataset.view === S.view.type &&
      (S.view.value === null || li.dataset.value === String(S.view.value)));
  });
}

function bindNav() {
  document.querySelectorAll("#sidebar nav li[data-view]").forEach((li) => {
    li.onclick = (e) => {
      if (e.target.dataset.rescan !== undefined) return;
      if (e.target.dataset.rename !== undefined) return;
      S.view = { type: li.dataset.view, value: li.dataset.value ?? null };
      document.querySelectorAll("#sidebar nav li").forEach(
        (x) => x.classList.remove("active"));
      li.classList.add("active");
      loadList();
    };
  });
  document.querySelectorAll("[data-rescan]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      await api(`/folders/${btn.dataset.rescan}/rescan`, { method: "POST" });
      refreshTasks();
    };
  });
  document.querySelectorAll("[data-rename]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.rename);
      const people = await api("/people?kind=painter");
      const old = people.find((p) => p.id === id);
      const name = prompt("画家名称：", old ? old.canonical_name : "");
      if (!name || !name.trim()) return;
      await api(`/people/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          canonical_name: name.trim(), confirmed_status: "confirmed",
        }),
      });
      await refreshSidebar(); await loadList();
      if (S.current) openDetail(S.current.content_id);
    };
  });
}

/* ---------- sidebar section collapse ---------- */

const COLLAPSE_KEY = "lib-collapsed-sections";

function bindCollapse() {
  document.querySelectorAll("#sidebar nav section[data-sec] h2").forEach((h2) => {
    h2.onclick = (e) => {
      if (e.target.closest("#new-collection-btn")) return;
      const sec = h2.parentElement;
      sec.classList.toggle("collapsed");
      const saved = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
      const name = sec.dataset.sec;
      sec.classList.contains("collapsed") ? saved.add(name) : saved.delete(name);
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...saved]));
    };
  });
  const saved = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
  document.querySelectorAll("#sidebar nav section[data-sec]").forEach((sec) => {
    sec.classList.toggle("collapsed", saved.has(sec.dataset.sec));
  });
}

$("#add-folder-btn").onclick = async () => {
  const path = prompt("输入视频文件夹的绝对路径：");
  if (!path) return;
  try {
    await api("/folders", {
      method: "POST", body: JSON.stringify({ path }),
    });
    await refreshSidebar(); loadList(); refreshTasks();
  } catch (err) { alert("添加失败：" + err.message); }
};

$("#new-collection-btn").onclick = async () => {
  const name = prompt("新合集名称：");
  if (!name) return;
  await api("/collections", { method: "POST", body: JSON.stringify({ name }) });
  refreshSidebar();
};

/* ---------- list ---------- */

async function loadList() {
  const p = new URLSearchParams();
  const { type, value } = S.view;
  if (type === "category") p.set("category", value);
  if (type === "painter") p.set("artist_id", value);
  if (type === "unknown-artist") p.set("unknown_artist", "true");
  if (type === "folder") p.set("folder_id", value);
  if (type === "collection") p.set("collection_id", value);
  if (type === "favorite") p.set("favorite", "true");
  if (type === "pending") p.set("pending", "true");
  if (S.q) p.set("q", S.q);
  const form = $("#filter-form").value;
  if (form) p.set("form", form);
  const dur = $("#filter-duration").value;
  let data;
  try { data = await api("/videos?" + p.toString()); }
  catch (err) { alert(err.message); return; }
  if (type === "painter") {
    data.items = data.items.filter((v) => attributedPainters(v).some((p) => String(p.id) === String(value)));
  }
  if (dur) {
    data.items = data.items.filter((v) => {
      const m = (v.duration_s || 0) / 60;
      return dur === "short" ? m < 10 : dur === "medium" ? m >= 10 && m <= 40 : m > 40;
    });
  }
  $("#empty-hint").hidden = data.items.length > 0;
  $("#video-grid").innerHTML = data.items.map(cardHTML).join("");
  bindCards();
}

function cardHTML(v) {
  const shot = (v.screenshots || [])[0];
  const painter = attributedPainters(v)[0];
  const status = v.analysis_status || "pending";
  const visualOnly = ["no_speech", "failed", "none", "visual_only_fallback"]
    .includes(v.transcript_status);
  const statusText = {
    done: visualOnly ? "视觉分析" : "已完成",
    analyzing: "分析中", pending: "待分析", failed: "失败",
  }[status] || status;
  return `
  <div class="card" data-id="${v.content_id}">
    ${shot
      ? `<img class="thumb" src="/api/screenshots/${shot.id}" loading="lazy">`
      : `<div class="thumb placeholder">暂无截图</div>`}
    <span class="badge">${fmtDur(v.duration_s)}</span>
    <span class="status-badge ${status}">${statusText}</span>
    <div class="info">
      <div class="title">${esc(v.title || "未命名")}</div>
      <div class="artist">${painter ? attributedPainters(v).map((p) => esc(p.canonical_name)).join("、") : "未知画家"}</div>
      <div class="summary">${esc((v.summary || "").slice(0, 80))}</div>
      <div class="tags">${(v.tags || []).slice(0, 4)
        .map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    </div>
    ${S.batchMode ? `<input type="checkbox" class="pick"
        ${S.selected.has(v.content_id) ? "checked" : ""}
        data-pick="${v.content_id}">` : ""}
  </div>`;
}

function bindCards() {
  document.querySelectorAll(".card").forEach((card) => {
    card.onclick = (e) => {
      if (e.target.dataset.pick !== undefined) return;
      openDetail(Number(card.dataset.id));
    };
  });
  document.querySelectorAll("[data-pick]").forEach((cb) => {
    cb.onclick = (e) => {
      e.stopPropagation();
      const id = Number(cb.dataset.pick);
      cb.checked ? S.selected.add(id) : S.selected.delete(id);
      $("#batch-count").textContent = `已选 ${S.selected.size}`;
    };
  });
}

const esc = (s) => String(s ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

/* search & filters */
let searchTimer;
$("#search").oninput = (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.q = e.target.value.trim(); loadList(); }, 300);
};
$("#filter-form").onchange = loadList;
$("#filter-duration").onchange = loadList;

/* ---------- batch ---------- */

$("#batch-mode-btn").onclick = () => {
  S.batchMode = !S.batchMode;
  S.selected.clear();
  $("#batch-bar").hidden = !S.batchMode;
  $("#batch-count").textContent = "已选 0";
  loadList();
};
$("#batch-done").onclick = () => $("#batch-mode-btn").click();
document.querySelectorAll("#batch-bar [data-action]").forEach((btn) => {
  btn.onclick = async () => {
    const action = btn.dataset.action;
    const ids = [...S.selected];
    if (!ids.length) return alert("先选择视频");
    let value;
    if (action === "artist") value = prompt("画家姓名：");
    else if (action === "category") value = prompt("主分类（如 绘画）：");
    else if (action === "tag") value = prompt("标签：");
    else if (action === "collection") {
      const cols = await api("/collections");
      value = prompt(`合集 id（${cols.map((c) => `${c.id}=${c.name}`).join("，")}）：`);
      value = Number(value);
    }
    if (!value) return;
    await api("/batch", {
      method: "POST",
      body: JSON.stringify({ content_ids: ids, action, value }),
    });
    S.selected.clear();
    $("#batch-count").textContent = "已选 0";
    await refreshSidebar(); await loadList();
  };
});

/* ---------- detail ---------- */

async function openDetail(id) {
  const v = await api(`/videos/${id}`);
  S.current = v;
  $("#detail-pane").hidden = false;
  const painter = attributedPainters(v)[0] || {};
  const account = (v.people || []).find((p) => p.kind === "source_account") || {};
  const evidence = safeJson(v.ai_evidence);
  $("#detail-body").innerHTML = `
    <h3>${esc(v.title || "未命名")}</h3>
    <div class="muted">${fmtDur(v.duration_s)} · ${esc(v.category || "未分类")}
      · ${esc(v.form || "")} ${v.user_favorite ? "· ★ 已收藏" : ""}</div>
    <div class="section">
      <div class="muted">代表截图（点击跳转播放）</div>
      <div class="shots">${(v.screenshots || []).map((s) => `
        <img src="/api/screenshots/${s.id}" data-ts="${s.timestamp_s}">`).join("")
        || '<span class="muted">暂无</span>'}</div>
    </div>
    <div class="section">
      <label>标题<input id="edit-title" value="${esc(v.title || "")}"></label>
      <label>简介<textarea id="edit-summary">${esc(v.summary || "")}</textarea></label>
      <label>分类<input id="edit-category" value="${esc(v.category || "")}"></label>
      <label>主题<input id="edit-topic" value="${esc(v.topic || "")}"></label>
      <label>形式<input id="edit-form" value="${esc(v.form || "")}"></label>
      <label>标签（逗号分隔）<input id="edit-tags"
        value="${esc((v.tags || []).join(", "))}"></label>
      <label>备注<textarea id="edit-notes">${esc(v.user_notes || "")}</textarea></label>
      <div class="actions">
        <button class="primary" id="save-btn">保存修改</button>
        <button id="fav-btn">${v.user_favorite ? "取消收藏" : "收藏"}</button>
        <button id="play-btn">▶ 播放</button>
        <button id="reveal-btn">定位原文件</button>
        <button id="reanalyze-btn">重新分析</button>
      </div>
    </div>
    <div class="section">
      <div class="muted">画家与来源</div>
      <div class="kv"><b>画家：</b>${attributedPainters(v).map((p) => esc(p.canonical_name)).join("、") || "未知"}
        ${painter.confirmed_status === "pending" && painter.id
          ? `<button class="link" data-confirm-painter="${painter.id}">确认</button>` : ""}
        ${painter.id
          ? `<button class="link" data-rename-painter="${painter.id}">改名</button>` : ""}
        <button class="link" data-change-artist="1">
          ${painter.id ? "换画家" : "指定画家"}</button></div>
      <div class="kv"><b>依据：</b>${esc(painter.evidence || "")}</div>
      <div class="kv"><b>相关人物：</b>${(v.people || []).filter((p) => p.kind === "painter" && !attributedPainters(v).some((a) => a.id === p.id)).map((p) => esc(p.canonical_name)).join("、") || "无"}</div>
      <div class="kv"><b>来源账号：</b>${esc(account.canonical_name || "未知")}</div>
    </div>
    <div class="section">
      <div class="muted">分析信息</div>
      <div class="kv"><b>状态：</b>${esc(v.analysis_status)}
        ${v.transcript_status ? `（转写：${esc(v.transcript_status)}）` : ""}</div>
      <div class="kv"><b>模型：</b>${esc(v.pipeline_version || "")}
        ${evidence.techniques ? "· 技法：" + esc(evidence.techniques.join("、")) : ""}</div>
      ${v.analysis_error ? `<div class="kv" style="color:var(--warn)"><b>错误：</b>${esc(v.analysis_error)}</div>` : ""}
    </div>
    <div class="section">
      <div class="muted">文件位置</div>
      ${(v.files || []).map((f) => `
        <div class="kv"><b>${f.status === "available" ? "" : "⚠ "}</b>${esc(f.path)}</div>`).join("")}
    </div>
    <div class="section">
      <div class="muted">转写</div>
      <div class="transcript" id="transcript-box">加载中…</div>
    </div>`;
  bindDetail(v);
  api(`/videos/${id}/transcripts`).then((segs) => {
    $("#transcript-box").innerHTML = segs.length
      ? segs.map((s) => `<p><span class="t" data-ts="${s.start_s}">
          [${fmtDur(s.start_s)}]</span> ${esc(s.text)}</p>`).join("")
      : "（无转写）";
    bindSeek("#transcript-box .t");
  });
}

function safeJson(x) { try { return typeof x === "string" ? JSON.parse(x) : (x || {}); } catch { return {}; } }

function bindDetail(v) {
  bindSeek("#detail-body .shots img");
  $("#save-btn").onclick = async () => {
    await api(`/videos/${v.content_id}`, {
      method: "PATCH",
      body: JSON.stringify({
        title: $("#edit-title").value,
        summary: $("#edit-summary").value,
        category: $("#edit-category").value,
        topic: $("#edit-topic").value,
        form: $("#edit-form").value,
        tags: $("#edit-tags").value.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
        notes: $("#edit-notes").value,
      }),
    });
    loadList(); refreshSidebar(); openDetail(v.content_id);
  };
  $("#fav-btn").onclick = async () => {
    await api(`/videos/${v.content_id}`, {
      method: "PATCH",
      body: JSON.stringify({ favorite: !v.user_favorite }),
    });
    await refreshSidebar(); await loadList(); await openDetail(v.content_id);
  };
  $("#play-btn").onclick = () => play(v, 0);
  $("#reveal-btn").onclick = () => api(`/videos/${v.content_id}/reveal`, { method: "POST" });
  $("#reanalyze-btn").onclick = async () => {
    await api(`/videos/${v.content_id}/reanalyze`, { method: "POST" });
    refreshTasks(); openDetail(v.content_id);
  };
  const cp = document.querySelector("[data-confirm-painter]");
  if (cp) cp.onclick = async () => {
    await api(`/videos/${v.content_id}/people/${cp.dataset.confirmPainter}/confirm/author`, { method: "POST" });
    await refreshSidebar(); await loadList(); await openDetail(v.content_id);
  };
  const rp = document.querySelector("[data-rename-painter]");
  if (rp) rp.onclick = async () => {
    const cur = attributedPainters(v)[0] || {};
    const name = prompt("画家名称：", cur.canonical_name || "");
    if (!name || !name.trim()) return;
    await api(`/people/${rp.dataset.renamePainter}`, {
      method: "PATCH",
      body: JSON.stringify({
        canonical_name: name.trim(), confirmed_status: "confirmed",
      }),
    });
    await refreshSidebar(); await loadList(); await openDetail(v.content_id);
  };
  const ca = document.querySelector("[data-change-artist]");
  if (ca) ca.onclick = async () => {
    const name = prompt("新的画家姓名（将替换当前画家）：");
    if (!name || !name.trim()) return;
    await api(`/videos/${v.content_id}/artist`, {
      method: "POST", body: JSON.stringify({ name: name.trim() }),
    });
    await refreshSidebar(); await loadList(); await openDetail(v.content_id);
  };
}

$("#detail-close").onclick = () => { $("#detail-pane").hidden = true; S.current = null; };

function bindSeek(sel) {
  document.querySelectorAll(sel).forEach((el) => {
    el.onclick = () => play(S.current, Number(el.dataset.ts) || 0);
  });
}

/* ---------- player ---------- */

function play(v, startAt) {
  const file = (v.files || []).find((f) => f.status === "available");
  if (!file) return alert("没有可播放的文件");
  $("#player-modal").hidden = false;
  $("#player-title").textContent = v.title || "未命名";
  const player = $("#player");
  player.src = `/api/files/${file.id}/stream?start=${startAt}`;
  player.play().catch(() => {});
}

function closePlayer() {
  $("#player").pause();
  $("#player-modal").hidden = true;
}

$("#player-close").onclick = closePlayer;
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#player-modal").hidden) closePlayer();
});

/* ---------- tasks ---------- */

const STAGE_LABELS = {
  probe: "读取信息", frames: "提取关键帧", audio: "提取音频",
  transcribe: "语音转写", analyze: "联合分析", finalize: "生成结果",
};

let lastFinished = -1; // done+failed count seen last poll

async function refreshTasks() {
  const tasks = await api("/tasks");
  const active = tasks.filter((t) =>
    ["queued", "running", "retry"].includes(t.status));
  $("#task-count").textContent = active.length
    ? `(${active.length})` : "";

  // running/queued analyze tasks → per-card badge (stage + %)
  const byContent = {};
  for (const t of tasks) {
    const cid = t.payload && t.payload.content_id;
    if (t.type === "analyze" && cid != null &&
        ["queued", "running", "retry"].includes(t.status)) {
      byContent[cid] = t;
    }
  }
  document.querySelectorAll(".card").forEach((card) => {
    const badge = card.querySelector(".status-badge");
    if (!badge) return;
    const t = byContent[card.dataset.id];
    if (t && t.status === "running") {
      const label = STAGE_LABELS[t.stage] || t.stage || "";
      const pct = Math.round((t.progress || 0) * 100);
      badge.textContent = `${label} ${pct}%`;
      badge.className = "status-badge analyzing";
    } else if (t) {
      badge.textContent = "排队中";
      badge.className = "status-badge pending";
    }
  });

  // refresh list+sidebar when a task finishes (new results to show)
  const finished = tasks.filter((t) =>
    ["done", "failed"].includes(t.status)).length;
  if (finished !== lastFinished) {
    await refreshSidebar();
    await loadList();
  }
  lastFinished = finished;

  const shown = tasks.slice(0, 12);
  $("#tasks-panel").innerHTML = shown.map((t) => `
    <div class="task ${t.status}">
      <div>#${t.id} ${t.type === "analyze" ? "分析" : "扫描"}${
        t.stage && t.status === "running"
          ? " · " + (STAGE_LABELS[t.stage] || t.stage) : ""
      }${t.status === "queued" ? " 排队中" : ""}
        ${t.status === "failed" ? `<button class="link" data-retry="${t.id}">重试</button>` : ""}
        <span class="muted">${esc(t.error || "")}</span></div>
      <div class="bar"><div style="width:${Math.round((t.progress || 0) * 100)}%"></div></div>
    </div>`).join("") || "暂无任务";
  document.querySelectorAll("[data-retry]").forEach((btn) => {
    btn.onclick = async () => {
      await api(`/tasks/${btn.dataset.retry}/retry`, { method: "POST" });
      refreshTasks();
    };
  });
}

$("#tasks-toggle").onclick = () => {
  const p = $("#tasks-panel");
  p.hidden = !p.hidden;
  if (!p.hidden) refreshTasks();
};

/* ---------- boot ---------- */

async function boot() {
  bindCollapse();
  await refreshSidebar();
  await loadList();
  S.tasksTimer = setInterval(refreshTasks, 4000);
}
boot();
