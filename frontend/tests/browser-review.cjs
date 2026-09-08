const assert = require('node:assert/strict');
const test = require('node:test');
const { React, loader, mount, settle, click, input, deferred } = require('./support/dom.cjs');
const h = React.createElement;
const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.includes(text));
const summary = id => ({ id, title: `Note ${id}`, pinned: false, color: '', created_at: '2026-09-08T10:00:00Z', updated_at: '2026-09-08T10:00:00Z' });
function setup(api, otherMocks = {}) {
  localStorage.clear(); localStorage.setItem('selfmailer.lang', 'en');
  const load = loader({ 'src/lib/api.ts': { api, fetchHealth: async () => ({ version: 'test' }) }, ...otherMocks });
  const { LangProvider } = load('src/lib/i18n');
  const { DialogHost } = load('src/lib/dialog');
  return { load, wrap: component => h(LangProvider, null, component, h(DialogHost)) };
}
async function cancelDialog() {
  await React.act(async () => document.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true })));
}

test('notes overview never fetches bodies; explicit open loads detail', async () => {
  const calls = [];
  const { load, wrap } = setup({ get: async url => {
    calls.push(url);
    return url.startsWith('/notes/summaries') ? [summary(1)] : { ...summary(1), body: 'synthetic private text' };
  } });
  const stop = await mount(wrap(h(load('src/pages/Notes').Notes)));
  try {
    await settle(10);
    assert.deepEqual(calls, ['/notes/summaries?q=']);
    assert.equal(document.querySelector('textarea'), null);
    await click(button('Note 1'));
    assert.equal(calls.at(-1), '/notes/1');
    assert.equal(document.querySelector('textarea').value, 'synthetic private text');
    assert.ok(!document.querySelector('.md-list').textContent.includes('synthetic private text'));
  } finally { await stop(); }
});

test('dirty note guards navigation, local cancel and reload without storing its text', async () => {
  const { load, wrap } = setup({ get: async () => [] });
  const guard = { current: null };
  const stop = await mount(wrap(h(load('src/pages/Notes').Notes, { leaveGuard: guard })));
  try {
    await click(button('New note'));
    await input(document.querySelector('textarea'), 'synthetic draft');
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
    let navigation;
    await React.act(async () => { navigation = guard.current(); });
    assert.ok(document.querySelector('dialog').textContent.includes('Discard unsaved'));
    await cancelDialog(); assert.equal(await navigation, false);
    assert.equal(document.querySelector('textarea').value, 'synthetic draft');
    await click(button('Cancel')); await cancelDialog();
    assert.equal(document.querySelector('textarea').value, 'synthetic draft');
    assert.ok(!JSON.stringify({ ...localStorage, ...sessionStorage }).includes('synthetic draft'));
    await click(button('Cancel')); await click(button('OK'));
    assert.equal(document.querySelector('textarea'), null);
  } finally { await stop(); }
  assert.equal(guard.current, null);
});

test('failed save preserves draft; duplicate saves and leaving during a write are blocked', async () => {
  const pending = deferred(); let writes = 0;
  const { load, wrap } = setup({ get: async () => [], post: () => { writes++; return pending.promise; } });
  const guard = { current: null };
  const stop = await mount(wrap(h(load('src/pages/Notes').Notes, { leaveGuard: guard })));
  try {
    await click(button('New note')); await input(document.querySelector('textarea'), 'keep on failure');
    const saveButton = button('Save note'); await click(saveButton); await click(saveButton);
    assert.equal(writes, 1); assert.equal(await guard.current(), false);
    assert.equal(document.querySelector('textarea').disabled, true);
    await React.act(async () => pending.reject(new Error('synthetic save failure')));
    assert.equal(document.querySelector('textarea').value, 'keep on failure');
    assert.match(document.querySelector('[role=alert]').textContent, /synthetic save failure/);
    assert.equal(document.querySelector('textarea').disabled, false);
  } finally { await stop(); }
});

