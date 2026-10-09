(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DungeonCaptureReliability = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const STATES = Object.freeze({
    HEALTHY: 'healthy',
    SUSPECT: 'suspect',
    INTERRUPTED: 'interrupted',
    RECOVERING: 'recovering',
    RECOVERED_WITH_GAP: 'recovered_with_gap',
    STOPPED: 'stopped',
    FINALIZING: 'finalizing',
    ERROR: 'error',
  });
  const HEARTBEAT_MS = 10000;
  const HEALTH_CHECK_MS = 5000;
  const CLOCK_GAP_MS = 25000;
  const WATCHDOG_GRACE_MS = 30000;
  const ALERT_REPEAT_MS = 45000;
  const STORAGE_KEY = 'dungeontracker.capture.pending.v1';

  const copy = value => JSON.parse(JSON.stringify(value));
  const finite = value => Number.isFinite(Number(value)) ? Number(value) : null;

  class CaptureReliabilityController {
    constructor(options = {}) {
      this.now = options.now || (() => Date.now());
      this.setInterval = options.setInterval || globalThis.setInterval.bind(globalThis);
      this.clearInterval = options.clearInterval || globalThis.clearInterval.bind(globalThis);
      this.signalProvider = options.signalProvider || (() => ({}));
      this.sendHeartbeat = options.sendHeartbeat || (async () => ({}));
      this.sendDiagnostic = options.sendDiagnostic || (async () => ({}));
      this.onState = options.onState || (() => {});
      this.onAlert = options.onAlert || (() => {});
      this.wakeLockProvider = options.wakeLockProvider || null;
      this.document = options.document || null;
      this.storage = options.storage || null;
      this.heartbeatMs = options.heartbeatMs || HEARTBEAT_MS;
      this.healthCheckMs = options.healthCheckMs || HEALTH_CHECK_MS;
      this.clockGapMs = options.clockGapMs || CLOCK_GAP_MS;
      this.watchdogGraceMs = options.watchdogGraceMs || WATCHDOG_GRACE_MS;
      this.alertRepeatMs = options.alertRepeatMs || ALERT_REPEAT_MS;
      this.state = this._empty();
      this.heartbeatTimer = null;
      this.healthTimer = null;
      this.alertTimer = null;
      this.wakeLock = null;
      this.consecutiveHeartbeatFailures = 0;
      this.boundVisibility = () => this.handleVisibilityRestore();
      if (this.document?.addEventListener) this.document.addEventListener('visibilitychange', this.boundVisibility);
    }

    _empty() {
      return {
        state: STATES.STOPPED,
        sessionId: '',
        sessionOpen: false,
        interrupted: false,
        partialCapture: false,
        recoveryRequired: false,
        alertAcknowledged: false,
        interruptionReason: '',
        startedAtMs: null,
        lastObservationAtMs: null,
        lastChunkEmittedAtMs: null,
        lastChunkUploadStartedAtMs: null,
        lastChunkAcknowledgedAtMs: null,
        lastChunkLocallySavedAtMs: null,
        lastRecorderStartedAtMs: null,
        saveQueue: { ready: false, pending: 0, blocked: 0, storageError: '' },
        lastAcknowledgedChunkIndex: null,
        chunkIntervalMs: 120000,
        keepAwake: { supported: null, active: false, error: '' },
        screenWakeLock: { supported: null, active: false, error: '' },
        openGap: null,
        gaps: [],
        pendingFinalization: null,
      };
    }

    snapshot() { return copy(this.state); }

    _emit() { this.onState(this.snapshot()); }

    _persist() {
      if (!this.storage) return;
      try {
        if (this.state.sessionOpen || this.state.pendingFinalization) {
          this.storage.setItem(STORAGE_KEY, JSON.stringify({
            sessionId: this.state.sessionId,
            state: this.state.state,
            interrupted: this.state.interrupted,
            partialCapture: this.state.partialCapture,
            gaps: this.state.gaps,
            openGap: this.state.openGap,
            pendingFinalization: this.state.pendingFinalization,
            lastAcknowledgedChunkIndex: this.state.lastAcknowledgedChunkIndex,
            lastChunkAcknowledgedAtMs: this.state.lastChunkAcknowledgedAtMs,
            lastChunkLocallySavedAtMs: this.state.lastChunkLocallySavedAtMs,
            chunkIntervalMs: this.state.chunkIntervalMs,
          }));
        } else {
          this.storage.removeItem(STORAGE_KEY);
        }
      } catch (_) {}
    }

    restorePending() {
      if (!this.storage) return null;
      try {
        const parsed = JSON.parse(this.storage.getItem(STORAGE_KEY) || 'null');
        if (!parsed || !parsed.sessionId) return null;
        this.state = { ...this._empty(), ...parsed, state: parsed.pendingFinalization ? STATES.ERROR : STATES.INTERRUPTED,
          sessionOpen: !parsed.pendingFinalization, recoveryRequired: !parsed.pendingFinalization,
          interruptionReason: parsed.pendingFinalization ? 'finalization_incomplete' : 'page_reloaded_during_capture' };
        this.state.chunkIntervalMs = Math.max(60000, Number(parsed.chunkIntervalMs) || 120000);
        if (parsed.pendingFinalization?.serverState === 'waiting_for_transcription') {
          this.state.state = STATES.FINALIZING;
          this.state.interruptionReason = '';
        }
        this._emit();
        return this.snapshot();
      } catch (_) { return null; }
    }

    async start({ sessionId, chunkIntervalMs }) {
      const now = this.now();
      const saveQueue = this.state.saveQueue;
      this.state = { ...this._empty(), state: STATES.HEALTHY, sessionId: String(sessionId),
        sessionOpen: true, startedAtMs: now, lastObservationAtMs: now, saveQueue,
        chunkIntervalMs: Math.max(60000, Number(chunkIntervalMs) || 120000) };
      this._startTimers();
      await this.acquireScreenWakeLock();
      this._persist();
      this._emit();
    }

    _startTimers() {
      this._stopTimers();
      this.heartbeatTimer = this.setInterval(() => this.heartbeat(), this.heartbeatMs);
      this.healthTimer = this.setInterval(() => this.observe(), this.healthCheckMs);
    }

    _stopTimers() {
      if (this.heartbeatTimer) this.clearInterval(this.heartbeatTimer);
      if (this.healthTimer) this.clearInterval(this.healthTimer);
      this.heartbeatTimer = null;
      this.healthTimer = null;
    }

    setKeepAwake(status) {
      this.state.keepAwake = {
        supported: status?.supported !== false,
        active: Boolean(status?.active),
        error: String(status?.error || ''),
        leaseExpiresAt: status?.leaseExpiresAt || null,
      };
      this._emit();
    }

    currentSignals() {
      const value = this.signalProvider() || {};
      return {
        recorderState: String(value.recorderState || 'unknown'),
        streamActive: value.streamActive !== false,
        trackState: String(value.trackState || 'unknown'),
        paused: Boolean(value.paused),
        stopRequested: Boolean(value.stopRequested),
      };
    }

    async heartbeat() {
      if (!this.state.sessionOpen) return;
      const signals = this.currentSignals();
      try {
        const response = await this.sendHeartbeat({
          sessionId: this.state.sessionId,
          clientState: this.state.state,
          clientTimestampMs: this.now(),
          ...signals,
          lastChunkEmittedAtMs: this.state.lastChunkEmittedAtMs,
          lastChunkUploadStartedAtMs: this.state.lastChunkUploadStartedAtMs,
          lastChunkAcknowledgedAtMs: this.state.lastChunkAcknowledgedAtMs,
          lastAcknowledgedChunkIndex: this.state.lastAcknowledgedChunkIndex,
          chunkIntervalSeconds: Math.round(this.state.chunkIntervalMs / 1000),
          watchdogGraceSeconds: Math.round(this.watchdogGraceMs / 1000),
        });
        this.consecutiveHeartbeatFailures = 0;
        if (this.state.state === STATES.SUSPECT && this.state.interruptionReason === 'heartbeat_failed') {
          this.state.state = STATES.HEALTHY;
          this.state.interruptionReason = '';
          this._emit();
        }
        if (response?.capture?.keepAwake) this.setKeepAwake(response.capture.keepAwake);
        const localCaptureActive = this.state.saveQueue.ready && signals.streamActive && signals.trackState !== 'ended'
          && ['recording', 'starting'].includes(signals.recorderState);
        if (response?.capture?.state === STATES.INTERRUPTED && this.state.state !== STATES.INTERRUPTED && !localCaptureActive) {
          this.interrupt('server_reported_interruption', { serverState: response.capture.state });
        }
      } catch (error) {
        this.consecutiveHeartbeatFailures += 1;
        if (this.consecutiveHeartbeatFailures === 1 && this.state.state === STATES.HEALTHY) {
          this.state.state = STATES.SUSPECT;
          this.state.interruptionReason = 'heartbeat_failed';
          this._emit();
        }
        if (this.consecutiveHeartbeatFailures >= 3 && !this.state.saveQueue.ready) {
          this.interrupt('heartbeat_expired', { error: String(error?.message || error) });
        }
      }
    }

    observe() {
      if (!this.state.sessionOpen) return;
      const now = this.now();
      const prior = this.state.lastObservationAtMs;
      this.state.lastObservationAtMs = now;
      if (prior && now - prior > this.clockGapMs) {
        if ([STATES.HEALTHY, STATES.RECOVERED_WITH_GAP].includes(this.state.state)) {
          this.state.state = STATES.SUSPECT;
          this.state.interruptionReason = 'timer_delayed_capture_unverified';
          this.sendDiagnostic('capture_suspect', { reason: 'timer_delayed_capture_unverified' }).catch(() => {});
          this._emit();
        }
        return;
      }
      const signals = this.currentSignals();
      if (!signals.paused && !signals.stopRequested && [STATES.HEALTHY, STATES.SUSPECT, STATES.RECOVERED_WITH_GAP].includes(this.state.state)) {
        if (signals.trackState === 'ended') return this.interrupt('track_ended');
        if (!signals.streamActive) return this.interrupt('stream_inactive');
        if (!['recording', 'starting'].includes(signals.recorderState)) return this.interrupt('recorder_not_recording', { recorderState: signals.recorderState });
        const saved = this.state.saveQueue.ready
          ? Math.max(this.state.lastChunkEmittedAtMs || 0, this.state.lastRecorderStartedAtMs || 0, this.state.lastChunkLocallySavedAtMs || 0, this.state.startedAtMs || 0)
          : (this.state.lastChunkAcknowledgedAtMs || this.state.startedAtMs);
        if (saved && now - saved > this.state.chunkIntervalMs + this.watchdogGraceMs) {
          return this.interrupt('chunk_watchdog_expired', { lastAcknowledgedAt: saved / 1000 });
        }
      }
      if (this.state.state === STATES.INTERRUPTED && this.state.openGap && !this.state.openGap.endedAtMs) {
        this.state.openGap.durationSeconds = Math.max(0, Math.round((now - this.state.openGap.startedAtMs) / 1000));
        this._persist();
        this._emit();
      }
    }

    _startGap(reason, startedAtMs, endedAtMs = null) {
      if (this.state.openGap) return this.state.openGap;
      const start = finite(startedAtMs) || this.now();
      const end = finite(endedAtMs);
      const gap = { gapId: `gap_${Math.round(start)}_${Math.random().toString(16).slice(2, 10)}`,
        startedAtMs: start, endedAtMs: end, durationSeconds: end ? Math.max(0, Math.round((end - start) / 1000)) : null,
        reason: String(reason), recovered: false };
      this.state.openGap = gap;
      return gap;
    }

    interrupt(reason, details = {}) {
      if (!this.state.sessionOpen || [STATES.FINALIZING, STATES.STOPPED].includes(this.state.state)) return;
      const first = this.state.state !== STATES.INTERRUPTED;
      const gap = this._startGap(reason, Math.max(this.state.lastChunkLocallySavedAtMs || 0,
        this.state.lastChunkAcknowledgedAtMs || 0) || this.now());
      this.state.state = STATES.INTERRUPTED;
      this.state.interrupted = true;
      this.state.partialCapture = true;
      this.state.recoveryRequired = true;
      this.state.alertAcknowledged = false;
      this.state.interruptionReason = String(reason);
      this._persist();
      this._emit();
      if (first) {
        this.sendDiagnostic('capture_interrupted', { reason, gapId: gap.gapId,
          gapStartedAt: gap.startedAtMs / 1000, ...details }).catch(() => {});
        this._alert();
      }
    }

    _alert() {
      this.onAlert(this.snapshot());
      if (this.alertTimer) this.clearInterval(this.alertTimer);
      this.alertTimer = this.setInterval(() => {
        if (this.state.state === STATES.INTERRUPTED && !this.state.alertAcknowledged) this.onAlert(this.snapshot());
      }, this.alertRepeatMs);
    }

    acknowledgeAlert() {
      if (this.state.state !== STATES.INTERRUPTED) return;
      this.state.alertAcknowledged = true;
      if (this.alertTimer) this.clearInterval(this.alertTimer);
      this.alertTimer = null;
      this.sendDiagnostic('alert_acknowledged', { reason: this.state.interruptionReason }).catch(() => {});
      this._persist();
      this._emit();
    }

    beginRecovery() {
      if (![STATES.INTERRUPTED, STATES.ERROR].includes(this.state.state) || this.state.pendingFinalization) return false;
      this.state.state = STATES.RECOVERING;
      this.state.recoveryRequired = true;
      this.sendDiagnostic('capture_recovery_started', { reason: this.state.interruptionReason }).catch(() => {});
      this._persist();
      this._emit();
      return true;
    }

    async recovered() {
      if (this.state.state !== STATES.RECOVERING) return;
      const now = this.now();
      const gap = this.state.openGap || this._startGap('unknown_interruption', this.state.lastChunkAcknowledgedAtMs || now);
      gap.endedAtMs = now;
      gap.durationSeconds = Math.max(0, Math.round((now - gap.startedAtMs) / 1000));
      gap.recovered = true;
      this.state.gaps.push(copy(gap));
      this.state.openGap = null;
      this.state.state = STATES.RECOVERED_WITH_GAP;
      this.state.recoveryRequired = false;
      this.state.interrupted = false;
      this.state.lastObservationAtMs = now;
      this.state.lastRecorderStartedAtMs = now;
      if (this.alertTimer) this.clearInterval(this.alertTimer);
      this.alertTimer = null;
      await this.sendDiagnostic('capture_recovered', { gapId: gap.gapId,
        gapStartedAt: gap.startedAtMs / 1000, gapEndedAt: gap.endedAtMs / 1000,
        durationSeconds: gap.durationSeconds, reason: gap.reason, recovered: true }).catch(() => {});
      await this.acquireScreenWakeLock();
      this._startTimers();
      await this.heartbeat();
      this._persist();
      this._emit();
    }

    recoveryFailed(error) {
      this.state.state = STATES.INTERRUPTED;
      this.state.recoveryRequired = true;
      this.state.interrupted = true;
      this.state.interruptionReason = 'recovery_failed';
      this.sendDiagnostic('capture_recovery_failed', { error: String(error?.message || error) }).catch(() => {});
      this._persist();
      this._emit();
      this._alert();
    }

    recorderStarted() {
      this.state.lastRecorderStartedAtMs = this.now();
      this.sendDiagnostic('recorder_started', {}).catch(() => {});
    }
    updateSaveQueue(status) {
      this.state.saveQueue = { ...status };
      this._emit();
    }
    chunkLocallySaved() {
      this.state.lastChunkLocallySavedAtMs = this.now();
      if (this.state.state === STATES.SUSPECT && this.state.interruptionReason === 'timer_delayed_capture_unverified') {
        this.state.state = STATES.HEALTHY;
        this.state.interruptionReason = '';
      }
      this._persist();
      this._emit();
    }
    chunkEmitted(index) {
      this.state.lastChunkEmittedAtMs = this.now();
      this.sendDiagnostic('chunk_emitted', { chunkIndex: index }).catch(() => {});
      this._emit();
    }
    chunkUploadStarted(index) {
      this.state.lastChunkUploadStartedAtMs = this.now();
      this.sendDiagnostic('chunk_upload_started', { chunkIndex: index }).catch(() => {});
    }
    chunkAcknowledged(index) {
      this.state.lastChunkAcknowledgedAtMs = this.now();
      this.state.lastAcknowledgedChunkIndex = Number(index);
      if (this.state.state === STATES.SUSPECT && this.consecutiveHeartbeatFailures === 0) this.state.state = STATES.HEALTHY;
      this.sendDiagnostic('chunk_acknowledged', { chunkIndex: index }).catch(() => {});
      this._persist();
      this._emit();
    }
    chunkUploadFailed(index, error) {
      this.sendDiagnostic('chunk_upload_failed', { chunkIndex: index, error: String(error?.message || error) }).catch(() => {});
      this.interrupt('chunk_upload_failed', { chunkIndex: index });
    }
    trackEnded() { this.sendDiagnostic('track_ended', {}).catch(() => {}); this.interrupt('track_ended'); }
    streamInactive() { this.sendDiagnostic('stream_inactive', {}).catch(() => {}); this.interrupt('stream_inactive'); }
    recorderError(error) { this.sendDiagnostic('recorder_error', { error: String(error?.message || error) }).catch(() => {}); this.interrupt('recorder_error'); }
    unexpectedRecorderStop() { this.sendDiagnostic('unexpected_recorder_stop', {}).catch(() => {}); this.interrupt('unexpected_recorder_stop'); }

    async acquireScreenWakeLock() {
      if (!this.state.sessionOpen || !this.wakeLockProvider?.request) {
        this.state.screenWakeLock = { supported: false, active: false, error: 'unsupported' };
        this.sendDiagnostic('screen_wake_lock_failed', { reason: 'unsupported' }).catch(() => {});
        return;
      }
      try {
        if (this.wakeLock && !this.wakeLock.released) return;
        this.wakeLock = await this.wakeLockProvider.request('screen');
        this.state.screenWakeLock = { supported: true, active: true, error: '' };
        this.wakeLock?.addEventListener?.('release', () => {
          this.state.screenWakeLock = { supported: true, active: false, error: 'released' };
          this.sendDiagnostic('screen_wake_lock_lost', {}).catch(() => {});
          this._emit();
        });
        this.sendDiagnostic('screen_wake_lock_acquired', {}).catch(() => {});
      } catch (error) {
        this.state.screenWakeLock = { supported: true, active: false, error: String(error?.message || error) };
        this.sendDiagnostic('screen_wake_lock_failed', { error: this.state.screenWakeLock.error }).catch(() => {});
      }
      this._emit();
    }

    async releaseScreenWakeLock() {
      try { await this.wakeLock?.release?.(); } catch (_) {}
      this.wakeLock = null;
      this.state.screenWakeLock.active = false;
    }

    handleVisibilityRestore() {
      if (!this.state.sessionOpen || this.document?.visibilityState === 'hidden') return;
      this.observe();
      this.acquireScreenWakeLock();
    }

    beginFinalization(finalExpectedChunkIndex) {
      this._stopTimers();
      if (this.alertTimer) this.clearInterval(this.alertTimer);
      this.alertTimer = null;
      this.state.state = STATES.FINALIZING;
      this.state.sessionOpen = false;
      this.state.keepAwake.active = false;
      this.state.pendingFinalization = { sessionId: this.state.sessionId,
        finalExpectedChunkIndex: Number(finalExpectedChunkIndex), state: 'pending', updatedAtMs: this.now() };
      this.releaseScreenWakeLock();
      this.sendDiagnostic('stop_requested', { finalExpectedChunkIndex }).catch(() => {});
      this._persist();
      this._emit();
    }

    finalizeCompleted() {
      this.state.state = STATES.STOPPED;
      this.state.pendingFinalization = null;
      this.state.sessionOpen = false;
      this.state.recoveryRequired = false;
      this._persist();
      this._emit();
    }

    finalizationPending(serverState = '') {
      if (!this.state.pendingFinalization) return;
      this.state.state = STATES.FINALIZING;
      this.state.pendingFinalization.state = 'pending';
      this.state.pendingFinalization.serverState = String(serverState || 'waiting');
      this.state.pendingFinalization.error = '';
      this.state.pendingFinalization.updatedAtMs = this.now();
      this._persist();
      this._emit();
    }

    finalizeFailed(error) {
      this.state.state = STATES.ERROR;
      if (!this.state.pendingFinalization) this.state.pendingFinalization = { sessionId: this.state.sessionId };
      this.state.pendingFinalization.state = 'failed';
      this.state.pendingFinalization.error = String(error?.message || error);
      this.state.pendingFinalization.updatedAtMs = this.now();
      this.state.interruptionReason = 'finalization_incomplete';
      this._persist();
      this._emit();
    }

    requiresUnloadWarning() {
      return Boolean(this.state.sessionOpen || (this.state.pendingFinalization && this.state.pendingFinalization.serverState !== 'waiting_for_transcription'));
    }
  }

  return { CaptureReliabilityController, STATES, HEARTBEAT_MS, HEALTH_CHECK_MS,
    CLOCK_GAP_MS, WATCHDOG_GRACE_MS, ALERT_REPEAT_MS, STORAGE_KEY };
});
