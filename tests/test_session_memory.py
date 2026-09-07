import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from session_events import create_event, read_event_store
from session_highlights import create_highlight, read_highlight_store
from session_memory import (
    EDITS_FILENAME,
    PUBLICATIONS_FILENAME,
    apply_edit_transaction,
    build_publish_payload,
    publication_status,
    read_canonical_memory,
    read_edit_operations,
    read_publication_operations,
    record_publication,
    record_rebuild,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "session-memory-v1.json"


class SessionMemoryEditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_dir = self.temp_dir.name
        self.session_id = "1788720000000"
        event_record = create_event(
            self.session_dir,
            self.session_id,
            {
                "type": "discovery",
                "status": "active",
                "importance": "high",
                "confidence": "medium",
                "summary": "The party found a chamber.",
                "facts": ["A bell was present."],
                "entities": [{"canonicalName": "The Hollow Bell", "kind": "item"}],
                "sourceChunks": [1, 2],
            },
            actor="reconciliation",
        )
        self.event_id = event_record["payload"]["eventId"]
        highlight_record = create_highlight(
            self.session_dir,
            self.session_id,
            {
                "categories": ["memorable_action"],
                "confidence": "medium",
                "summary": "Marcel noticed the crest.",
                "participants": ["Marcel"],
                "sourceChunks": [2],
                "relatedEventIds": [self.event_id],
            },
            actor="reconciliation",
        )
        self.highlight_id = highlight_record["payload"]["highlightId"]
        self.event_store = read_event_store(self.session_dir, self.session_id)
        self.highlight_store = read_highlight_store(self.session_dir, self.session_id)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _memory(self, event_store=None, highlight_store=None):
        return read_canonical_memory(
            self.session_dir,
            self.session_id,
            event_store or self.event_store,
            highlight_store or self.highlight_store,
            built=True,
        )

    def _edit(self, memory, operations):
        return apply_edit_transaction(
            self.session_dir,
            self.session_id,
            self.event_store,
            self.highlight_store,
            base_revision=memory["revision"],
            base_digest=memory["digest"],
            operations=operations,
            built=True,
        )

    def test_one_save_edits_both_streams_without_touching_generated_files(self):
        preserved_paths = [
            "session_events.json",
            "session_event_operations.jsonl",
            "session_highlights.json",
            "session_highlight_operations.jsonl",
            "transcript.txt",
            "audio_000001.webm",
            "notes.jsonl",
            "notes.txt",
            "game_summary.txt",
            "game_narrative.txt",
        ]
        for name in preserved_paths[4:]:
            Path(self.session_dir, name).write_bytes(f"unchanged:{name}".encode("utf-8"))
        before_bytes = {
            name: Path(self.session_dir, name).read_bytes() for name in preserved_paths
        }
        baseline = self._memory()
        self.assertEqual(1, baseline["revision"])

        result = self._edit(
            baseline,
            [
                {
                    "operation": "UPDATE_EVENT",
                    "eventId": self.event_id,
                    "changes": {
                        "summary": "The party found the bell chamber.",
                        "facts": ["The bronze bell rang without being touched."],
                        "entities": ["The Hollow Bell", "Marcel"],
                        "confidence": "high",
                    },
                },
                {
                    "operation": "UPDATE_HIGHLIGHT",
                    "highlightId": self.highlight_id,
                    "changes": {
                        "summary": "Marcel identified the forgotten crest.",
                        "participants": ["Marcel", "Vayne"],
                        "confidence": "high",
                    },
                },
            ],
        )

        memory = result["memory"]
        self.assertEqual(2, memory["revision"])
        self.assertEqual(
            "The party found the bell chamber.",
            memory["events"][self.event_id]["summary"],
        )
        self.assertEqual(
            "Marcel identified the forgotten crest.",
            memory["highlights"][self.highlight_id]["summary"],
        )
        self.assertEqual(1, len(read_edit_operations(self.session_dir)))
        self.assertEqual(
            {"human"},
            {item["source"] for item in result["operation"]["actions"]},
        )
        for name, value in before_bytes.items():
            self.assertEqual(value, Path(self.session_dir, name).read_bytes(), name)
        self.assertFalse(os.path.exists(os.path.join(self.session_dir, PUBLICATIONS_FILENAME)))

        reloaded = self._memory()
        self.assertEqual(2, reloaded["revision"])
        self.assertEqual(
            "The party found the bell chamber.",
            reloaded["events"][self.event_id]["summary"],
        )

    def test_stale_or_internal_edits_are_rejected_without_a_partial_save(self):
        baseline = self._memory()
        with self.assertRaisesRegex(ValueError, "digest is stale"):
            apply_edit_transaction(
                self.session_dir,
                self.session_id,
                self.event_store,
                self.highlight_store,
                base_revision=baseline["revision"],
                base_digest="0" * 64,
                operations=[{
                    "operation": "UPDATE_EVENT",
                    "eventId": self.event_id,
                    "changes": {"summary": "Nope"},
                }],
                built=True,
            )
        with self.assertRaisesRegex(ValueError, "Unsupported event fields: sourceChunks"):
            self._edit(
                baseline,
                [{
                    "operation": "UPDATE_EVENT",
                    "eventId": self.event_id,
                    "changes": {"sourceChunks": [99]},
                }],
            )
        with self.assertRaisesRegex(ValueError, "Unknown event ID"):
            self._edit(
                baseline,
                [{
                    "operation": "REMOVE_EVENT",
                    "eventId": "evt_00000000000000000000000000000000",
                }],
            )
        self.assertFalse(os.path.exists(os.path.join(self.session_dir, EDITS_FILENAME)))

    def test_remove_restore_and_conflicts_are_reversible_and_explicit(self):
        baseline = self._memory()
        removed = self._edit(
            baseline,
            [
                {"operation": "REMOVE_EVENT", "eventId": self.event_id},
                {"operation": "REMOVE_HIGHLIGHT", "highlightId": self.highlight_id},
            ],
        )["memory"]
        self.assertEqual(2, removed["revision"])
        self.assertNotIn(self.event_id, removed["events"])
        self.assertNotIn(self.highlight_id, removed["highlights"])
        self.assertEqual(self.event_id, removed["removed"]["events"][0]["targetId"])
        self.assertTrue(removed["removed"]["events"][0]["canRestore"])

        restored = self._edit(
            removed,
            [
                {"operation": "RESTORE_EVENT", "eventId": self.event_id},
                {"operation": "RESTORE_HIGHLIGHT", "highlightId": self.highlight_id},
            ],
        )["memory"]
        self.assertEqual(3, restored["revision"])
        self.assertIn(self.event_id, restored["events"])
        self.assertIn(self.highlight_id, restored["highlights"])

        edited = self._edit(
            restored,
            [{
                "operation": "UPDATE_EVENT",
                "eventId": self.event_id,
                "changes": {"summary": "Human-canonical wording"},
            }],
        )["memory"]
        changed_generated = copy.deepcopy(self.event_store)
        changed_generated["events"][self.event_id]["revision"] += 1
        changed_generated["events"][self.event_id]["summary"] = "Later generated wording"
        conflicted = self._memory(event_store=changed_generated)
        self.assertEqual(
            "Human-canonical wording", conflicted["events"][self.event_id]["summary"]
        )
        self.assertEqual("generated_revision_changed", conflicted["conflicts"][0]["code"])

        missing_generated = copy.deepcopy(changed_generated)
        missing_generated["events"].pop(self.event_id)
        missing = self._memory(event_store=missing_generated)
        self.assertIn(
            "missing_generated_item", {item["code"] for item in missing["conflicts"]}
        )

    def test_rebuild_marker_increments_once_and_is_idempotent(self):
        baseline = self._memory()
        edited = self._edit(
            baseline,
            [{
                "operation": "UPDATE_EVENT",
                "eventId": self.event_id,
                "changes": {"summary": "Human wording"},
            }],
        )["memory"]
        first = record_rebuild(
            self.session_dir,
            self.session_id,
            self.event_store,
            self.highlight_store,
            finalization_id="fin_rebuild",
            rebuild_of_finalization_id="fin_original",
            before_generated_digest=edited["generatedDigest"],
        )
        repeated = record_rebuild(
            self.session_dir,
            self.session_id,
            self.event_store,
            self.highlight_store,
            finalization_id="fin_rebuild",
            rebuild_of_finalization_id="fin_original",
            before_generated_digest=edited["generatedDigest"],
        )
        self.assertEqual(first["operationId"], repeated["operationId"])
        self.assertEqual(3, first["memoryRevision"])
        self.assertEqual(2, len(read_edit_operations(self.session_dir)))
        self.assertFalse(os.path.exists(os.path.join(self.session_dir, PUBLICATIONS_FILENAME)))


class SessionMemoryPublishContractTests(unittest.TestCase):
    @staticmethod
    def _empty_memory(revision=1):
        return {
            "built": True,
            "revision": revision,
            "events": {},
            "highlights": {},
        }

    def _build_empty_payload(self, **overrides):
        values = {
            "memory": self._empty_memory(),
            "campaign_id": "campaign-three-friends",
            "campaign_name": "The Three Friends",
            "dungeon_share_slug": "three-friends",
            "world": {"id": "world-arentor", "name": "Arentor"},
            "session_id": "1788720000000",
            "session_title": "The Bell Beneath Briar Hollow",
            "session_date": "2026-09-06",
            "published_at": "2026-09-06T19:45:00.000Z",
        }
        values.update(overrides)
        memory = values.pop("memory")
        return build_publish_payload(memory, **values)

    def test_deterministic_payload_matches_shared_consumer_fixture(self):
        event_id = "evt_11111111111111111111111111111111"
        highlight_id = "hlt_22222222222222222222222222222222"
        memory = {
            "built": True,
            "revision": 3,
            "events": {
                event_id: {
                    "eventId": event_id,
                    "firstChunk": 2,
                    "type": "discovery",
                    "status": "active",
                    "importance": "high",
                    "confidence": "high",
                    "summary": "The party found the bell chamber beneath Briar Hollow.",
                    "facts": [
                        "The bronze bell rang without being touched.",
                        "Marcel recognized the crest carved into its frame.",
                    ],
                    "entities": [
                        {"canonicalName": "Briar Hollow", "private": "dropped"},
                        {"displayName": "Marcel"},
                        "The Hollow Bell",
                    ],
                    "sourceChunks": [2],
                    "reviewStatus": "kept",
                    "revision": 7,
                }
            },
            "highlights": {
                highlight_id: {
                    "highlightId": highlight_id,
                    "firstChunk": 2,
                    "categories": ["memorable_action", "character_moment"],
                    "confidence": "high",
                    "summary": "Marcel identified the forgotten crest just before the bell rang.",
                    "participants": ["Marcel", "Vayne"],
                    "relatedEventIds": [event_id],
                    "sourceChunks": [2],
                    "reviewStatus": "kept",
                    "revision": 4,
                }
            },
        }
        payload = build_publish_payload(
            memory,
            campaign_id="campaign-three-friends",
            campaign_name="The Three Friends",
            dungeon_share_slug="three-friends",
            world={"id": "world-arentor", "name": "Arentor"},
            session_id="1788720000000",
            session_title="The Bell Beneath Briar Hollow",
            session_date="2026-09-06",
            published_at="2026-09-06T19:45:00.000Z",
        )

        expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(expected, payload)
        encoded = json.dumps(payload)
        for forbidden in (
            "sourceChunks",
            "reviewStatus",
            "revision\": 7",
            "private",
            "transcript",
            "audio",
            "dmOnlyNotes",
            "reasoning",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_producer_enforces_consumer_contract_boundaries(self):
        invalid_cases = (
            ({"memory": self._empty_memory(0)}, "positive integer"),
            ({"campaign_id": "campaign with spaces"}, "campaign.id"),
            ({"campaign_name": "x" * 161}, "at most 160"),
            ({"session_title": "x" * 241}, "at most 240"),
            ({"session_date": "09/06/2026"}, "ISO date"),
            ({"published_at": "2026-09-06 19:45:00"}, "ISO datetime"),
        )
        for overrides, message in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    self._build_empty_payload(**overrides)

        too_long_fact = self._empty_memory()
        event_id = "evt_11111111111111111111111111111111"
        too_long_fact["events"][event_id] = {
            "eventId": event_id,
            "type": "discovery",
            "status": "active",
            "importance": "high",
            "confidence": "high",
            "summary": "A valid summary.",
            "facts": ["x" * 1001],
            "entities": [],
        }
        with self.assertRaisesRegex(ValueError, "at most 1000"):
            self._build_empty_payload(memory=too_long_fact)

    def test_publication_history_preserves_success_across_failed_update(self):
        with tempfile.TemporaryDirectory() as session_dir:
            first = record_publication(
                session_dir,
                "1788720000000",
                succeeded=True,
                memory_revision=1,
                memory_digest="a" * 64,
                dungeon_share_slug="three-friends",
                occurred_at=100,
                destination_session_id="1788720000000",
                action="created",
                state="published",
            )
            current = publication_status(session_dir, 1, "a" * 64)
            self.assertEqual("current", current["state"])
            self.assertEqual(first["publicationId"], current["lastSuccessful"]["publicationId"])

            stale = publication_status(session_dir, 2, "b" * 64)
            self.assertEqual("stale", stale["state"])
            self.assertTrue(stale["updateAvailable"])
            record_publication(
                session_dir,
                "1788720000000",
                succeeded=False,
                memory_revision=2,
                memory_digest="b" * 64,
                dungeon_share_slug="three-friends",
                occurred_at=200,
                error="offline",
            )
            failed = publication_status(session_dir, 2, "b" * 64)
            self.assertEqual("needs_attention", failed["state"])
            self.assertEqual(1, failed["publishedRevision"])
            self.assertEqual(first["publicationId"], failed["lastSuccessful"]["publicationId"])
            self.assertEqual(2, len(read_publication_operations(session_dir)))
            self.assertTrue(os.path.exists(os.path.join(session_dir, PUBLICATIONS_FILENAME)))


class SessionMemoryServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.uploads_dir = os.path.join(self.temp_dir.name, "uploads")
        self.campaigns_dir = os.path.join(self.temp_dir.name, "campaigns")
        self.worlds_dir = os.path.join(self.temp_dir.name, "worlds")
        self.session_id = "1788720000000"
        self.patches = [
            mock.patch.object(server, "UPLOADS_DIR", self.uploads_dir),
            mock.patch.object(server, "CAMPAIGNS_DIR", self.campaigns_dir),
            mock.patch.object(server, "WORLDS_DIR", self.worlds_dir),
        ]
        for patcher in self.patches:
            patcher.start()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        os.makedirs(session_dir, exist_ok=True)
        server.write_json_atomic(os.path.join(session_dir, "status.json"), {
            "sessionId": self.session_id,
            "createdAt": 1788720000,
            "sessionName": "The Bell Beneath Briar Hollow",
            "campaignId": "campaign-three-friends",
            "finalization": {"state": "finalized", "finalizationId": "fin_original"},
        })
        os.makedirs(os.path.join(self.worlds_dir, "world-arentor"), exist_ok=True)
        server.write_json_atomic(
            os.path.join(self.worlds_dir, "world-arentor", "world.manifest.json"),
            {"schemaVersion": 1, "worldId": "world-arentor", "displayName": "Arentor"},
        )
        server.write_campaign("campaign-three-friends", {
            "campaignId": "campaign-three-friends",
            "name": "The Three Friends",
            "worldId": "world-arentor",
        })
        event_record = create_event(
            session_dir,
            self.session_id,
            {
                "type": "discovery",
                "status": "active",
                "importance": "high",
                "confidence": "high",
                "summary": "The party found the bell chamber.",
                "facts": ["The bell rang."],
                "entities": ["The Hollow Bell"],
                "sourceChunks": [1],
            },
        )
        self.event_id = event_record["payload"]["eventId"]

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_ui_change_shape_is_strict_and_returns_sibling_publication(self):
        baseline = server.read_session_memory(self.session_id)
        result = server.edit_session_memory(
            self.session_id,
            base_revision=baseline["revision"],
            base_digest=baseline["digest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "update",
                "fields": {"summary": "Human-corrected chamber."},
            }],
        )
        self.assertEqual(2, result["memory"]["revision"])
        self.assertEqual(result["memory"]["digest"], result["memory"]["canonicalDigest"])
        self.assertIsInstance(result["memory"]["events"], list)
        self.assertEqual(self.event_id, result["memory"]["events"][0]["eventId"])
        self.assertEqual(result["memory"]["lastEditedAt"], result["memory"]["editedAt"])
        self.assertIn("removedEvents", result["memory"])
        self.assertIn("publication", result)
        self.assertNotIn("publication", result["memory"])

        removed = server.edit_session_memory(
            self.session_id,
            base_revision=result["memory"]["revision"],
            base_digest=result["memory"]["canonicalDigest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "remove",
            }],
        )["memory"]
        self.assertEqual([], removed["events"])
        self.assertEqual(self.event_id, removed["removedEvents"][0]["eventId"])
        self.assertTrue(removed["removedEvents"][0]["canRestore"])

    def test_publish_is_one_attempt_and_failed_update_keeps_prior_success(self):
        baseline = server.read_session_memory(self.session_id)
        calls = []

        def success(payload):
            calls.append(payload)
            return {
                "ok": True,
                "action": "created",
                "state": "published",
                "post": {"id": "post-destination-id"},
            }

        first = server.publish_session_memory_to_dungeonshare(
            self.session_id,
            "three-friends",
            expected_revision=baseline["revision"],
            expected_digest=baseline["digest"],
            request_fn=success,
            now=1788723900,
        )
        self.assertEqual(1, len(calls))
        self.assertEqual("current", first["status"]["state"])
        self.assertEqual(
            "post-destination-id",
            first["publication"]["destinationSessionId"],
        )

        edited = server.edit_session_memory(
            self.session_id,
            base_revision=baseline["revision"],
            base_digest=baseline["digest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "update",
                "fields": {"summary": "Changed after publication."},
            }],
        )["memory"]
        self.assertEqual("stale", edited["publication"]["state"] if "publication" in edited else server.read_session_memory(self.session_id)["publication"]["state"])

        failed_calls = []

        def fail(payload):
            failed_calls.append(payload)
            raise RuntimeError("offline")

        with self.assertRaisesRegex(RuntimeError, "offline"):
            server.publish_session_memory_to_dungeonshare(
                self.session_id,
                "three-friends",
                expected_revision=edited["revision"],
                expected_digest=edited["canonicalDigest"],
                request_fn=fail,
                now=1788724000,
            )
        self.assertEqual(1, len(failed_calls))
        status = server.read_session_memory(self.session_id)["publication"]
        self.assertEqual("needs_attention", status["state"])
        self.assertEqual(1, status["publishedRevision"])

    def test_preview_uses_effective_edit_and_never_publishes(self):
        baseline = server.read_session_memory(self.session_id)
        edited = server.edit_session_memory(
            self.session_id,
            base_revision=baseline["revision"],
            base_digest=baseline["digest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "update",
                "fields": {"summary": "The reviewed bell chamber."},
            }],
        )["memory"]
        preview = server.preview_session_memory_publication(
            self.session_id,
            "three-friends",
            now=1788723900,
        )
        self.assertEqual(edited["revision"], preview["memoryRevision"])
        self.assertEqual(edited["canonicalDigest"], preview["canonicalDigest"])
        self.assertEqual(
            "The reviewed bell chamber.",
            preview["payload"]["memory"]["events"][0]["summary"],
        )
        self.assertFalse(
            os.path.exists(
                os.path.join(self.uploads_dir, self.session_id, PUBLICATIONS_FILENAME)
            )
        )

    def test_successful_republish_clears_stale_state_without_duplicate_identity(self):
        baseline = server.read_session_memory(self.session_id)
        destination_ids = []

        def destination(payload):
            destination_ids.append(payload["session"]["id"])
            return {
                "ok": True,
                "action": "created" if len(destination_ids) == 1 else "updated",
                "state": "published",
                "post": {"id": "one-dungeonshare-post"},
            }

        server.publish_session_memory_to_dungeonshare(
            self.session_id,
            "three-friends",
            expected_revision=baseline["revision"],
            expected_digest=baseline["digest"],
            request_fn=destination,
            now=1788723900,
        )
        edited = server.edit_session_memory(
            self.session_id,
            base_revision=baseline["revision"],
            base_digest=baseline["digest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "update",
                "fields": {"summary": "Reviewed after first publication."},
            }],
        )["memory"]
        self.assertEqual("stale", server.read_session_memory(self.session_id)["publication"]["state"])
        republished = server.publish_session_memory_to_dungeonshare(
            self.session_id,
            "three-friends",
            expected_revision=edited["revision"],
            expected_digest=edited["canonicalDigest"],
            request_fn=destination,
            now=1788724000,
        )
        self.assertEqual("current", republished["status"]["state"])
        self.assertEqual(edited["revision"], republished["status"]["publishedRevision"])
        self.assertEqual([self.session_id, self.session_id], destination_ids)
        self.assertEqual("one-dungeonshare-post", republished["publication"]["destinationSessionId"])

    def test_malformed_destination_response_records_failure_without_changing_memory(self):
        baseline = server.read_session_memory(self.session_id)
        with self.assertRaisesRegex(RuntimeError, "invalid publication response"):
            server.publish_session_memory_to_dungeonshare(
                self.session_id,
                "three-friends",
                expected_revision=baseline["revision"],
                expected_digest=baseline["digest"],
                request_fn=lambda payload: {"unexpected": True},
                now=1788723900,
            )
        after = server.read_session_memory(self.session_id)
        self.assertEqual(baseline["digest"], after["digest"])
        self.assertEqual("needs_attention", after["publication"]["state"])

    def test_publish_conflict_stops_before_destination_request(self):
        calls = []
        conflicted = {
            "built": True,
            "revision": 2,
            "digest": "a" * 64,
            "conflicts": [{"code": "generated_revision_changed"}],
        }
        with mock.patch.object(server, "read_session_memory", return_value=conflicted):
            with self.assertRaisesRegex(ValueError, "conflicts"):
                server.publish_session_memory_to_dungeonshare(
                    self.session_id,
                    "three-friends",
                    expected_revision=2,
                    expected_digest="a" * 64,
                    request_fn=lambda payload: calls.append(payload),
                )
        self.assertEqual([], calls)

    def test_human_edits_block_rebuild_before_any_model_call(self):
        baseline = server.read_session_memory(self.session_id)
        edited = server.edit_session_memory(
            self.session_id,
            base_revision=baseline["revision"],
            base_digest=baseline["digest"],
            changes=[{
                "kind": "event",
                "id": self.event_id,
                "action": "update",
                "fields": {"summary": "Protected human edit."},
            }],
        )["memory"]
        calls = []
        with mock.patch.dict(os.environ, {"ENABLE_STRUCTURED_RECONCILIATION": "1"}):
            with self.assertRaisesRegex(ValueError, "contains human edits"):
                server.run_structured_reconciliation(
                    self.session_id,
                    confirm=True,
                    rebuild=True,
                    model_client=lambda payload: calls.append(payload),
                )
            with self.assertRaisesRegex(ValueError, "revision is stale"):
                server.run_structured_reconciliation(
                    self.session_id,
                    confirm=True,
                    rebuild=True,
                    confirm_human_edits=True,
                    expected_memory_revision=edited["revision"] - 1,
                    model_client=lambda payload: calls.append(payload),
                )
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
