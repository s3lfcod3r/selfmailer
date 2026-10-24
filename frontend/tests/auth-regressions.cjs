const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
// TypeScript 7 (nativer Compiler) hat keine JS-API mehr - Tests nutzen die
// JS-Version unter dem Alias typescript-api (siehe package.json).
const ts = require('typescript-api');

function logout(env) {
  const tree = ts.createSourceFile('App.tsx', fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8'), ts.ScriptTarget.Latest, true);
  let fn;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'logout') fn = node;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(fn);
  const code = ts.transpileModule(fn.getText(tree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return vm.runInNewContext(code + '\nlogout', env);
}

function environment(post) {
  const state = { user: { id: 1 }, error: '', removed: [] };
  const env = {
    logoutBusy: false, mayLeave: async () => true, api: { post },
    setLogoutBusy: value => { env.logoutBusy = value; },
    setLogoutError: value => { state.error = value; },
    setUser: value => { state.user = value; },
    localStorage: { removeItem: key => state.removed.push(key) },
  };
  return { env, state };
}

test('logout waits for server revocation and prevents duplicate requests', async () => {
  let resolve, calls = 0;
  const pending = new Promise(done => { resolve = done; });
  const { env, state } = environment(path => { assert.equal(path, '/auth/logout'); calls++; return pending; });
  const run = logout(env);
  const first = run();
  await new Promise(done => setImmediate(done));
  assert.equal(calls, 1);
  assert.ok(state.user);
  await run();
  assert.equal(calls, 1);
  resolve({ ok: true }); await first;
  assert.equal(state.user, null);
  assert.deepEqual(state.removed, ['selfmailer.token']);
  assert.equal(env.logoutBusy, false);
});

test('failed logout preserves session and makes retry available', async () => {
  const { env, state } = environment(async () => { throw new Error('synthetic offline'); });
  await logout(env)();
  assert.ok(state.user);
  assert.equal(state.error, 'synthetic offline');
  assert.equal(state.removed.length, 0);
  assert.equal(env.logoutBusy, false);
});

test('cancelled leave guard does not revoke the session', async () => {
  let calls = 0;
  const { env, state } = environment(async () => { calls++; });
  env.mayLeave = async () => false;
  await logout(env)();
  assert.equal(calls, 0);
  assert.ok(state.user);
});
