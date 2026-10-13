const assert = require('node:assert/strict');
const test = require('node:test');
const { React, loader, mount, click, input, deferred } = require('./support/dom.cjs');
const h = React.createElement;
const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.includes(text));
const row = uid => ({ uid, uidvalidity: 321, folder: 'INBOX', from: 'shop@example.org', subject: 'Invoice', date: '2026-10-01', seen: true, flagged: false });
const response = (extra = {}) => ({ items: [row('250'), row('249')], total_count: 250, complete: true, truncated: true, ...extra });
async function select(node, value) {
  await React.act(async () => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function setup(api, extra = {}) {
  const load = loader({ 'src/lib/api.ts': { api } });
  return mount(h(load('src/components/MailCleanup').MailCleanup, {
    account: 1, folder: 'INBOX', folders: [{ name: 'INBOX' }, { name: 'Archive' }, { name: 'Trash' }], labels: [],
    source: row('1'), de: false, onClose() {}, onChanged() {}, onRead() {}, ...extra,
  }));
}

test('cleanup searches the whole folder and pages beyond loaded headers', async () => {
  const calls = [];
  const stop = await setup({ get: async url => { calls.push(url); return response(); } });
  try {
    await click(button('Find all')); await click(button('More matches'));
    const first = new URL(calls[0], 'https://test');
    assert.equal(first.searchParams.get('from'), 'shop@example.org');
    assert.equal(first.searchParams.get('flagged'), 'false');
    assert.equal(first.searchParams.get('folder'), 'INBOX');
    assert.equal(first.searchParams.get('limit'), '100');
    assert.equal(new URL(calls[1], 'https://test').searchParams.get('offset'), '100');
    await input(document.querySelector('[aria-label="Search value"]'), 'other@example.org');
    assert.equal(button('More matches'), undefined);
    await click(button('Find all')); assert.equal(new URL(calls[2], 'https://test').searchParams.get('offset'), '0');
  } finally { await stop(); }
});

test('cleanup moves only preview UIDs with generation after explicit review', async () => {
  const writes = []; let changed = 0;
  const stop = await setup({ get: async () => response(), post: async (url, data) => writes.push([url, data]) }, { onChanged() { changed++; } });
  try {
    await click(button('Find all')); await select(document.querySelectorAll('select')[1], 'Trash');
    await click(button('Clean up this preview')); assert.equal(writes.length, 0);
    await click(button('Move these 2'));
    assert.deepEqual(writes, [['/mail/1/messages/batch-move', { folder: 'INBOX', dest: 'Trash', uids: ['250', '249'], uidvalidity: 321 }]]);
    assert.equal(changed, 1); assert.equal(button('Move these 2'), undefined);
  } finally { await stop(); }
});

test('incomplete preview cannot be cleaned up', async () => {
  const stop = await setup({ get: async () => response({ complete: false, errors: 1 }) });
  try {
    await click(button('Find all')); await select(document.querySelectorAll('select')[1], 'Archive');
    assert.equal(button('Clean up this preview').disabled, true);
    assert.match(document.querySelector('[role=status]').textContent, /incomplete/);
  } finally { await stop(); }
});

test('failed move requires a fresh preview and never creates a rule', async () => {
  const writes = [];
  const stop = await setup({ get: async () => response(), post: async url => { writes.push(url); throw new Error('synthetic failure'); } });
  try {
    await click(button('Find all')); await select(document.querySelectorAll('select')[1], 'Archive');
    await click(document.querySelectorAll('input[type=checkbox]')[2]);
    await click(button('Clean up this preview')); await click(button('Move these 2'));
    assert.deepEqual(writes, ['/mail/1/messages/batch-move']);
    assert.match(document.querySelector('[role=alert]').textContent, /refresh the preview/);
    assert.equal(button('Clean up this preview'), undefined);
  } finally { await stop(); }
});

test('rule failure reports that moving succeeded without repeating the move', async () => {
  const writes = [];
  const stop = await setup({ get: async () => response(), post: async (url, data) => {
    writes.push([url, data]); if (url.endsWith('/rules')) throw new Error('rule unavailable');
  } });
  try {
    await click(button('Find all')); await select(document.querySelectorAll('select')[1], 'Archive');
    await click(document.querySelectorAll('input[type=checkbox]')[2]);
    await click(button('Clean up this preview')); await click(button('Move these 2'));
    assert.equal(writes.length, 2); assert.equal(writes[1][1].target_folder, 'Archive');
    assert.equal(writes[1][1].delete_msg, undefined);
    assert.match(document.querySelector('[role=alert]').textContent, /Messages were moved/);
  } finally { await stop(); }
});

test('saved views persist criteria without launching actions', async () => {
  localStorage.clear(); let requests = 0;
  let stop = await setup({ get: async () => { requests++; return response(); } });
  await input(document.querySelector('[aria-label="Saved view name"]'), 'Shop');
  await click(button('Save view')); await stop();
  stop = await setup({ get: async () => { requests++; return response(); } });
  try {
    await select(document.querySelectorAll('select')[0], '0');
    assert.equal(document.querySelector('[aria-label="Search value"]').value, 'shop@example.org');
    assert.equal(requests, 0);
    await click(button('Remove views')); assert.equal(localStorage.getItem('selfmailer.savedCleanup:1:INBOX'), null);
  } finally { await stop(); localStorage.clear(); }
});

test('duplicate move clicks and leaving during a write are blocked', async () => {
  const pending = deferred(); let writes = 0; let closes = 0;
  const stop = await setup({ get: async () => response(), post: () => { writes++; return pending.promise; } }, { onClose() { closes++; } });
  try {
    await click(button('Find all')); await select(document.querySelectorAll('select')[1], 'Archive');
    await click(button('Clean up this preview')); const action = button('Move these 2');
    await click(action); await click(action); await click(button('Close'));
    assert.equal(writes, 1); assert.equal(closes, 0);
    await React.act(async () => pending.resolve({ ok: true }));
  } finally { await stop(); }
});
