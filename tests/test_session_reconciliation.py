import json
import os
import tempfile
import unittest
from unittest import mock

import server
from reconciliation_context import (
    build_orientation_context,
    build_reference_canon_packet,
    build_session_evidence_packet,
)
from session_events import apply_event_operations_batch, read_event_operations, read_event_store
from session_highlights import create_highlight, read_highlight_operations, read_highlight_store
from session_reconciliation import (
    RECONCILIATION_INSTRUCTIONS,
    build_reconciliation_request,
    reconciliation_output_schema,
    validate_reconciliation_result,
    validate_reconciliation_output,
)


def _event_payload(chunk=0, summary="The party learned the gate opens at moonrise."):
    return {
        "type": "discovery",
        "status": "active",
        "importance": "high",
        "confidence": "high",
        "summary": summary,
        "facts": ["The gate opens at moonrise."],
        "entities": ["Moon gate"],
        "sourceChunks": [chunk],
    }


def _highlight_payload(chunk=0, related_event_ids=None):
    return {
        "categories": ["clever_solution", "memorable_action"],
        "confidence": "high",
        "summary": "The rogue used the hanging banner to cross the collapsing bridge.",
        "participants": ["Rogue"],
        "sourceChunks": [chunk],
        "relatedEventIds": related_event_ids or [],
    }


def _response(operations, highlights=None, response_id="resp_offline"):
    return {
        "id": response_id,
        "status": "completed",
        "output_text": json.dumps({
            "operations": operations,
            "highlightOperations": highlights or [],
        }),
        "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
    }


