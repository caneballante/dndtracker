'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { DurableChunkQueue } = require('../recording-storage.js');

function store() {
  const rows = new Map();
  return { rows, list: async () => [...rows.values()].map(item => structuredClone(item)),
    put: async item => rows.set(item.id, structuredClone(item)), remove: async id => rows.delete(id) };
}
function queue(storage, upload, extras = {}) {
  return new DurableChunkQueue({ store: storage, upload, setTimeout: () => 1, clearTimeout: () => {}, ...extras });
}
const chunk = index => ({ sessionId: '1234567890123', index, blob: new Blob(['audio']), mimeType: 'audio/webm', deferTranscription: true });

test('failed saves survive reload and retry the same prepared bytes', async () => {
  const storage = store();
  let converted = 0;
  const first = queue(storage, async () => { throw new Error('Failed to fetch'); }, {
    prepare: async item => { converted++; return { blob: new Blob(['WAV payload']), mimeType: 'audio/wav' }; }
  });
  await first.init();
  await first.enqueue(chunk(4));
  assert.equal(await storage.rows.get('1234567890123:4').blob.text(), 'audio');
  await first.pump();
  assert.equal(first.snapshot().pending, 1);
  const sent = [];
  const reloaded = queue(storage, async item => { sent.push(await item.blob.text()); return { ok: true }; }, {
    prepare: async () => { throw new Error('must not convert a retry again'); }
  });
  await reloaded.init();
  assert.equal(reloaded.highestIndex('1234567890123'), 4);
  await reloaded.pump();
  assert.deepEqual(sent, ['WAV payload']);
  assert.equal(converted, 1);
  assert.equal(storage.rows.size, 0);
});

test('one failed chunk does not prevent subsequent audio from being retained and saved', async () => {
  const storage = store(), saved = [];
  let now = 100;
  const q = queue(storage, async item => { if (item.index === 0) throw new Error('offline'); saved.push(item.index); }, { now: () => now });
  await q.init();
  await q.enqueue(chunk(0));
  await q.pump();
  await q.enqueue(chunk(1));
  await q.pump();
  assert.deepEqual(saved, [1]);
  assert.equal(storage.rows.size, 1);
});

test('permanent rejection retains audio and is not retried automatically', async () => {
  const storage = store();
  let calls = 0;
  const q = queue(storage, async () => { calls++; throw Object.assign(new Error('conflicting chunk'), { permanent: true }); });
  await q.init(); await q.enqueue(chunk(0)); await q.pump(); await q.pump();
  assert.equal(calls, 1);
  assert.equal(q.snapshot().blocked, 1);
  assert.equal(storage.rows.size, 1);
  const reload = queue(storage, async () => { throw new Error('should not retry'); });
  await reload.init(); await reload.pump();
  assert.equal(reload.snapshot().blocked, 1);
});

test('storage failure still saves to a healthy server without claiming a browser backup', async () => {
  const storage = store();
  storage.put = async () => { throw new Error('quota exceeded'); };
  let sent = 0;
  const q = queue(storage, async () => { sent++; });
  await q.init();
  assert.equal(await q.enqueue(chunk(0)), false);
  await q.pump();
  assert.equal(q.snapshot().pending, 0);
  assert.match(q.snapshot().storageError, /quota/);
  assert.equal(sent, 1);
});

test('a lost acknowledgment preserves exact bytes for an idempotent retry', async () => {
  const storage = store(), received = [];
  let now = 0;
  const q = queue(storage, async item => {
    received.push(await item.blob.text());
    if (received.length === 1) throw new Error('connection closed after server saved');
    return { duplicate: true };
  }, { now: () => now });
  await q.init(); await q.enqueue(chunk(0)); await q.pump();
  now = 2000; await q.pump();
  assert.deepEqual(received, ['audio', 'audio']);
  assert.equal(q.snapshot().pending, 0);
});

