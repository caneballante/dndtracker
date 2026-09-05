// Executes the actual inline module with an offline DOM/fetch fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.textContent = ''; this.value = ''; }
  appendChild(child) {
    this.children.push(child);
    child.parent = this;
    return child;
  }
  replaceChildren() { this.children = []; this.textContent = ''; }
  setAttribute() {}
  addEventListener(event, fn) { this.listeners[event] = fn; }
  set selected(value) { if (value && this.parent) this.parent.value = this.value; }
}
const descendants = (el) => [el, ...el.children.flatMap(descendants)];
const text = (el) => descendants(el).map(e => e.textContent).join('\n');
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'dnd-audio.html'), 'utf8');
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new Function(match[1]);
  const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .find(m => m[1].includes('// PLAYER COMPANION P0:'))[1];
  const live = new Element(); live.dataset.playerCompanion = 'live';
  const history = new Element(); history.dataset.playerCompanion = 'history';
  const current = new Element(); current.textContent = '12345678';
  const selected = new Element('select'); selected.value = '87654321';
  const ids = { sessionId: current, 'sessions-select': selected };
  const requests = [];
  let nextResult;
  let settle;
  const context = {
    document: {
      getElementById: id => ids[id],
      querySelectorAll: () => [live, history],
      createElement: tag => new Element(tag),
    },
    window: {},
    MutationObserver: class { observe() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith('/status')) return { json: async () => ({ ok: true, enabled: true, sessionBudgetUsd: 1 }) };
      await new Promise(resolve => { settle = resolve; });
      if (nextResult instanceof Error) throw nextResult;
      return { ok: true, json: async () => nextResult };
    },
  };
  vm.runInNewContext(script, context);
  await tick();
  assert.equal(requests.length, 1, 'initialization must only check availability');
  const button = descendants(history).find(el => el.tag === 'button');
  const selector = descendants(history).find(el => el.tag === 'select');
  const liveButton = descendants(live).find(el => el.tag === 'button');
  assert.equal(selector.value, '5');
  assert.equal(button.disabled, false);
  nextResult = { ok: true, state: 'complete', cached: false, windowMinutes: 5,
    answer: { summary: '<script>Untrusted transcript prose</script>',
      keyDevelopments: [{ text: 'The party chose a route.' }],
      peopleMentioned: [{ name: 'Branna', context: 'Public director', confidence: 'high' }], uncertainties: [] },
    evidence: { throughTimestamp: 1700000000, timingNote: 'Completion timestamps.' },
    newSpend: 0.02, budget: { spent: 0.08, limit: 1 } };
  const active = button.listeners.click();
  assert.equal(button.disabled, true);
  assert.equal(liveButton.disabled, true);
  await button.listeners.click();
  assert.equal(requests.length, 2, 'double click must not dispatch again');
  assert.equal(requests[1].url, '/api/session/player/missed');
  assert.deepEqual(JSON.parse(requests[1].options.body), { sessionId: '87654321', windowMinutes: 5 });
  settle(); await active;
  assert.match(text(history), /0\.02 new spend/);
  assert.match(text(history), /0\.08 \/ \$1\.00/);
  assert.match(text(history), /Based on transcript through/);
  assert.equal(descendants(history).some(el => el.tag === 'script'), false, 'prose must not become HTML');
  const previous = text(history);
  nextResult = new Error('offline failure');
  const failed = button.listeners.click(); settle(); await failed;
  assert.match(text(history), /PLAYER COMPANION NEEDS ATTENTION/);
  assert.match(text(history), /The party chose a route/);
  nextResult = { ok: true, state: 'complete', cached: true, windowMinutes: 5,
    answer: { summary: 'Cached answer', keyDevelopments: [], peopleMentioned: [], uncertainties: [] },
    newSpend: 0, budget: { spent: 0.08, limit: 1 } };
  const cached = button.listeners.click(); settle(); await cached;
  assert.match(text(history), /No new transcript since this answer/);
  assert.match(text(history), /\$0 new spend/);
  selector.value = '10';
  const different = button.listeners.click(); settle(); await different;
  assert.equal(JSON.parse(requests.at(-1).options.body).windowMinutes, 10);
  const liveRequest = liveButton.listeners.click(); settle(); await liveRequest;
  assert.equal(JSON.parse(requests.at(-1).options.body).sessionId, '12345678');
  assert.equal(requests.filter(r => r.url.includes('/reconcile') || r.url.includes('/reprocess')).length, 0);
  console.log('Player Companion UI: offline click, duplicate, window, render, cost, cache, error, live/history and script syntax checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
