(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DungeonRecordingStorage = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  class IndexedDBChunkStore {
    constructor(indexedDB) { this.indexedDB = indexedDB; this.database = null; }
    async open() {
      if (this.database) return;
      if (!this.indexedDB) throw new Error('Local audio backup is unavailable in this browser.');
      this.database = await new Promise((resolve, reject) => {
        const request = this.indexedDB.open('dungeontracker-audio', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('chunks', { keyPath: 'id' });
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error('Close an older tracker tab to enable local audio backup.'));
      });
      this.database.onversionchange = () => { this.database.close(); this.database = null; };
    }
    async transact(mode, action) {
      await this.open();
      return new Promise((resolve, reject) => {
        const transaction = this.database.transaction('chunks', mode);
        const request = action(transaction.objectStore('chunks'));
        transaction.oncomplete = () => resolve(request.result);
        transaction.onabort = transaction.onerror = () => reject(transaction.error || request.error || new Error('Local audio backup failed.'));
      });
    }
    async list() {
      await this.open();
      return new Promise((resolve, reject) => {
        const rows = [], tx = this.database.transaction('chunks', 'readonly');
        const request = tx.objectStore('chunks').openCursor();
        request.onsuccess = () => {
          const cursor = request.result;
          if (!cursor) return;
          const { blob, ...metadata } = cursor.value;
          rows.push(metadata);
          cursor.continue();
        };
        tx.oncomplete = () => resolve(rows);
        tx.onabort = tx.onerror = () => reject(tx.error || request.error);
      });
    }
    put(item) { return this.transact('readwrite', store => store.put(item)); }
    get(id) { return this.transact('readonly', store => store.get(id)); }
    remove(id) { return this.transact('readwrite', store => store.delete(id)); }
  }

  class DurableChunkQueue {
    constructor(options) {
      this.store = options.store;
      this.prepare = options.prepare || (async item => item);
      this.upload = options.upload;
      this.onState = options.onState || (() => {});
      this.onSaved = options.onSaved || (() => {});
      this.now = options.now || (() => Date.now());
      this.setTimeout = options.setTimeout || globalThis.setTimeout.bind(globalThis);
      this.clearTimeout = options.clearTimeout || globalThis.clearTimeout.bind(globalThis);
      this.items = new Map();
      this.busy = false;
      this.timer = null;
      this.ready = false;
      this.storageError = '';
      this.waiters = [];
    }
    snapshot() {
      const items = [...this.items.values()];
      return { ready: this.ready, pending: items.length, blocked: items.filter(item => item.blocked).length,
        storageError: this.storageError, saving: this.busy,
        lastError: items.find(item => item.lastError)?.lastError || '',
        pendingSessions: [...new Set(items.map(item => item.sessionId))] };
    }
    emit() { this.onState(this.snapshot()); }
    async init() {
      const items = await this.store.list();
      const journals = new Map();
      for (const item of items) {
        if (item.kind === 'checkpoint') {
          const key = `${item.sessionId}:${item.index}`;
          if (!journals.has(key)) journals.set(key, []);
          journals.get(key).push(item);
        } else this.items.set(item.id, item);
      }
      for (const [id, parts] of journals) {
        if (this.items.has(id)) continue;
        parts.sort((a, b) => a.part - b.part);
        // Only a contiguous prefix with its container header can be recovered.
        const prefix = [];
        for (const part of parts) { if (part.part !== prefix.length) break; prefix.push(part); }
        if (prefix.length) {
          const blobs = [];
          for (const part of prefix) blobs.push(part.blob || (await this.store.get(part.id)).blob);
          await this.enqueue({ ...prefix[0], kind: 'audio',
          blob: new Blob(blobs, { type: prefix[0].mimeType }),
          journalIds: prefix.map(part => part.id) });
        }
        if (prefix.length !== parts.length) this.storageError = 'Some checkpoint pieces are incomplete. They remain in browser storage; only the continuous prefix can be recovered.';
      }
      this.ready = true;
      this.emit();
      this.schedule(0);
    }
    highestIndex(sessionId) {
      return Math.max(-1, ...[...this.items.values()].filter(item => item.sessionId === sessionId).map(item => item.index));
    }
    async enqueue(record) {
      const item = { ...record, id: `${record.sessionId}:${record.index}`, createdAt: this.now(), attempts: 0 };
      if (!item.sourceSha256 && globalThis.crypto?.subtle) {
        const digest = await globalThis.crypto.subtle.digest('SHA-256', await item.blob.arrayBuffer());
        item.sourceSha256 = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
      }
      if (this.items.has(item.id)) throw new Error('This audio chunk is already queued.');
      // Keep a memory copy if the browser runs out of space; never discard the blob on a storage error.
      this.items.set(item.id, item);
      let backedUp = false;
      try {
        await this.store.put(item); backedUp = true; this.storageError = '';
        if (this.store.get) delete item.blob;
      }
      catch (error) { this.storageError = String(error?.message || error); }
      this.emit();
      this.schedule(0);
      return backedUp;
    }
    async checkpoint(record) {
      const item = { ...record, kind: 'checkpoint', id: `${record.sessionId}:checkpoint:${record.index}:${record.part}` };
      try { await this.store.put(item); this.storageError = ''; this.emit(); return true; }
      catch (error) { this.storageError = String(error?.message || error); this.emit(); return false; }
    }
    schedule(delay) {
      if (this.timer !== null) this.clearTimeout(this.timer);
      this.timer = this.setTimeout(() => { this.timer = null; this.pump().catch(() => {}); }, delay);
    }
    async pump() {
      if (this.busy || !this.ready) return;
      this.busy = true;
      this.emit();
      try {
        for (const item of [...this.items.values()].sort((a, b) => a.createdAt - b.createdAt || a.index - b.index)) {
          if (item.blocked || (item.retryAt || 0) > this.now()) continue;
          try {
            if (!item.blob) {
              const stored = await this.store.get(item.id);
              if (stored) Object.assign(item, { blob: stored.blob, mimeType: stored.mimeType, prepared: stored.prepared });
            }
            if (!item.blob) throw new Error('Pending audio could not be read from browser storage.');
            const original = { blob: item.blob, mimeType: item.mimeType };
            if (!item.prepared) {
              const prepared = await this.prepare(item);
              Object.assign(item, prepared, { prepared: true });
              // Store the exact bytes we retry, so ambiguous responses cannot change the file format.
            }
            try { await this.store.put(item); this.storageError = ''; }
            catch (error) {
              this.storageError = String(error?.message || error);
              // A stable source digest identifies either representation after reload.
              if (item.sourceSha256) Object.assign(item, original);
            }
            const result = await this.upload(item);
            // A lost acknowledgment is safe: the server treats repeated bytes as the same upload.
            for (const id of item.journalIds || []) await this.store.remove(id);
            await this.store.remove(item.id);
            this.items.delete(item.id);
            this.onSaved(item, result);
          } catch (error) {
            item.attempts += 1;
            item.lastError = String(error?.message || error);
            item.blocked = Boolean(error?.permanent);
            item.retryAt = this.now() + Math.min(30000, 1000 * 2 ** Math.min(item.attempts - 1, 5));
            if (item.blocked) {
              try { await this.store.put(item); } catch (_) {}
            }
            // Persisted blobs are loaded on demand, not held for the whole outage.
            if (this.store.get) {
              try { if ((await this.store.get(item.id))?.blob) delete item.blob; } catch (_) {}
            }
          }
          this.emit();
          // Let the page respond between large chunks and bound an explicit flush to one attempt.
          break;
        }
      } finally {
        this.busy = false;
        this.emit();
        for (const waiter of this.waiters.splice(0)) waiter.resolve();
        const retryable = [...this.items.values()].filter(item => !item.blocked);
        if (retryable.length) this.schedule(Math.max(0, Math.min(...retryable.map(item => item.retryAt || this.now())) - this.now()));
      }
    }
    async flushOnce() {
      if (this.busy) await new Promise(resolve => this.waiters.push({ resolve }));
      await this.pump();
      return this.snapshot();
    }
    retry() {
      for (const item of this.items.values()) { item.blocked = false; item.retryAt = 0; }
      this.schedule(0);
    }
    close() { if (this.timer !== null) this.clearTimeout(this.timer); this.timer = null; }
  }
  async function acquireRecordingLease(locks) {
    if (!locks?.request) throw new Error('This browser cannot safely coordinate recording tabs. Use a browser with Web Locks support.');
    return new Promise((resolve, reject) => {
      locks.request('dungeontracker-recording-owner-v1', { ifAvailable: true }, async lock => {
        if (!lock) { resolve(false); return; }
        // Browser releases this automatically if the owning tab closes or crashes.
        await new Promise(release => resolve({ release }));
      }).catch(reject);
    });
  }
  return { IndexedDBChunkStore, DurableChunkQueue, acquireRecordingLease };
});