class ReconciliationContractTests(unittest.TestCase):
    def test_request_is_ordered_strict_and_focused_on_durable_events(self):
        session_evidence = build_session_evidence_packet(
            ordered_entries=[{"chunkIndex": 0, "text": "first"}, {"chunkIndex": 1, "text": "second"}],
            tracker_state={"round": 2},
            tracker_events=[{"eventType": "damage"}],
            reviewer_corrections="[0001] edited: corrected name",
            evidence_markers=[],
        )
        orientation = build_orientation_context(
            snapshot={
                "campaignId": "test",
                "campaignName": "Test Campaign",
                "campaignSummaryText": "Locked campaign snapshot",
            },
            party_roster="DM and party",
        )
        request, diagnostics, document = build_reconciliation_request(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            max_output_tokens=4000,
            max_input_tokens=10000,
            session_id="12345678",
            finalization_id="fin_test",
            session_evidence=session_evidence,
            orientation_context=orientation,
            reference_canon=build_reference_canon_packet(),
            existing_events={},
        )

        self.assertEqual(
            [0, 1],
            [item["chunkIndex"] for item in document["sessionEvidence"]["orderedTranscriptEvidence"]],
        )
        self.assertEqual("orientation_context", document["orientationContext"]["contextClass"])
        self.assertEqual("reference_canon", document["referenceCanon"]["contextClass"])
        self.assertEqual({}, document["existingSessionHighlights"])
        self.assertEqual("json_schema", request["text"]["format"]["type"])
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertNotIn("tools", request)
        self.assertIn("Do not write a recap", RECONCILIATION_INSTRUCTIONS)
        self.assertIn("Frequency of discussion is not importance", RECONCILIATION_INSTRUCTIONS)
        self.assertIn("what will the players remember?", RECONCILIATION_INSTRUCTIONS)
        self.assertIn("combat memory, not a combat log", RECONCILIATION_INSTRUCTIONS)
        self.assertGreater(diagnostics["approximateInputTokens"], 0)

    def test_quality_guidance_is_general_and_preserves_schema_contract(self):
        instructions = RECONCILIATION_INSTRUCTIONS
        for requirement in (
            "one underlying meaningful session event",
            "Explicit player decisions are durable information",
            "relationship with an NPC or faction",
            "later explicit correction or clear clarification",
            "retain that supported name in facts and entities",
            "silently perform a final coverage check",
            "Confidence measures reliability of the supporting evidence",
        ):
            self.assertIn(requirement, instructions)
        for benchmark_answer in (
            "1787792586573",
            "Vexatious",
            "Blanders",
            "Seralith",
            "rabbit fever",
        ):
            self.assertNotIn(benchmark_answer, instructions)

        schema = reconciliation_output_schema()
        encoded = json.dumps(schema)
        self.assertIn('"decision"', encoded)
        self.assertIn('"relationship_change"', encoded)
        self.assertEqual(False, schema["additionalProperties"])
        self.assertEqual(
            ["operations", "highlightOperations"], schema["required"]
        )
        self.assertIn('"humor"', encoded)

    def test_complete_result_validates_highlights_provenance_and_optional_event_links(self):
        event_id = "evt_11111111111111111111111111111111"
        result = validate_reconciliation_result(
            {
                "operations": [],
                "highlightOperations": [{
                    "operation": "CREATE_HIGHLIGHT",
                    "highlight": _highlight_payload(2, [event_id]),
                    "reason": "Distinctive contribution",
                }],
            },
            existing_event_ids=[event_id],
            existing_highlight_ids=[],
            valid_source_chunks=[2],
        )
        self.assertEqual([event_id], result["highlightOperations"][0]["highlight"]["relatedEventIds"])

        with self.assertRaisesRegex(ValueError, "unavailable transcript chunks"):
            validate_reconciliation_result(
                {
                    "operations": [],
                    "highlightOperations": [{
                        "operation": "CREATE_HIGHLIGHT",
                        "highlight": _highlight_payload(9),
                        "reason": "No supplied evidence",
                    }],
                },
                existing_event_ids=[],
                existing_highlight_ids=[],
                valid_source_chunks=[2],
            )

        with self.assertRaisesRegex(ValueError, "unknown related event IDs"):
            validate_reconciliation_result(
                {
                    "operations": [],
                    "highlightOperations": [{
                        "operation": "CREATE_HIGHLIGHT",
                        "highlight": _highlight_payload(2, [event_id]),
                        "reason": "Dangling link",
                    }],
                },
                existing_event_ids=[],
                existing_highlight_ids=[],
                valid_source_chunks=[2],
            )

    def test_unordered_evidence_and_unknown_event_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "not ordered"):
            build_session_evidence_packet(
                ordered_entries=[{"chunkIndex": 2, "text": "late"}, {"chunkIndex": 1, "text": "early"}],
            )
        with self.assertRaisesRegex(ValueError, "unknown eventId"):
            validate_reconciliation_output(
                {"operations": [{
                    "operation": "UPDATE_EVENT",
                    "eventId": "evt_00000000000000000000000000000000",
                    "changes": _event_payload(),
                    "reason": "Unsupported ID",
                }]},
                existing_event_ids=[],
                valid_source_chunks=[0],
            )

    def test_missing_provenance_is_rejected(self):
        event = _event_payload()
        event["sourceChunks"] = []
        with self.assertRaisesRegex(ValueError, "sourceChunks"):
            validate_reconciliation_output(
                {"operations": [{"operation": "CREATE_EVENT", "event": event, "reason": "test"}]},
                existing_event_ids=[],
                valid_source_chunks=[0],
            )

    def test_update_accepts_only_a_supplied_durable_id(self):
        event_id = "evt_11111111111111111111111111111111"
        operations = validate_reconciliation_output(
            {"operations": [{
                "operation": "UPDATE_EVENT",
                "eventId": event_id,
                "changes": _event_payload(),
                "reason": "Later evidence clarified the discovery.",
            }]},
            existing_event_ids=[event_id],
            valid_source_chunks=[0],
        )
        self.assertEqual(event_id, operations[0]["eventId"])

        with self.assertRaisesRegex(ValueError, "operation is invalid"):
            validate_reconciliation_output(
                {"operations": [{"operation": "WRITE_RECAP"}]},
                existing_event_ids=[event_id],
                valid_source_chunks=[0],
            )


class ReconciliationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.uploads_dir = os.path.join(self.temp_dir.name, "uploads")
        self.campaigns_dir = os.path.join(self.temp_dir.name, "campaigns")
        self.missing_env = os.path.join(self.temp_dir.name, "no-env-file")
        self.patch_uploads = mock.patch.object(server, "UPLOADS_DIR", self.uploads_dir)
        self.patch_campaigns = mock.patch.object(server, "CAMPAIGNS_DIR", self.campaigns_dir)
        self.patch_env_path = mock.patch.object(server, "ENV_PATH", self.missing_env)
        self.patch_uploads.start()
        self.patch_campaigns.start()
        self.patch_env_path.start()
        self.env = mock.patch.dict(os.environ, {
            "ENABLE_STRUCTURED_RECONCILIATION": "1",
            "STRUCTURED_RECONCILIATION_MODEL": "gpt-5.6-sol",
            "STRUCTURED_RECONCILIATION_REASONING_EFFORT": "high",
            "STRUCTURED_RECONCILIATION_MAX_INPUT_TOKENS": "250000",
            "STRUCTURED_RECONCILIATION_MAX_OUTPUT_TOKENS": "32000",
        })
        self.env.start()
        self.session_id = "12345678"

    def tearDown(self):
        self.env.stop()
        self.patch_env_path.stop()
        self.patch_campaigns.stop()
        self.patch_uploads.stop()
        self.temp_dir.cleanup()

    def _ready_session(self, transcript="The party learned the gate opens at moonrise."):
        server.init_session(self.session_id)
        for index in (1, 0):
            server.update_status_for_chunk(self.session_id, index, f"chunk_{index:04d}.wav", 100)
            server._append_transcript(self.session_id, index, f"{transcript} Evidence {index}.")
        result = server._request_session_finalization(self.session_id, 1)
        self.assertEqual("ready_for_reconciliation", result["finalization"]["state"])
        return result

    def test_disabled_feature_and_unready_session_never_call_model(self):
        calls = []
        server.init_session(self.session_id)
        client = lambda payload: calls.append(payload)
        with mock.patch.dict(os.environ, {"ENABLE_STRUCTURED_RECONCILIATION": "0"}):
            with self.assertRaisesRegex(ValueError, "disabled"):
                server.run_structured_reconciliation(self.session_id, confirm=True, model_client=client)
        with self.assertRaisesRegex(ValueError, "ready_for_reconciliation"):
            server.run_structured_reconciliation(self.session_id, confirm=True, model_client=client)
        self.assertEqual([], calls)

    def test_dry_run_reports_size_and_cost_without_claim_or_model_call(self):
        self._ready_session()
        calls = []
        result = server.run_structured_reconciliation(
            self.session_id, dry_run=True, model_client=lambda payload: calls.append(payload)
        )

        self.assertTrue(result["wouldRun"])
        self.assertFalse(result["modelCalled"])
        self.assertEqual([], calls)
        self.assertIn("estimatedCostAtOutputCap", result["diagnostics"]["costSafety"])
        self.assertIn("orientationContext", result["diagnostics"]["contextContributions"])
        self.assertEqual(0, result["diagnostics"]["referenceRecordCount"])
        state = server._session_status_response(self.session_id)["finalization"]["state"]
        self.assertEqual("ready_for_reconciliation", state)

    def test_ambiguous_recap_boundary_blocks_before_model_call(self):
        self._ready_session(transcript="Last session, the party entered the sealed crypt.")
        calls = []

        with self.assertRaisesRegex(ValueError, "Current-session play could not be identified"):
            server.run_structured_reconciliation(
                self.session_id,
                confirm=True,
                model_client=lambda payload: calls.append(payload),
            )

        self.assertEqual([], calls)
        self.assertEqual(
            "ready_for_reconciliation",
            server._session_status_response(self.session_id)["finalization"]["state"],
        )

    def test_stored_clean_benchmark_policy_controls_dry_run_and_billable_path(self):
        self._ready_session()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        server.write_text(
            os.path.join(session_dir, server.RECONCILIATION_BENCHMARK_FILENAME),
            json.dumps({"schemaVersion": 1, "mode": "clean"}),
        )
        server.write_text(
            os.path.join(session_dir, "notes_overrides.json"),
            json.dumps({"0": {"editedText": "ANSWER KEY MUST STAY OUT"}}),
        )
        calls = []

        with mock.patch.object(
            server,
            "_reviewed_timeline_context_text",
            side_effect=AssertionError("clean benchmark touched reviewer evidence"),
        ):
            preview = server.run_structured_reconciliation(self.session_id, dry_run=True)
            result = server.run_structured_reconciliation(
                self.session_id,
                confirm=True,
                model_client=lambda payload: calls.append(payload) or _response([]),
            )

        self.assertEqual("clean", preview["benchmark"]["mode"])
        self.assertEqual("session_policy", preview["benchmark"]["source"])
        self.assertEqual(
            0,
            preview["diagnostics"]["contextContributions"]["humanReviewEvidence"]["bytes"],
        )
        self.assertEqual(1, len(calls))
        document = json.loads(calls[0]["input"])
        self.assertEqual("clean", document["benchmarkMode"])
        self.assertNotIn("reviewerConfirmedCorrections", document["sessionEvidence"])
        self.assertNotIn("ANSWER KEY MUST STAY OUT", calls[0]["input"])
        self.assertEqual([], document["sessionEvidence"]["dmEvidenceMarkers"]["markers"])
        self.assertTrue(document["sessionEvidence"]["trackerEvidence"]["operationHistory"] == [])
        self.assertEqual([], document["referenceCanon"]["includedUpFront"])
        self.assertEqual({}, document["existingStructuredEvents"])
        self.assertEqual({}, document["existingSessionHighlights"])
        self.assertEqual("finalized", result["finalization"]["state"])

    def test_clean_benchmark_rejects_nonempty_event_store(self):
        self._ready_session()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        server.write_text(
            os.path.join(session_dir, server.RECONCILIATION_BENCHMARK_FILENAME),
            json.dumps({"schemaVersion": 1, "mode": "clean"}),
        )
        apply_event_operations_batch(
            session_dir,
            self.session_id,
            [{"operation": "CREATE_EVENT", "event": _event_payload(), "reason": "preexisting"}],
        )

        with self.assertRaisesRegex(ValueError, "empty structured event store"):
            server.run_structured_reconciliation(self.session_id, dry_run=True)

    def test_clean_benchmark_rejects_nonempty_highlight_store(self):
        self._ready_session()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        server.write_text(
            os.path.join(session_dir, server.RECONCILIATION_BENCHMARK_FILENAME),
            json.dumps({"schemaVersion": 1, "mode": "clean"}),
        )
        create_highlight(
            session_dir,
            self.session_id,
            _highlight_payload(),
        )

        with self.assertRaisesRegex(ValueError, "empty session highlight store"):
            server.run_structured_reconciliation(self.session_id, dry_run=True)

    def test_success_commits_one_batch_records_usage_and_is_idempotent(self):
        self._ready_session()
        calls = []

        def client(payload):
            calls.append(payload)
            return _response(
                [{
                    "operation": "CREATE_EVENT",
                    "event": _event_payload(chunk=1),
                    "reason": "Important durable discovery",
                }],
                [{
                    "operation": "CREATE_HIGHLIGHT",
                    "highlight": _highlight_payload(chunk=1),
                    "reason": "Distinctive character action",
                }],
            )

        first = server.run_structured_reconciliation(
            self.session_id, confirm=True, model_client=client
        )
        repeated = server.run_structured_reconciliation(
            self.session_id, confirm=True, model_client=client
        )

        self.assertEqual(1, len(calls))
        request_document = json.loads(calls[0]["input"])
        self.assertEqual("session_evidence", request_document["sessionEvidence"]["contextClass"])
        self.assertEqual("orientation_context", request_document["orientationContext"]["contextClass"])
        self.assertEqual([], request_document["referenceCanon"]["includedUpFront"])
        self.assertEqual("finalized", first["finalization"]["state"])
        self.assertTrue(repeated["alreadyFinalized"])
        event_id = next(iter(first["eventStore"]["events"]))
        self.assertRegex(event_id, r"^evt_[0-9a-f]{32}$")
        highlight_id = next(iter(first["highlightStore"]["highlights"]))
        self.assertRegex(highlight_id, r"^hlt_[0-9a-f]{32}$")
        self.assertEqual(1, first["highlightOperationCount"])
        history = read_event_operations(os.path.join(self.uploads_dir, self.session_id))
        self.assertEqual(["RECONCILIATION_BATCH"], [item["operation"] for item in history])
        highlight_history = read_highlight_operations(
            os.path.join(self.uploads_dir, self.session_id)
        )
        self.assertEqual(
            ["RECONCILIATION_HIGHLIGHT_BATCH"],
            [item["operation"] for item in highlight_history],
        )
        with open(os.path.join(self.uploads_dir, self.session_id, "ai_usage.jsonl"), encoding="utf-8") as handle:
            usage = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual("session_reconciliation", usage[0]["stage"])
        self.assertEqual("gpt-5.6-sol", usage[0]["model"])

    def test_malformed_output_preserves_events_and_deliberate_retry_succeeds(self):
        self._ready_session()
        bad = lambda payload: {
            "id": "resp_bad", "status": "completed", "output_text": "not-json",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            server.run_structured_reconciliation(self.session_id, confirm=True, model_client=bad)

        session_dir = os.path.join(self.uploads_dir, self.session_id)
        failed = server._session_status_response(self.session_id)["finalization"]
        self.assertEqual("reconciliation_error", failed["state"])
        self.assertEqual({}, read_event_store(session_dir, self.session_id)["events"])

        good = lambda payload: _response([{
            "operation": "CREATE_EVENT", "event": _event_payload(), "reason": "retry",
        }], response_id="resp_retry")
        retried = server.run_structured_reconciliation(
            self.session_id, confirm=True, model_client=good
        )
        self.assertEqual("finalized", retried["finalization"]["state"])
        self.assertEqual(2, retried["finalization"]["reconciliationAttempt"])

    def test_invalid_operation_id_leaves_store_unchanged(self):
        self._ready_session()
        invalid = lambda payload: _response([{
            "operation": "MARK_RESOLVED",
            "eventId": "evt_00000000000000000000000000000000",
            "reason": "unknown",
        }])
        with self.assertRaisesRegex(ValueError, "unknown eventId"):
            server.run_structured_reconciliation(self.session_id, confirm=True, model_client=invalid)

        session_dir = os.path.join(self.uploads_dir, self.session_id)
        self.assertEqual([], read_event_operations(session_dir))
        self.assertEqual("reconciliation_error", server._session_status_response(self.session_id)["finalization"]["state"])

    def test_invalid_highlight_rejects_both_streams_before_any_store_change(self):
        self._ready_session()
        invalid = lambda payload: _response(
            [{
                "operation": "CREATE_EVENT",
                "event": _event_payload(chunk=1),
                "reason": "Would otherwise be valid",
            }],
            [{
                "operation": "CREATE_HIGHLIGHT",
                "highlight": _highlight_payload(chunk=99),
                "reason": "Unavailable evidence",
            }],
        )

        with self.assertRaisesRegex(ValueError, "unavailable transcript chunks"):
            server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=invalid
            )

        session_dir = os.path.join(self.uploads_dir, self.session_id)
        self.assertEqual({}, read_event_store(session_dir, self.session_id)["events"])
        self.assertEqual({}, read_highlight_store(session_dir, self.session_id)["highlights"])
        self.assertEqual([], read_event_operations(session_dir))
        self.assertEqual([], read_highlight_operations(session_dir))

    def test_oversized_input_stops_before_model_and_records_retryable_error(self):
        self._ready_session(transcript="large evidence " * 100)
        calls = []
        with mock.patch.object(server, "_structured_reconciliation_max_input_tokens", return_value=5):
            preview = server.run_structured_reconciliation(self.session_id, dry_run=True)
            self.assertFalse(preview["wouldRun"])
            with self.assertRaisesRegex(ValueError, "exceeds configured limit"):
                server.run_structured_reconciliation(
                    self.session_id, confirm=True, model_client=lambda payload: calls.append(payload)
                )
        self.assertEqual([], calls)
        self.assertEqual("reconciliation_error", server._session_status_response(self.session_id)["finalization"]["state"])

    def test_active_claim_blocks_duplicate_and_committed_batch_recovers_without_model(self):
        ready = self._ready_session()["finalization"]
        claimed = server._claim_session_reconciliation(self.session_id, "recon_interrupted")
        calls = []
        with self.assertRaisesRegex(ValueError, "already in progress"):
            server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=lambda payload: calls.append(payload)
            )

        session_dir = os.path.join(self.uploads_dir, self.session_id)
        apply_event_operations_batch(
            session_dir,
            self.session_id,
            [{"operation": "CREATE_EVENT", "event": _event_payload(), "reason": "committed"}],
            batch_metadata={
                "finalizationId": ready["finalizationId"],
                "reconciliationId": claimed["reconciliationId"],
                "model": "gpt-5.6-sol",
                "responseId": "resp_committed",
            },
        )
        with mock.patch.object(server, "SERVER_STARTED_AT", server.SERVER_STARTED_AT + 1):
            recovered = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=lambda payload: calls.append(payload)
            )
        self.assertTrue(recovered["alreadyCommitted"])
        self.assertEqual([], calls)
        self.assertEqual("finalized", recovered["finalization"]["state"])

    def test_committed_event_batch_recovers_pending_highlight_batch_without_model(self):
        ready = self._ready_session()["finalization"]
        claimed = server._claim_session_reconciliation(self.session_id, "recon_interrupted")
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        pending_highlights = [{
            "operation": "CREATE_HIGHLIGHT",
            "highlight": _highlight_payload(chunk=1),
            "reason": "Validated before interruption",
        }]
        apply_event_operations_batch(
            session_dir,
            self.session_id,
            [],
            batch_metadata={
                "finalizationId": ready["finalizationId"],
                "reconciliationId": claimed["reconciliationId"],
                "model": "gpt-5.6-sol",
                "responseId": "resp_committed",
                "highlightOperations": pending_highlights,
            },
        )
        calls = []

        with mock.patch.object(server, "SERVER_STARTED_AT", server.SERVER_STARTED_AT + 1):
            recovered = server.run_structured_reconciliation(
                self.session_id,
                confirm=True,
                model_client=lambda payload: calls.append(payload),
            )

        self.assertTrue(recovered["alreadyCommitted"])
        self.assertEqual([], calls)
        self.assertEqual(
            1,
            len(read_highlight_store(session_dir, self.session_id)["highlights"]),
        )
        self.assertEqual("finalized", recovered["finalization"]["state"])

    def test_stale_uncommitted_claim_is_failed_then_retried_once(self):
        self._ready_session()
        server._claim_session_reconciliation(self.session_id, "recon_stale")
        calls = []

        def client(payload):
            calls.append(payload)
            return _response([], response_id="resp_after_restart")

        with mock.patch.object(server, "SERVER_STARTED_AT", server.SERVER_STARTED_AT + 1):
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )

        self.assertEqual(1, len(calls))
        self.assertEqual("finalized", result["finalization"]["state"])
        self.assertEqual(2, result["finalization"]["reconciliationAttempt"])

    def test_historical_rebuild_is_explicit_and_preserves_evidence_legacy_and_published_outputs(self):
        self._ready_session()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        protected_session_files = {
            "chunk_0000.wav": b"durable audio evidence",
            "notes.jsonl": b'{"chunkIndex":0,"timeline":["legacy note"]}\n',
            "notes_overrides.json": b'{"0":{"editedText":"approved legacy note"}}',
            "notes.txt": b"legacy notes text",
            "game_summary.txt": b"published player recap",
        }
        for name, content in protected_session_files.items():
            with open(os.path.join(session_dir, name), "wb") as handle:
                handle.write(content)
        server._set_session_status_fields(self.session_id, {
            "campaignId": "parmedia-redux",
            "gameSummaryHandoffAt": 1712345678,
            "gameSummaryHandoffHash": "approved-hash",
            "gameSummaryHandoffCampaignId": "parmedia-redux",
        })
        campaign_dir = os.path.join(self.campaigns_dir, "parmedia-redux")
        os.makedirs(campaign_dir, exist_ok=True)
        campaign_path = os.path.join(campaign_dir, "campaign.json")
        campaign_bytes = b'{"sessionSummaries":[{"summary":"published player recap"}]}'
        with open(campaign_path, "wb") as handle:
            handle.write(campaign_bytes)

        first = server.run_structured_reconciliation(
            self.session_id,
            confirm=True,
            model_client=lambda payload: _response(
                [{
                    "operation": "CREATE_EVENT",
                    "event": _event_payload(chunk=1, summary="First memory"),
                    "reason": "Initial build",
                }],
                [{
                    "operation": "CREATE_HIGHLIGHT",
                    "highlight": _highlight_payload(chunk=1),
                    "reason": "Initial build",
                }],
                response_id="resp_initial_memory",
            ),
        )
        event_id = next(iter(first["eventStore"]["events"]))
        highlight_id = next(iter(first["highlightStore"]["highlights"]))
        usage_status = server.structured_reconciliation_status(self.session_id)["usage"]
        self.assertEqual(1, usage_status["requests"])
        self.assertIsNotNone(usage_status["estimatedCost"])
        self.assertEqual(first["usage"]["estimatedCost"], usage_status["estimatedCost"])
        transcript_before = {}
        for name in ("transcript.txt", "transcripts.jsonl"):
            with open(os.path.join(session_dir, name), "rb") as handle:
                transcript_before[name] = handle.read()
        status_before = server._session_status_response(self.session_id)
        calls = []

        with self.assertRaisesRegex(ValueError, "confirm=true"):
            server.run_structured_reconciliation(
                self.session_id,
                rebuild=True,
                model_client=lambda payload: calls.append(payload),
            )
        preview = server.run_structured_reconciliation(
            self.session_id,
            dry_run=True,
            rebuild=True,
            model_client=lambda payload: calls.append(payload),
        )
        self.assertTrue(preview["wouldRun"])
        self.assertTrue(preview["rebuild"])
        self.assertEqual([], calls)
        self.assertEqual(
            status_before["finalization"]["finalizationId"],
            server._session_status_response(self.session_id)["finalization"]["finalizationId"],
        )

        with self.assertRaisesRegex(RuntimeError, "offline failure"):
            server.run_structured_reconciliation(
                self.session_id,
                confirm=True,
                rebuild=True,
                model_client=lambda payload: (_ for _ in ()).throw(RuntimeError("offline failure")),
            )
        failed_store = read_event_store(session_dir, self.session_id)
        self.assertEqual("First memory", failed_store["events"][event_id]["summary"])
        self.assertEqual(
            "reconciliation_error",
            server._session_status_response(self.session_id)["finalization"]["state"],
        )

        def rebuild_client(payload):
            calls.append(payload)
            return _response(
                [{
                    "operation": "UPDATE_EVENT",
                    "eventId": event_id,
                    "changes": _event_payload(chunk=1, summary="Rebuilt memory"),
                    "reason": "Explicit rebuild",
                }],
                [{
                    "operation": "UPDATE_HIGHLIGHT",
                    "highlightId": highlight_id,
                    "changes": _highlight_payload(chunk=1, related_event_ids=[event_id]),
                    "reason": "Explicit rebuild",
                }],
                response_id="resp_rebuilt_memory",
            )

        rebuilt = server.run_structured_reconciliation(
            self.session_id,
            confirm=True,
            rebuild=True,
            model_client=rebuild_client,
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("finalized", rebuilt["finalization"]["state"])
        self.assertEqual("Rebuilt memory", rebuilt["eventStore"]["events"][event_id]["summary"])
        self.assertEqual(1, len(rebuilt["eventStore"]["events"]))
        self.assertEqual(1, len(rebuilt["highlightStore"]["highlights"]))
        after_status = server._session_status_response(self.session_id)
        self.assertEqual(1, len(after_status.get("reconciliationHistory") or []))
        for name, content in protected_session_files.items():
            with open(os.path.join(session_dir, name), "rb") as handle:
                self.assertEqual(content, handle.read(), name)
        for name, content in transcript_before.items():
            with open(os.path.join(session_dir, name), "rb") as handle:
                self.assertEqual(content, handle.read(), name)
        with open(campaign_path, "rb") as handle:
            self.assertEqual(campaign_bytes, handle.read())
        for key in (
            "gameSummaryHandoffAt",
            "gameSummaryHandoffHash",
            "gameSummaryHandoffCampaignId",
        ):
            self.assertEqual(status_before[key], after_status[key])

    def test_session_memory_status_treats_old_session_as_empty_and_reports_store_counts(self):
        server.init_session(self.session_id)
        server.update_status_for_chunk(self.session_id, 0, "chunk_0000.wav", 100)
        server._append_transcript(self.session_id, 0, "The party crossed the gate.")

        empty = server.structured_reconciliation_status(self.session_id)
        self.assertFalse(empty["memory"]["built"])
        self.assertEqual(0, empty["memory"]["eventCount"])
        self.assertEqual(0, empty["memory"]["highlightCount"])
        self.assertEqual(1, empty["evidence"]["availableChunkCount"])
        self.assertEqual(0, empty["usage"]["requests"])

        session_dir = os.path.join(self.uploads_dir, self.session_id)
        apply_event_operations_batch(
            session_dir,
            self.session_id,
            [{"operation": "CREATE_EVENT", "event": _event_payload(), "reason": "test"}],
        )
        create_highlight(session_dir, self.session_id, _highlight_payload())
        populated = server.structured_reconciliation_status(self.session_id)
        self.assertTrue(populated["memory"]["built"])
        self.assertEqual(1, populated["memory"]["eventCount"])
        self.assertEqual(1, populated["memory"]["highlightCount"])

    def test_session_memory_refuses_to_overlap_a_legacy_notes_rebuild(self):
        self._ready_session()
        calls = []
        with mock.patch.object(
            server,
            "_normalize_reprocess_status",
            return_value={"running": True, "phase": "notes", "processed": 3, "total": 79},
        ):
            with self.assertRaisesRegex(ValueError, "Legacy DM Notes rebuild"):
                server.run_structured_reconciliation(
                    self.session_id,
                    confirm=True,
                    model_client=lambda payload: calls.append(payload),
                )
        self.assertEqual([], calls)
        self.assertEqual(
            "ready_for_reconciliation",
            server._session_status_response(self.session_id)["finalization"]["state"],
        )


if __name__ == "__main__":
    unittest.main()
