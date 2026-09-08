// Execute actual TypeScript function bodies with controlled state and promises.
// No browser, mailbox, or backend connection; complements the production build.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function original(relative, name, env) {
  const file = path.join(root, relative);
  const tree = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(found, name);
  const source = found.getText(tree).replace(/^export /, '');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return vm.runInNewContext(`${code}\n${name}`, env);
}
const page = 'src/pages/Mail.tsx';
const messageKey = original('src/lib/mailIdentity.ts', 'messageKey', {});
const messageGeneration = original('src/lib/mailIdentity.ts', 'messageGeneration', {});
const withoutPendingDeletes = original('src/lib/mailIdentity.ts', 'withoutPendingDeletes', { messageKey, Date });
const flush = () => new Promise(resolve => setImmediate(resolve));
const cacheExports = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'src/lib/requestCache.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: cacheExports, Map, Promise, Date, Math });
const { RequestCache } = cacheExports;
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function threadHarness() {
  const state = { result: null, calls: [], pending: [] };
  const environment = {
    activeId: 1, folder: 'INBOX', encodeURIComponent, messageGeneration,
    threadReqRef: { current: 0 }, threadCacheRef: { current: new RequestCache() },
    listContextRef: { current: 'account1' }, selRef: { current: { acc: 1, folder: 'INBOX' } },
    api: { get: url => { const d = deferred(); state.calls.push(url); state.pending.push(d); return d.promise; } },
    sameMsg: (a, b) => a.uid === b.uid && a.folder === b.folder,
    mergeThread: (base, extra) => [...base, ...extra],
    groupThreads: messages => [{ messages }],
    setOpenThread: result => { state.result = result; },
  };
  const latest = { uid: '42', folder: 'INBOX', uidvalidity: 100, account_id: 1 };
  return { state, environment, conv: { latest, messages: [latest], count: 2 }, augment: original(page, 'augmentThread', environment) };
}

test('thread augmentation shares in-flight work and caches quick reopenings', async () => {
  const { state, conv, augment } = threadHarness();
  augment(conv); augment(conv); await flush();
  assert.equal(state.calls.length, 1);
  assert.ok(state.calls[0].includes('uidvalidity=100'));
  state.pending[0].resolve([{ uid: 'sent-1', folder: 'Sent' }]); await flush();
  assert.equal(state.result.messages.length, 2);
  augment(conv); await flush(); assert.equal(state.calls.length, 1);
});

test('late thread augmentation cannot reopen a closed thread or replace another account', async () => {
  for (const change of ['close', 'account']) {
    const { state, conv, augment, environment } = threadHarness();
    augment(conv); await flush();
    if (change === 'close') environment.threadReqRef.current++;
    else { environment.selRef.current = { acc: 2, folder: 'INBOX' }; environment.listContextRef.current = 'account2'; }
    state.pending[0].resolve([{ uid: 'sent-1', folder: 'Sent' }]); await flush();
    assert.equal(state.result, null);
  }
});

test('closing a thread advances its request guard; mutations invalidate cache', () => {
  const { environment } = threadHarness();
  let clears = 0;
  const env = { ...environment, setOpenThreadRaw() {}, invalidateThreadCache: () => clears++ };
  const update = original(page, 'setOpenThread', env);
  update(null); assert.equal(environment.threadReqRef.current, 1);
  update(previous => previous); assert.equal(clears, 1);
  original(page, 'setMessages', { ...env, setMessagesRaw() {} })([]);
  assert.equal(clears, 2);
});
function listHarness() {
  const state = { messages: [], errors: [], loading: [], pending: [] };
  const common = {
    Set, Map, Math, JSON, encodeURIComponent,
    selRef: { current: { acc: 1, folder: 'INBOX' } }, pageRef: { current: 1 },
    listVersionRef: { current: 0 }, listContextRef: { current: 'account-1' },
    setMessages: value => { state.messages = typeof value === 'function' ? value(state.messages) : value; },
    setLoading: value => state.loading.push(value), setErr: value => state.errors.push(value),
    setOpen() {}, setOpenThread() {}, setSelected() {}, setSelectAllFolder() {}, setPage() {},
    setSearchTruncated() {}, setLoadingMore() {}, warmBodies() {}, bgSync() {},
    fetchPage: (acc, fol, p) => { const d = deferred(); state.pending.push({ acc, fol, p, ...d }); return d.promise; },
  };
  common.beginListLoad = original(page, 'beginListLoad', common);
  return { common, state };
}

