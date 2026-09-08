// Real React components, synthetic APIs only. No page scripts or network resources.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://selfmailer.test/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'HTMLInputElement', 'HTMLTextAreaElement', 'Event', 'MouseEvent', 'KeyboardEvent', 'localStorage', 'sessionStorage']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true, writable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom does not implement browser top-layer/focus containment. Those require a
// real-browser smoke test; this shim tests React cancellation/cleanup wiring only.
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute('open', '');
  (this.querySelector('[autofocus], input, button') || this).focus();
};
dom.window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const React = require('react');
const { createRoot } = require('react-dom/client');
const rootDir = path.resolve(__dirname, '../..');

function loader(mocks = {}) {
  const cache = new Map();
  function load(relative) {
    let file = path.resolve(rootDir, relative);
    file = [file, file + '.ts', file + '.tsx', path.join(file, 'index.tsx')].find(f => fs.existsSync(f) && fs.statSync(f).isFile());
    if (!file) throw new Error(`Missing test import: ${relative}`);
    const key = path.relative(rootDir, file).replaceAll('\\', '/');
    if (key in mocks) return mocks[key];
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} };
    cache.set(file, module);
    const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    }, fileName: file }).outputText;
    const scopedRequire = name => name.startsWith('.') ? load(path.resolve(path.dirname(file), name)) : require(name);
    vm.runInThisContext(`(function(require,module,exports){${js}\n})`, { filename: file })(scopedRequire, module, module.exports);
    return module.exports;
  }
  return load;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function settle(ms = 0) { await React.act(async () => { await pause(ms); }); }
async function mount(node) {
  const container = document.createElement('div'); document.body.append(container);
  const root = createRoot(container);
  await React.act(async () => root.render(node));
  return async () => { await React.act(async () => root.unmount()); container.remove(); };
}
async function click(node) {
  if (!node) throw new Error('Missing click target');
  await React.act(async () => node.click());
}
async function input(node, value) {
  await React.act(async () => {
    const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value);
    node.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
module.exports = { React, loader, mount, settle, click, input, deferred };
