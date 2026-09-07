/* Table-ready presentation and focused Session Memory interactions. Existing nodes retain their IDs and listeners. */
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
    $('sessions-memory-publication').scrollIntoView({ block: 'center' });
    $('sessions-memory-preview-publish').focus();
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
      tab('tab-sessions');
      $('history-legacy').open = true;
      $('history-publishing').open = true;
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
  const publishing = fold('Legacy player recap / campaign handoff & DungeonShare draft');
  publishing.id = 'history-publishing';
  const recap = $('sessions-game-summary').closest('.section-card');
  const narrative = $('sessions-game-narrative').closest('.section-card');
  publishing.append(recap);
  const legacyNarrative = fold('Legacy Narrative / Recap', narrative);
  legacyNarrative.id = 'history-narrative';
  historyLegacy.append(publishing, legacyNarrative);
  const evidence = $('sessions-transcript').closest('.section-card');
  historyLegacy.append(fold('Transcript & evidence tools', evidence));
  const historyIntro = $('pane-sessions').querySelector('.help-callout .small.text-muted');
  historyIntro.textContent = 'Select a session. Review its participants and durable Session Memory. Older outputs and repair tools are below.';

  // Canonical Session Memory editing is draft-first: only Save writes, and publishing is separate.
  const memoryEditTrigger = $('sessions-edit-memory');
  const memoryEditDialog = $('sessions-memory-edit-dialog');
  const memoryEditClose = $('sessions-memory-edit-close');
  const memoryEditCancel = $('sessions-memory-edit-cancel');
  const memoryEditSave = $('sessions-memory-edit-save');
  const memoryEditStatus = $('sessions-memory-edit-status');
  const memoryEditRevision = $('sessions-memory-edit-revision');
  const memoryEditBody = $('sessions-memory-edit-body');
  const memoryEditEvents = $('sessions-memory-edit-events');
  const memoryEditHighlights = $('sessions-memory-edit-highlights');
  const memoryConflictPanel = $('sessions-memory-conflicts');
  const memoryPublishPanel = $('sessions-memory-publication');
  const memoryPublishState = $('sessions-memory-publish-state');
  const memoryCampaignSelect = $('sessions-memory-dungeonshare-campaign');
  const memoryPreviewTrigger = $('sessions-memory-preview-publish');
  const memoryPublishDialog = $('sessions-memory-publish-dialog');
  const memoryPublishClose = $('sessions-memory-publish-close');
  const memoryPublishCancel = $('sessions-memory-publish-cancel');
  const memoryPublishConfirm = $('sessions-memory-publish-confirm');
  const memoryPublishDialogStatus = $('sessions-memory-publish-dialog-status');
  const memoryPreviewContent = $('sessions-memory-preview-content');
  const copyValue = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const emptyMemoryState = () => ({
    sessionId: '', status: {},
    memory: {
      revision: 0, editedAt: null, hasHumanEdits: false, canonicalDigest: '',
      events: [], highlights: [], removedEvents: [], removedHighlights: [], conflicts: [],
    },
    publication: { state: 'not_published', lastSuccessful: null, lastAttempt: null },
  });
  let memoryUiState = emptyMemoryState();
  let memoryDraft = null;
  let memoryEditPending = false;
  let memoryPublishPending = false;
  let memoryBuildPending = false;
  let memoryEditOpener = null;
  let memoryPublishOpener = null;
  let memoryPreviewSnapshot = null;
  let memoryPreviewRequest = 0;

  function recordId(kind, record) {
    return String(record?.[kind === 'event' ? 'eventId' : 'highlightId'] || record?.id || '').trim();
  }

  function listValues(value, objectKeys = []) {
    if (!Array.isArray(value)) return [];
    return value.map(item => {
      if (typeof item === 'string' || typeof item === 'number') return String(item).trim();
      if (!item || typeof item !== 'object') return '';
      for (const key of objectKeys) {
        const candidate = String(item[key] || '').trim();
        if (candidate) return candidate;
      }
      return '';
    }).filter(Boolean);
  }

  function listText(value, objectKeys = []) {
    return listValues(value, objectKeys).join('\n');
  }

  function editableValues(kind, record) {
    if (kind === 'event') {
      return {
        summary: String(record?.summary || ''),
        facts: listText(record?.facts, ['summary', 'text', 'fact']),
        entities: listText(record?.entities, ['canonicalName', 'displayName', 'name', 'label']),
        type: String(record?.type || 'other'),
        status: String(record?.status || 'unresolved'),
        importance: String(record?.importance || 'medium'),
        confidence: String(record?.confidence || 'unknown'),
      };
    }
    return {
      summary: String(record?.summary || ''),
      categories: listValues(record?.categories).join(', '),
      participants: listText(record?.participants, ['displayName', 'name', 'label']),
      confidence: String(record?.confidence || 'unknown'),
    };
  }

  function makeDraftEntries(kind, current, removed) {
    const entries = [];
    const seen = new Set();
    const append = (record, isRemoved) => {
      const id = recordId(kind, record);
      if (!id || seen.has(id)) return;
      seen.add(id);
      const values = editableValues(kind, record);
      entries.push({
        kind, id, record: copyValue(record), initialRemoved: isRemoved, removed: isRemoved,
        canRestore: !isRemoved || record?.canRestore !== false,
        baseline: copyValue(values), values: copyValue(values),
      });
    };
    (Array.isArray(current) ? current : []).forEach(record => append(record, false));
    (Array.isArray(removed) ? removed : []).forEach(record => append(record, true));
    return entries;
  }

  function memoryChoiceField(entry, field, labelText, choices) {
    const label = make('label', 'memory-edit-field');
    label.append(make('span', 'system-label', labelText));
    const select = make('select', 'form-select form-select-sm');
    select.dataset.memoryKind = entry.kind;
    select.dataset.memoryId = entry.id;
    select.dataset.memoryField = field;
    choices.forEach(value => {
      const option = make('option', '', value.replace(/[_-]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase()));
      option.value = value;
      select.append(option);
    });
    if (!choices.includes(entry.values[field])) {
      const option = make('option', '', entry.values[field] || 'Other');
      option.value = entry.values[field];
      select.append(option);
    }
    select.value = entry.values[field];
    select.disabled = entry.removed;
    label.append(select);
    return label;
  }

  function memoryTextField(entry, field, labelText, rows = 1, help = '') {
    const label = make('label', 'memory-edit-field');
    label.append(make('span', 'system-label', labelText));
    const input = rows > 1 ? make('textarea', 'form-control') : make('input', 'form-control');
    if (rows > 1) input.rows = rows;
    input.value = entry.values[field];
    input.dataset.memoryKind = entry.kind;
    input.dataset.memoryId = entry.id;
    input.dataset.memoryField = field;
    input.disabled = entry.removed;
    if (field === 'summary') input.maxLength = entry.kind === 'event' ? 2000 : 1200;
    label.append(input);
    if (help) label.append(make('small', 'text-muted', help));
    return label;
  }

  function renderMemoryDraftCard(entry) {
    const card = make('article', `memory-edit-card${entry.removed ? ' is-removed' : ''}`);
    card.dataset.memoryKind = entry.kind;
    card.dataset.memoryId = entry.id;
    const header = make('div', 'memory-edit-card-header');
    const heading = make('div');
    heading.append(make('div', 'system-label', entry.kind === 'event' ? 'Event' : 'Highlight'));
    heading.append(make('div', 'small text-muted', entry.removed
      ? (entry.canRestore
          ? 'Removed from canonical Session Memory. Restore to include it again.'
          : 'No longer available in generated Session Memory; rebuild review is required.')
      : 'Canonical content; generated provenance remains unchanged.'));
    const toggle = make('button', `btn btn-sm ${entry.removed ? 'btn-outline-primary' : ''}`,
      entry.removed ? (entry.canRestore ? 'Restore' : 'Restore unavailable') : 'Remove from Session Memory');
    toggle.type = 'button';
    toggle.disabled = entry.removed && !entry.canRestore;
    toggle.dataset.memoryToggle = 'true';
    toggle.dataset.memoryKind = entry.kind;
    toggle.dataset.memoryId = entry.id;
    header.append(heading, toggle);
    card.append(header);
    const fields = make('div', 'memory-edit-fields');
    fields.append(memoryTextField(entry, 'summary', 'Summary', 2));
    if (entry.kind === 'event') {
      fields.append(memoryTextField(entry, 'facts', 'Facts', 4, 'One fact per line.'));
      fields.append(memoryTextField(entry, 'entities', 'Entities', 3, 'One canonical display name per line.'));
      const choices = make('div', 'memory-edit-choices');
      choices.append(memoryTextField(entry, 'type', 'Type'));
      choices.append(memoryChoiceField(entry, 'status', 'Status', ['active', 'unresolved', 'resolved']));
      choices.append(memoryChoiceField(entry, 'importance', 'Importance', ['low', 'medium', 'high', 'critical']));
      choices.append(memoryChoiceField(entry, 'confidence', 'Confidence', ['unknown', 'low', 'medium', 'high']));
      fields.append(choices);
    } else {
      fields.append(memoryTextField(entry, 'categories', 'Categories', 1, 'Separate categories with commas.'));
      fields.append(memoryTextField(entry, 'participants', 'Participants', 3, 'One participant per line.'));
      fields.append(memoryChoiceField(entry, 'confidence', 'Confidence', ['unknown', 'low', 'medium', 'high']));
    }
    card.append(fields);
    return card;
  }

  function renderMemoryDraft() {
    memoryEditEvents.replaceChildren();
    memoryEditHighlights.replaceChildren();
    if (!memoryDraft) return;
    const events = memoryDraft.items.filter(item => item.kind === 'event');
    const highlights = memoryDraft.items.filter(item => item.kind === 'highlight');
    events.forEach(entry => memoryEditEvents.append(renderMemoryDraftCard(entry)));
    highlights.forEach(entry => memoryEditHighlights.append(renderMemoryDraftCard(entry)));
    if (!events.length) memoryEditEvents.append(make('p', 'memory-empty', 'No Events are available to edit.'));
    if (!highlights.length) memoryEditHighlights.append(make('p', 'memory-empty', 'No Highlights are available to edit.'));
    updateMemoryEditSaveState();
  }

  function splitLines(value) {
    return String(value || '').split(/\r?\n/).map(item => item.trim()).filter((item, index, all) => item && all.indexOf(item) === index);
  }

  function splitCategories(value) {
    return String(value || '').split(/[\r\n,]+/).map(item => item.trim().toLowerCase()).filter((item, index, all) => item && all.indexOf(item) === index);
  }

  function normalizedField(entry, field, value) {
    if (field === 'facts' || field === 'entities' || field === 'participants') return splitLines(value);
    if (field === 'categories') return splitCategories(value);
    return String(value || '').trim();
  }

  function draftChanges() {
    const changes = [];
    if (!memoryDraft) return { changes, error: '' };
    for (const entry of memoryDraft.items) {
      if (!entry.removed && !String(entry.values.summary || '').trim()) {
        return { changes: [], error: `${entry.kind === 'event' ? 'Event' : 'Highlight'} summary cannot be empty.` };
      }
      if (!entry.removed && entry.kind === 'event' && !String(entry.values.type || '').trim()) {
        return { changes: [], error: 'Event type cannot be empty.' };
      }
      if (!entry.removed && entry.kind === 'highlight' && !splitCategories(entry.values.categories).length) {
        return { changes: [], error: 'Each included Highlight needs at least one category.' };
      }
      if (entry.removed !== entry.initialRemoved) {
        changes.push({ kind: entry.kind, id: entry.id, action: entry.removed ? 'remove' : 'restore' });
      }
      if (entry.removed) continue;
      const fields = {};
      Object.keys(entry.values).forEach(field => {
        const before = normalizedField(entry, field, entry.baseline[field]);
        const after = normalizedField(entry, field, entry.values[field]);
        if (JSON.stringify(before) !== JSON.stringify(after)) fields[field] = after;
      });
      if (Object.keys(fields).length) changes.push({ kind: entry.kind, id: entry.id, action: 'update', fields });
    }
    return { changes, error: '' };
  }

  function updateMemoryEditSaveState() {
    const result = draftChanges();
    memoryEditSave.disabled = memoryEditPending || !memoryDraft || Boolean(result.error) || !result.changes.length;
    if (!memoryEditPending) memoryEditStatus.textContent = result.error;
  }

  function updateDraftInput(event) {
    const input = event.target.closest?.('[data-memory-field]');
    if (!input || !memoryDraft) return;
    const entry = memoryDraft.items.find(item => item.kind === input.dataset.memoryKind && item.id === input.dataset.memoryId);
    if (!entry || entry.removed) return;
    entry.values[input.dataset.memoryField] = input.value;
    updateMemoryEditSaveState();
  }

  function closeMemoryEditor() {
    if (!memoryEditPending && memoryEditDialog.open) memoryEditDialog.close();
  }

  function openMemoryEditor() {
    const state = copyValue(window.tableSessionMemory?.get?.() || memoryUiState) || emptyMemoryState();
    const sessionId = String(state.sessionId || window.tableSessionMemory?.identity?.() || '').trim();
    const memory = state.memory || {};
    if (!sessionId || !Number(memory.revision || 0)) return;
    memoryEditOpener = document.activeElement;
    memoryDraft = {
      sessionId,
      baseRevision: Number(memory.revision || 0),
      baseDigest: String(memory.canonicalDigest || ''),
      items: [
        ...makeDraftEntries('event', memory.events, memory.removedEvents),
        ...makeDraftEntries('highlight', memory.highlights, memory.removedHighlights),
      ],
    };
    memoryEditRevision.textContent = `Revision ${memoryDraft.baseRevision}`;
    memoryEditStatus.textContent = '';
    renderMemoryDraft();
    memoryEditDialog.showModal();
    memoryEditDialog.querySelector('[data-memory-field]:not(:disabled)')?.focus();
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const json = await response.json().catch(() => ({}));
    if (!response.ok || !json.ok) throw new Error(json?.error || `HTTP ${response.status}`);
    return json;
  }

  async function saveMemoryDraft() {
    if (!memoryDraft || memoryEditPending) return;
    const result = draftChanges();
    if (result.error) { memoryEditStatus.textContent = result.error; return; }
    if (!result.changes.length) { memoryEditStatus.textContent = 'No changes to save.'; return; }
    const savedSessionId = memoryDraft.sessionId;
    memoryEditPending = true;
    memoryEditStatus.textContent = 'Saving canonical Session Memory…';
    updateMemoryEditSaveState();
    try {
      await requestJson('/api/session/memory/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId: savedSessionId,
          baseRevision: memoryDraft.baseRevision,
          baseDigest: memoryDraft.baseDigest,
          changes: result.changes,
        }),
      });
      memoryEditPending = false;
      memoryEditDialog.close();
      try {
        await window.tableSessionMemory?.reload?.(savedSessionId);
      } catch (error) {
        $('sessions-memory-status').textContent = `SESSION MEMORY SAVED — Refresh needed: ${error.message}`;
      }
    } catch (error) {
      memoryEditPending = false;
      updateMemoryEditSaveState();
      memoryEditStatus.textContent = `SESSION MEMORY NEEDS ATTENTION — ${error.message}`;
    }
  }

  memoryEditBody.addEventListener('input', updateDraftInput);
  memoryEditBody.addEventListener('change', updateDraftInput);
  memoryEditBody.addEventListener('click', event => {
    const toggle = event.target.closest?.('[data-memory-toggle]');
    if (!toggle || !memoryDraft || memoryEditPending) return;
    const entry = memoryDraft.items.find(item => item.kind === toggle.dataset.memoryKind && item.id === toggle.dataset.memoryId);
    if (!entry || (entry.removed && !entry.canRestore)) return;
    entry.removed = !entry.removed;
    renderMemoryDraft();
    memoryEditStatus.textContent = entry.removed
      ? 'Marked for removal. Save Changes to apply, or Restore / Cancel.'
      : 'Restored in this draft. Save Changes to apply.';
  });
  memoryEditTrigger.addEventListener('click', openMemoryEditor);
  memoryEditClose.addEventListener('click', closeMemoryEditor);
  memoryEditCancel.addEventListener('click', closeMemoryEditor);
  memoryEditSave.addEventListener('click', saveMemoryDraft);
  memoryEditDialog.addEventListener('cancel', event => { if (memoryEditPending) event.preventDefault(); });
  memoryEditDialog.addEventListener('close', () => {
    memoryDraft = null;
    memoryEditStatus.textContent = '';
    memoryEditOpener?.focus?.();
  });

  function dateLabel(value) {
    if (!value) return '';
    const numeric = Number(value);
    const date = new Date(Number.isFinite(numeric) && numeric > 0
      ? (numeric < 1e12 ? numeric * 1000 : numeric)
      : value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric'
    });
  }

  function publicationRevision(value) {
    const revision = Number(value?.memoryRevision ?? value?.revision ?? value?.publishedRevision ?? 0);
    return Number.isFinite(revision) && revision > 0 ? revision : 0;
  }

  function failedPublicationAttempt(publication) {
    const attempt = publication?.lastAttempt && typeof publication.lastAttempt === 'object'
      ? publication.lastAttempt : null;
    const state = String(publication?.state || '').toLowerCase();
    const status = String(attempt?.status || attempt?.state || '').toLowerCase();
    const failed = state.includes('fail') || state.includes('attention') || status.includes('fail') || status.includes('error');
    if (!failed) return null;
    return {
      at: attempt?.occurredAt ?? attempt?.attemptedAt ?? attempt?.publishedAt ?? attempt?.updatedAt,
      error: String(attempt?.error || attempt?.message || publication?.error || 'DungeonShare did not accept the publication.').trim(),
    };
  }

  function conflictMessage(conflict) {
    if (typeof conflict === 'string') return conflict.trim();
    if (!conflict || typeof conflict !== 'object') return '';
    return String(conflict.message || conflict.reason || conflict.summary || conflict.id || '').trim();
  }

  function renderMemoryConflicts(memory) {
    const conflicts = Array.isArray(memory?.conflicts) ? memory.conflicts : [];
    memoryConflictPanel.replaceChildren();
    memoryConflictPanel.hidden = !conflicts.length;
    if (!conflicts.length) return;
    memoryConflictPanel.append(make('div', 'system-label', 'Session Memory needs review'));
    memoryConflictPanel.append(make('p', 'small mb-2', 'Publishing is disabled until these human-edit conflicts are resolved.'));
    const list = make('ul', 'small mb-0');
    conflicts.slice(0, 8).forEach(conflict => {
      const message = conflictMessage(conflict);
      if (message) list.append(make('li', '', message));
    });
    if (list.children.length) memoryConflictPanel.append(list);
  }

  function renderPublicationState() {
    const memory = memoryUiState.memory || {};
    const publication = memoryUiState.publication || {};
    const currentRevision = Number(memory.revision || 0);
    const successful = publication.lastSuccessful && typeof publication.lastSuccessful === 'object'
      ? publication.lastSuccessful : null;
    const publishedRevision = publicationRevision(successful);
    const publishedAt = dateLabel(successful?.occurredAt ?? successful?.publishedAt ?? successful?.succeededAt ?? successful?.updatedAt);
    const explicitState = String(publication.state || '').toLowerCase();
    const stale = Boolean(successful && (
      explicitState === 'stale' || explicitState.includes('update') ||
      (currentRevision && publishedRevision && currentRevision > publishedRevision)
    ));
    const failure = failedPublicationAttempt(publication);
    memoryPublishState.replaceChildren();
    memoryPublishState.className = `memory-publication-state${stale ? ' is-stale' : ''}${failure ? ' needs-attention' : ''}`;
    if (!successful) {
      memoryPublishState.append(make('div', 'data-readout', 'Not published'));
    } else {
      const pieces = [`Published Revision ${publishedRevision || '—'}`];
      if (publishedAt) pieces.push(publishedAt);
      memoryPublishState.append(make('div', 'data-readout', pieces.join(' · ')));
      if (stale) {
        memoryPublishState.append(make('div', 'memory-update-available', `Current Revision ${currentRevision} · UPDATE AVAILABLE`));
      } else {
        memoryPublishState.append(make('div', 'small', 'DungeonShare is current.'));
      }
    }
    if (failure) {
      const when = dateLabel(failure.at);
      memoryPublishState.append(make('div', 'memory-publish-attention',
        `DUNGEONSHARE PUBLISH NEEDS ATTENTION${when ? ` · ${when}` : ''} — ${failure.error}`));
    }
    memoryPreviewTrigger.textContent = stale ? 'Preview & Republish' : 'Preview & Publish';
    renderMemoryConflicts(memory);
  }

  function syncMemoryControls(nextState) {
    const source = nextState && typeof nextState === 'object'
      ? nextState : (window.tableSessionMemory?.get?.() || emptyMemoryState());
    memoryUiState = copyValue(source) || emptyMemoryState();
    const memory = memoryUiState.memory || {};
    const status = memoryUiState.status || {};
    const hasRecords = [memory.events, memory.highlights, memory.removedEvents, memory.removedHighlights]
      .some(records => Array.isArray(records) && records.length);
    const built = Boolean(Number(memory.revision || 0) || hasRecords || status?.memory?.built);
    const running = Boolean(status?.operation?.running || memoryBuildPending);
    const conflicts = Array.isArray(memory.conflicts) ? memory.conflicts : [];
    memoryEditTrigger.disabled = Boolean(!built || running || memoryEditPending || memoryPublishPending);
    memoryPreviewTrigger.disabled = Boolean(
      !built || running || memoryEditPending || memoryPublishPending || conflicts.length ||
      memoryCampaignSelect.disabled || !String(memoryCampaignSelect.value || '').trim()
    );
    memoryPublishPanel.classList.toggle('has-update', String(memoryUiState.publication?.state || '').toLowerCase() === 'stale');
    renderPublicationState();
  }

  function previewMemoryData(preview, payload) {
    if (preview?.memory && typeof preview.memory === 'object') return preview.memory;
    if (payload?.memory && typeof payload.memory === 'object') return payload.memory;
    return {
      revision: preview?.revision,
      events: preview?.events,
      highlights: preview?.highlights,
    };
  }

  function renderPreviewList(container, headingText, records, kind) {
    const section = make('section', 'memory-preview-section');
    const heading = make('div', 'memory-preview-heading');
    heading.append(make('h3', 'panel-header mb-0', headingText));
    heading.append(make('span', 'memory-tag', String(records.length)));
    section.append(heading);
    if (!records.length) {
      section.append(make('p', 'memory-empty', `No ${headingText.toLowerCase()} will be published.`));
    }
    records.forEach(record => {
      const card = make('article', `memory-item${kind === 'highlight' ? ' memory-item-highlight' : ''}`);
      card.append(make('div', 'memory-title', `${kind === 'highlight' ? '★ ' : ''}${String(record.summary || `Untitled ${kind}`)}`));
      if (kind === 'event') {
        const facts = listValues(record.facts, ['summary', 'text', 'fact']);
        if (facts.length) {
          const list = make('ul', 'memory-facts');
          facts.forEach(fact => list.append(make('li', '', fact)));
          card.append(list);
        }
      } else {
        const participants = listValues(record.participants, ['displayName', 'name', 'label']);
        if (participants.length) card.append(make('div', 'small text-muted mt-2', `Participants: ${participants.join(', ')}`));
      }
      section.append(card);
    });
    container.append(section);
  }

  function renderPublishPreview(preview, payload) {
    memoryPreviewContent.replaceChildren();
    const session = (preview?.session && typeof preview.session === 'object' ? preview.session : null)
      || (payload?.session && typeof payload.session === 'object' ? payload.session : {});
    const memory = previewMemoryData(preview, payload);
    const events = Array.isArray(memory.events) ? memory.events : [];
    const highlights = Array.isArray(memory.highlights) ? memory.highlights : [];
    const header = make('header', 'memory-preview-header');
    header.append(make('div', 'soft-label', 'Session snapshot'));
    header.append(make('h3', 'h4 mb-1', String(session.title || preview?.title || 'Session Memory')));
    const date = String(session.date || session.eventDate || preview?.date || '').trim();
    const revision = Number(memory.revision || preview?.revision || 0);
    header.append(make('div', 'small text-muted', `${date ? `${date} · ` : ''}Revision ${revision || '—'} · ${events.length} Events · ${highlights.length} Highlights`));
    memoryPreviewContent.append(header);
    renderPreviewList(memoryPreviewContent, 'Highlights', highlights, 'highlight');
    renderPreviewList(memoryPreviewContent, 'Events', events, 'event');
  }

  async function openPublishPreview() {
    if (memoryPublishPending) return;
    const state = copyValue(window.tableSessionMemory?.get?.() || memoryUiState) || emptyMemoryState();
    const sessionId = String(state.sessionId || window.tableSessionMemory?.identity?.() || '').trim();
    const campaignSlug = String(memoryCampaignSelect.value || '').trim();
    const conflicts = Array.isArray(state.memory?.conflicts) ? state.memory.conflicts : [];
    if (!sessionId || !campaignSlug || !Number(state.memory?.revision || 0) || conflicts.length) {
      renderMemoryConflicts(state.memory || {});
      return;
    }
    memoryPublishOpener = document.activeElement;
    memoryPreviewSnapshot = null;
    memoryPreviewContent.replaceChildren(make('p', 'memory-empty', 'Preparing the deterministic DungeonShare preview…'));
    memoryPublishDialogStatus.textContent = '';
    memoryPublishConfirm.disabled = true;
    memoryPublishDialog.showModal();
    const requestId = ++memoryPreviewRequest;
    try {
      const json = await requestJson(
        `/api/session/dungeonshare/preview?sessionId=${encodeURIComponent(sessionId)}&campaignSlug=${encodeURIComponent(campaignSlug)}`,
        { cache: 'no-store' }
      );
      if (requestId !== memoryPreviewRequest || !memoryPublishDialog.open) return;
      const preview = json.preview && typeof json.preview === 'object' ? json.preview : {};
      const payload = json.payload && typeof json.payload === 'object' ? json.payload : {};
      const memory = previewMemoryData(preview, payload);
      memoryPreviewSnapshot = {
        sessionId,
        campaignSlug,
        expectedRevision: Number(memory.revision || state.memory.revision || 0),
        expectedDigest: String(
          preview.canonicalDigest || memory.canonicalDigest || json.canonicalDigest || json.memoryDigest || state.memory.canonicalDigest || ''
        ),
      };
      renderPublishPreview(preview, payload);
      memoryPublishDialogStatus.textContent = 'Review the complete snapshot below. Nothing is published until you confirm.';
      memoryPublishConfirm.disabled = false;
      memoryPublishConfirm.focus();
    } catch (error) {
      if (requestId !== memoryPreviewRequest || !memoryPublishDialog.open) return;
      memoryPreviewContent.replaceChildren(make('p', 'memory-empty', 'The preview could not be prepared.'));
      memoryPublishDialogStatus.textContent = `DUNGEONSHARE PUBLISH NEEDS ATTENTION — ${error.message}`;
    }
  }

  function closePublishPreview() {
    if (memoryPublishPending) return;
    memoryPreviewRequest += 1;
    if (memoryPublishDialog.open) memoryPublishDialog.close();
  }

  async function publishMemoryPreview() {
    if (!memoryPreviewSnapshot || memoryPublishPending) return;
    const snapshot = copyValue(memoryPreviewSnapshot);
    memoryPublishPending = true;
    memoryPublishConfirm.disabled = true;
    memoryPublishCancel.disabled = true;
    memoryPublishClose.disabled = true;
    memoryPublishDialogStatus.textContent = 'Publishing this canonical revision to DungeonShare…';
    syncMemoryControls(memoryUiState);
    try {
      await requestJson('/api/session/dungeonshare/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId: snapshot.sessionId,
          campaignSlug: snapshot.campaignSlug,
          expectedRevision: snapshot.expectedRevision,
          expectedDigest: snapshot.expectedDigest,
          confirm: true,
        }),
      });
      memoryPublishPending = false;
      memoryPublishCancel.disabled = false;
      memoryPublishClose.disabled = false;
      memoryPublishDialog.close();
      try {
        await window.tableSessionMemory?.reload?.(snapshot.sessionId);
      } catch (error) {
        $('sessions-memory-status').textContent = `PUBLISHED — Refresh needed: ${error.message}`;
      }
    } catch (error) {
      memoryPublishPending = false;
      memoryPublishConfirm.disabled = false;
      memoryPublishCancel.disabled = false;
      memoryPublishClose.disabled = false;
      memoryPublishDialogStatus.textContent = `DUNGEONSHARE PUBLISH NEEDS ATTENTION — ${error.message}`;
      syncMemoryControls(memoryUiState);
    }
  }

  memoryPreviewTrigger.addEventListener('click', openPublishPreview);
  memoryPublishConfirm.addEventListener('click', publishMemoryPreview);
  memoryPublishCancel.addEventListener('click', closePublishPreview);
  memoryPublishClose.addEventListener('click', closePublishPreview);
  memoryPublishDialog.addEventListener('cancel', event => {
    if (memoryPublishPending) event.preventDefault();
    else memoryPreviewRequest += 1;
  });
  memoryPublishDialog.addEventListener('close', () => {
    memoryPreviewSnapshot = null;
    memoryPreviewContent.replaceChildren();
    memoryPublishDialogStatus.textContent = '';
    memoryPublishConfirm.disabled = true;
    memoryPublishCancel.disabled = false;
    memoryPublishClose.disabled = false;
    memoryPublishOpener?.focus?.();
  });
  window.addEventListener('table-session-memory-updated', event => syncMemoryControls(event.detail));
  window.addEventListener('table-session-memory-busy', event => {
    if (!event.detail?.sessionId || event.detail.sessionId === memoryUiState.sessionId) {
      memoryBuildPending = Boolean(event.detail?.busy);
      syncMemoryControls(memoryUiState);
    }
  });
  window.addEventListener('table-dungeonshare-campaigns-updated', () => syncMemoryControls(memoryUiState));
  syncMemoryControls();

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
