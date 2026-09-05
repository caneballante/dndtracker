import json
import os
import tempfile
import unittest
from unittest import mock

import server
from session_evidence import (
    build_ordered_evidence,
    claim_reconciliation,
    fail_reconciliation,
    mark_finalized,
    read_ordered_transcript_entries,
    refresh_finalization,
    request_finalization,
)


class SessionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_transcripts(self, indexes):
        with open(os.path.join(self.session_dir, "transcripts.jsonl"), "w", encoding="utf-8") as handle:
            for index in indexes:
                handle.write(json.dumps({"chunkIndex": index, "text": f"chunk {index}"}) + "\n")

    @staticmethod
    def _status(indexes, state="succeeded"):
        return {
            "chunks": [
                {"chunkIndex": index, "filename": f"chunk_{index:04d}.wav", "transcriptionStatus": state}
                for index in indexes
            ]
        }

    def test_out_of_order_jsonl_is_presented_in_chunk_order(self):
        self._write_transcripts([1, 3, 2])

        entries = read_ordered_transcript_entries(self.session_dir)

        self.assertEqual([1, 2, 3], [entry["chunkIndex"] for entry in entries])

    def test_finalize_waits_for_missing_upload_then_pending_transcript(self):
        self._write_transcripts([0])
        evidence = build_ordered_evidence(
            self.session_dir, self._status([0]), final_expected_chunk_index=1
        )
        finalization = request_finalization({}, evidence, 1, now=100)
        self.assertEqual("waiting_for_uploads", finalization["state"])
        self.assertEqual([1], finalization["missingChunks"])

        pending_status = self._status([0, 1])
        pending_status["chunks"][1]["transcriptionStatus"] = "pending"
        evidence = build_ordered_evidence(
            self.session_dir, pending_status, final_expected_chunk_index=1
        )
        finalization = refresh_finalization(finalization, evidence, now=101)
        self.assertEqual("waiting_for_transcription", finalization["state"])
        self.assertEqual([1], finalization["pendingChunks"])

    def test_finalize_becomes_ready_only_after_all_transcripts_exist(self):
        self._write_transcripts([1, 0])
        evidence = build_ordered_evidence(
            self.session_dir, self._status([0, 1]), final_expected_chunk_index=1
        )

        finalization = request_finalization({}, evidence, 1, now=200)

        self.assertEqual("ready_for_reconciliation", finalization["state"])
        self.assertEqual([0, 1], [entry["chunkIndex"] for entry in evidence["reconciliationEntries"]])

    def test_repeated_finalize_request_is_idempotent(self):
        self._write_transcripts([0])
        evidence = build_ordered_evidence(
            self.session_dir, self._status([0]), final_expected_chunk_index=0
        )
        first = request_finalization({}, evidence, 0, now=300)

        repeated = request_finalization(first, evidence, 0, now=999)

        self.assertEqual(first, repeated)
        self.assertEqual(first["finalizationId"], repeated["finalizationId"])
        self.assertEqual(300, repeated["requestedAt"])

    def test_failed_chunk_blocks_readiness_and_successful_retry_clears_it(self):
        self._write_transcripts([0])
        failed_status = self._status([0, 1])
        failed_status["chunks"][1]["transcriptionStatus"] = "failed"
        failed_status["transcriptionFailures"] = {"1": {"message": "offline test"}}
        evidence = build_ordered_evidence(
            self.session_dir, failed_status, final_expected_chunk_index=1
        )
        finalization = request_finalization({}, evidence, 1, now=400)
        self.assertEqual("error", finalization["state"])
        self.assertEqual([1], finalization["failedChunks"])

        self._write_transcripts([1, 0])
        retried = build_ordered_evidence(
            self.session_dir, failed_status, final_expected_chunk_index=1
        )
        refreshed = refresh_finalization(finalization, retried, now=401)
        self.assertEqual("ready_for_reconciliation", refreshed["state"])
        self.assertEqual([], refreshed["failedChunks"])

    def test_old_session_without_finalization_metadata_stays_unfinalized(self):
        self._write_transcripts([0])
        evidence = build_ordered_evidence(self.session_dir, self._status([0]))

        self.assertIsNone(refresh_finalization(None, evidence, now=500))
        self.assertEqual([0], [entry["chunkIndex"] for entry in evidence["entries"]])

    def test_finalized_barrier_accepts_only_the_same_reconciliation_run(self):
        self._write_transcripts([0])
        evidence = build_ordered_evidence(
            self.session_dir, self._status([0]), final_expected_chunk_index=0
        )
        ready = request_finalization({}, evidence, 0, now=600)
        claimed = claim_reconciliation(ready, "run_one", now=601)
        self.assertEqual(claimed, claim_reconciliation(claimed, "run_one", now=999))
        with self.assertRaisesRegex(ValueError, "different reconciliation run"):
            claim_reconciliation(claimed, "run_two", now=602)
        finalized = mark_finalized(claimed, "run_one", now=603)

        self.assertEqual(finalized, mark_finalized(finalized, "run_one", now=999))
        with self.assertRaisesRegex(ValueError, "different reconciliation run"):
            mark_finalized(finalized, "run_two", now=602)

    def test_failed_reconciliation_requires_explicit_retry(self):
        self._write_transcripts([0])
        evidence = build_ordered_evidence(
            self.session_dir, self._status([0]), final_expected_chunk_index=0
        )
        ready = request_finalization({}, evidence, 0, now=700)
        claimed = claim_reconciliation(ready, "run_one", now=701)
        failed = fail_reconciliation(claimed, "run_one", "malformed output", now=702)

        self.assertEqual("reconciliation_error", failed["state"])
        with self.assertRaisesRegex(ValueError, "explicit retry"):
            claim_reconciliation(failed, "run_two", now=703)
        retried = claim_reconciliation(failed, "run_two", now=704, allow_retry=True)
        self.assertEqual("reconciliation_in_progress", retried["state"])
        self.assertEqual(2, retried["reconciliationAttempt"])

    def test_server_transcript_completion_advances_requested_barrier(self):
        uploads_dir = os.path.join(self.session_dir, "uploads")
        with mock.patch.object(server, "UPLOADS_DIR", uploads_dir):
            server.init_session("12345678")
            server.update_status_for_chunk("12345678", 0, "chunk_0000.wav", 10)
            server._append_transcript("12345678", 0, "First evidence")
            requested = server._request_session_finalization("12345678", 1)
            self.assertEqual("waiting_for_uploads", requested["finalization"]["state"])

            server.update_status_for_chunk("12345678", 1, "chunk_0001.wav", 10)
            pending = server._session_status_response("12345678")["finalization"]
            self.assertEqual("waiting_for_transcription", pending["state"])

            server._append_transcript("12345678", 1, "Final evidence")
            ready = server._session_status_response("12345678")["finalization"]
            repeated = server._request_session_finalization("12345678", 1)["finalization"]

        self.assertEqual("ready_for_reconciliation", ready["state"])
        self.assertEqual(ready["finalizationId"], repeated["finalizationId"])
        self.assertEqual(ready["requestedAt"], repeated["requestedAt"])


if __name__ == "__main__":
    unittest.main()
