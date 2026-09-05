import json
import unittest

from reconciliation_context import (
    build_orientation_context,
    build_reference_canon_packet,
    build_session_evidence_packet,
    classify_transcript_evidence_phases,
)
from session_reconciliation import (
    RECONCILIATION_INSTRUCTIONS,
    build_reconciliation_request,
    validate_reconciliation_result,
)


def _event(chunk, summary="The wizard opened the gate during current play."):
    return {
        "operation": "CREATE_EVENT",
        "event": {
            "type": "decision",
            "status": "resolved",
            "importance": "high",
            "confidence": "high",
            "summary": summary,
            "facts": [summary],
            "entities": ["sealed gate"],
            "sourceChunks": [chunk],
        },
        "reason": "Current-session action",
    }


def _highlight(chunk):
    return {
        "operation": "CREATE_HIGHLIGHT",
        "highlight": {
            "categories": ["humor"],
            "confidence": "high",
            "summary": "The table laughed about the bard falling into a fountain.",
            "participants": ["Bard"],
            "sourceChunks": [chunk],
            "relatedEventIds": [],
        },
        "reason": "Memorable moment",
    }


class TranscriptPhaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"chunkIndex": 0, "text": "Last session, you discovered the sealed gate."},
            {"chunkIndex": 1, "text": "You learned that moonfruit activated it. Everyone laughed about the bard."},
            {"chunkIndex": 2, "text": "Back in play. You are standing before that gate now."},
            {"chunkIndex": 3, "text": "I cast Knock on the gate and open it."},
        ]

    def test_recap_then_current_play_is_labelled_without_a_model(self):
        packet = build_session_evidence_packet(
            self.entries,
            prior_session_summary="The party discovered a sealed gate activated by moonfruit.",
        )
        phases = [item["evidencePhase"] for item in packet["orderedTranscriptEvidence"]]
        self.assertEqual(
            ["prior_session_recap", "prior_session_recap", "uncertain", "current_session_play"],
            phases,
        )
        boundary = packet["transcriptPhaseAssessment"]["currentPlayBoundary"]
        self.assertEqual(3, boundary["firstCurrentSessionPlayChunkIndex"])
        self.assertEqual([2], boundary["uncertainChunkIndexes"])
        self.assertGreater(
            packet["transcriptPhaseAssessment"]["priorApprovedSummarySignal"]["overlapTermCount"],
            0,
        )

    def test_recap_discovery_cannot_create_a_current_event(self):
        with self.assertRaisesRegex(ValueError, "current_session_play"):
            validate_reconciliation_result(
                {"operations": [_event(1, "The party discovered how to activate the gate.")], "highlightOperations": []},
                existing_event_ids=[],
                existing_highlight_ids=[],
                valid_source_chunks=[0, 1, 2, 3],
                current_session_source_chunks=[3],
            )

    def test_current_action_after_a_recap_can_create_an_event(self):
        result = validate_reconciliation_result(
            {"operations": [_event(3)], "highlightOperations": []},
            existing_event_ids=[],
            existing_highlight_ids=[],
            valid_source_chunks=[0, 1, 2, 3],
            current_session_source_chunks=[3],
        )
        self.assertEqual([3], result["operations"][0]["event"]["sourceChunks"])

    def test_humorous_recap_cannot_create_tonights_highlight(self):
        with self.assertRaisesRegex(ValueError, "current_session_play"):
            validate_reconciliation_result(
                {"operations": [], "highlightOperations": [_highlight(1)]},
                existing_event_ids=[],
                existing_highlight_ids=[],
                valid_source_chunks=[0, 1, 2, 3],
                current_session_source_chunks=[3],
            )

    def test_session_that_begins_directly_in_play_stays_current(self):
        assessment = classify_transcript_evidence_phases([
            {"chunkIndex": 0, "text": "I draw my sword and open the door."},
            {"chunkIndex": 1, "text": "Roll initiative."},
        ])
        self.assertEqual(
            ["current_session_play", "current_session_play"],
            [item["evidencePhase"] for item in assessment["chunkPhases"]],
        )

    def test_ambiguous_transition_does_not_claim_current_play(self):
        assessment = classify_transcript_evidence_phases([
            {"chunkIndex": 0, "text": "Last session, you entered the crypt."},
            {"chunkIndex": 1, "text": "The party reached a sealed chamber."},
            {"chunkIndex": 2, "text": "There are voices beyond the wall."},
        ])
        self.assertEqual("uncertain", assessment["inferenceStatus"])
        self.assertIsNone(
            assessment["currentPlayBoundary"]["firstCurrentSessionPlayChunkIndex"]
        )
        self.assertNotIn(
            "current_session_play",
            [item["evidencePhase"] for item in assessment["chunkPhases"]],
        )

    def test_boundary_is_inspectable_and_human_overrideable(self):
        assessment = classify_transcript_evidence_phases(
            self.entries,
            phase_override={"currentPlayStartsAtChunkIndex": 2},
        )
        self.assertEqual("overridden", assessment["inferenceStatus"])
        self.assertEqual(2, assessment["currentPlayBoundary"]["firstCurrentSessionPlayChunkIndex"])

    def test_clean_benchmark_exclusions_and_no_tools_remain_intact(self):
        packet = build_session_evidence_packet(
            self.entries,
            reviewer_corrections="ANSWER KEY",
            include_reviewer_evidence=False,
        )
        request, diagnostics, document = build_reconciliation_request(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            max_output_tokens=4000,
            max_input_tokens=10000,
            session_id="12345678",
            finalization_id="fin_test",
            session_evidence=packet,
            orientation_context=build_orientation_context(),
            reference_canon=build_reference_canon_packet(),
            existing_events={},
            existing_highlights={},
            benchmark_mode="clean",
        )
        self.assertNotIn("ANSWER KEY", json.dumps(document))
        self.assertEqual(0, diagnostics["contextContributions"]["humanReviewEvidence"]["bytes"])
        self.assertEqual([], document["referenceCanon"]["includedUpFront"])
        self.assertNotIn("tools", request)
        self.assertIn("prior_session_recap", RECONCILIATION_INSTRUCTIONS)
        self.assertIn("not a new highlight for tonight", RECONCILIATION_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
