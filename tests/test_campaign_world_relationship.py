import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


REPO_ROOT = Path(__file__).resolve().parents[1]


class CampaignWorldModelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.campaigns_dir = os.path.join(self.temp_dir.name, "campaigns")
        self.worlds_dir = os.path.join(self.temp_dir.name, "worlds")
        self.campaigns_patch = mock.patch.object(
            server, "CAMPAIGNS_DIR", self.campaigns_dir
        )
        self.worlds_patch = mock.patch.object(server, "WORLDS_DIR", self.worlds_dir)
        self.campaigns_patch.start()
        self.worlds_patch.start()

    def tearDown(self):
        self.worlds_patch.stop()
        self.campaigns_patch.stop()
        self.temp_dir.cleanup()

    def _install_world(self, world_id="arentor"):
        path = os.path.join(self.worlds_dir, world_id, "world.manifest.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        server.write_json_atomic(path, {
            "schemaVersion": 1,
            "worldId": world_id,
            "displayName": "Test World",
        })

    def test_campaign_without_world_remains_backward_compatible(self):
        path = os.path.join(self.campaigns_dir, "legacy", "campaign.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        server.write_json_atomic(path, {"campaignId": "legacy", "name": "Legacy"})

        campaign = server.read_campaign("legacy")

        self.assertIsNone(campaign["worldId"])
        self.assertEqual("Legacy", campaign["name"])

    def test_normalization_and_round_trip_preserve_world_id(self):
        self._install_world()
        normalized = server._normalize_campaign_payload(
            "test-campaign",
            {"campaignId": "test-campaign", "name": "Test", "worldId": "arentor"},
        )
        self.assertEqual("arentor", normalized["worldId"])

        saved = server.write_campaign("test-campaign", normalized)
        reloaded = server.read_campaign("test-campaign")

        self.assertEqual("arentor", saved["worldId"])
        self.assertEqual("arentor", reloaded["worldId"])
        self.assertEqual(
            "arentor",
            server._campaign_world_id_for_save("test-campaign", {"name": "Legacy client"}),
        )
        listed = {item["campaignId"]: item for item in server.list_campaigns()}
        self.assertEqual("arentor", listed["test-campaign"]["worldId"])
        persisted = json.loads(
            Path(self.campaigns_dir, "test-campaign", "campaign.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("arentor", persisted["worldId"])

    def test_nonexistent_or_mismatched_world_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            server.write_campaign("test-campaign", {"worldId": "missing-world"})

        self._install_world("other-world")
        manifest_path = Path(self.worlds_dir, "other-world", "world.manifest.json")
        server.write_json_atomic(
            str(manifest_path),
            {"schemaVersion": 1, "worldId": "different-world"},
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            server.write_campaign("test-campaign", {"worldId": "other-world"})


class InstalledCampaignWorldRelationshipTests(unittest.TestCase):
    def test_parmedia_redux_resolves_to_arentor_world(self):
        campaign_path = REPO_ROOT / "campaigns" / "parmedia-redux" / "campaign.json"
        world_manifest_path = REPO_ROOT / "worlds" / "arentor" / "world.manifest.json"
        persisted = json.loads(campaign_path.read_text(encoding="utf-8"))
        world_manifest = json.loads(world_manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("arentor", persisted["worldId"])
        self.assertEqual("arentor", world_manifest["worldId"])
        self.assertEqual("arentor", server.read_campaign("parmedia-redux")["worldId"])


if __name__ == "__main__":
    unittest.main()
