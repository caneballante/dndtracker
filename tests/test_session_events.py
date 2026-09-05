import json
import os
import tempfile
import unittest

from session_events import (
    EVENTS_FILENAME,
    OPERATIONS_FILENAME,
    apply_event_operation,
    apply_event_operations_batch,
    create_event,
    mark_superseded,
    merge_events,
    read_event_operations,
    read_event_store,
    update_event,
)


class SessionEventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_dir = self.temp_dir.name
        self.session_id = "12345678"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create(self, summary, chunks, **extra):
        event = {
            "type": "npc_interaction",
            "summary": summary,
            "sourceChunks": chunks,
            "facts": extra.pop("facts", []),
            "entities": extra.pop("entities", []),
            **extra,
        }
        record = create_event(self.session_dir, self.session_id, event, actor="test")
        return record["payload"]["eventId"]

    def test_ids_are_uuid_based_and_independent_of_prose(self):
        first = self._create("The same generated sentence.", [1])
        second = self._create("The same generated sentence.", [1])

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^evt_[0-9a-f]{32}$")
        self.assertEqual(2, len(read_event_store(self.session_dir, self.session_id)["events"]))

    def test_update_keeps_identity_evidence_and_prior_history(self):
        event_id = self._create("Initial account", [2, 3], facts=["Initial fact"])
        update_event(
            self.session_dir,
            self.session_id,
            event_id,
            {"summary": "Corrected account", "sourceChunks": [5], "facts": ["Corrected fact"]},
            actor="reviewer",
        )

        event = read_event_store(self.session_dir, self.session_id)["events"][event_id]
        history = read_event_operations(self.session_dir)
        self.assertEqual(event_id, event["eventId"])
        self.assertEqual([2, 3, 5], event["sourceChunks"])
        self.assertEqual("Initial account", history[0]["afterEvents"][event_id]["summary"])
        self.assertEqual("Initial account", history[1]["beforeEvents"][event_id]["summary"])
        self.assertEqual("Corrected account", history[1]["afterEvents"][event_id]["summary"])

    def test_merge_retains_events_history_and_all_evidence(self):
        target_id = self._create("Met Vayne", [1], facts=["Vayne offered work"], entities=["Vayne"])
        source_id = self._create("Discussed an object", [4, 5], facts=["An object is missing"], entities=["Relic"])
        merge_events(
            self.session_dir,
            self.session_id,
            target_id,
            [source_id],
            changes={"summary": "Vayne offered work to recover the missing relic"},
        )

        store = read_event_store(self.session_dir, self.session_id)
        target = store["events"][target_id]
        source = store["events"][source_id]
        merge_record = read_event_operations(self.session_dir)[-1]
        self.assertEqual([1, 4, 5], target["sourceChunks"])
        self.assertIn(source_id, target["mergedFrom"])
        self.assertEqual("superseded", source["status"])
        self.assertEqual([target_id], source["supersededBy"])
        self.assertIn(source_id, merge_record["beforeEvents"])
        self.assertIn(source_id, merge_record["afterEvents"])

    def test_materialized_state_rebuilds_from_append_only_history(self):
        event_id = self._create("A durable event", [8])
        os.remove(os.path.join(self.session_dir, EVENTS_FILENAME))

        rebuilt = read_event_store(self.session_dir, self.session_id)

        self.assertIn(event_id, rebuilt["events"])
        self.assertEqual(1, rebuilt["lastSequence"])
        self.assertTrue(os.path.exists(os.path.join(self.session_dir, EVENTS_FILENAME)))
        self.assertTrue(os.path.exists(os.path.join(self.session_dir, OPERATIONS_FILENAME)))

    def test_explicit_supersede_and_dispatch_are_recorded(self):
        first = self._create("First", [1])
        replacement = self._create("Replacement", [2])
        mark_superseded(self.session_dir, self.session_id, first, replacement, reason="duplicate")
        result = apply_event_operation(
            self.session_dir,
            self.session_id,
            {"operation": "MARK_RESOLVED", "eventId": replacement, "actor": "reviewer"},
        )

        events = result["eventStore"]["events"]
        self.assertEqual("superseded", events[first]["status"])
        self.assertEqual([replacement], events[first]["supersededBy"])
        self.assertEqual("resolved", events[replacement]["status"])
        self.assertEqual([1, 2, 3, 4], [item["sequence"] for item in read_event_operations(self.session_dir)])

    def test_future_review_can_keep_reject_edit_and_change_importance(self):
        event_id = self._create("Reviewable event", [1])
        apply_event_operation(
            self.session_dir,
            self.session_id,
            {
                "operation": "UPDATE_EVENT",
                "eventId": event_id,
                "changes": {"summary": "Edited event", "importance": "critical"},
                "actor": "reviewer",
            },
        )
        apply_event_operation(
            self.session_dir,
            self.session_id,
            {"operation": "KEEP_EVENT", "eventId": event_id, "actor": "reviewer"},
        )
        apply_event_operation(
            self.session_dir,
            self.session_id,
            {"operation": "REJECT_EVENT", "eventId": event_id, "actor": "reviewer"},
        )

        event = read_event_store(self.session_dir, self.session_id)["events"][event_id]
        self.assertEqual("Edited event", event["summary"])
        self.assertEqual("critical", event["importance"])
        self.assertEqual("rejected", event["reviewStatus"])
        self.assertEqual(
            ["CREATE_EVENT", "UPDATE_EVENT", "KEEP_EVENT", "REJECT_EVENT"],
            [item["operation"] for item in read_event_operations(self.session_dir)],
        )

    def test_event_requires_transcript_evidence_and_leaves_notes_untouched(self):
        notes_path = os.path.join(self.session_dir, "notes.jsonl")
        with open(notes_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"chunkIndex": 1, "timeline": ["legacy"]}) + "\n")
        with open(notes_path, "r", encoding="utf-8") as handle:
            original_notes = handle.read()

        with self.assertRaisesRegex(ValueError, "source transcript chunk"):
            create_event(self.session_dir, self.session_id, {"summary": "No evidence", "sourceChunks": []})
        self._create("Structured but separate", [1])

        with open(notes_path, "r", encoding="utf-8") as handle:
            self.assertEqual(original_notes, handle.read())

    def test_reconciliation_batch_is_atomic_and_assigns_new_event_ids(self):
        result = apply_event_operations_batch(
            self.session_dir,
            self.session_id,
            [{
                "operation": "CREATE_EVENT",
                "event": {
                    "type": "discovery",
                    "status": "active",
                    "importance": "high",
                    "confidence": "high",
                    "summary": "The sealed door opens with moonlight.",
                    "facts": ["Moonlight is the key."],
                    "entities": ["Sealed door"],
                    "sourceChunks": [2],
                },
                "reason": "Durable discovery",
            }],
            batch_metadata={"finalizationId": "fin_test"},
        )

        event_id = next(iter(result["eventStore"]["events"]))
        history = read_event_operations(self.session_dir)
        self.assertRegex(event_id, r"^evt_[0-9a-f]{32}$")
        self.assertEqual("RECONCILIATION_BATCH", history[0]["operation"])
        self.assertEqual("fin_test", history[0]["payload"]["batchMetadata"]["finalizationId"])
        self.assertEqual([2], history[0]["afterEvents"][event_id]["sourceChunks"])

    def test_invalid_late_operation_rolls_back_entire_batch(self):
        with self.assertRaisesRegex(ValueError, "Unknown eventId"):
            apply_event_operations_batch(
                self.session_dir,
                self.session_id,
                [
                    {
                        "operation": "CREATE_EVENT",
                        "event": {"type": "other", "summary": "Would be rolled back", "sourceChunks": [1]},
                    },
                    {"operation": "MARK_RESOLVED", "eventId": "evt_00000000000000000000000000000000"},
                ],
            )

        self.assertEqual([], read_event_operations(self.session_dir))
        self.assertEqual({}, read_event_store(self.session_dir, self.session_id)["events"])


if __name__ == "__main__":
    unittest.main()
