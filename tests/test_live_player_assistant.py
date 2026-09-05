import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import live_player_assistant as live
import server
from ai_usage import record_response_usage, read_usage_events
from session_evidence import read_ordered_transcript_entries


ROOT = Path(__file__).resolve().parents[1]


def answer(chunk=19):
    return {"summary": "The party agreed to ask the clerk for help.", "sourceChunks": [chunk],
            "keyDevelopments": [{"text": "The party is waiting for a reply.", "sourceChunks": [chunk]}],
            "peopleMentioned": [], "uncertainties": []}


def response(payload=None, calls=None):
    return {"id": "resp_offline", "status": "completed", "output": calls or [],
            "output_text": json.dumps(payload if payload is not None else answer()),
            "usage": {"input_tokens": 1000, "output_tokens": 100}}


def tool_call(query="Branna Coalvein", ordinal=1):
    return {"type": "function_call", "name": "search_campaign_reference",
            "call_id": f"call_{ordinal}", "arguments": json.dumps({"query": query,
            "sources": [], "entity_types": [], "limit": 5})}


class LivePlayerAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sid = "12345678"
        self.session = self.root / "uploads" / self.sid
        self.session.mkdir(parents=True)
        self.patchers = [
            mock.patch.object(server, "UPLOADS_DIR", str(self.root / "uploads")),
            mock.patch.object(server, "CAMPAIGNS_DIR", str(self.root / "campaigns")),
            mock.patch.object(server, "load_env_file"),
            mock.patch.object(server, "_arentoria_database_path", return_value=""),
            mock.patch.object(server, "urlopen", side_effect=AssertionError("Network forbidden")),
            mock.patch.dict(os.environ, {"ENABLE_LIVE_PLAYER_ASSISTANT": "1",
                "LIVE_PLAYER_ASSISTANT_SESSION_BUDGET_USD": "1.00"}),
        ]
        for patch in self.patchers:
            patch.start()
            self.addCleanup(patch.stop)
        self.base = 1700000000
        self.entries = [{"chunkIndex": i, "createdAt": self.base + i * 60,
            "text": f"The party at minute {i} decided to ask the city clerk about their registration and wait for an answer."}
            for i in range(20)]
        self.write_entries()
        (self.session / "status.json").write_text(json.dumps({"sessionId": self.sid,
            "finalization": {"state": "reconciliation_in_progress"},
            "contextSnapshot": {"campaignSummaryText": "SECRET HISTORY NOT INPUT"},
            "chunks": [{"chunkIndex": 20, "transcriptionStatus": "pending", "text": "PENDING NOT INPUT"}]}))
        self.client = mock.Mock(return_value=response())

    def write_entries(self):
        (self.session / "transcripts.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in self.entries), encoding="utf-8")

    def run_missed(self, minutes=5, **kwargs):
        return server.player_missed(self.sid, minutes, model_client=kwargs.pop("client", self.client))

    def test_feature_flag_off_never_calls_model(self):
        with mock.patch.dict(os.environ, {"ENABLE_LIVE_PLAYER_ASSISTANT": "0"}):
            self.assertEqual("disabled", self.run_missed()["state"])
        self.client.assert_not_called()
        self.assertFalse((self.session / live.AUDIT_FILE).exists())

    def test_supported_windows_use_timestamps_with_separate_leadin(self):
        for minutes in (2, 5, 10):
            with self.subTest(minutes=minutes):
                chosen = live.select_window(str(self.session), minutes)
                self.assertEqual(list(range(20 - minutes, 20)), chosen["evidence"]["chunkIds"])
                self.assertEqual([18 - minutes, 19 - minutes], chosen["evidence"]["leadInChunkIds"])
                self.assertEqual(self.base + 19 * 60, chosen["evidence"]["throughTimestamp"])
        # Sparse/gapped chunks still select by timestamp, not chunk ordinal.
        self.entries[-1]["chunkIndex"] = 999
        self.write_entries()
        self.assertEqual([18, 999], live.select_window(str(self.session), 2)["evidence"]["chunkIds"])

    def test_recent_request_is_bounded_and_excludes_pending_and_campaign_history(self):
        result = self.run_missed()
        request = self.client.call_args.args[0]
        doc = json.loads(request["input"][0]["content"])
        self.assertEqual([15, 16, 17, 18, 19], [e["chunkIndex"] for e in doc["REQUESTED_WINDOW"]])
        self.assertEqual([13, 14], [e["chunkIndex"] for e in doc["CONTEXT_ONLY"]])
        encoded = json.dumps(request)
        self.assertNotIn("SECRET HISTORY", encoded)
        self.assertNotIn("PENDING NOT INPUT", encoded)
        self.assertNotIn(0, [entry["chunkIndex"] for entry in doc["REQUESTED_WINDOW"]])
        self.assertEqual("low", request["reasoning"]["effort"])
        self.assertEqual("gpt-5.6-sol", request["model"])
        self.assertEqual("complete", result["state"])
        self.assertEqual("transcription_completed", result["evidence"]["timingBasis"])

    def test_no_transcript_and_too_little_are_nonbillable(self):
        for entries in ([], [{"chunkIndex": 0, "createdAt": self.base, "text": "Hi."}],
                        [{"chunkIndex": 0, "text": "Unclocked text cannot provide a minute window."}]):
            self.entries = entries
            self.write_entries()
            self.assertEqual("insufficient", self.run_missed()["state"])
        self.client.assert_not_called()

    def test_invalid_session_and_window_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exist"):
            server.player_missed("99999999", 5, model_client=self.client)
        for minutes in (True, "5", 0, 3, 10.5):
            with self.assertRaisesRegex(ValueError, "windowMinutes"):
                self.run_missed(minutes)
        self.client.assert_not_called()

    def test_leadin_cannot_be_reported_as_requested_evidence(self):
        self.client.return_value = response(answer(14))
        with self.assertRaisesRegex(ValueError, "outside the requested window"):
            self.run_missed()
        self.assertIn("never report its exclusive events", live.INSTRUCTIONS)

    def test_reference_cannot_establish_occurrence_or_supply_provenance(self):
        self.client.return_value = response(answer(1000))
        with self.assertRaisesRegex(ValueError, "outside the requested window"):
            self.run_missed()
        self.assertIn("cannot create an occurrence", live.INSTRUCTIONS)
        self.assertIn("uncertain", live.INSTRUCTIONS)

    def test_installed_mixed_visibility_canon_is_player_safe(self):
        campaign = self.root / "campaigns" / "parmedia-redux"
        campaign.mkdir(parents=True)
        (campaign / "campaign.json").write_text(json.dumps({"campaignId": "parmedia-redux", "worldId": "arentor"}))
        status = json.loads((self.session / "status.json").read_text())
        status["campaignId"] = "parmedia-redux"
        (self.session / "status.json").write_text(json.dumps(status))
        orientation, search, sources = server._live_player_reference_context(self.sid)
        safe = search("Branna Coalvein")
        self.assertIn("Stonehome Heritage House", json.dumps(safe))
        for forbidden in ("Deep Ledger", "First Auditor", "dmOnly", "provenance"):
            self.assertNotIn(forbidden, json.dumps(safe))
        self.assertEqual([], search("Deep Ledger")["results"])
        self.assertEqual([], search("First Auditor")["results"])
        self.assertEqual([], search("Star-Loom Carpet", sources=["current_adventure"])["results"])
        captured = []
        def model(payload):
            captured.append(copy.deepcopy(payload))
            return response(calls=[tool_call()]) if len(captured) == 1 else response()
        self.run_missed(client=model)
        self.assertIn("Stonehome Heritage House", json.dumps(captured[-1]))
        self.assertNotIn("Deep Ledger", json.dumps(captured[-1]))
        self.assertNotIn("First Auditor", json.dumps(captured[-1]))

    def test_search_cap_is_two_and_third_call_is_explicitly_suppressed(self):
        search = mock.Mock(return_value={"resolution": "no_match", "results": []})
        with mock.patch.object(server, "_live_player_reference_context", return_value=({}, search, [])):
            self.client.side_effect = [response(calls=[tool_call(ordinal=i) for i in (1, 2, 3)]), response()]
            result = self.run_missed()
        self.assertEqual(2, search.call_count)
        self.assertEqual(2, self.client.call_count)
        self.assertEqual(2, result["searches"])
        last = self.client.call_args.args[0]
        self.assertEqual("none", last["tool_choice"])
        self.assertIn("search_budget_exhausted", json.dumps(last))

    def test_model_cap_is_two_even_if_model_asks_again(self):
        self.client.return_value = response(calls=[tool_call()])
        with self.assertRaisesRegex(ValueError, "model request limit"):
            self.run_missed()
        self.assertEqual(2, self.client.call_count)

    def test_no_retry_and_uncertain_transport_cost_blocks_further_calls(self):
        self.client.side_effect = RuntimeError("offline timeout")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.run_missed()
        self.assertEqual("attention", self.run_missed()["state"])
        self.assertEqual("attention", self.run_missed(10)["state"])
        self.assertEqual(1, self.client.call_count)
        self.assertIsNone(read_usage_events(str(self.session))[0]["estimatedCost"])

    def test_budget_persists_and_only_counts_live_assistant_usage(self):
        for stage in ("session_reconciliation", "notes_rebuild"):
            record_response_usage(str(self.session), stage, live.MODEL, "openai",
                {"usage": {"input_tokens": 1000000, "output_tokens": 0}}, server.AI_PRICING_PATH)
        self.assertEqual("complete", self.run_missed()["state"])
        self.assertAlmostEqual(0.006, live.spending(str(self.session), 1)["spent"])
        record_response_usage(str(self.session), live.STAGE, live.MODEL, "openai",
            {"usage": {"input_tokens": 250000, "output_tokens": 0}}, server.AI_PRICING_PATH)
        self.assertEqual("budget_reached", self.run_missed(10)["state"])
        # Existing cache is still free after the persisted budget is exhausted.
        self.assertTrue(self.run_missed()["cached"])
        self.assertEqual(1, self.client.call_count)

    def test_request_reserve_blocks_before_budget_would_be_exceeded(self):
        with mock.patch.dict(os.environ, {"LIVE_PLAYER_ASSISTANT_SESSION_BUDGET_USD": "0.01"}):
            self.assertEqual("budget_reached", self.run_missed()["state"])
        self.client.assert_not_called()

    def test_cached_duplicate_has_zero_new_spend_and_new_evidence_changes_fingerprint(self):
        first = self.run_missed()
        second = self.run_missed()
        self.assertTrue(second["cached"])
        self.assertEqual(0, second["newSpend"])
        self.assertEqual(first["answer"], second["answer"])
        self.entries[-1]["text"] += " The clerk agreed to help them."
        self.write_entries()
        third = self.run_missed()
        self.assertFalse(third["cached"])
        self.assertNotEqual(first["fingerprint"], third["fingerprint"])
        self.assertEqual(2, self.client.call_count)

    def test_concurrent_duplicate_is_busy_without_second_call(self):
        entered = threading.Event()
        finish = threading.Event()
        results = []
        def model(payload):
            entered.set()
            self.assertTrue(finish.wait(3))
            return response()
        worker = threading.Thread(target=lambda: results.append(self.run_missed(client=model)))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual("busy", self.run_missed()["state"])
            self.client.assert_not_called()
        finally:
            finish.set()
            worker.join(3)
        self.assertEqual("complete", results[0]["state"])

    def test_historical_and_active_requests_preserve_all_other_artifacts(self):
        files = ("session_events.json", "session_highlights.json", "notes.jsonl", "notes.txt",
                 "notes_overrides.json", "game_summary.txt", "transcript.txt", "chunk_0000.wav")
        for name in files:
            (self.session / name).write_bytes(b"Protected user content")
        campaign = self.root / "campaigns" / "default"
        campaign.mkdir(parents=True)
        (campaign / "campaign.json").write_text('{"sessionSummaries":["Published handoff"]}')
        for state, minutes in (("recording", 5), ("finalized", 10)):
            status_path = self.session / "status.json"
            status = json.loads(status_path.read_text())
            status["finalization"]["state"] = state
            status["gameSummaryHandoffHash"] = "approved"
            status_path.write_text(json.dumps(status))
            before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()
                      and p.name not in {live.AUDIT_FILE, "ai_usage.jsonl"}}
            self.assertEqual("complete", self.run_missed(minutes)["state"])
            for p, content in before.items():
                self.assertEqual(content, p.read_bytes(), str(p))
        self.assertEqual(self.entries, read_ordered_transcript_entries(str(self.session)))
        self.assertTrue(all(r["stage"] == live.STAGE for r in read_usage_events(str(self.session))))

    def test_input_limit_fails_before_model(self):
        self.entries[-1]["text"] = "excessive transcript " * 4000
        self.write_entries()
        with self.assertRaisesRegex(ValueError, "too large"):
            self.run_missed()
        self.client.assert_not_called()

    def test_endpoint_contract_works_without_http_or_network(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/session/player/missed"
        handler._read_body = lambda: json.dumps({"sessionId": self.sid, "windowMinutes": 5}).encode()
        handler._send_json = mock.Mock()
        with mock.patch.object(server, "_request_live_player_model", self.client):
            handler.do_POST()
        self.assertEqual(200, handler._send_json.call_args.args[0])
        self.assertEqual("complete", handler._send_json.call_args.args[1]["state"])
        handler._read_body = lambda: b'{"sessionId":"12345678","transcript":"do not accept"}'
        handler.do_POST()
        self.assertEqual(400, handler._send_json.call_args.args[0])

    def test_transport_does_not_use_retry_wrapper(self):
        with mock.patch.object(server, "_openai_api_key", return_value="offline-placeholder"), \
             mock.patch.object(server, "urlopen", side_effect=RuntimeError("offline")) as transport:
            with self.assertRaisesRegex(RuntimeError, "No retry"):
                server._request_live_player_model({})
        self.assertEqual(1, transport.call_count)


if __name__ == "__main__":
    unittest.main()
