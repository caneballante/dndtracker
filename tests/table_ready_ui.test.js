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
  const eventId = 'evt_11111111111111111111111111111111';
  const removedEventId = 'evt_22222222222222222222222222222222';
  const highlightId = 'hlt_11111111111111111111111111111111';
  let memoryState = {
    sessionId: '1234567890124',
    status: { memory: { built: true }, operation: { running: false } },
    memory: {
      revision: 3, editedAt: 1788730807, hasHumanEdits: true, canonicalDigest: 'digest-r3', conflicts: [],
      events: [{ eventId, summary: 'The party opened the gate.', facts: ['A bronze key worked.'], entities: ['Vayne'],
        type: 'discovery', status: 'active', importance: 'high', confidence: 'high', sourceChunks: [2, 3] }],
      highlights: [{ highlightId, summary: 'Vayne solved the lock.', categories: ['clever_solution'], participants: ['Vayne'],
        confidence: 'high', sourceChunks: [3] }],
      removedEvents: [{ eventId: removedEventId, summary: 'Incorrect generated event.', facts: [], entities: [],
        type: 'other', status: 'active', importance: 'low', confidence: 'low', sourceChunks: [4] }],
      removedHighlights: [],
    },
    publication: { state: 'stale', lastSuccessful: { revision: 2, publishedAt: 1788644407 }, lastAttempt: null },
  };
  w.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url === '/api/session/player/status') {
      return { ok: true, json: async () => ({ ok: true, enabled: true, sessionBudgetUsd: 1 }) };
    }
    if (url === '/api/session/player/missed') {
      return { ok: true, json: async () => ({
        ok: true, state: 'complete', cached: true, windowMinutes: JSON.parse(options.body).windowMinutes,
        answer: { summary: 'The party chose a route.', keyDevelopments: [], peopleMentioned: [], uncertainties: [] },
        evidence: { throughTimestamp: 1700000000 }, newSpend: 0, budget: { spent: 0.08, limit: 1 }
      }) };
    }
    if (url === '/api/session/memory/edit') {
      const body = JSON.parse(options.body);
      const eventUpdate = body.changes.find(change => change.kind === 'event' && change.action === 'update');
      if (eventUpdate?.fields?.summary) memoryState.memory.events[0].summary = eventUpdate.fields.summary;
      memoryState.memory.revision += 1;
      memoryState.memory.canonicalDigest = 'digest-r4';
      return { ok: true, json: async () => ({ ok: true, memory: copy(memoryState.memory) }) };
    }
    if (String(url).startsWith('/api/session/dungeonshare/preview?')) {
      return { ok: true, json: async () => ({ ok: true,
        preview: { session: { title: 'The Bronze Gate', date: '2026-09-05' }, memory: copy(memoryState.memory) },
        payload: { schemaVersion: 1, kind: 'dungeontracker_session_memory', memory: copy(memoryState.memory) }
      }) };
    }
    if (url === '/api/session/dungeonshare/publish') {
      const body = JSON.parse(options.body);
      memoryState.publication = { state: 'current', lastSuccessful: { revision: body.expectedRevision, publishedAt: 1788730807 }, lastAttempt: { status: 'success' } };
      return { ok: true, json: async () => ({ ok: true }) };
    }
    throw Error(`Unexpected network path: ${url}`);
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
  const memoryCampaign = d.getElementById('sessions-memory-dungeonshare-campaign');
  memoryCampaign.innerHTML = '<option value="three-friends">Three Friends</option>';
  memoryCampaign.disabled = false;
  w.tableSessionMemory = {
    identity: () => memoryState.sessionId,
    get: () => copy(memoryState),
    reload: async sessionId => {
      assert.equal(sessionId, memoryState.sessionId);
      w.dispatchEvent(new w.CustomEvent('table-session-memory-updated', { detail: copy(memoryState) }));
      return copy(memoryState);
    },
  };
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
  assert.ok(d.getElementById('history-publishing').closest('#history-legacy'), 'legacy recap/handoff is demoted');
  assert.ok(d.getElementById('sessions-game-narrative').closest('#history-narrative'), 'narrative is demoted intact');
  assert.ok(d.getElementById('live-guidance-text').closest('details'));
  assert.match(d.getElementById('sessions-memory-publish-state').textContent, /Published Revision 2/);
  assert.match(d.getElementById('sessions-memory-publish-state').textContent, /UPDATE AVAILABLE/);
  assert.equal(d.getElementById('sessions-memory-preview-publish').textContent, 'Preview & Republish');
  assert.equal(d.getElementById('sessions-memory-conflicts').hidden, true);
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
  assert.equal(tabs.at(-1), 'tab-sessions');
  assert.equal(d.activeElement.id, 'sessions-memory-preview-publish');

  // Session Memory editing is transactional: Cancel writes nothing; Save makes one bounded request.
  const memoryEditDialog = d.getElementById('sessions-memory-edit-dialog');
  const editCallsBefore = requests.filter(call => call.url === '/api/session/memory/edit').length;
  d.getElementById('sessions-edit-memory').click();
  assert.equal(memoryEditDialog.open, true);
  assert.equal(
    memoryEditDialog.querySelector(`[data-memory-kind="event"][data-memory-id="${removedEventId}"][data-memory-field="summary"]`).value,
    'Incorrect generated event.'
  );
  let eventSummary = memoryEditDialog.querySelector(`[data-memory-kind="event"][data-memory-id="${eventId}"][data-memory-field="summary"]`);
  eventSummary.value = 'Canceled correction';
  eventSummary.dispatchEvent(new w.Event('input', { bubbles: true }));
  clickText(memoryEditDialog, 'Cancel');
  assert.equal(memoryEditDialog.open, false);
  assert.equal(requests.filter(call => call.url === '/api/session/memory/edit').length, editCallsBefore);

  d.getElementById('sessions-edit-memory').click();
  eventSummary = memoryEditDialog.querySelector(`[data-memory-kind="event"][data-memory-id="${eventId}"][data-memory-field="summary"]`);
  eventSummary.value = 'The party opened the bronze gate.';
  eventSummary.dispatchEvent(new w.Event('input', { bubbles: true }));
  const removedCard = memoryEditDialog.querySelector(`article[data-memory-id="${removedEventId}"]`);
  clickText(removedCard, 'Restore');
  const highlightCard = memoryEditDialog.querySelector(`article[data-memory-id="${highlightId}"]`);
  clickText(highlightCard, 'Remove from Session Memory');
  clickText(memoryEditDialog, 'Save Changes');
  await tick(); await tick();
  const editCalls = requests.filter(call => call.url === '/api/session/memory/edit');
  assert.equal(editCalls.length, editCallsBefore + 1);
  const editBody = JSON.parse(editCalls.at(-1).options.body);
  assert.equal(editBody.sessionId, '1234567890124');
  assert.equal(editBody.baseRevision, 3);
  assert.equal(editBody.baseDigest, 'digest-r3');
  assert.ok(editBody.changes.some(change => change.kind === 'event' && change.action === 'update' && change.fields.summary === 'The party opened the bronze gate.'));
  assert.ok(editBody.changes.some(change => change.id === removedEventId && change.action === 'restore'));
  assert.ok(editBody.changes.some(change => change.id === highlightId && change.action === 'remove'));
  assert.doesNotMatch(JSON.stringify(editBody), /sourceChunks|firstChunk|lastChunk|createdAt|updatedAt/);
  assert.equal(memoryEditDialog.open, false);

  // Preview is readable and cancelable; only the second explicit confirmation publishes.
  const publishDialog = d.getElementById('sessions-memory-publish-dialog');
  d.getElementById('sessions-memory-preview-publish').click();
  await tick();
  assert.equal(publishDialog.open, true);
  assert.match(d.getElementById('sessions-memory-preview-content').textContent, /The Bronze Gate/);
  assert.match(d.getElementById('sessions-memory-preview-content').textContent, /Highlights/);
  assert.match(d.getElementById('sessions-memory-preview-content').textContent, /Events/);
  clickText(publishDialog, 'Cancel');
  assert.equal(requests.filter(call => call.url === '/api/session/dungeonshare/publish').length, 0);
  d.getElementById('sessions-memory-preview-publish').click();
  await tick();
  clickText(publishDialog, 'Publish to DungeonShare');
  await tick(); await tick();
  const publishCalls = requests.filter(call => call.url === '/api/session/dungeonshare/publish');
  assert.equal(publishCalls.length, 1);
  const publishBody = JSON.parse(publishCalls[0].options.body);
  assert.deepEqual(publishBody, {
    sessionId: '1234567890124', campaignSlug: 'three-friends', expectedRevision: 4, expectedDigest: 'digest-r4', confirm: true
  });
  assert.match(d.getElementById('sessions-memory-publish-state').textContent, /DungeonShare is current/);

  // Conflicts are prominent and hard-disable publishing; a later failed attempt keeps prior success visible.
  memoryState.memory.revision = 5;
  memoryState.memory.conflicts = [{ message: 'Edited Event no longer matches the rebuilt generated item.' }];
  memoryState.publication.state = 'stale';
  w.dispatchEvent(new w.CustomEvent('table-session-memory-updated', { detail: copy(memoryState) }));
  assert.equal(d.getElementById('sessions-memory-conflicts').hidden, false);
  assert.match(d.getElementById('sessions-memory-conflicts').textContent, /Edited Event no longer matches/);
  assert.equal(d.getElementById('sessions-memory-preview-publish').disabled, true);
  memoryState.memory.conflicts = [];
  memoryState.publication = {
    state: 'failed',
    lastSuccessful: { revision: 4, publishedAt: 1788730807 },
    lastAttempt: { status: 'failed', attemptedAt: 1788817207, error: 'Destination unavailable.' },
  };
  w.dispatchEvent(new w.CustomEvent('table-session-memory-updated', { detail: copy(memoryState) }));
  assert.match(d.getElementById('sessions-memory-publish-state').textContent, /Published Revision 4/);
  assert.match(d.getElementById('sessions-memory-publish-state').textContent, /PUBLISH NEEDS ATTENTION/);
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