test('message identity includes account, folder, generation and UID', () => {
  const m = { uid: '7', folder: 'INBOX', uidvalidity: 100 };
  const keys = [messageKey(m, 1, ''), messageKey(m, 2, ''), messageKey({ ...m, folder: 'Sent' }, 1, ''), messageKey({ ...m, uidvalidity: 101 }, 1, '')];
  assert.equal(new Set(keys).size, 4);
  assert.equal(messageGeneration(m), '&uidvalidity=100');
});

test('late reload cannot replace another account or clear its loading state', async () => {
  const { common, state } = listHarness();
  original(page, 'reload', { ...common, sel: { acc: 1, folder: 'INBOX' } })();
  common.selRef.current = { acc: 2, folder: 'INBOX' };
  common.listContextRef.current = 'account-2';
  original(page, 'reload', { ...common, sel: { acc: 2, folder: 'INBOX' } })();
  state.pending[0].resolve([{ uid: '7', account_id: 1 }]);
  await flush();
  assert.equal(state.messages.length, 0);
  assert.equal(state.loading.at(-1), true);
  state.pending[1].resolve([{ uid: '9', account_id: 2 }]);
  await flush();
  assert.equal(state.messages[0].account_id, 2);
  assert.equal(state.loading.at(-1), false);
});

test('late pagination response and error cannot replace newer page', async () => {
  const { common, state } = listHarness();
  const render = { ...common, sel: { acc: 1, folder: 'INBOX' }, page: 1, totalPages: 5 };
  const goPage = original(page, 'goPage', render);
  goPage(2); goPage(3);
  state.pending[1].resolve([{ uid: 'third-page' }]);
  await flush();
  state.pending[0].reject(new Error('late error'));
  await flush();
  assert.equal(state.messages[0].uid, 'third-page');
  assert.ok(!state.errors.includes('late error'));
});

test('filter change invalidates in-flight list load immediately', () => {
  const { common } = listHarness();
  const current = common.beginListLoad(1, 'INBOX', 1);
  assert.equal(current(), true);
  common.listContextRef.current = 'new-filter';
  assert.equal(current(), false);
});

test('deletion remains bound to displayed account and generation after confirmation', async () => {
  const { common, state } = listHarness();
  const confirm = deferred(), urls = [];
  const env = { ...common, activeId: 2, folder: 'INBOX', askConfirm: () => confirm.promise,
    t: value => value, messageGeneration, api: { del: async url => urls.push(url) }, bumpUnseen() {}, open: null,
    messageKey, withoutPendingDeletes, Date, pendingDeletesRef: { current: new Map() }, setDeleting() {},
    threadReqRef: { current: 0 }, openThread: null, setMobilePane() {} };
  env.deleteOptimistically = original(page, 'deleteOptimistically', env);
  const action = original(page, 'del', env)({ uid: '7', folder: 'Sent', account_id: 1, uidvalidity: 100, seen: true });
  common.selRef.current = { acc: 3, folder: 'INBOX' };
  state.messages = [{ uid: '7', account_id: 3 }];
  confirm.resolve(true);
  await action;
  assert.equal(urls[0], '/mail/1/messages/7?folder=Sent&uidvalidity=100');
  assert.equal(state.messages[0].account_id, 3);
});

