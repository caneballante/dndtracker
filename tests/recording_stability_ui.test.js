'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { DurableChunkQueue, acquireRecordingLease } = require('../recording-storage.js');
const { CaptureReliabilityController } = require('../capture-reliability.js');
const html = fs.readFileSync(path.join(__dirname, '../dnd-audio.html'), 'utf8');
function store() {
  const rows = new Map();
  return { rows, put: async item => rows.set(item.id, structuredClone(item)),
    get: async id => structuredClone(rows.get(id)), remove: async id => rows.delete(id),
    list: async () => [...rows.values()].map(({ blob, ...metadata }) => metadata) };
}
const chunk = (index, value = 'audio') => ({ sessionId: '1234567890123', index, blob: new Blob([value]), mimeType: 'audio/webm', deferTranscription: true });
const queue = (storage, upload = async () => ({}), extras = {}) => new DurableChunkQueue({
  store: storage, upload, setTimeout: () => 1, clearTimeout() {}, ...extras });

test('crash checkpoints recover an ordered container prefix and are cleaned only after receipt', async () => {
  const storage = store(), first = queue(storage);
  await first.init();
  await first.checkpoint({ ...chunk(7, 'header+first'), part: 0 });
  await first.checkpoint({ ...chunk(7, '+second'), part: 1 });
  const sent = [], recovered = queue(storage, async item => { sent.push(await item.blob.text()); });
  await recovered.init();
  assert.equal(recovered.highestIndex('1234567890123'), 7);
  assert.equal(storage.rows.size, 3);
  await recovered.pump();
  assert.deepEqual(sent, ['header+first+second']);
  assert.equal(storage.rows.size, 0);
});

test('five hours of queued chunks stay on disk and resume in order', async () => {
  const storage = store(), sent = [];
  const first = queue(storage); await first.init();
  for (let index = 0; index < 150; index++) await first.enqueue(chunk(index, `chunk-${index}`));
  assert.equal([...first.items.values()].filter(item => item.blob).length, 0);
  const restored = queue(storage, async item => sent.push(item.index)); await restored.init();
  for (let i = 0; i < 150; i++) await restored.pump();
  assert.deepEqual(sent, Array.from({ length: 150 }, (_, i) => i));
  assert.equal(storage.rows.size, 0);
});

test('quota during WAV expansion uploads original bytes with stable source identity', async () => {
  const storage = store(), originalPut = storage.put;
  storage.put = async item => { if (item.prepared) throw new Error('QuotaExceededError'); return originalPut(item); };
  const received = [];
  const q = queue(storage, async item => { received.push({ bytes: await item.blob.text(), digest: item.sourceSha256 }); },
    { prepare: async () => ({ blob: new Blob(['expanded WAV']), mimeType: 'audio/wav' }) });
  await q.init(); await q.enqueue(chunk(0, 'compressed')); await q.pump();
  assert.equal(received[0].bytes, 'compressed');
  assert.match(received[0].digest, /^[a-f0-9]{64}$/);
  assert.equal(q.snapshot().pending, 0);
});

test('a second tab cannot own the recorder or mutate its queue', async () => {
  let held = false;
  const locks = { request: async (name, options, callback) => {
    if (held) return callback(null);
    held = true; try { await callback({ name }); } finally { held = false; }
  } };
  const first = await acquireRecordingLease(locks);
  assert.equal(await acquireRecordingLease(locks), false);
  first.release(); await new Promise(resolve => setImmediate(resolve));
  const next = await acquireRecordingLease(locks); assert.ok(next); next.release();
});

test('retry reloads the metadata belonging to persisted bytes after quota failure', async () => {
  const storage = store(), originalPut = storage.put;
  storage.put = async item => { if (item.prepared) throw new Error('quota'); return originalPut(item); };
  let attempts = 0, preparations = 0, now = 1000;
  const q = queue(storage, async item => {
    assert.equal(item.mimeType, 'audio/webm');
    assert.equal(await item.blob.text(), 'compressed');
    if (++attempts === 1) throw new Error('lost receipt');
  }, { now: () => now, prepare: async () => { preparations++; return { blob: new Blob(['WAV']), mimeType: 'audio/wav' }; } });
  await q.init(); await q.enqueue(chunk(0, 'compressed')); await q.pump();
  now += 30000; await q.pump();
  assert.equal(preparations, 2);
  assert.equal(q.snapshot().pending, 0);
});

