import json
import os
import tempfile
import unittest

from session_events import create_event, read_event_store
from session_highlights import (
    HIGHLIGHTS_FILENAME,
    OPERATIONS_FILENAME,
    apply_highlight_operation,
    apply_highlight_operations_batch,
    create_highlight,
    read_highlight_operations,
    read_highlight_store,
    update_highlight,
)


class SessionHighlightStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_dir = self.temp_dir.name
        self.session_id = "12345678"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create(self, summary="A memorable moment", chunks=None, **extra):
        record = create_highlight(
            self.session_dir,
            self.session_id,
            {
                "categories": extra.pop("categories", ["other"]),
                "confidence": extra.pop("confidence", "high"),
                "summary": summary,
                "participants": extra.pop("participants", []),
                "sourceChunks": [1] if chunks is None else chunks,
                "relatedEventIds": extra.pop("relatedEventIds", []),
                **extra,
            },
            actor="test",
        )
        return record["payload"]["highlightId"]

    def test_highlights_are_separate_from_events_and_legacy_notes(self):
        notes_path = os.path.join(self.session_dir, "notes.jsonl")
        with open(notes_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"chunkIndex": 1, "timeline": ["legacy"]}) + "\n")
        with open(notes_path, encoding="utf-8") as handle:
            original_notes = handle.read()

        create_event(
            self.session_dir,
            self.session_id,
            {"type": "combat_outcome", "summary": "The party won.", "sourceChunks": [1]},
        )
        self._create("The fighter won with an improvised chandelier swing.", [1])

        self.assertEqual(1, len(read_event_store(self.session_dir, self.session_id)["events"]))
        self.assertEqual(1, len(read_highlight_store(self.session_dir, self.session_id)["highlights"]))
        self.assertTrue(os.path.isfile(os.path.join(self.session_dir, HIGHLIGHTS_FILENAME)))
        self.assertTrue(os.path.isfile(os.path.join(self.session_dir, OPERATIONS_FILENAME)))
        with open(notes_path, encoding="utf-8") as handle:
            self.assertEqual(original_notes, handle.read())

    def test_ids_are_stable_prose_independent_and_keep_provenance_and_links(self):
        event_record = create_event(
            self.session_dir,
            self.session_id,
            {"type": "combat_outcome", "summary": "The ogre fell.", "sourceChunks": [2]},
        )
        event_id = event_record["payload"]["eventId"]
        first = self._create(
            "Same generated prose",
            [4, 2, 4],
            categories=["combat", "triumph"],
            participants=["Mira"],
            relatedEventIds=[event_id],
        )
        second = self._create("Same generated prose", [2])

        highlight = read_highlight_store(self.session_dir, self.session_id)["highlights"][first]
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^hlt_[0-9a-f]{32}$")
        self.assertEqual([2, 4], highlight["sourceChunks"])
        self.assertEqual(["combat", "triumph"], highlight["categories"])
        self.assertEqual([event_id], highlight["relatedEventIds"])

    def test_edit_keep_and_reject_preserve_append_only_history(self):
        highlight_id = self._create("Initial memory", [2])
        update_highlight(
            self.session_dir,
            self.session_id,
            highlight_id,
            {"summary": "Corrected memory", "sourceChunks": [5]},
            actor="reviewer",
        )
        apply_highlight_operation(
            self.session_dir,
            self.session_id,
            {"operation": "KEEP_HIGHLIGHT", "highlightId": highlight_id, "actor": "reviewer"},
        )
        apply_highlight_operation(
            self.session_dir,
            self.session_id,
            {"operation": "REJECT_HIGHLIGHT", "highlightId": highlight_id, "actor": "reviewer"},
        )

        history = read_highlight_operations(self.session_dir)
        current = read_highlight_store(self.session_dir, self.session_id)["highlights"][highlight_id]
        self.assertEqual(
            ["CREATE_HIGHLIGHT", "UPDATE_HIGHLIGHT", "KEEP_HIGHLIGHT", "REJECT_HIGHLIGHT"],
            [item["operation"] for item in history],
        )
        self.assertEqual("Initial memory", history[1]["beforeHighlights"][highlight_id]["summary"])
        self.assertEqual("Corrected memory", current["summary"])
        self.assertEqual([2, 5], current["sourceChunks"])
        self.assertEqual("rejected", current["reviewStatus"])

    def test_reconciliation_batch_and_old_session_compatibility(self):
        old_dir = os.path.join(self.temp_dir.name, "old-session")
        os.makedirs(old_dir)
        self.assertEqual(
            {}, read_highlight_store(old_dir, "87654321")["highlights"]
        )
        self.assertFalse(os.path.exists(os.path.join(old_dir, HIGHLIGHTS_FILENAME)))

        result = apply_highlight_operations_batch(
            self.session_dir,
            self.session_id,
            [{
                "operation": "CREATE_HIGHLIGHT",
                "highlight": {
                    "categories": ["humor", "character_moment"],
                    "confidence": "medium",
                    "summary": "The table kept returning to the wizard's terrible alias.",
                    "participants": ["Wizard"],
                    "sourceChunks": [3, 6],
                    "relatedEventIds": [],
                },
                "reason": "Repeated table reaction",
            }],
            batch_metadata={"finalizationId": "fin_test"},
        )
        highlight_id = next(iter(result["highlightStore"]["highlights"]))
        self.assertRegex(highlight_id, r"^hlt_[0-9a-f]{32}$")
        self.assertEqual(
            "RECONCILIATION_HIGHLIGHT_BATCH",
            read_highlight_operations(self.session_dir)[0]["operation"],
        )

    def test_highlights_require_transcript_evidence(self):
        with self.assertRaisesRegex(ValueError, "source transcript chunk"):
            self._create(chunks=[])


if __name__ == "__main__":
    unittest.main()