test('ThreadReader separates same UID across folders and deduplicates concurrent loads', async () => {
  const requests = [], pending = [];
  const env = {
    details: {}, errUid: {}, loadingUid: new Set(), pendingDetails: { current: new Set() },
    accountId: 1, msgFolder: m => m.folder, keyFor: m => messageKey(m, 1, 'INBOX'), messageGeneration,
    seenSent: { current: new Set() }, onSeen() {}, encodeURIComponent,
    api: { get: url => { requests.push(url); const d = deferred(); pending.push(d); return d.promise; } },
  };
  for (const key of ['details', 'errUid', 'loadingUid']) env['set' + key[0].toUpperCase() + key.slice(1)] = fn => { env[key] = fn(env[key]); };
  const load = original('src/components/ThreadReader.tsx', 'loadDetail', env);
  const inbox = { uid: '7', folder: 'INBOX', uidvalidity: 100, seen: true };
  const sent = { ...inbox, folder: 'Sent' };
  const a = load(inbox), duplicate = load(inbox), b = load(sent);
  assert.equal(requests.length, 2);
  pending[1].resolve({ text: 'SENT' }); pending[0].resolve({ text: 'INBOX' });
  await Promise.all([a, b, duplicate]);
  assert.equal(env.details[env.keyFor(inbox)].text, 'INBOX');
  assert.equal(env.details[env.keyFor(sent)].text, 'SENT');
  assert.equal(env.pendingDetails.current.size, 0);
});

test('headers returned by fetchPage carry their source account and folder', async () => {
  const env = { api: { get: async () => [{ uid: '7', uidvalidity: 101 }] }, PAGE_SIZE: 50, pinParam: '', unreadParam: '', encodeURIComponent };
  const rows = await original(page, 'fetchPage', env)(42, 'Archive', 1);
  assert.equal(rows[0].account_id, 42);
  assert.equal(rows[0].folder, 'Archive');
});

test('auth display describes domain authentication without claiming account safety', () => {
  const view = original(page, 'authView', {});
  assert.equal(view({ verdict: 'pass', dmarc: 'pass' }, true).short, 'Domain bestätigt');
  const suspicious = view({ verdict: 'fail', self_spoof: true }, true);
  assert.ok(!suspicious.text.includes('NICHT gehackt'));
  assert.equal(view(null, true).short, 'Nicht prüfbar');
});

test('overlapping background sync requests are deduplicated', async () => {
  const { common } = listHarness();
  const network = deferred();
  let calls = 0;
  const env = { ...common, api: { post: () => { calls++; return network.promise; } },
    bgSyncPendingRef: { current: new Set() }, bgSyncSeqRef: { current: new Map() },
    bgSyncFailRef: { current: new Map() }, setSyncing() {} };
  const sync = original(page, 'bgSync', env);
  sync(1, 'INBOX'); sync(1, 'INBOX');
  assert.equal(calls, 1);
  // Changing account before completion also prevents another list fetch.
  common.selRef.current = { acc: 2, folder: 'INBOX' };
  network.resolve();
  await flush();
  assert.equal(env.bgSyncPendingRef.current.size, 0);
});

test('pending delete suppresses stale rows but not another account or generation', () => {
  const row = { uid: '7', folder: 'INBOX', uidvalidity: 100, account_id: 1 };
  const pending = new Map([[messageKey(row, 1, 'INBOX'), Infinity]]);
  const result = withoutPendingDeletes([row, { ...row, account_id: 2 }, { ...row, uidvalidity: 101 }], pending, 1, 'INBOX');
  assert.equal(result.length, 2);
  pending.delete(messageKey(row, 1, 'INBOX'));
  assert.equal(withoutPendingDeletes([row], pending, 1, 'INBOX').length, 1);
});

