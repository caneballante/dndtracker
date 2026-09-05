import copy
import hashlib
import json
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORLD_ROOT = REPO_ROOT / "worlds" / "arentor"
MASTER_ROOT = WORLD_ROOT / "canon" / "master"
ORIGINAL_SOURCE = MASTER_ROOT / "source" / "ARENTOR_CANON_SOURCE_FINAL_2026-09-04.docx"
SYNCED_SOURCE = MASTER_ROOT / "source" / "ARENTOR_CANON_SOURCE_FINAL_2026-09-04-r2.docx"
CANDIDATE = MASTER_ROOT / "candidates" / "arentor_master_canon_reviewed_2026-09-04.json"
PRODUCTION = MASTER_ROOT / "arentor_master_canon.json"

ORIGINAL_SOURCE_SHA256 = "e2996815800c58bba84dbcf90503852aac99dca401d06411d36834c0160ea3f4"
SYNCED_SOURCE_SHA256 = "f2a8a5f6e7f65ba433c4241c3060d115179a4b59893144ef08c850fbd750d422"
CANDIDATE_SHA256 = "5c8d21370392a8c67a6d33d6f991a03065aabe66fd76ddd36718b5b901fe287a"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def docx_paragraphs(path):
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    return [
        "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
        for paragraph in root.iter()
        if paragraph.tag.endswith("}p")
    ]


class ArentorCanonInstallationTests(unittest.TestCase):
    def test_source_revision_preserves_original_and_passes_preflight(self):
        self.assertTrue(ORIGINAL_SOURCE.is_file())
        self.assertTrue(SYNCED_SOURCE.is_file())
        self.assertEqual(ORIGINAL_SOURCE_SHA256, sha256(ORIGINAL_SOURCE))
        self.assertEqual(SYNCED_SOURCE_SHA256, sha256(SYNCED_SOURCE))

        paragraphs = docx_paragraphs(SYNCED_SOURCE)
        text = " ".join(paragraphs)
        for anchor in (
            "Arentor",
            "Valentia",
            "Arentoria",
            "Vexatious Vayne",
            "Councilor Vayne",
            "Moon Castle is the proper name",
            "Blanders Everything Institute of Dungeoneering",
            "Silverwood fruit is apple-like and has healing powers",
            "without a key",
            "public director of Stonehome Heritage House",
            "First Auditor of the Deep Ledger",
            "Varney is a large black royal guardian dog",
            "Seralith's Testament",
            "King's Spire Nexus Chamber",
            "King's Spire Royal Kitchen Attendants",
        ):
            self.assertIn(anchor, text)
        self.assertNotIn("Chancellor", text)
        self.assertNotIn("Parmedia", text)

        branna_public = next(
            paragraph
            for paragraph in paragraphs
            if "public director of Stonehome Heritage House" in paragraph
        )
        self.assertNotIn("Deep Ledger", branna_public)
        self.assertTrue(
            any(
                paragraph.startswith("DM-only:")
                and "First Auditor of the Deep Ledger" in paragraph
                for paragraph in paragraphs
            )
        )

    def test_reviewed_candidate_is_unchanged_and_production_preserves_entities(self):
        self.assertEqual(CANDIDATE_SHA256, sha256(CANDIDATE))
        candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        production = json.loads(PRODUCTION.read_text(encoding="utf-8"))

        self.assertEqual("reviewed_candidate_pending_source_sync", candidate["status"])
        self.assertEqual("approved", production["status"])
        self.assertEqual(139, len(candidate["entities"]))
        self.assertEqual(candidate["entities"], production["entities"])
        self.assertEqual(
            [entity["id"] for entity in candidate["entities"]],
            [entity["id"] for entity in production["entities"]],
        )
        self.assertEqual(
            len(candidate["entities"]),
            len({entity["id"] for entity in candidate["entities"]}),
        )

        candidate_core = copy.deepcopy(candidate)
        production_core = copy.deepcopy(production)
        for payload in (candidate_core, production_core):
            payload.pop("status")
            payload.pop("sourceAuthority")
            payload.pop("promotion", None)
            payload["reviewOverlay"].pop("pendingSourceSync")
            payload["reviewOverlay"].pop("sourceSynchronizedAt", None)
        self.assertEqual(candidate_core, production_core)

    def test_production_provenance_points_to_synced_source(self):
        production = json.loads(PRODUCTION.read_text(encoding="utf-8"))
        authority = production["sourceAuthority"]
        promotion = production["promotion"]

        self.assertEqual(SYNCED_SOURCE.name, authority["document"])
        self.assertEqual(SYNCED_SOURCE_SHA256, authority["sha256"])
        self.assertEqual(sha256(SYNCED_SOURCE), authority["sha256"])
        self.assertEqual(CANDIDATE.name, promotion["reviewedCandidate"])
        self.assertEqual(CANDIDATE_SHA256, promotion["reviewedCandidateSha256"])
        self.assertTrue(promotion["entityContentPreserved"])
        self.assertFalse(promotion["reExtractionPerformed"])
        self.assertFalse(production["reviewOverlay"]["pendingSourceSync"])

    def test_manifest_uses_approved_master_and_preserves_adventure_reference(self):
        manifest = json.loads((WORLD_ROOT / "world.manifest.json").read_text(encoding="utf-8"))
        master = manifest["canon"]["master"]
        adventure = manifest["canon"]["adventureReferences"]

        self.assertEqual("arentor", manifest["worldId"])
        self.assertEqual("canon/master/arentor_master_canon.json", master["approved"])
        self.assertEqual("approved", master["status"])
        self.assertEqual(
            "canon/master/source/ARENTOR_CANON_SOURCE_FINAL_2026-09-04-r2.docx",
            master["sourceDocument"],
        )
        self.assertEqual(
            [
                {
                    "id": "kings-spire",
                    "displayName": "King's Spire",
                    "path": "canon/adventures/kings-spire/kings_spire_adventure_reference_2026-09-04.json",
                    "sourcePath": "canon/adventures/kings-spire/source/dng_kings_spire.recorder.json",
                    "authorityClass": "current_adventure",
                    "status": "current",
                }
            ],
            adventure,
        )

        runtime_paths = [
            master["approved"],
            master["reviewedCandidate"],
            master["sourceDocument"],
            adventure[0]["path"],
            adventure[0]["sourcePath"],
        ]
        self.assertFalse(any(path.lower().endswith(".md") for path in runtime_paths))
        self.assertFalse(any("legacy" in path.lower() for path in runtime_paths))
        for path in runtime_paths:
            self.assertTrue((WORLD_ROOT / path).is_file())
        for review_path in manifest["review"].values():
            self.assertTrue((WORLD_ROOT / review_path).is_file())
            self.assertIn("canon/review/", review_path)

    def test_parmedia_campaign_relationship_is_unchanged(self):
        campaign_path = REPO_ROOT / "campaigns" / "parmedia-redux" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        self.assertEqual("arentor", campaign["worldId"])
        self.assertEqual("parmedia-redux", campaign["campaignId"])
        self.assertEqual("Parmedia redux", campaign["name"])
        manifest = json.loads((WORLD_ROOT / "world.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(campaign["worldId"], manifest["worldId"])


if __name__ == "__main__":
    unittest.main()
