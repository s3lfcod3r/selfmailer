const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const options = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS };
const policyExports = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'src/lib/syncBackoff.ts'), 'utf8'), {
  compilerOptions: options,
}).outputText, { exports: policyExports, Map, Date, Math });
const { SyncBackoff } = policyExports;
const tree = ts.createSourceFile('Mail.tsx', fs.readFileSync(path.join(root, 'src/pages/Mail.tsx'), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function original(name, env) {
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node;
    ts.forEachChild(node, visit);
  }
  visit(tree); assert.ok(found, name);
  const code = ts.transpileModule(found.getText(tree), { compilerOptions: options }).outputText;
  return vm.runInNewContext(`${code}\n${name}`, env);
}
const flush = () => new Promise(setImmediate);
const ref = current => ({ current });
function harness(result = { busy: true }) {
  const counts = { posts: 0, lists: 0, prefetch: 0, counts: 0, renders: 0 };
  let now = 1000;
  const policy = new SyncBackoff(() => now);
  const env = {
    Map, Set, encodeURIComponent,
    bgSyncPendingRef: ref(new Set()), bgSyncSeqRef: ref(new Map()),
    listContextRef: ref('synthetic'), selRef: ref({ acc: 9, folder: 'INBOX' }), pageRef: ref(1),
    syncBackoffRef: ref(policy), listAllRef: ref(false), searchActiveRef: ref(false),
    api: { post: async () => { counts.posts++; if (result instanceof Error) throw result; return result; } },
    fetchPage: async () => { counts.lists++; return [{ uid: 'synthetic' }]; },
    beginListLoad: () => { const context = env.listContextRef.current; return () => context === env.listContextRef.current; },
    setMessages: () => { counts.renders++; }, warmBodies: () => { counts.prefetch++; },
    refreshCounts: () => { counts.counts++; }, setSyncing() {}, setLoading() {}, setLoadingMore() {}, setSearchTruncated() {},
  };
  env.bgSyncDue = original('bgSyncDue', env);
  return { counts, env, policy, sync: original('bgSync', env), advance: ms => { now += ms; } };
}

test('HTTP 200 busy preserves cache and does not prefetch, recount or reset cooldown', async () => {
  const h = harness();
  h.sync(9, 'INBOX'); await flush();
  assert.deepEqual(h.counts, { posts: 1, lists: 0, prefetch: 0, counts: 0, renders: 0 });
  assert.equal(h.policy.due('9:INBOX'), false);
  h.sync(9, 'INBOX'); assert.equal(h.counts.posts, 1);
  h.advance(20000); h.sync(9, 'INBOX'); await flush();
  assert.equal(h.counts.posts, 2);
  h.advance(39999); assert.equal(h.policy.due('9:INBOX'), false);
  h.advance(1); assert.equal(h.policy.due('9:INBOX'), true);
});

test('manual refresh bypasses cooldown, not the in-flight guard', async () => {
  const h = harness();
  h.sync(9, 'INBOX'); h.sync(9, 'INBOX', 1, true);
  assert.equal(h.counts.posts, 1); await flush();
  h.sync(9, 'INBOX', 1, true); await flush();
  assert.equal(h.counts.posts, 2);
});

test('only completed sync resets cooldown and starts follow-up work', async () => {
  const h = harness({ new: 0 });
  h.policy.record('9:INBOX', 'busy');
  h.sync(9, 'INBOX', 1, true); await flush();
  assert.equal(h.policy.due('9:INBOX'), true);
  assert.deepEqual(h.counts, { posts: 1, lists: 1, prefetch: 1, counts: 1, renders: 1 });
});

test('transport failure does not start further work and backs off', async () => {
  const h = harness(new Error('synthetic'));
  h.sync(9, 'INBOX'); await flush();
  assert.equal(h.policy.due('9:INBOX'), false);
  assert.equal(h.counts.lists, 0);
  assert.equal(h.env.bgSyncPendingRef.current.size, 0);
});

test('cooldown is bounded and isolated by account/folder', () => {
  let now = 1000;
  const p = new SyncBackoff(() => now);
  for (const delay of [20000, 40000, 80000, 120000, 120000]) {
    p.record('9:INBOX', 'busy');
    assert.equal(p.due('10:INBOX'), true); assert.equal(p.due('9:Sent'), true);
    now += delay - 1; assert.equal(p.due('9:INBOX'), false);
    now++; assert.equal(p.due('9:INBOX'), true);
  }
  p.record('9:INBOX', 'success');
  assert.equal(p.due('9:INBOX'), true);
});

test('server event reads cache even during cooldown and never performs IMAP sync', async () => {
  const h = harness();
  h.policy.record('9:INBOX', 'busy');
  original('refreshCachedFolder', h.env)(9, 'INBOX', 1); await flush();
  assert.deepEqual(h.counts, { posts: 0, lists: 1, prefetch: 0, counts: 0, renders: 1 });
  assert.equal(h.policy.due('9:INBOX'), false);
});

test('late event cache response cannot replace another account', async () => {
  const h = harness();
  original('refreshCachedFolder', h.env)(9, 'INBOX', 1);
  h.env.listContextRef.current = 'another-account'; await flush();
  assert.equal(h.counts.renders, 0);
});

test('ordinary badge refresh reads cached counts; explicit refresh requests live counts', async () => {
  const urls = [], pending = [];
  const env = { countSeqRef: ref(new Map()),
    countRequestsRef: ref({ get: (url, fetch) => fetch() }),
    api: { get: url => { urls.push(url); return new Promise(resolve => pending.push(resolve)); } },
    setFoldersByAcc() {},
  };
  const refresh = original('refreshCounts', env);
  refresh(9); refresh(9, true);
  assert.deepEqual(urls, ['/mail/9/folders/counts', '/mail/9/folders/counts?live=1']);
  pending.forEach(resolve => resolve([])); await flush();
});