test('saved notes become clean; a late detail cannot replace a newer selection', async () => {
  const first = deferred(), second = deferred();
  const { load, wrap } = setup({
    get: url => url.startsWith('/notes/summaries') ? Promise.resolve([summary(1), summary(2)]) : url === '/notes/1' ? first.promise : second.promise,
    patch: async (_url, body) => ({ ...summary(2), ...body }),
  });
  const guard = { current: null };
  const stop = await mount(wrap(h(load('src/pages/Notes').Notes, { leaveGuard: guard })));
  try {
    await settle(10); await click(button('Note 1')); await click(button('Note 2'));
    await React.act(async () => second.resolve({ ...summary(2), body: 'newer' }));
    await React.act(async () => first.resolve({ ...summary(1), body: 'stale' }));
    assert.equal(document.querySelector('textarea').value, 'newer');
    await input(document.querySelector('textarea'), 'saved change'); await click(button('Save note'));
    assert.equal(await guard.current(), true);
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
  } finally { await stop(); }
});

test('notes search ignores old results during the debounce interval', async () => {
  const old = deferred();
  const { load, wrap } = setup({ get: url => url === '/notes/summaries?q=' ? old.promise : Promise.resolve([summary(2)]) });
  const stop = await mount(wrap(h(load('src/pages/Notes').Notes)));
  try {
    await settle(10); await input(document.querySelector('.md-search input'), 'needle');
    await React.act(async () => old.resolve([summary(1)]));
    assert.equal(button('Note 1'), undefined);
    await settle(280); assert.ok(button('Note 2'));
  } finally { await stop(); }
});

test('confirmation queue resolves both callers; cancel is focused and hotkeys do not escape', async () => {
  const { load, wrap } = setup({});
  const stop = await mount(wrap(h('button', { id: 'opener' }, 'opener')));
  const opener = document.getElementById('opener'); opener.focus();
  let a, b, keys = 0;
  const onKey = () => keys++;
  window.addEventListener('keydown', onKey);
  try {
    const { confirmDialog, promptDialog } = load('src/lib/dialog');
    await React.act(async () => { a = confirmDialog('First'); b = promptDialog('Second', 'initial'); });
    assert.match(document.activeElement.textContent, /Cancel/);
    await React.act(async () => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true })));
    assert.equal(keys, 0);
    await cancelDialog(); assert.equal(await a, false);
    assert.equal(document.querySelector('dialog').getAttribute('aria-label'), 'Second');
    await input(document.querySelector('dialog input'), 'answer'); await click(button('OK'));
    assert.equal(await b, 'answer'); assert.equal(document.querySelector('dialog'), null);
    assert.equal(document.activeElement.id, 'opener');
  } finally { window.removeEventListener('keydown', onKey); await stop(); }
});

test('unmount cancels pending dialog promises', async () => {
  const { load, wrap } = setup({});
  const stop = await mount(wrap(null));
  let first, second;
  await React.act(async () => { first = load('src/lib/dialog').confirmDialog('a'); second = load('src/lib/dialog').promptDialog('b'); });
  await stop(); assert.equal(await first, false); assert.equal(await second, null);
});

test('calendar editor is named, dates are labeled, cancel event closes without writing', async () => {
  let writes = 0;
  const { load, wrap } = setup({ get: async url => url === '/calendar/hidden' ? { keys: [] } : url === '/contacts/birthday-calendar' ? {} : [], post: async () => writes++ });
  const stop = await mount(wrap(h(load('src/pages/Calendar').Calendar)));
  try {
    await click(button('New event'));
    const dialog = document.querySelector('dialog'); assert.ok(dialog?.open);
    assert.ok(dialog.getAttribute('aria-label'));
    assert.equal(document.activeElement.getAttribute('aria-label'), 'Title');
    const dates = [...dialog.querySelectorAll('input[type=datetime-local]')]; assert.equal(dates.length, 2);
    for (const date of dates) assert.ok(date.labels.length);
    await cancelDialog(); assert.equal(document.querySelector('dialog'), null); assert.equal(writes, 0);
  } finally { await stop(); }
});

