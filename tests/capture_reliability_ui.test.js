'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  CaptureReliabilityController,
  STATES,
  STORAGE_KEY,
} = require('../capture-reliability.js');

test('offline saves and heartbeat failures do not falsely declare microphone capture stopped', async () => {
  const h = harness({ sendHeartbeat: async () => { throw new Error('local server unavailable'); } });
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.controller.updateSaveQueue({ ready: true, pending: 1 });
  for (let i = 0; i < 12; i++) {
    h.advance(20000);
    if (i === 5 || i === 11) { h.controller.chunkEmitted(i); h.controller.chunkLocallySaved(); }
    await h.controller.heartbeat(); h.controller.observe();
  }
  assert.notEqual(h.controller.snapshot().state, STATES.INTERRUPTED);
  assert.equal(h.alerts.length, 0);
  h.controller.trackEnded();
  assert.equal(h.controller.snapshot().state, STATES.INTERRUPTED);
});

test('reloaded recording restores interval and recovery never invents a saved chunk', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 180000 });
  const reload = harness({ storage: h.storage });
  assert.equal(reload.controller.restorePending().chunkIntervalMs, 180000);
  assert.equal(reload.controller.beginRecovery(), true);
  await reload.controller.recovered();
  assert.equal(reload.controller.snapshot().lastChunkAcknowledgedAtMs, null);
});

test('deferred transcription after saved offline audio is not restored as a recording error', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.controller.beginFinalization(0);
  h.controller.finalizationPending('waiting_for_transcription');
  const reload = harness({ storage: h.storage });
  assert.equal(reload.controller.restorePending().state, STATES.FINALIZING);
});

function harness(options = {}) {
  let now = 1000;
  let nextTimer = 1;
  const timers = new Map();
  const diagnostics = [];
  const alerts = [];
  const states = [];
  const heartbeats = [];
  const storageData = new Map();
  const storage = options.storage || {
    getItem: key => storageData.get(key) || null,
    setItem: (key, value) => storageData.set(key, value),
    removeItem: key => storageData.delete(key),
  };
  const signals = { recorderState: 'recording', streamActive: true, trackState: 'live' };
  const controller = new CaptureReliabilityController({
    now: () => now,
    setInterval: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id),
    signalProvider: () => ({ ...signals }),
    sendHeartbeat: options.sendHeartbeat || (async body => {
      heartbeats.push(body);
      return { capture: { state: 'healthy', keepAwake: { supported: true, active: true } } };
    }),
    sendDiagnostic: async (event, details) => diagnostics.push({ event, details }),
    onState: state => states.push(state),
    onAlert: state => alerts.push(state),
    storage,
    wakeLockProvider: options.wakeLockProvider,
    document: options.document,
    chunkIntervalMs: 120000,
  });
  return {
    controller, diagnostics, alerts, states, signals, storage, storageData, timers, heartbeats,
    advance(ms) { now += ms; },
  };
}

test('browser wake lock is supplemental and failure does not stop recording', async () => {
  const h = harness({ wakeLockProvider: { request: async () => { throw new Error('denied'); } } });
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  assert.equal(h.controller.snapshot().state, STATES.HEALTHY);
  assert.equal(h.controller.snapshot().screenWakeLock.active, false);
  assert.equal(h.diagnostics.some(row => row.event === 'screen_wake_lock_failed'), true);
});

test('track, stream, recorder error, and unexpected stop immediately interrupt', async (t) => {
  for (const method of ['trackEnded', 'streamInactive', 'recorderError', 'unexpectedRecorderStop']) {
    await t.test(method, async () => {
      const h = harness();
      await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
      h.controller[method](new Error('lost'));
      assert.equal(h.controller.snapshot().state, STATES.INTERRUPTED);
      assert.equal(h.controller.snapshot().partialCapture, true);
    });
  }
});

test('healthy browser heartbeat cannot conceal stale durable audio', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  for (let i = 0; i < 8; i += 1) { h.advance(20000); h.controller.observe(); }
  assert.equal(h.controller.snapshot().state, STATES.INTERRUPTED);
  assert.equal(h.controller.snapshot().interruptionReason, 'chunk_watchdog_expired');
});

test('acknowledged chunks avoid false interruption', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  for (let i = 0; i < 12; i += 1) {
    h.advance(20000);
    if (i === 5 || i === 11) h.controller.chunkAcknowledged(i);
    h.controller.observe();
  }
  assert.notEqual(h.controller.snapshot().state, STATES.INTERRUPTED);
});

test('delayed browser timer is uncertainty, and a new checkpoint confirms capture', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.advance(90000);
  h.controller.observe();
  assert.equal(h.controller.snapshot().state, STATES.SUSPECT);
  assert.equal(h.diagnostics.some(row => row.event === 'capture_suspect'), true);
  h.controller.chunkLocallySaved();
  const state = h.controller.snapshot();
  assert.equal(state.sessionId, '12345678');
  assert.equal(state.state, STATES.HEALTHY);
  assert.equal(state.gaps.length, 0);
});

test('recovery failure remains red and first interruption requests one alert', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.controller.trackEnded();
  h.controller.trackEnded();
  assert.equal(h.alerts.length, 1);
  assert.equal(h.controller.beginRecovery(), true);
  h.controller.recoveryFailed(new Error('permission denied'));
  assert.equal(h.controller.snapshot().state, STATES.INTERRUPTED);
  assert.equal(h.controller.snapshot().recoveryRequired, true);
});

test('acknowledge silences alarm without claiming health', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.controller.streamInactive();
  h.controller.acknowledgeAlert();
  assert.equal(h.controller.snapshot().state, STATES.INTERRUPTED);
  assert.equal(h.controller.snapshot().alertAcknowledged, true);
});

test('pending finalization, gap, and unload warning survive reload', async () => {
  const h = harness();
  await h.controller.start({ sessionId: '12345678', chunkIntervalMs: 120000 });
  h.controller.streamInactive();
  assert.equal(h.controller.requiresUnloadWarning(), true);
  h.controller.beginFinalization(4);
  assert.equal(h.controller.requiresUnloadWarning(), true);
  const reload = harness({ storage: h.storage });
  const restored = reload.controller.restorePending();
  assert.equal(restored.sessionId, '12345678');
  assert.equal(restored.state, STATES.ERROR);
  assert.equal(restored.pendingFinalization.finalExpectedChunkIndex, 4);
  assert.equal(restored.openGap !== null, true);
  reload.controller.finalizeFailed(new Error('offline'));
  assert.equal(reload.controller.requiresUnloadWarning(), true);
  reload.controller.finalizationPending('waiting_for_transcription');
  assert.equal(reload.controller.snapshot().state, STATES.FINALIZING);
  assert.equal(reload.controller.requiresUnloadWarning(), false);
  reload.controller.finalizeCompleted();
  assert.equal(reload.controller.requiresUnloadWarning(), false);
  assert.equal(h.storage.getItem(STORAGE_KEY), null);
});