function deletionHarness() {
  const { common, state } = listHarness();
  const response = deferred();
  const row = { uid: '7', account_id: 1, folder: 'INBOX', uidvalidity: 100, seen: false };
  state.messages = [row];
  state.deleting = 0;
  state.badges = 0;
  const env = { ...common, Date, Infinity, messageKey, messageGeneration, withoutPendingDeletes,
    pendingDeletesRef: { current: new Map() }, threadReqRef: { current: 0 }, openThread: null, open: null,
    api: { del: () => response.promise }, setDeleting: f => { state.deleting = f(state.deleting); },
    bumpUnseen: () => { state.badges++; }, setMobilePane() {} };
  const remove = original(page, 'deleteOptimistically', env);
  return { env, state, row, response, remove };
}

test('single delete reacts before server response and success retains late-response protection', async () => {
  const { env, state, row, response, remove } = deletionHarness();
  const action = remove(row, 1, 'INBOX');
  assert.equal(state.messages.length, 0);
  assert.equal(state.deleting, 1);
  assert.equal(state.badges, 0); // no success claim before acknowledgement
  response.resolve();
  await action;
  assert.equal(state.deleting, 0);
  assert.equal(state.badges, 1);
  assert.equal(withoutPendingDeletes([row], env.pendingDeletesRef.current, 1, 'INBOX').length, 0);
});

test('failed delete restores the row and keeps the error visible', async () => {
  const { state, row, response, remove } = deletionHarness();
  const action = remove(row, 1, 'INBOX');
  response.reject(new Error('Gmail offline'));
  await action;
  assert.equal(state.messages.length, 1);
  assert.equal(state.errors.at(-1), 'Gmail offline');
  assert.equal(state.deleting, 0);
  assert.equal(state.badges, 0);
});


test('failed delete restores the same thread despite cache invalidation, but never reopens a closed thread', async () => {
  for (const changed of [false, true]) {
    const { env, state, row, response } = deletionHarness();
    const companion = { ...row, uid: '8' };
    state.thread = { messages: [row, companion], latest: companion, count: 2 };
    env.openThread = state.thread;
    env.threadCacheRef = { current: new RequestCache() };
    env.invalidateThreadCache = original(page, 'invalidateThreadCache', env);
    env.setMessagesRaw = update => { state.messages = update(state.messages); };
    env.setOpenThreadRaw = update => { state.thread = update(state.thread); };
    env.setMessages = original(page, 'setMessages', env);
    env.setOpenThread = original(page, 'setOpenThread', env);
    const action = original(page, 'deleteOptimistically', env)(row, 1, 'INBOX');
    assert.equal(state.thread.messages.length, 1);
    if (changed) env.setOpenThread(null);
    response.reject(new Error('offline'));
    await action;
    assert.equal(state.messages.length, 1);
    if (changed) assert.equal(state.thread, null);
    else assert.equal(state.thread.messages.length, 2);
  }
});

test('failed delete never restores old rows into a switched account or new generation', async () => {
  for (const changed of ['account', 'generation']) {
    const { env, state, row, response, remove } = deletionHarness();
    const action = remove(row, 1, 'INBOX');
    const replacement = { ...row, account_id: changed === 'account' ? 2 : 1, uidvalidity: 101 };
    if (changed === 'account') env.selRef.current = { acc: 2, folder: 'INBOX' };
    state.messages = [replacement];
    response.reject(new Error('offline'));
    await action;
    assert.equal(state.messages.length, 1);
    assert.equal(state.messages[0].uidvalidity, 101);
  }
});

test('prefetch is limited to three headers and deduplicates identical attempts', async () => {
  const requests = [];
  const env = { messageKey, prefetchedRef: { current: new Set() },
    api: { post: (url, body) => { requests.push(body); return Promise.resolve(); } } };
  const warm = original(page, 'warmBodies', env);
  const rows = Array.from({ length: 50 }, (_, i) => ({ uid: String(i + 1), uidvalidity: 101 }));
  warm(1, 'INBOX', rows); warm(1, 'INBOX', rows);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].uids.length, 3);
  assert.equal(requests[0].uidvalidity, 101);
});
