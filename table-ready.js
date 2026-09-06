/* Phase 4.1 presentation only. Existing nodes retain their IDs and listeners. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const make = (tag, className = '', text = '') => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };
  function button(text, action, className = '') {
    const node = make('button', `btn hardware-button ${className}`, text);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }
  function panel(title, className = '') {
    const node = make('section', `appliance-panel ${className}`);
    node.append(make('h2', 'panel-header', title));
    return node;
  }
  function fold(title, node) {
    const wrapper = make('details', 'appliance-fold');
    wrapper.append(make('summary', 'system-label', title));
    if (node) wrapper.append(node);
    return wrapper;
  }
  function tab(id) {
    const target = $(id);
    window.bootstrap?.Tab.getOrCreateInstance(target).show();
    target?.focus();
  }

  // Retain the original Bootstrap tab controls, including programmatic callers.
  const nav = $('sessionTabs');
  nav.classList.add('appliance-nav');
  nav.setAttribute('aria-label', 'Workspace sections');
  nav.setAttribute('aria-orientation', 'vertical');
  const labels = { 'tab-recording': '◉  Live Session', 'tab-sessions': '▤  Sessions',
    'tab-prep': '▥  Campaigns', 'tab-live-review': '⚙  Advanced / Legacy', 'tab-test': '⌘  Notes Lab' };
  Object.entries(labels).forEach(([id, text]) => { $(id).textContent = text; });
  nav.append($('tab-sessions').parentElement, $('tab-prep').parentElement,
    $('tab-live-review').parentElement, $('tab-test').parentElement);
  const shareNav = make('li', 'nav-item');
  shareNav.append(button('↗  DungeonShare', () => {
    tab('tab-sessions');
    $('history-publishing').open = true;
    $('sessions-dungeonshare-campaign').scrollIntoView({ block: 'center' });
    $('sessions-dungeonshare-campaign').focus();
  }, 'nav-link'));
  nav.insertBefore(shareNav, $('tab-live-review').parentElement);
  const shellBody = $('workspaceMain');
  const content = shellBody.querySelector('.tab-content');
  const shell = make('div', 'appliance-workspace');
  content.before(shell);
  shell.append(nav, content);
  content.classList.add('appliance-content');
  document.querySelector('.workspace-hero').classList.add('appliance-masthead');

  // Campaign context and session scope live beside each other above the controls.
  const context = make('div', 'context-strip');
  const campaign = shellBody.querySelector('.workspace-anchor');
  campaign.before(context);
  context.append(campaign);
  const session = panel('Session', 'session-context');
  const sessionInput = $('sessionNameInput').closest('.section-card');
  session.append($('sessionId'), sessionInput);
  context.append(session);
  const preview = $('recordingCampaignContextPreview').closest('.section-card');

  const recording = $('pane-recording');
  const recorderStatus = $('statusDot').parentElement;
  const settings = $('micSelect').closest('.ms-auto');
  const meter = $('micLevelBar').closest('.progress').parentElement;
  const liveRosterLegacy = $('partyRosterTable').closest('.row');
  const diagnostics = recording.querySelector('details');
  const rawStatus = make('div');
  rawStatus.append($('status-line'), $('status-json'));
  const advanced = $('pane-live-review');
  const legacyReview = advanced.querySelector('.row');
  const companion = $('live-player-companion');
  const memoryProgress = $('recording-memory-status');
  // Old correction/steering controls stay available only in their legacy context.
  const legacyReviewFold = fold('Legacy note review & guidance', legacyReview);
  advanced.replaceChildren(make('h2', 'panel-header', 'Advanced / Legacy'),
    make('p', 'text-muted', 'Older workflows and troubleshooting. These tools do not rebuild Session Memory.'),
    legacyReviewFold, fold('Transcript & evidence diagnostics', diagnostics),
    fold('Raw capture status', rawStatus), fold('Legacy session roster fields', liveRosterLegacy),
    fold('Compiled campaign context', preview));
  // Preserve microphone input, refresh and reset in a compact setup disclosure.
  meter.classList.add('audio-meter');
  const recorder = panel('Recording', 'recorder-module');
  const recorderDisplay = make('div', 'instrument-display recorder-display');
  const timer = make('output', 'data-readout elapsed-time', '00:00:00');
  timer.id = 'table-elapsed';
  timer.setAttribute('aria-label', 'Session elapsed time, including pauses');
  recorderStatus.classList.add('recorder-state');
  recorderStatus.append(timer);
  recorderDisplay.append(recorderStatus, meter);
  const chunkStatus = recorderStatus.querySelector('.text-muted');
  settings.append(chunkStatus);
  const transport = make('div', 'recorder-transport');
  ['startBtn', 'pauseBtn', 'resumeBtn', 'stopBtn'].forEach(id => transport.append($(id)));
  recorderDisplay.append(transport);
  recorder.append(recorderDisplay, fold('Microphone & recording settings', settings));
  const playback = $('latestChunkLink');
  recorder.append(playback);
  // Keep hidden campaign guards updated by the existing recorder code.
  const guards = make('div', 'd-none');
  guards.append($('recording-selected-campaign'), $('recording-active-campaign'));
  recorder.append(guards);

  const dashboard = make('div', 'table-dashboard');
  const main = make('div', 'table-main');
  const side = make('aside', 'table-side');
  const liveTable = participantPanel('live');
  const recent = recentPanel('live');
  const memory = panel('Session Memory', 'memory-overview');
  memory.append(make('p', 'text-muted', 'Durable Events + Highlights. Generated after Stop.'),
    button('View / Build / Rebuild…', () => {
      const id = $('sessionId').textContent.trim();
      if (/^\d{8,20}$/.test(id)) window.tableOpenSessionMemory?.(id);
      else tab('tab-sessions');
    }), memoryProgress);
  const shortcuts = fold('Advanced / Legacy');
  shortcuts.append(button('Transcript & diagnostics', () => tab('tab-live-review')),
    button('Legacy: Rebuild DM Notes', () => {
      tab('tab-sessions');
      $('history-legacy').open = true;
      $('sessions-reprocess').closest('details').open = true;
      $('sessions-reprocess').scrollIntoView({ block: 'center' });
    }), button('Player recap / handoff', () => {
      tab('tab-sessions'); $('history-publishing').open = true;
    }));
  main.append(liveTable, recorder, recent);
  side.append(companion, memory, shortcuts);
  dashboard.append(main, side);
  recording.replaceChildren(dashboard);

  // Historical memory stays expanded. The old large roster and outputs are demoted.
  const historyRoster = $('sessions-roster-body').closest('.section-card');
  const historyStack = historyRoster.parentElement;
  const historyTable = participantPanel('history');
  historyRoster.before(historyTable);
  const oldNotes = $('sessions-notes-summary').closest('.section-card');
  const historicalRecent = recentPanel('history');
  const historicalCompanion = $('history-player-companion');
  const memorySection = $('session-memory-section');
  historyTable.after(historicalRecent, historicalCompanion, memorySection);
  const historyLegacy = fold('Advanced / Legacy', oldNotes);
  historyLegacy.id = 'history-legacy';
  historyLegacy.append(fold('Legacy session roster fields', historyRoster));
  historyStack.append(historyLegacy);
  const publishing = fold('Player recap / handoff & DungeonShare');
  publishing.id = 'history-publishing';
  const recap = $('sessions-game-summary').closest('.section-card');
  const narrative = $('sessions-game-narrative').closest('.section-card');
  publishing.append(recap, narrative);
  historyLegacy.before(publishing);
  const evidence = $('sessions-transcript').closest('.section-card');
  historyLegacy.append(fold('Transcript & evidence tools', evidence));
  const historyIntro = $('pane-sessions').querySelector('.help-callout .small.text-muted');
  historyIntro.textContent = 'Select a session. Review its participants and durable Session Memory. Older outputs and repair tools are below.';
  const canonNames = $('campaignCanonNamesTable').closest('.border');
  if (canonNames) {
    const holder = fold('Advanced: saved campaign names & historical figures');
    canonNames.before(holder); holder.append(canonNames);
  }
  const footer = make('footer', 'appliance-footer', 'DUNGEONTRACKER 4.1 · Local session console');
  footer.append(make('span', '', 'Capture. Remember. Play on.'));
  document.body.append(footer);

  // Segmented presentation of the existing selector; the original click contract stays intact.
  document.querySelectorAll('[data-player-companion]').forEach(root => {
    const select = root.querySelector('select');
    if (!select) return;
    const segments = make('div', 'companion-windows');
    segments.setAttribute('role', 'group'); segments.setAttribute('aria-label', 'Recent transcript window');
    const choices = [2, 5, 10].map(minutes => {
      const choice = button(`${minutes} MIN`, () => { select.value = String(minutes); select.dispatchEvent(new Event('change')); sync(); });
      segments.append(choice); return choice;
    });
    function sync() {
      choices.forEach((choice, index) => {
        choice.disabled = select.disabled;
        const active = select.value === String([2, 5, 10][index]);
        choice.classList.toggle('btn-primary', active);
        choice.setAttribute('aria-pressed', String(active));
      });
    }
    select.hidden = true; select.before(segments);
    new MutationObserver(sync).observe(select, { attributes: true, attributeFilter: ['disabled'] });
    sync();
  });

  function recentPanel(mode) {
    const node = panel('Recent Play', 'recent-play');
    const text = make('div', 'instrument-display recent-readout', 'Waiting for a completed mini-summary.');
    text.id = `recent-play-${mode}`;
    const timestamp = make('div', 'small text-muted recent-timestamp');
    timestamp.id = `recent-play-${mode}-time`;
    node.append(text, timestamp, make('p', 'small text-muted mb-0', 'A quick glance at recent play — not a permanent record.'));
    return node;
  }
  window.addEventListener('table-recent-play', event => {
    $('recent-play-live').textContent = event.detail.text || 'Waiting for a completed mini-summary.';
    $('recent-play-live-time').textContent = event.detail.updatedAt
      ? `Updated ${new Date(event.detail.updatedAt * 1000).toLocaleTimeString()}` : '';
  });
  const historicalNotes = $('sessions-notes-summary');
  new MutationObserver(() => {
    $('recent-play-history').textContent = historicalNotes.textContent.replace(/^Rolling Notes Summary:\s*/, '') || 'No saved mini-summary.';
    $('recent-play-history-time').textContent = `Saved output · Session ${$('sessions-select').value || 'not selected'}`;
  }).observe(historicalNotes, { childList: true, subtree: true, characterData: true });

  let elapsed = 0;
  function updateRecorder() {
    const state = $('statusText').textContent;
    const live = /recording|paused/i.test(state);
    const sid = Number($('sessionId').textContent);
    if (live && Number.isFinite(sid)) elapsed = Math.max(0, Math.floor((Date.now() - sid) / 1000));
    if (/idle/i.test(state)) elapsed = 0;
    timer.textContent = [Math.floor(elapsed / 3600), Math.floor(elapsed / 60) % 60, elapsed % 60]
      .map(value => String(value).padStart(2, '0')).join(':');
    recorder.classList.toggle('is-recording', /recording/i.test(state));
    ['pauseBtn', 'resumeBtn'].forEach(id => { $(id).hidden = $(id).disabled; });
  }
  setInterval(updateRecorder, 1000); // Display clock only: no network or model work.
  updateRecorder();
  new MutationObserver(() => {
    $('recent-play-live').textContent = 'Waiting for a completed mini-summary.';
    $('recent-play-live-time').textContent = '';
  }).observe($('sessionId'), { childList: true, subtree: true, characterData: true });

  // A native dialog provides focus trapping, Escape, and an inert background.
  const dialog = make('dialog', 'retro-modal');
  dialog.id = 'table-participants-dialog';
  dialog.setAttribute('aria-labelledby', 'table-dialog-title');
  const titlebar = make('div', 'retro-titlebar');
  const title = make('h2', 'h5 mb-0', "Who's at the table?"); title.id = 'table-dialog-title';
  const close = button('×', () => dialog.close()); close.setAttribute('aria-label', 'Close without saving');
  titlebar.append(title, close);
  const modalBody = make('div', 'retro-modal-body');
  const lists = make('div', 'participant-lists');
  const searchPane = make('div', 'participant-search');
  const searchLabel = make('label', 'system-label', 'Search saved campaign names');
  const search = make('input', 'form-control'); search.id = 'table-person-search';
  search.type = 'search'; search.placeholder = 'Type a player or character name…'; searchLabel.htmlFor = search.id;
  const results = make('div', 'participant-results');
  searchPane.append(searchLabel, search, results,
    make('p', 'small text-muted', 'Select only who is participating tonight. World identities remain available to reference retrieval; you do not need to preload NPCs.'));
  modalBody.append(lists, searchPane);
  const status = make('div', 'small participant-save-status'); status.setAttribute('role', 'status');
  const actions = make('div', 'retro-modal-actions');
  const add = button('+ Add person…', () => { addForm.hidden = !addForm.hidden; personName.focus(); });
  const cancel = button('Cancel', () => dialog.close());
  const save = button('Save', saveDraft, 'btn-primary');
  actions.append(add, cancel, save);
  const addForm = make('div', 'participant-add'); addForm.hidden = true;
  const nameLabel = make('label', '', 'Name');
  const personName = make('input', 'form-control'); personName.id = 'table-person-name'; personName.maxLength = 120; nameLabel.htmlFor = personName.id;
  const roleLabel = make('label', '', 'Role');
  const personRole = make('select', 'form-select'); personRole.id = 'table-person-role'; roleLabel.htmlFor = personRole.id;
  ['Player', 'NPC', 'DM'].forEach(role => { const option = make('option', '', role === 'NPC' ? 'Other character' : role); option.value = role; personRole.append(option); });
  addForm.append(nameLabel, personName, roleLabel, personRole, button('Add to selection', () => {
    const name = personName.value.trim();
    if (!name || /[|\r\n]/.test(name)) { status.textContent = 'Enter a name without | or line breaks.'; return; }
    draft.push({ included: true, role: personRole.value, playerName: personRole.value === 'NPC' ? '' : name,
      characterName: personRole.value === 'NPC' ? name : '', className: '', race: '', aliases: '' });
    personName.value = ''; addForm.hidden = true; renderDraft();
  }));
  dialog.append(titlebar, modalBody, addForm, status, actions);
  document.body.append(dialog);
  let adapter, identity, draft = [], busy = false, opener, knownRows = [], knownNames = [];
  const rowName = row => row.playerName && row.playerName !== '(unknown)' ? row.playerName : (row.characterName || row.role || 'Unnamed');
  const rowKey = row => [row.role, row.playerName, row.characterName].map(value => String(value || '').toLowerCase().trim()).join('|');

  function participantPanel(mode) {
    const node = panel('At the table', 'at-the-table');
    const readout = make('div', 'instrument-display participant-readout');
    const names = make('div', 'data-readout'); names.id = `table-${mode}-names`;
    const count = make('div', 'small'); count.id = `table-${mode}-count`;
    const values = make('div'); values.append(names, count);
    const edit = button('Edit…', () => openEditor(mode, edit)); edit.id = `table-${mode}-edit`;
    readout.append(values, edit); node.append(readout);
    return node;
  }
  function readouts() {
    [['live', window.tableLiveRoster], ['history', window.tableHistoryRoster]].forEach(([mode, source]) => {
      const rows = (source?.get() || []).filter(row => row.included && (row.playerName || row.characterName));
      $(`table-${mode}-names`).textContent = rows.map(rowName).join(' · ') || 'No participants selected';
      $(`table-${mode}-count`).textContent = `${rows.length} participant${rows.length === 1 ? '' : 's'} · ${mode === 'live' ? 'This session only' : 'Selected session only'}`;
      $(`table-${mode}-edit`).disabled = mode === 'history' && !source?.identity();
    });
  }
  function openEditor(mode, trigger) {
    adapter = mode === 'live' ? window.tableLiveRoster : window.tableHistoryRoster;
    if (!adapter) return;
    identity = adapter.identity();
    if (!identity) return;
    draft = adapter.get().map(row => ({ ...row }));
    const sameCampaign = mode === 'live' || window.tableHistoryCampaignId === $('campaignSelect').value;
    knownRows = sameCampaign ? (window.tableSavedRoster || []) : [];
    knownNames = sameCampaign ? (window.tableKnownPeople || []) : [];
    // Absent saved players stay selectable, without changing the actual session.
    knownRows.forEach(row => {
      if (!draft.some(item => rowKey(item) === rowKey(row))) draft.push({ ...row, included: false });
    });
    opener = trigger;
    search.value = ''; status.textContent = ''; addForm.hidden = true;
    renderDraft(); renderSearch(); dialog.showModal(); search.focus();
  }
  function renderDraft() {
    lists.replaceChildren();
    [['Players / DM', row => row.role !== 'NPC'], ['Other characters', row => row.role === 'NPC']].forEach(([heading, predicate]) => {
      lists.append(make('h3', 'system-label', heading));
      const entries = draft.map((row, index) => ({ row, index })).filter(({ row }) => predicate(row));
      if (!entries.length) lists.append(make('p', 'small text-muted', 'None added.'));
      entries.forEach(({ row, index }) => {
        const label = make('label', 'participant-option');
        const checkbox = make('input'); checkbox.type = 'checkbox'; checkbox.checked = Boolean(row.included);
        checkbox.dataset.rosterIndex = String(index);
        checkbox.addEventListener('change', () => { draft[index].included = checkbox.checked; });
        const value = make('span', '', rowName(row));
        if (row.characterName && row.characterName !== rowName(row)) value.append(make('small', '', row.characterName));
        label.append(checkbox, value); lists.append(label);
      });
    });
  }
  function renderSearch() {
    results.replaceChildren();
    const query = search.value.toLowerCase().trim();
    if (!query) { results.append(make('p', 'small text-muted', 'Search the saved campaign roster and NPC / historical name list.')); return; }
    const known = [...knownRows, ...knownNames.map(item => ({
      role: 'NPC', characterName: item.name, playerName: '', aliases: item.aliases || '', included: false
    }))];
    const matches = known.filter(row => `${rowName(row)} ${row.characterName || ''} ${row.aliases || ''}`.toLowerCase().includes(query));
    const seen = new Set();
    matches.filter(row => { const key = rowKey(row); if (seen.has(key)) return false; seen.add(key); return true; }).slice(0, 12).forEach(row => {
      results.append(button(`${rowName(row)} · ${row.role === 'NPC' ? 'Character' : row.role}`, () => {
        const existing = draft.find(item => rowKey(item) === rowKey(row));
        if (existing) existing.included = true;
        else draft.push({ ...row, included: true });
        renderDraft(); status.textContent = `${rowName(row)} selected. Save to apply.`;
      }));
    });
    if (!results.children.length) results.append(make('p', 'small text-muted', 'No saved campaign name matches. You can add a participant; this does not create canon.'));
  }
  async function saveDraft() {
    if (busy) return;
    busy = true;
    dialog.querySelectorAll('button, input, select').forEach(node => { node.disabled = true; });
    status.textContent = 'Saving session participants…';
    try {
      await adapter.save(draft.map(row => ({ ...row })), identity);
      dialog.close(); readouts();
    } catch (error) { status.textContent = error.message; }
    finally {
      busy = false;
      dialog.querySelectorAll('button, input, select').forEach(node => { node.disabled = false; });
    }
  }
  search.addEventListener('input', renderSearch);
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { draft = []; opener?.focus(); });
  window.addEventListener('table-roster-updated', readouts);
  $('sessions-select').addEventListener('change', () => {
    $('recent-play-history').textContent = 'Loading the selected session’s saved mini-summary…';
    $('recent-play-history-time').textContent = '';
    readouts();
  });
  readouts();
})();
