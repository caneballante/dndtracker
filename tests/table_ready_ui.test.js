// Offline real-DOM contracts. No resources are loaded and fetch is always mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch { console.error('Optional jsdom dependency not installed; set NODE_PATH to an existing installation.'); process.exit(77); }
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'dnd-audio.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'table-ready.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'table-ready.css'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

async function run() {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://offline.invalid/' });
  const w = dom.window, d = w.document;
  const errors = [];
  w.addEventListener('error', event => { errors.push(event.error); });
  w.setInterval = () => 0;
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  const tabs = [], requests = [], saved = [];
  w.bootstrap = { Tab: { getOrCreateInstance: node => ({ show: () => tabs.push(node.id) }) } };
  w.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url !== '/api/session/player/status' && url !== '/api/session/player/missed') throw Error('Unexpected network path');
    return { ok: true, json: async () => url.endsWith('/status') ? { ok: true, enabled: true, sessionBudgetUsd: 1 } : {
      ok: true, state: 'complete', cached: true, windowMinutes: JSON.parse(options.body).windowMinutes,
      answer: { summary: 'The party chose a route.', keyDevelopments: [], peopleMentioned: [], uncertainties: [] },
      evidence: { throughTimestamp: 1700000000 }, newSpend: 0, budget: { spent: 0.08, limit: 1 }
    } };
  };
  let live = [{ role: 'Player', playerName: 'Jon', characterName: 'Pippin', included: true },
    { role: 'NPC', playerName: '', characterName: 'Vexatious', included: true }];
  let history = [{ role: 'Player', playerName: 'Jeanne', included: true }];
  let rejectSave = false;
  const adapter = mode => ({ get: () => copy(mode === 'live' ? live : history),
    identity: () => mode === 'live' ? '1234567890123' : '1234567890124',
    save: async (rows, id) => {
      if (rejectSave) throw Error('Offline save failure');
      saved.push({ mode, rows: copy(rows), id });
      if (mode === 'live') live = copy(rows); else history = copy(rows);
    }
  });
  w.tableLiveRoster = adapter('live'); w.tableHistoryRoster = adapter('history');
  w.tableSavedRoster = [...copy(live), { role: 'Player', playerName: 'Geoff', included: true }];
  w.tableKnownPeople = [{ name: 'Seralith', type: 'npc' }, { name: '<img src=x onerror=alert(1)>', type: 'npc' }];
  w.tableHistoryCampaignId = 'default';
  d.getElementById('sessionId').textContent = '1234567890123';
  d.getElementById('sessions-select').innerHTML = '<option value="1234567890124">Previous session</option>';
  const memoryIds = [];
  w.tableOpenSessionMemory = id => memoryIds.push(id);
  const pc = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].find(m => m[1].includes('// PLAYER COMPANION P0:'))[1];
  w.eval(pc); await tick();
  const originalIds = [...d.querySelectorAll('[id]')].map(el => el.id);
  let starts = 0, stops = 0;
  d.getElementById('startBtn').addEventListener('click', () => starts++);
  d.getElementById('stopBtn').addEventListener('click', () => stops++);
  w.eval(script);
  assert.equal(errors.length, 0);
  assert.deepEqual(originalIds.filter(id => !d.getElementById(id)), [], 'all existing IDs survive');
  assert.equal(new Set([...d.querySelectorAll('[id]')].map(el => el.id)).size, d.querySelectorAll('[id]').length, 'no duplicate IDs');
  assert.equal(d.querySelector('.table-main').children.length, 3);
  assert.equal(d.querySelector('.table-side').children.length, 3);
  assert.equal(d.querySelector('.context-strip').children.length, 2);
  assert.ok(d.querySelector('.session-context #sessionNameInput'));
  assert.ok(d.querySelector('.workspace-anchor #workspaceCampaignTitle'));
  assert.ok(d.querySelector('.appliance-nav #tab-sessions'));
  assert.ok(d.getElementById('partyRosterTable').closest('details'));
  assert.ok(d.getElementById('campaignCanonNamesTable').closest('details'));
  assert.ok(!d.getElementById('session-memory-section').closest('details'), 'durable memory remains expanded');
  assert.ok(d.getElementById('sessions-reprocess').closest('#history-legacy'));
  assert.ok(d.getElementById('live-guidance-text').closest('details'));
  assert.match(d.getElementById('table-live-names').textContent, /Jon · Vexatious/);
  assert.match(d.getElementById('table-live-count').textContent, /2 participants/);
  assert.equal(d.querySelector('.recent-play [data-live-action]'), null);
  assert.match(d.querySelector('.recent-play').textContent, /not a permanent record/);
  w.dispatchEvent(new w.CustomEvent('table-recent-play', { detail: { text: 'Existing mini summary', updatedAt: 1700000000 } }));
  assert.equal(d.getElementById('recent-play-live').textContent, 'Existing mini summary');
  d.getElementById('sessions-notes-summary').textContent = 'Rolling Notes Summary: Saved mini summary'; await tick();
  assert.equal(d.getElementById('recent-play-history').textContent, 'Saved mini summary');

  const clickText = (parent, text) => [...parent.querySelectorAll('button')].find(node => node.textContent === text).click();
  const dialog = d.getElementById('table-participants-dialog');
  d.getElementById('table-live-edit').click();
  assert.equal(dialog.open, true);
  assert.match(dialog.textContent, /Geoff/);
  assert.match(dialog.textContent, /Other characters/);
  const check = dialog.querySelector('input[type=checkbox]'); check.checked = false; check.dispatchEvent(new w.Event('change'));
  clickText(dialog, 'Cancel');
  assert.equal(dialog.open, false); assert.equal(live[0].included, true); assert.equal(saved.length, 0);
  d.getElementById('table-live-edit').click();
  const check2 = dialog.querySelector('input[type=checkbox]'); check2.checked = false; check2.dispatchEvent(new w.Event('change'));
  clickText(dialog, 'Save'); await tick();
  assert.equal(live[0].included, false); assert.equal(saved.length, 1);
  assert.equal(history[0].playerName, 'Jeanne'); assert.match(d.getElementById('table-live-count').textContent, /1 participant/);
  d.getElementById('table-live-edit').click();
  const search = d.getElementById('table-person-search'); search.value = 'seralith'; search.dispatchEvent(new w.Event('input'));
  clickText(dialog, 'Seralith · Character'); clickText(dialog, 'Save'); await tick();
  assert.equal(live.find(row => row.characterName === 'Seralith').included, true);
  assert.equal(w.tableKnownPeople.length, 2, 'adding session participation never adds canon');
  d.getElementById('table-history-edit').click();
  rejectSave = true; clickText(dialog, 'Save'); await tick();
  assert.equal(dialog.open, true); assert.match(dialog.textContent, /Offline save failure/);
  assert.equal(history.length, 1); rejectSave = false; clickText(dialog, 'Cancel');
  d.getElementById('table-live-edit').click();
  clickText(dialog, '+ Add person…'); d.getElementById('table-person-name').value = 'Guest';
  clickText(dialog, 'Add to selection'); clickText(dialog, 'Save'); await tick();
  assert.ok(live.some(row => row.playerName === 'Guest'));

  const pcRoot = d.getElementById('live-player-companion');
  assert.equal(pcRoot.querySelectorAll('.companion-windows button').length, 3);
  clickText(pcRoot, '10 MIN'); assert.equal(pcRoot.querySelector('select').value, '10');
  clickText(pcRoot, 'What Did I Miss?'); await tick();
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { sessionId: '1234567890123', windowMinutes: 10 });
  assert.match(pcRoot.textContent, /\$0 new spend/); assert.match(pcRoot.textContent, /0\.08 \/ \$1\.00/);
  assert.match(pcRoot.textContent, /Based on transcript through/);
  clickText(d.querySelector('.memory-overview'), 'View / Build / Rebuild…');
  assert.deepEqual(memoryIds, ['1234567890123']);
  clickText(d.querySelector('.appliance-nav'), '↗  DungeonShare');
  assert.equal(tabs.at(-1), 'tab-sessions'); assert.equal(d.getElementById('history-publishing').open, true);
  d.getElementById('startBtn').click(); d.getElementById('stopBtn').disabled = false; d.getElementById('stopBtn').click();
  assert.equal(starts, 1); assert.equal(stops, 1);
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new Function(match[1]);
  assert.match(css, /prefers-reduced-motion/); assert.match(css, /max-width: 720px/);
  assert.match(css, /grid-template-columns: minmax\(0, 1fr\)/);
  assert.equal(errors.length, 0);
  dom.window.close();

  // Exercise the actual save adapters independently, including their HTTP payloads.
  for (const mode of ['Live', 'History']) {
    const match = html.match(new RegExp(`window.table${mode}Roster = \\{[\\s\\S]*?^  \\};`, 'm'))[0];
    const calls = [], rows = [{ role: 'Player', playerName: 'Original', included: true }];
    let sid = '1234567890123';
    const ctx = { window: {}, partyRoster: copy(rows), sessionsPartyRoster: copy(rows),
      tableHistoryLoadedSessionId: sid, campaignSelectEl: { value: 'test' },
      activeRecordingSessionId: () => sid, currentHistorySessionId: () => sid,
      rosterToPartyMetaText: value => value.filter(row => row.included).map(row => row.playerName).join('\n'),
      renderPartyRoster() {}, renderSessionsRoster() {}, setPartyMetaStatus() {}, setSessionsPartyStatus() {},
      fetch: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return { ok: true, json: async () => ({ ok: true }) }; }
    };
    vm.runInNewContext(match, ctx);
    const source = ctx.window[`table${mode}Roster`];
    const edited = source.get(); edited[0].playerName = 'Changed';
    assert.equal(source.get()[0].playerName, 'Original', 'draft does not mutate source');
    await source.save(edited, sid);
    assert.deepEqual(calls, [{ url: '/api/meta', body: { sessionId: sid, party: 'Changed' } }]);
    sid = '9999999999999';
    await assert.rejects(source.save(edited, '1234567890123'), /Session changed/);
    assert.equal(calls.length, 1);
  }
  console.log('Table-ready UI: structure, scope, modal cancel/save/error, players, NPC search, recent play, companion, memory, legacy, recorder, sharing, script and responsive checks passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