// Execute the production recorder function with deterministic MediaRecorder event ordering.
function recorderHarness() {
  const html = fs.readFileSync(require('node:path').join(__dirname, '../dnd-audio.html'), 'utf8');
  const cycle = html.slice(html.indexOf('  function startRecorderCycle()'), html.indexOf('  // START BUTTON HANDLER'));
  const timers = [], events = [], saved = [];
  const signals = { state: 'healthy' };
  const localRows = new Map();
  const context = vm.createContext({ Blob, WeakMap, Math, Number, String,
    localStorage: { getItem: key => localRows.get(key), setItem: (key, value) => localRows.set(key, value) },
    stream: { active: true, getTracks: () => [] }, recorder: null, paused: false, stopRequested: false,
    chunkMs: 0, baseMimeType: 'audio/webm', sessionId: '1234567890123', chunkIndex: 0, highestEmittedChunkIndex: -1,
    pendingChunkUploads: 0, sessionInitPending: false, recordingSessionMeta: null, deferTranscription: true,
    recorderStarting: false, recorderStopReasons: new WeakMap(), chunkTimer: null, recoveryAttempt: null,
    chunkCounterEl: {}, startBtn: {}, stopBtn: {}, pauseBtn: {}, resumeBtn: {},
    captureController: { snapshot: () => signals, recorderStarted: () => events.push('started'),
      recovered: () => { signals.state = 'recovered_with_gap'; }, chunkEmitted: () => {}, chunkLocallySaved: () => events.push('backup'),
      recoveryStage: () => {},
      recorderError: () => events.push('error'), unexpectedRecorderStop: () => events.push('unexpected') },
    recordingQueue: { checkpoint: async () => true, enqueue: async item => { saved.push(item); return true; } },
    pickMimeType: () => 'audio/webm', setStatus: () => {}, log: () => {}, startCountdown: () => {},
    stopCountdown: () => {}, stopMeter: () => {}, stopChunkTimer: () => {}, finalizeStoppedSession: () => {},
    finishPendingChunkUpload: () => {},
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    MediaRecorder: class {
      constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
      start() { this.state = 'recording'; this.onstart(); }
      stop() { this.state = 'inactive'; }
    },
  });
  vm.runInContext(cycle, context);
  return { context, timers, events, signals, saved };
}

test('restored zero interval cannot produce an immediate empty-file loop', () => {
  const h = recorderHarness(); h.context.startRecorderCycle();
  assert.equal(h.context.chunkMs, 120000);
  assert.equal(h.timers.at(-1).delay, 120000);
});

test('late stop and error from the replaced recorder cannot interrupt the recovered recorder', async () => {
  const h = recorderHarness(); h.context.startRecorderCycle();
  const old = h.context.recorder;
  h.context.recorderStopReasons.set(old, 'recovery'); old.stop();
  h.signals.state = 'recovering'; h.context.startRecorderCycle();
  const current = h.context.recorder;
  const timerCount = h.timers.length;
  await old.ondataavailable({ data: new Blob(['old final audio']) });
  old.onstop(); old.onerror({ error: new Error('old stream ended') });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.context.recorder, current);
  assert.equal(current.state, 'recording');
  assert.equal(h.signals.state, 'recovering'); // A recorder start alone is not durable recovery.
  assert.equal(h.events.includes('unexpected'), false);
  assert.equal(h.events.includes('error'), false);
  assert.equal(h.timers.length, timerCount);
  // The old recorder's final audio still reaches the durable queue.
  assert.equal(h.saved.length, 1);
  assert.equal(await h.saved[0].blob.text(), 'old final audio');
});

test('normal rollover creates one new recorder while audio is retained locally', async () => {
  const h = recorderHarness(); h.context.startRecorderCycle();
  const first = h.context.recorder;
  h.timers.at(-1).fn();
  await first.ondataavailable({ data: new Blob(['completed chunk']) });
  first.onstop();
  await new Promise(resolve => setImmediate(resolve));
  assert.notEqual(h.context.recorder, first);
  assert.equal(h.context.recorder.state, 'recording');
  assert.equal(h.saved[0].deferTranscription, true);
  assert.equal(h.events.includes('unexpected'), false);
});

