import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
HTML_PATH = os.path.join(ROOT, "dnd-audio.html")
TABLE_READY_PATH = os.path.join(ROOT, "table-ready.js")
SERVER_PATH = os.path.join(ROOT, "server.py")


class SessionMemoryUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(HTML_PATH, encoding="utf-8") as handle:
            cls.html = handle.read()
        with open(TABLE_READY_PATH, encoding="utf-8") as handle:
            cls.table_ready = handle.read()
        with open(SERVER_PATH, encoding="utf-8") as handle:
            cls.server = handle.read()

    def _function(self, name, next_marker):
        start = self.html.index(f"function {name}")
        end = self.html.index(next_marker, start)
        return self.html[start:end]

    def test_first_class_memory_surface_has_events_highlights_and_backward_compatible_empty_state(self):
        self.assertIn('id="session-memory-section"', self.html)
        self.assertIn('id="sessions-memory-events"', self.html)
        self.assertIn('id="sessions-memory-highlights"', self.html)
        self.assertIn('id="sessions-edit-memory"', self.html)
        self.assertIn('id="sessions-memory-publication"', self.html)
        self.assertIn('id="sessions-memory-conflicts"', self.html)
        self.assertIn("No Session Memory has been generated for this session.", self.html)
        self.assertIn("Transcript: ${chunkCount} chunk", self.html)

    def test_build_action_uses_production_reconciliation_and_explicit_confirmation(self):
        action = self._function("buildSessionMemoryForSession", "window.buildSessionMemoryAfterStop")
        self.assertIn("fetch('/api/session/reconcile'", action)
        self.assertIn("dryRun: true", action)
        self.assertIn("confirm: true", action)
        self.assertIn("window.confirm", action)
        self.assertIn("billable model call", action)
        self.assertIn("This session contains human edits", action)
        self.assertIn("confirmHumanEdits: true", action)
        self.assertIn("expectedMemoryRevision", action)
        self.assertNotIn("/api/session/reprocess/start", action)

    def test_legacy_rebuild_remains_separate_and_unmistakably_labeled(self):
        self.assertIn("Legacy: Rebuild DM Notes", self.html)
        self.assertIn("This does not rebuild Session Memory.", self.html)
        listener_start = self.html.index("sessionsReprocessEl.addEventListener('click'")
        listener_end = self.html.index("if (sessionsStopReprocessEl)", listener_start)
        legacy_action = self.html[listener_start:listener_end]
        self.assertIn("/api/session/reprocess/start", legacy_action)
        self.assertNotIn("confirm: true", legacy_action)

    def test_session_load_fetches_server_canonical_memory_and_status_then_refreshes_after_success(self):
        loader = self._function("loadSessionMemory", "function syncSessionsPartyMeta")
        self.assertIn("/api/session/memory?", loader)
        self.assertIn("/api/session/reconciliation/status?", loader)
        self.assertNotIn("/api/session/events?", loader)
        self.assertNotIn("/api/session/highlights?", loader)
        self.assertIn("currentHistorySessionId() !== sessionId", loader)
        action = self._function("buildSessionMemoryForSession", "window.buildSessionMemoryAfterStop")
        self.assertRegex(action, r"await loadSessionMemory\(sessionId, \{ completed: true, runUsage: json\.usage \}\)")

    def test_rendering_uses_display_fields_without_raw_audits_or_secret_canon(self):
        event_renderer = self._function("renderSessionMemoryEvents", "function renderSessionMemoryHighlights")
        for field in ("summary", "importance", "type", "confidence", "status", "facts", "entities", "sourceChunks"):
            self.assertIn(f"event.{field}", event_renderer)
        highlight_renderer = self._function("renderSessionMemoryHighlights", "function reconciliationCost")
        for field in ("summary", "categories", "confidence", "participants", "sourceChunks"):
            self.assertIn(f"highlight.{field}", highlight_renderer)
        memory_section = self.html[
            self.html.index('id="session-memory-section"'):
            self.html.index('id="sessions-reprocess"')
        ]
        self.assertNotIn("reconciliation_reference_searches", memory_section)
        self.assertNotIn("dmOnlyNotes", memory_section)
        self.assertNotRegex(event_renderer + highlight_renderer, r"JSON\.stringify\s*\(")

    def test_existing_memory_label_cost_error_and_duplicate_protection_are_real_state_driven(self):
        renderer = self._function("renderSessionMemory", "async function fetchSessionMemoryJson")
        self.assertIn("built ? 'Rebuild Session Memory' : 'Build Session Memory'", renderer)
        self.assertIn("reconciliationCost", renderer)
        self.assertIn("SESSION MEMORY NEEDS ATTENTION", renderer)
        self.assertIn("operation.running", renderer)
        self.assertIn("sessionsBuildMemoryEl.disabled", renderer)
        self.assertNotRegex(renderer, r"\bpercent(age)?\b")

    def test_stop_flow_uses_existing_finalize_barrier_before_explicit_memory_flow(self):
        finalizer = self._function("finalizeStoppedSession", "function stopChunkTimer")
        finalize_index = finalizer.index("fetch('/api/session/finalize'")
        memory_index = finalizer.index("await window.buildSessionMemoryAfterStop")
        self.assertLess(finalize_index, memory_index)
        self.assertIn("await waitForPendingChunkUploads()", finalizer)
        self.assertIn("if (stopFinalizationPromise) return stopFinalizationPromise", finalizer)
        memory_action = self._function("buildSessionMemoryForSession", "window.buildSessionMemoryAfterStop")
        self.assertLess(memory_action.index("ensureSessionReadyForMemory"), memory_action.index("confirm: true"))
        self.assertIn("window.confirm", memory_action)

    def test_editing_is_transactional_and_only_submits_human_facing_fields(self):
        self.assertIn('id="sessions-memory-edit-dialog"', self.html)
        self.assertIn('id="sessions-memory-edit-cancel"', self.html)
        self.assertIn('id="sessions-memory-edit-save"', self.html)
        self.assertIn("'/api/session/memory/edit'", self.table_ready)
        self.assertIn("baseRevision: memoryDraft.baseRevision", self.table_ready)
        self.assertIn("baseDigest: memoryDraft.baseDigest", self.table_ready)
        self.assertIn("changes: result.changes", self.table_ready)
        editable_start = self.table_ready.index("function editableValues")
        editable_end = self.table_ready.index("function makeDraftEntries", editable_start)
        editable = self.table_ready[editable_start:editable_end]
        for field in ("summary", "facts", "entities", "type", "status", "importance", "confidence", "categories", "participants"):
            self.assertIn(field, editable)
        for protected in ("sourceChunks", "firstChunk", "lastChunk", "createdAt", "updatedAt", "eventId", "highlightId"):
            self.assertNotIn(protected, editable)
        self.assertIn("action: entry.removed ? 'remove' : 'restore'", self.table_ready)

    def test_publish_has_readable_preview_and_separate_explicit_post(self):
        self.assertIn('id="sessions-memory-publish-dialog"', self.html)
        self.assertIn('id="sessions-memory-publish-cancel"', self.html)
        self.assertIn('id="sessions-memory-publish-confirm"', self.html)
        self.assertIn("/api/session/dungeonshare/preview?", self.table_ready)
        self.assertIn("'/api/session/dungeonshare/publish'", self.table_ready)
        self.assertIn("expectedRevision: snapshot.expectedRevision", self.table_ready)
        self.assertIn("expectedDigest: snapshot.expectedDigest", self.table_ready)
        self.assertIn("confirm: true", self.table_ready)
        self.assertNotRegex(self.table_ready, re.compile(r"preview[^\n]*JSON\.stringify", re.I))
        self.assertIn("Publishing is disabled until these human-edit conflicts are resolved.", self.table_ready)

    def test_server_publish_route_is_local_guarded_and_requires_confirmation(self):
        start = self.server.index('if parsed.path == "/api/session/dungeonshare/publish"')
        end = self.server.index('if parsed.path == "/api/session/memory/edit"', start)
        route = self.server[start:end]
        self.assertIn("_local_session_memory_request", route)
        self.assertIn('data.get("confirm") is not True', route)
        self.assertIn("publish_session_memory_to_dungeonshare", route)
        self.assertIn('"expectedRevision"', route)
        self.assertIn('"expectedDigest"', route)


if __name__ == "__main__":
    unittest.main()
