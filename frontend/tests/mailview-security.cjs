// Sicherheitsregeln fuer die Mail-Ansicht (Befunde 6, 7, 8, 9, 17 der
// Durchsicht vom 27.09.2026). Laeuft ohne Browser und ohne Netz: der Loader
// aus support/dom.cjs uebersetzt die echten TypeScript-Quellen und fuehrt sie
// gegen ein jsdom-Fenster aus.
const assert = require('node:assert/strict');
const test = require('node:test');
const { loader } = require('./support/dom.cjs');

const mv = loader()('src/lib/mailview');

test('buildSrcDoc setzt IMMER eine CSP - auch wenn Bilder erlaubt sind', () => {
  const html = '<p>hallo</p>';
  const blocked = mv.buildSrcDoc(html, true, false);
  const allowed = mv.buildSrcDoc(html, false, false);
  for (const doc of [blocked, allowed]) {
    assert.match(doc, /http-equiv="Content-Security-Policy"/);
    assert.match(doc, /default-src 'none'/);
  }
  // Der einzige Unterschied ist die Bildquelle.
  assert.doesNotMatch(blocked, /img-src[^"]*https:/);
  assert.match(allowed, /img-src[^"]*https:/);
  // Alles andere bleibt in beiden Faellen zu.
  for (const doc of [blocked, allowed]) {
    assert.doesNotMatch(doc, /frame-src/);
    assert.doesNotMatch(doc, /script-src/);
    assert.match(doc, /font-src data:/);
    assert.match(doc, /media-src data:/);
  }
});

test('Mail-HTML laeuft durch DOMPurify - Skript und Handler verschwinden', () => {
  const boese = '<p onclick="alert(1)">text</p><script>alert(2)<\/script>' +
    '<iframe src="https://boese.example/"></iframe><img src="x" onerror="alert(3)">';
  const doc = mv.buildSrcDoc(boese, false, false);
  assert.doesNotMatch(doc, /<script/i);
  assert.doesNotMatch(doc, /onerror/i);
  assert.doesNotMatch(doc, /onclick/i);
  assert.doesNotMatch(doc, /<iframe/i);
  assert.match(doc, /text/);
});

test('normale Mail-Formatierung ueberlebt die Reinigung', () => {
  const html = '<div style="color:#a00"><b>fett</b> <a href="https://example.com/x">Link</a>' +
    '<table><tr><td>Zelle</td></tr></table><img src="cid:teil1"></div>';
  const doc = mv.buildSrcDoc(html, false, false);
  assert.match(doc, /<b>fett<\/b>/);
  assert.match(doc, /href="https:\/\/example\.com\/x"/);
  assert.match(doc, /<td>Zelle<\/td>/);
  assert.match(doc, /cid:teil1/);
  assert.match(doc, /color:#a00/);
});

test('bei blockierten Bildern bleibt kein externer Verweis im Dokument', () => {
  const html = '<img src="https://tracker.example/pixel.gif">' +
    '<div background="http://tracker.example/bg.png">x</div>' +
    '<div style="background-image:url(https://tracker.example/b.png)">y</div>';
  const doc = mv.buildSrcDoc(html, true, false);
  assert.doesNotMatch(doc, /tracker\.example/);
});

test('Druckfassung nutzt denselben Rumpf und dieselbe CSP', () => {
  const html = '<img src="https://tracker.example/pixel.gif"><p>inhalt</p>';
  // Befund 7: mit blockierten Bildern darf der Druck keinen Tracker nachladen.
  const gedruckt = mv.buildPrintBody(html, true);
  assert.doesNotMatch(gedruckt, /tracker\.example/);
  assert.match(gedruckt, /inhalt/);
  assert.match(mv.printCsp(true), /img-src data: cid:;/);
  assert.match(mv.printCsp(false), /img-src[^"]*https:/);
  // und auch hier faellt das Skript weg
  assert.doesNotMatch(mv.buildPrintBody('<script>alert(1)<\/script>a', false), /<script/i);
});

test('kein Mail-Sandbox-Wert erlaubt Skripte', () => {
  for (const wert of [mv.MAIL_SANDBOX, mv.MAIL_SANDBOX_MEASURED, mv.MAIL_SANDBOX_PRINT]) {
    assert.equal(typeof wert, 'string');
    assert.doesNotMatch(wert, /allow-scripts/);
  }
  // Die Leseansicht braucht kein same-origin (Befund 9).
  assert.doesNotMatch(mv.MAIL_SANDBOX, /allow-same-origin/);
});

test('hasRemoteContent fragt den DOM, nicht den Rohtext', () => {
  assert.equal(mv.hasRemoteContent('<img src="https://x.example/p.gif">'), true);
  assert.equal(mv.hasRemoteContent('<div style="background:url(http://x.example/b.png)">a</div>'), true);
  assert.equal(mv.hasRemoteContent('<img\n  src="https://x.example/p.gif">'), true);
  assert.equal(mv.hasRemoteContent('<img src="cid:teil1"><img src="data:image/gif;base64,AA">'), false);
  // Befund 17: nur ERWAEHNTE URLs sind kein Nachladen.
  assert.equal(mv.hasRemoteContent('<p>schreib mir: src="https://example.com/bild.png" ist gemeint</p>'), false);
  assert.equal(mv.hasRemoteContent('<!-- <img src="https://example.com/a.png"> -->text'), false);
});

test("protokollrelative Bild-URLs (//host/x) gelten als extern und werden blockiert", () => {
  assert.equal(mv.hasRemoteContent('<img src="//t.example/p.gif">'), true);
  assert.equal(mv.hasRemoteContent('<div style="background:url(//t.example/b.png)">a</div>'), true);
  const doc = mv.buildSrcDoc('<p>x</p><img src="//t.example/p.gif">', true, false);
  assert.ok(!doc.includes("t.example"), "protokollrelative URL muss im Block-Modus raus");
});
