const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function sidebarContext(responses) {
  const nodes = {};
  const calls = [];
  const ctx = vm.createContext({
    document: {
      querySelector: (selector) => nodes[selector] ||= {},
      querySelectorAll: () => [],
    },
    fetch: async (url, opts) => {
      calls.push(url);
      assert.ok(Object.hasOwn(responses, url), `Unexpected request: ${url}`);
      return { ok: true, json: async () => typeof responses[url] === 'function' ? responses[url](opts) : responses[url] };
    },
    esc: (s) => s,
    bindNav: () => {},
  });
  const source = fs.readFileSync(path.join(__dirname, '../../src/library/static/app.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf('function bindNav()')), ctx);
  return { ctx, nodes, calls };
}

test('empty painter vanishes and active view resets after last video moves', async () => {
  const responses = {
    '/api/stats': { categories: {}, total: 2, pending: 1, favorites: 0, unknown_artists: 2 },
    '/api/folders': [],
    '/api/collections': [],
    '/api/people?kind=painter': [
      { id: 1, canonical_name: '田田', video_count: 0 },
      { id: 2, canonical_name: '大馨', video_count: 1 },
    ],
  };
  const { ctx, nodes, calls } = sidebarContext(responses);
  vm.runInContext('S.view = {type: "painter", value: "1"}', ctx);
  await vm.runInContext('refreshSidebar()', ctx);
  assert.doesNotMatch(nodes['#nav-painters'].innerHTML, /田田/);
  assert.match(nodes['#nav-painters'].innerHTML, /大馨/);
  assert.equal(vm.runInContext('S.view.type', ctx), 'all');
  assert.equal(calls.length, 4);
  assert.equal(nodes['#browse-total'].textContent, 2);
  assert.equal(nodes['#browse-pending'].textContent, 1);
  assert.equal(nodes['#browse-favorites'].textContent, 0);
  assert.match(nodes['#nav-painters'].innerHTML, /未知画家<\/span><span class="count">2/);
  vm.runInContext('S.view = {type: "unknown-artist", value: null}', ctx);
  responses['/api/stats'] = { categories: {}, total: 3, pending: 0, favorites: 1, unknown_artists: 0 };
  await vm.runInContext('refreshSidebar()', ctx);
  assert.equal(nodes['#browse-total'].textContent, 3);
  assert.equal(nodes['#browse-pending'].textContent, 0);
  assert.equal(nodes['#browse-favorites'].textContent, 1);
  assert.match(nodes['#nav-painters'].innerHTML, /未知画家<\/span><span class="count">0/);
  assert.equal(vm.runInContext('S.view.type', ctx), 'unknown-artist');
});

test('old server without counts uses author roles, excluding guest-only matches', async () => {
  const responses = {
    '/api/people?kind=painter': [{ id: 1 }, { id: 2 }],
    '/api/videos?artist_id=1&limit=500&offset=0': {
      items: [{ category: '生活', people: [{ id: 1, kind: 'painter', role: 'presenter' }] }],
    },
    '/api/videos?artist_id=2&limit=500&offset=0': {
      items: [{ category: '生活', people: [{ id: 2, kind: 'painter', role: 'author' }] }],
    },
  };
  const { ctx } = sidebarContext(responses);
  const result = await vm.runInContext('sidebarPainters()', ctx);
  assert.deepEqual(Array.from(result, p => p.video_count), [0, 1]);
});

test('coauthors remain visible while a manual choice takes precedence', () => {
  const { ctx } = sidebarContext({});
  const result = vm.runInContext(`(() => {
    const v = {people: [
      {id: 1, kind: 'painter', role: 'author', source: 'ai'},
      {id: 2, kind: 'painter', role: 'author', source: 'ai'},
      {id: 3, kind: 'painter', role: 'presenter', source: 'ai'}
    ]};
    const joint = attributedPainters(v).map(p => p.id);
    v.people[1].source = 'user';
    return [joint, attributedPainters(v).map(p => p.id)];
  })()`, ctx);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), [[1, 2], [2]]);
});

test('favorite and unfavorite actions immediately refresh the sidebar count', async () => {
  const stats = { categories: {}, total: 1, pending: 0, favorites: 0 };
  const responses = {
    '/api/stats': stats,
    '/api/folders': [], '/api/collections': [], '/api/people?kind=painter': [],
    '/api/videos/1': (opts) => {
      stats.favorites = Number(JSON.parse(opts.body).favorite);
      return {};
    },
  };
  const { ctx, nodes } = sidebarContext(responses);
  ctx.bindSeek = () => {};
  ctx.loadList = async () => {};
  ctx.openDetail = async () => {};
  const source = fs.readFileSync(path.join(__dirname, '../../src/library/static/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('function bindDetail(v)'), source.indexOf('$("#detail-close")')), ctx);
  vm.runInContext('bindDetail({content_id: 1, user_favorite: 0})', ctx);
  await nodes['#fav-btn'].onclick();
  assert.equal(nodes['#browse-favorites'].textContent, 1);
  vm.runInContext('bindDetail({content_id: 1, user_favorite: 1})', ctx);
  await nodes['#fav-btn'].onclick();
  assert.equal(nodes['#browse-favorites'].textContent, 0);
});