test('health check between rollover stop and queued onstop preserves capture and saving', async () => {
  const { CaptureReliabilityController } = require('../capture-reliability.js');
  const html = fs.readFileSync(require('node:path').join(__dirname, '../dnd-audio.html'), 'utf8');
  const h = recorderHarness();
  h.context.stream.getAudioTracks = () => [{ readyState: 'live' }];
  vm.runInContext(html.slice(html.indexOf('  function captureSignals()'), html.indexOf('  function primeCaptureAlert()')), h.context);
  const capture = new CaptureReliabilityController({ signalProvider: () => h.context.captureSignals(),
    setInterval: () => 1, clearInterval() {} });
  await capture.start({ sessionId: h.context.sessionId, chunkIntervalMs: 120000 });
  capture.updateSaveQueue({ ready: true }); h.context.captureController = capture;
  h.context.startRecorderCycle(); const old = h.context.recorder;
  h.timers.at(-1).fn(); // stop() changes state synchronously; final callbacks are still queued.
  capture.observe(); await capture.heartbeat();
  assert.notEqual(capture.snapshot().state, 'interrupted');
  await old.ondataavailable({ data: new Blob(['synthetic final audio']) }); old.onstop();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.context.recorder.state, 'recording');
  assert.equal(h.saved.length, 1);
  assert.equal(await h.saved[0].blob.text(), 'synthetic final audio');
});

test('upload failures and invalid receipts leave the recorder running and backups restorable', async () => {
  const { CaptureReliabilityController } = require('../capture-reliability.js');
  const html = fs.readFileSync(require('node:path').join(__dirname, '../dnd-audio.html'), 'utf8');
  for (const failure of ['unavailable', 'initialization', 'validation', 'receipt']) {
    const h = recorderHarness(), storage = store();
    const capture = new CaptureReliabilityController({
      signalProvider: () => ({ recorderState: h.context.recorder?.state, streamActive: true, trackState: 'live' }),
      setInterval: () => 1, clearInterval() {},
    });
    await capture.start({ sessionId: '1234567890123', chunkIntervalMs: 120000 });
    h.context.captureController = capture;
    h.context.localFetch = async () => {
      if (failure === 'unavailable') throw new Error('Failed to fetch');
      const status = failure === 'initialization' ? 503 : failure === 'validation' ? 400 : 200;
      return { response: { ok: status === 200, status }, json: { ok: status === 200,
        sessionId: 'wrong-session', chunkIndex: 0, bytes: 5 } };
    };
    vm.runInContext(html.slice(html.indexOf('  async function uploadChunk(item)'), html.indexOf('  function waitForPendingChunkUploads()')), h.context);
    const q = queue(storage, item => h.context.uploadChunk(item), { onState: state => capture.updateSaveQueue(state) });
    let enqueued;
    const queued = new Promise(resolve => { enqueued = resolve; });
    const enqueue = q.enqueue.bind(q);
    q.enqueue = async item => { const result = await enqueue(item); enqueued(); return result; };
    await q.init(); h.context.recordingQueue = q;
    h.context.startRecorderCycle();
    const first = h.context.recorder;
    // Input not yet delivered by MediaRecorder has no backup.
    assert.equal(storage.rows.size, 0);
    await first.ondataavailable({ data: new Blob(['audio']) });
    h.timers.at(-1).fn(); first.onstop();
    await queued;
    await q.pump(); capture.observe();
    assert.equal(h.context.recorder.state, 'recording', failure);
    assert.equal(capture.snapshot().openGap, null, failure);
    assert.notEqual(capture.snapshot().state, 'interrupted', failure);
    assert.equal(q.snapshot().pending, 1, failure);
    assert.equal(q.snapshot().memoryOnly, 0, failure);
    assert.equal(q.snapshot().blocked, failure === 'validation' ? 1 : 0, failure + ': ' + q.snapshot().lastError);
    const restored = queue(storage, async () => ({})); await restored.init();
    assert.equal(restored.snapshot().pending, 1, failure);
    assert.equal(await restored.items.values().next().value.blob.text(), 'audio', failure);
    q.close(); restored.close();
  }
});

test('later successful checkpoints cannot claim an earlier memory-only chunk is backed up', async () => {
  const storage = store(), put = storage.put;
  storage.put = async item => { if (item.kind !== 'checkpoint') throw new Error('quota'); return put(item); };
  const q = queue(storage, async () => { throw new Error('server offline'); });
  await q.init();
  assert.equal(await q.enqueue(chunk(0)), false);
  await q.checkpoint({ ...chunk(1), part: 0 });
  assert.equal(q.snapshot().storageError, '');
  assert.equal(q.snapshot().memoryOnly, 1);
  await q.pump();
  assert.equal(await q.items.get('1234567890123:0').blob.text(), 'audio');
  storage.put = put; q.retry(); await q.pump();
  assert.equal(q.snapshot().memoryOnly, 0);
});