const rule = (id, account, value) => ({ id, account_id: account, field: 'from', value, target_folder: '', mark_read: true, star: false, delete_msg: false });
async function chooseAccount(value) {
  await React.act(async () => {
    const select = document.querySelector('select[aria-label]'); select.value = String(value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
test('rules ignore late rules and folders from another account', async () => {
  const oldRules = deferred(), oldFolders = deferred();
  const { load, wrap } = setup({ get: url => {
    if (url === '/accounts') return Promise.resolve([{ id: 1, email: 'one@example.test' }, { id: 2, email: 'two@example.test' }]);
    if (url === '/mail/1/rules') return oldRules.promise;
    if (url === '/mail/1/folders') return oldFolders.promise;
    return Promise.resolve(url.endsWith('/rules') ? [rule(2, 2, 'current-rule')] : ['current-folder']);
  } });
  const stop = await mount(wrap(h(load('src/pages/Rules').Rules)));
  try {
    await chooseAccount(2);
    await React.act(async () => { oldRules.resolve([rule(1, 1, 'stale-rule')]); oldFolders.resolve(['stale-folder']); });
    assert.match(document.body.textContent, /current-rule/); assert.match(document.body.textContent, /current-folder/);
    assert.ok(!document.body.textContent.includes('stale-rule')); assert.ok(!document.body.textContent.includes('stale-folder'));
  } finally { await stop(); }
});

test('a late rules save cannot reset the new account form or status', async () => {
  const pending = deferred(); let writes = [];
  const { load, wrap } = setup({
    get: async url => url === '/accounts' ? [{ id: 1, email: 'one@example.test' }, { id: 2, email: 'two@example.test' }] : url.endsWith('/rules') ? [rule(1, 1, 'old-rule')] : [],
    patch: (url, form) => { writes.push({ url, form }); return pending.promise; },
  });
  const stop = await mount(wrap(h(load('src/pages/Rules').Rules)));
  try {
    await click(button('Edit')); await React.act(async () => document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(writes[0].url, '/mail/1/rules/1');
    await chooseAccount(2); const field = document.querySelector('form input'); await input(field, 'new unsaved input');
    await React.act(async () => pending.resolve({}));
    assert.equal(field.value, 'new unsaved input');
  } finally { await stop(); }
});

test('thread request cache deduplicates, isolates keys, expires and evicts', async () => {
  const { RequestCache } = loader()('src/lib/requestCache');
  let now = 0, count = 0;
  const cache = new RequestCache(2, 100, () => now);
  const fetcher = async () => ++count;
  assert.equal(await cache.get('account1/folder/uidvalidity1/uid1', fetcher), 1);
  assert.equal(await cache.get('account1/folder/uidvalidity1/uid1', fetcher), 1);
  assert.equal(await cache.get('account2/folder/uidvalidity1/uid1', fetcher), 2);
  assert.equal(await cache.get('account1/folder/uidvalidity2/uid1', fetcher), 3);
  assert.equal(await cache.get('account1/folder/uidvalidity1/uid1', fetcher), 4);
  now = 101; assert.equal(await cache.get('account1/folder/uidvalidity1/uid1', fetcher), 5);
  const pending = deferred(); const first = cache.get('pending', () => pending.promise);
  now += 1000;
  assert.equal(first, cache.get('pending', fetcher));
  cache.clear(); pending.resolve('old'); await first;
  assert.equal(await cache.get('pending', fetcher), 6);
  await assert.rejects(cache.get('error', () => Promise.reject(new Error('test'))));
  assert.equal(await cache.get('error', fetcher), 7);
});
