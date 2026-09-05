import copy
import json
import unittest

from reconciliation_context import (
    build_orientation_context,
    build_reference_canon_packet,
    build_session_evidence_packet,
    local_campaign_reference_providers,
    reference_source_catalog,
    search_campaign_reference,
)
from session_reconciliation import RECONCILIATION_INSTRUCTIONS, build_reconciliation_request


class ReconciliationContextTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "campaignId": "moon-campaign",
            "campaignName": "Moon Campaign",
            "campaignSummaryText": "The party opposes the Dawnfire cult.",
            "recentSessionSummariesText": "They arrived outside the old granary.",
            "canonNames": [{
                "name": "Rathgar",
                "aliases": "Rath",
                "type": "npc",
                "descriptor": "A guarded councillor",
            }],
            "prepContext": {
                "dungeon": {"name": "Old Granary", "description": "DM-only final encounter"},
                "rooms": [{
                    "number": 1,
                    "short_name": "Cellar",
                    "background_lore_notes": "Rathgar secretly serves Dawnfire",
                }],
                "monsters": [{"name": "Hidden Horror", "dm_notes": "Appears after betrayal"}],
                "npcs": [{"name": "Rathgar", "dm_notes": "Secret cult agent"}],
            },
        }

    def test_three_context_classes_are_separate_and_authority_is_explicit(self):
        session = build_session_evidence_packet(
            [{"chunkIndex": 0, "speakerText": "DM: You meet Rathgar."}],
            tracker_state={"round": 1},
            tracker_events=[],
            reviewer_corrections="",
            evidence_markers=[{"type": "CANON", "chunkIndex": 0}],
        )
        orientation = build_orientation_context(self.snapshot, "player=Sam | character=Iris")
        reference = build_reference_canon_packet(
            included_records=[{
                "source": "dungeon_maker",
                "title": "Rathgar",
                "content": {"secret": "cult agent"},
            }],
            available_sources=reference_source_catalog(self.snapshot),
        )

        self.assertEqual("session_evidence", session["contextClass"])
        self.assertEqual("orientation_context", orientation["contextClass"])
        self.assertEqual("reference_canon", reference["contextClass"])
        self.assertNotIn("cult agent", json.dumps(session))
        self.assertIn("cult agent", json.dumps(reference))
        self.assertEqual("unknown", reference["includedUpFront"][0]["visibility"])
        self.assertEqual("CANON", session["dmEvidenceMarkers"]["markers"][0]["type"])
        self.assertIn("Only session evidence may establish", RECONCILIATION_INSTRUCTIONS)

    def test_orientation_is_compact_and_excludes_bulk_dungeon_maker_records(self):
        orientation = build_orientation_context(self.snapshot, "Current party")
        encoded = json.dumps(orientation)

        self.assertEqual("Old Granary", orientation["currentAdventure"]["name"])
        self.assertIn("The party opposes", orientation["adventureSoFar"]["text"])
        self.assertIn("Current party", orientation["currentPartyRoster"]["text"])
        self.assertNotIn("Hidden Horror", encoded)
        self.assertNotIn("Secret cult agent", encoded)
        self.assertNotIn("background_lore_notes", encoded)
        self.assertEqual("background_only_not_current_session_evidence", orientation["authority"])

    def test_local_reference_search_reads_canon_without_mutating_it(self):
        campaign = {
            "campaignSummary": "The party seeks the old granary.",
            "canonNames": self.snapshot["canonNames"],
            "sessionSummaries": [],
            "dungeonMakerJson": self.snapshot["prepContext"],
        }
        original = copy.deepcopy(campaign)
        result = search_campaign_reference(
            "Rathgar",
            sources=["dungeon_maker"],
            entity_types=["npc"],
            limit=5,
            providers=local_campaign_reference_providers(campaign),
        )

        self.assertEqual(1, result["resultCount"])
        self.assertEqual("dungeon_maker", result["results"][0]["source"])
        self.assertEqual("dm_only", result["results"][0]["visibility"])
        self.assertFalse(result["authoritativeStorageChanged"])
        self.assertEqual(original, campaign)

    def test_arentoria_is_a_prepared_provider_slot_not_connected_data(self):
        sources = {item["source"]: item for item in reference_source_catalog(self.snapshot)}
        self.assertFalse(sources["arentoria"]["available"])
        self.assertEqual("Arentoria", sources["arentoria"]["canonicalAuthority"])

    def test_clean_benchmark_omits_human_review_evidence(self):
        session = build_session_evidence_packet(
            [{"chunkIndex": 0, "text": "Evidence from tonight."}],
            tracker_state={"round": 3},
            tracker_events=[{"eventType": "damage"}],
            reviewer_corrections="answer key must not appear",
            evidence_markers=[],
            include_reviewer_evidence=False,
        )
        _, diagnostics, document = build_reconciliation_request(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            max_output_tokens=4000,
            max_input_tokens=10000,
            session_id="12345678",
            finalization_id="fin_test",
            session_evidence=session,
            orientation_context=build_orientation_context(self.snapshot, "Current party"),
            reference_canon=build_reference_canon_packet(),
            existing_events={},
            benchmark_mode="clean",
        )

        self.assertNotIn("reviewerConfirmedCorrections", document["sessionEvidence"])
        self.assertNotIn("answer key must not appear", json.dumps(document))
        self.assertEqual([], document["sessionEvidence"]["dmEvidenceMarkers"]["markers"])
        self.assertEqual([], document["referenceCanon"]["includedUpFront"])
        self.assertEqual({}, document["existingStructuredEvents"])
        self.assertEqual("clean", diagnostics["benchmarkMode"])
        self.assertEqual(0, diagnostics["contextContributions"]["humanReviewEvidence"]["bytes"])

    def test_size_diagnostics_partition_each_context_class(self):
        session = build_session_evidence_packet(
            [{"chunkIndex": 0, "text": "Evidence from tonight."}],
            tracker_state={"round": 3},
            tracker_events=[{"eventType": "damage"}],
            reviewer_corrections="[0000] edited: corrected evidence",
        )
        orientation = build_orientation_context(self.snapshot, "Current party")
        reference = build_reference_canon_packet(
            included_records=[{
                "source": "campaign_files",
                "entityType": "npc",
                "title": "Rathgar",
                "content": {"role": "councillor"},
                "visibility": "unknown",
            }],
            available_sources=reference_source_catalog(self.snapshot),
        )
        _, diagnostics, document = build_reconciliation_request(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            max_output_tokens=4000,
            max_input_tokens=10000,
            session_id="12345678",
            finalization_id="fin_test",
            session_evidence=session,
            orientation_context=orientation,
            reference_canon=reference,
            existing_events={},
        )

        contributions = diagnostics["contextContributions"]
        self.assertEqual(diagnostics["inputBytes"], sum(item["bytes"] for item in contributions.values()))
        self.assertGreater(contributions["sessionTranscriptEvidence"]["approximateTokens"], 0)
        self.assertGreater(contributions["orientationContext"]["approximateTokens"], 0)
        self.assertGreater(contributions["trackerEvidence"]["approximateTokens"], 0)
        self.assertGreater(contributions["humanReviewEvidence"]["approximateTokens"], 0)
        self.assertGreater(contributions["referenceCanonIncludedUpFront"]["approximateTokens"], 0)
        self.assertEqual("sessionEvidenceOnly", document["authorityPolicy"]["currentSessionOccurrenceAuthority"])
        self.assertEqual(64, len(diagnostics["contextPacketHashes"]["orientationContext"]))


if __name__ == "__main__":
    unittest.main()