test('other-tab status takes precedence over a stale restored interruption', () => {
  const classes = { add() {}, remove() {} };
  const context = vm.createContext({ recordingOwnedElsewhere: true,
    captureController: { snapshot: () => ({ state: 'interrupted' }) },
    statusDot: { classList: classes }, statusText: { classList: classes } });
  vm.runInContext(html.slice(html.indexOf('  function setStatus(state)'), html.indexOf('  function startCountdown(')), context);
  context.setStatus('idle');
  assert.equal(context.statusText.textContent, 'Recording managed in another tab');
});

for (const recovery of [false, true]) test(`watchdog allows a full new interval after long ${recovery ? 'recovery' : 'pause'}`, async () => {
  let now = 1000;
  const signals = { recorderState: 'recording', streamActive: true, trackState: 'live' };
  const c = new CaptureReliabilityController({ now: () => now, signalProvider: () => signals,
    setInterval: () => 1, clearInterval() {} });
  await c.start({ sessionId: '1234567890123', chunkIntervalMs: 120000 });
  c.updateSaveQueue({ ready: true }); c.chunkEmitted(0);
  if (recovery) c.interrupt('track_ended'); else signals.paused = true;
  for (let i = 0; i < 12; i++) { now += 20000; c.observe(); }
  signals.paused = false;
  if (recovery) { c.beginRecovery(); await c.recovered(); }
  c.recorderStarted(); now += 1000; c.observe();
  assert.notEqual(c.snapshot().state, 'interrupted');
});

test('Stop waits for saves and automatically finalizes after server returns', async () => {
  const storage = store(), q = queue(storage); await q.init(); await q.enqueue(chunk(0));
  const local = new Map(), states = [], calls = [];
  let online = false;
  const context = vm.createContext({ recordingQueue: q, sessionId: '1234567890123',
    highestEmittedChunkIndex: 0, chunkIndex: 1, stopFinalizationPromise: null, deferTranscription: true, startBtn: {}, recordAudioOnlyEl: {},
    localStorage: { getItem: key => local.get(key), setItem: (key, value) => local.set(key, value) },
    setInterval() {}, log() {}, window: {}, waitForPendingChunkUploads: async () => {},
    captureController: { snapshot: () => ({ sessionOpen: false }), beginFinalization() {},
      finalizationPending: value => states.push(value), finalizeFailed: () => assert.fail('normal Stop is not an error') },
    postCapture: async (url, body) => { if (!online) throw new Error('offline'); calls.push(body); return { finalization: { state: 'waiting_for_transcription' } }; },
  });
  vm.runInContext(html.slice(html.indexOf('  const finalizationStorageKey'), html.indexOf('  captureRecoverBtn?.addEventListener')), context);
  vm.runInContext(html.slice(html.indexOf('  function expectedAudioBoundary()'), html.indexOf('  function stopChunkTimer()')), context);
  await context.finalizeStoppedSession();
  assert.equal(calls.length, 0);
  await q.pump(); await context.settlePendingFinalizations();
  assert.equal(states.at(-1), 'waiting_for_server');
  online = true; await context.settlePendingFinalizations();
  assert.equal(calls.length, 1);
  assert.equal(states.at(-1), 'waiting_for_transcription');
  assert.deepEqual(JSON.parse(local.get('dungeontracker.finalizations.v1')), {});
});

test('recovery starts microphone while the local server is down', async () => {
  let started = 0;
  const context = vm.createContext({ sessionId: '1234567890123', chunkMs: 0, chunkIndex: 2,
    recoveryAttempt: null, captureOperationId: 0, recorderStopReasons: new WeakMap(), stopRequested: false, paused: false,
    setTimeout: () => 1, clearTimeout() {},
    highestEmittedChunkIndex: 1, selectedDeviceId: '', recorder: null, stream: null,
    captureController: { beginRecovery: () => true, recoveryStage() {}, snapshot: () => ({ state: 'recovering', chunkIntervalMs: 120000 }), recoveryFailed: e => assert.fail(e.message) },
    recordingQueueReady: Promise.resolve(), recordingQueue: { ready: true, highestIndex: () => 4 },
    localStorage: { getItem: () => '6', setItem() {} }, recordingSessionMeta: {}, sessionInitPending: false,
    startBtn: {}, chunkCounterEl: {}, recordAudioOnlyEl: { checked: true },
    pauseBtn: {}, resumeBtn: {}, stopBtn: {}, log() {}, localFetch: async () => { throw new Error('offline'); },
    stopChunkTimer() {}, attachCaptureStreamHandlers() {}, startMeter() {}, pickMimeType: () => 'audio/webm',
    startRecorderCycle: () => { started++; }, navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
  });
  vm.runInContext(html.slice(html.indexOf('  async function recoverCapture()'), html.indexOf('  async function finishPendingFinalization()')), context);
  await context.recoverCapture();
  assert.equal(started, 1);
  assert.equal(context.chunkIndex, 6);
});
