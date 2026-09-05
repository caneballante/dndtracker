import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
HTML_PATH = os.path.join(ROOT, "dnd-audio.html")


class SessionMemoryUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(HTML_PATH, encoding="utf-8") as handle:
            cls.html = handle.read()

    def _function(self, name, next_marker):
        start = self.html.index(f"function {name}")
        end = self.html.index(next_marker, start)
        return self.html[start:end]

    def test_first_class_memory_surface_has_events_highlights_and_backward_compatible_empty_state(self):
        self.assertIn('id="session-memory-section"', self.html)
        self.assertIn('id="sessions-memory-events"', self.html)
        self.assertIn('id="sessions-memory-highlights"', self.html)
        self.assertIn("No Session Memory has been generated for this session.", self.html)
        self.assertIn("Transcript: ${chunkCount} chunk", self.html)

    def test_build_action_uses_production_reconciliation_and_explicit_confirmation(self):
        action = self._function("buildSessionMemoryForSession", "window.buildSessionMemoryAfterStop")
        self.assertIn("fetch('/api/session/reconcile'", action)
        self.assertIn("dryRun: true", action)
        self.assertIn("confirm: true", action)
        self.assertIn("window.confirm", action)
        self.assertIn("billable model call", action)
        self.assertNotIn("/api/session/reprocess/start", action)

    def test_legacy_rebuild_remains_separate_and_unmistakably_labeled(self):
        self.assertIn("Legacy: Rebuild DM Notes", self.html)
        self.assertIn("This does not rebuild Session Memory.", self.html)
        listener_start = self.html.index("sessionsReprocessEl.addEventListener('click'")
        listener_end = self.html.index("if (sessionsStopReprocessEl)", listener_start)
        legacy_action = self.html[listener_start:listener_end]
        self.assertIn("/api/session/reprocess/start", legacy_action)
        self.assertNotIn("confirm: true", legacy_action)

    def test_session_load_fetches_both_durable_stores_and_status_then_refreshes_after_success(self):
        loader = self._function("loadSessionMemory", "function syncSessionsPartyMeta")
        self.assertIn("/api/session/events?", loader)
        self.assertIn("/api/session/highlights?", loader)
        self.assertIn("/api/session/reconciliation/status?", loader)
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

    def test_ui_does_not_add_event_or_highlight_editing_controls(self):
        memory_section = self.html[
            self.html.index('id="session-memory-section"'):
            self.html.index('id="sessions-reprocess"')
        ]
        self.assertNotRegex(memory_section, re.compile(r"Edit Event|Reject Event|Approve Highlight|Promote Highlight", re.I))


if __name__ == "__main__":
    unittest.main()
