import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from reconciliation_context import (
    ArentoriaSqliteReferenceProvider,
    LocalJsonReferenceProvider,
    build_orientation_context,
    build_reference_canon_packet,
    build_session_evidence_packet,
    load_world_reference_providers,
    local_campaign_reference_providers,
    search_campaign_reference,
)
from session_reconciliation import (
    RECONCILIATION_INSTRUCTIONS,
    build_reconciliation_request,
    validate_reconciliation_result,
)


def _record(reference_id, name, aliases=None, visibility="unknown"):
    return {
        "referenceId": reference_id,
        "source": "campaign_files",
        "provider": "campaign_files",
        "entityType": "npc",
        "canonicalName": name,
        "aliases": aliases or [],
        "shortDescriptor": "city official",
        "visibility": visibility,
    }


REPO_ROOT = Path(__file__).resolve().parents[1]
WORLDS_ROOT = REPO_ROOT / "worlds"
ARENTOR_MASTER = WORLDS_ROOT / "arentor" / "canon" / "master" / "arentor_master_canon.json"


def _authority_record(reference_id, source, authority, name, aliases=None):
    return {
        "referenceId": reference_id,
        "source": source,
        "provider": source,
        "authorityClass": authority,
        "entityType": "npc",
        "canonicalName": name,
        "aliases": aliases or [],
        "shortDescriptor": f"{authority} description",
        "visibility": "unknown",
    }


def _event(chunk=0, summary="The party opened the moon gate."):
    return {
        "operation": "CREATE_EVENT",
        "event": {
            "type": "decision",
            "status": "resolved",
            "importance": "high",
            "confidence": "high",
            "summary": summary,
            "facts": [summary],
            "entities": ["moon gate"],
            "sourceChunks": [chunk],
        },
        "reason": "Current-session evidence",
    }


def _final_response(operations=None, response_id="resp_final"):
    return {
        "id": response_id,
        "status": "completed",
        "output_text": json.dumps({
            "operations": operations or [],
            "highlightOperations": [],
        }),
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def _tool_response(calls, response_id="resp_tool", call_id_prefix="call"):
    return {
        "id": response_id,
        "status": "completed",
        "output": [{
            "type": "function_call",
            "id": f"fc_{call_id_prefix}_{index}",
            "call_id": f"{call_id_prefix}_{index}",
            "name": "search_campaign_reference",
            "arguments": json.dumps(arguments),
        } for index, arguments in enumerate(calls)],
        "usage": {"input_tokens": 80, "output_tokens": 10},
    }


def _search_calls(*queries):
    return [{
        "query": query,
        "sources": ["campaign_files"],
        "entity_types": ["npc"],
        "limit": 5,
    } for query in queries]


class ReferenceSearchTests(unittest.TestCase):
    def setUp(self):
        self.provider = LocalJsonReferenceProvider("campaign_files", [
            _record("ref_1", "Captain Elowen", ["The Ash Captain"]),
            _record("ref_2", "Marra Vale"),
            _record("ref_3", "Marra Vail"),
        ])

    def test_exact_canonical_and_alias_lookup(self):
        exact = search_campaign_reference("Captain Elowen", providers=[self.provider])
        alias = search_campaign_reference("The Ash Captain", providers=[self.provider])
        self.assertEqual("strong_candidate", exact["resolution"])
        self.assertEqual("exact_canonical_name", exact["results"][0]["match"]["kind"])
        self.assertEqual("Captain Elowen", alias["results"][0]["canonicalName"])
        self.assertEqual("exact_alias", alias["results"][0]["match"]["kind"])

    def test_modest_asr_corruption_can_be_strong(self):
        result = search_campaign_reference("Captin Elowin", providers=[self.provider])
        self.assertEqual("strong_candidate", result["resolution"])
        self.assertEqual("Captain Elowen", result["results"][0]["canonicalName"])

    def test_ambiguity_and_no_match_preserve_uncertainty(self):
        ambiguous = search_campaign_reference("Marra Val", providers=[self.provider])
        missing = search_campaign_reference("Zyxq Nobody", providers=[self.provider])
        self.assertEqual("ambiguous", ambiguous["resolution"])
        self.assertGreaterEqual(ambiguous["resultCount"], 2)
        self.assertEqual("no_match", missing["resolution"])
        self.assertEqual([], missing["results"])

    def test_result_limit_and_provider_allowlist_are_enforced(self):
        many = LocalJsonReferenceProvider(
            "campaign_files",
            [_record(f"ref_{index}", f"Elowen {index}") for index in range(10)],
        )
        result = search_campaign_reference("Elowen", limit=50, providers=[many])
        self.assertLessEqual(result["resultCount"], 5)
        with self.assertRaisesRegex(ValueError, "Unsupported or unavailable"):
            search_campaign_reference(
                "Elowen", sources=["filesystem"], providers=[many]
            )

    def test_gary_results_are_minimized_and_preserve_visibility(self):
        campaign = {
            "dungeonMakerJson": {
                "npcs": [{
                    "name": "Keeper Olan",
                    "aliases": "The Bell Keeper",
                    "role": "keeper",
                    "secret_history": "must never be returned",
                    "hidden_motive": "must never be returned",
                }],
            },
            "canonNames": [],
        }
        result = search_campaign_reference(
            "Keeper Olan",
            sources=["dungeon_maker"],
            providers=local_campaign_reference_providers(campaign),
        )
        encoded = json.dumps(result)
        self.assertEqual("dm_only", result["results"][0]["visibility"])
        self.assertEqual("", result["results"][0]["shortDescriptor"])
        self.assertNotIn("secret_history", encoded)
        self.assertNotIn("hidden_motive", encoded)
        self.assertNotIn("canonicalLocator", encoded)
        self.assertNotIn("content", result["results"][0])

    def test_reference_and_recap_only_data_cannot_supply_provenance(self):
        for summary in (
            "A canon record alone claims the gate opened.",
            "An opening recap says the party opened the gate last session.",
        ):
            with self.assertRaisesRegex(ValueError, "current_session_play"):
                validate_reconciliation_result(
                    {"operations": [_event(0, summary)], "highlightOperations": []},
                    existing_event_ids=[],
                    existing_highlight_ids=[],
                    valid_source_chunks=[0, 1],
                    current_session_source_chunks=[1],
                )
        self.assertIn("Never create an event or highlight from a reference result alone", RECONCILIATION_INSTRUCTIONS)
        self.assertIn("dm_only result is not evidence that players know", RECONCILIATION_INSTRUCTIONS)

    def test_arentoria_adapter_is_read_only_and_minimized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "arentoria.sqlite")
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE person (stable_id TEXT, display_name TEXT);
                CREATE TABLE resident_profile (
                    person_id TEXT, ancestry TEXT, occupation_daily_role TEXT,
                    home_district TEXT, private_pressure TEXT
                );
                CREATE TABLE building (stable_id TEXT, name TEXT, address TEXT, district_name TEXT);
                CREATE TABLE building_census (building_id TEXT, specific_use TEXT);
                CREATE TABLE faction (stable_id TEXT, name TEXT, faction_type TEXT, public_face TEXT);
                CREATE TABLE road (stable_id TEXT, name TEXT, generation_zone TEXT);
                CREATE TABLE district (stable_id TEXT, name TEXT);
                CREATE TABLE infrastructure (stable_id TEXT, name TEXT, location TEXT);
                INSERT INTO person VALUES ('person-1', 'Scholar Tovin');
                INSERT INTO resident_profile VALUES (
                    'person-1', 'human', 'archivist', 'North Ward', 'secret debt'
                );
            """)
            connection.commit()
            connection.close()
            before = os.path.getsize(path)
            result = search_campaign_reference(
                "Scholar Tovin",
                sources=["arentoria"],
                providers=[ArentoriaSqliteReferenceProvider(path)],
            )
            self.assertEqual("Scholar Tovin", result["results"][0]["canonicalName"])
            self.assertEqual("unknown", result["results"][0]["visibility"])
            self.assertNotIn("secret debt", json.dumps(result))
            self.assertEqual(before, os.path.getsize(path))

    def test_old_request_shape_stays_tool_free_by_default(self):
        evidence = build_session_evidence_packet([{"chunkIndex": 0, "text": "I open the door."}])
        request, diagnostics, document = build_reconciliation_request(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            max_output_tokens=4000,
            max_input_tokens=10000,
            session_id="12345678",
            finalization_id="fin_old",
            session_evidence=evidence,
            orientation_context=build_orientation_context(),
            reference_canon=build_reference_canon_packet(),
            existing_events={},
        )
        self.assertNotIn("tools", request)
        self.assertFalse(diagnostics["referenceRetrieval"]["enabled"])
        self.assertEqual("sessionEvidenceOnly", document["authorityPolicy"]["currentSessionOccurrenceAuthority"])


class WorldReferenceRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolution = server.resolve_campaign_world("parmedia-redux")
        cls.providers = cls.resolution["providers"]

    def _search(self, query, visibility_mode="dm", providers=None):
        return search_campaign_reference(
            query,
            providers=providers or self.providers,
            campaign_id="parmedia-redux",
            world_id="arentor",
            visibility_mode=visibility_mode,
        )

    def test_campaign_resolves_manifest_approved_master_and_current_adventure(self):
        self.assertEqual("arentor", self.resolution["worldId"])
        self.assertEqual("worlds/arentor/world.manifest.json", self.resolution["manifest"])
        self.assertEqual([], self.resolution["errors"])
        resolved = self.resolution["resolvedSources"]
        self.assertEqual(
            ["world_canon", "current_adventure"],
            [item["authorityClass"] for item in resolved],
        )
        paths = json.dumps(resolved).lower()
        self.assertIn("canon/master/arentor_master_canon.json", paths)
        self.assertIn("kings_spire_adventure_reference_2026-09-04.json", paths)
        for excluded in ("candidates/", ".docx", "canon/review", "history/", "dng_kings_spire.recorder.json"):
            self.assertNotIn(excluded, paths)

    def test_world_exact_alias_and_normalized_identity_searches(self):
        vexatious = self._search("Vexatious Vayne")
        blanders = self._search("Blanders")
        castle = self._search("Royal Castle")

        self.assertEqual("Vexatious Vayne", vexatious["results"][0]["canonicalName"])
        self.assertEqual("world_canon", vexatious["results"][0]["authorityClass"])
        self.assertEqual(
            "Blanders Everything Institute of Dungeoneering",
            blanders["results"][0]["canonicalName"],
        )
        self.assertEqual("Moon Castle", castle["results"][0]["canonicalName"])
        self.assertEqual("world_canon", castle["results"][0]["authorityClass"])

    def test_exact_phase_3_10_3b_query_regression_set(self):
        city = LocalJsonReferenceProvider(
            "arentoria",
            [
                _authority_record(
                    "city-algren", "arentoria", "structured_city",
                    "Chancellor Algren Vayne",
                ),
                _authority_record(
                    "city-marcel", "arentoria", "structured_city", "Marcel Dennet",
                ),
            ],
            authority_class="structured_city",
        )
        campaign_files = LocalJsonReferenceProvider("campaign_files", [])
        providers = [*self.providers, city, campaign_files]
        searches = [
            search_campaign_reference(
                "Arentoria city capital House of Crowns",
                sources=["arentoria", "world_canon", "campaign_files"],
                entity_types=["location", "city", "building"],
                providers=providers,
            ),
            search_campaign_reference(
                "Vexatious Councilman Vane counselor son wife contract",
                sources=["arentoria", "campaign_files", "current_adventure"],
                entity_types=["npc"],
                providers=providers,
            ),
            search_campaign_reference(
                "Marcel Dennett clerk House of Crowns",
                sources=["arentoria", "campaign_files", "current_adventure"],
                entity_types=["npc"],
                providers=providers,
            ),
            search_campaign_reference(
                "Blanders Glanders adventurer registration school",
                sources=["arentoria", "campaign_files", "world_canon"],
                entity_types=["location", "organization"],
                providers=providers,
            ),
            search_campaign_reference(
                "Sarah Lynn Seralyn queen royal tapestry crest",
                sources=["world_canon", "campaign_files", "current_adventure"],
                entity_types=["npc"],
                providers=providers,
            ),
        ]

        first_names = {item["canonicalName"] for item in searches[0]["results"]}
        self.assertTrue({"Arentoria", "House of Crowns"} <= first_names)
        self.assertTrue(all(
            item["authorityClass"] == "world_canon"
            for item in searches[0]["results"]
            if item["canonicalName"] in {"Arentoria", "House of Crowns"}
        ))
        second_names = {item["canonicalName"] for item in searches[1]["results"]}
        self.assertIn("Algren Vayne", second_names)
        self.assertNotIn("Chancellor Algren Vayne", second_names)
        self.assertEqual(
            "canonical_name_containment",
            searches[1]["upwardCanonicalizations"][0]["reason"],
        )
        self.assertEqual("Marcel Dennet", searches[2]["results"][0]["canonicalName"])
        self.assertEqual("world_canon", searches[2]["results"][0]["authorityClass"])
        self.assertEqual(
            "Blanders Everything Institute of Dungeoneering",
            searches[3]["results"][0]["canonicalName"],
        )
        self.assertFalse(any(
            item["canonicalName"] in {"Sarah Lynn", "Seralyn"}
            for item in searches[4]["results"]
        ))
        self.assertEqual([], searches[4]["upwardCanonicalizations"])

    def test_vayne_and_ghurzag_do_not_regain_rejected_legacy_facts(self):
        algren = self._search("Algren Vayne")
        ghurzag = self._search("Ghurzag")
        self.assertNotIn("chancellor", json.dumps(algren).lower())
        self.assertEqual("Ghurzag", ghurzag["results"][0]["canonicalName"])
        encoded = json.dumps(ghurzag).lower()
        for forbidden in ("flame-touched", "demon", "ancestry", "warlord"):
            self.assertNotIn(forbidden, encoded)

    def test_world_fact_visibility_is_player_safe_and_dm_complete(self):
        player = self._search("Branna Coalvein", visibility_mode="player_safe")
        dm = self._search("Branna Coalvein", visibility_mode="dm")
        player_text = json.dumps(player)
        dm_text = json.dumps(dm)

        self.assertEqual("Branna Coalvein", player["results"][0]["canonicalName"])
        self.assertIn("Stonehome Heritage House", player_text)
        self.assertNotIn("Deep Ledger", player_text)
        self.assertNotIn("First Auditor", player_text)
        self.assertIn("First Auditor of the Deep Ledger", dm_text)

        hidden = self._search("Deep Ledger", visibility_mode="player_safe")
        revealed = self._search("Deep Ledger", visibility_mode="dm")
        self.assertEqual("no_match", hidden["resolution"])
        self.assertEqual([], hidden["results"])
        self.assertEqual("dm_only", revealed["results"][0]["visibility"])

    def test_world_identity_wins_and_adventure_local_detail_remains_available(self):
        spire = self._search("King's Spire")
        local = self._search("Star-Loom Carpet")
        self.assertEqual("King's Spire", spire["results"][0]["canonicalName"])
        self.assertEqual("world_canon", spire["results"][0]["authorityClass"])
        self.assertEqual("Star-Loom Carpet", local["results"][0]["canonicalName"])
        self.assertEqual("current_adventure", local["results"][0]["authorityClass"])
        self.assertTrue(local["results"][0]["preparedReferenceOnly"])

    def test_authority_collapsing_is_deterministic(self):
        world = LocalJsonReferenceProvider(
            "world_canon",
            [_authority_record("world-1", "world_canon", "world_canon", "Moon Castle", ["Royal Castle"])],
            authority_class="world_canon",
        )
        city = LocalJsonReferenceProvider(
            "arentoria",
            [_authority_record("city-1", "arentoria", "structured_city", "Moon Castle", ["Royal Castle"])],
            authority_class="structured_city",
        )
        adventure = LocalJsonReferenceProvider(
            "current_adventure",
            [_authority_record("adventure-1", "current_adventure", "current_adventure", "Moon Castle", ["Royal Castle"])],
            authority_class="current_adventure",
        )
        resolved = self._search("Royal Castle", providers=[adventure, city, world])
        self.assertEqual(["world-1"], [item["referenceId"] for item in resolved["results"]])
        self.assertEqual(2, len(resolved["suppressedLowerAuthorityConflicts"]))

        city_only = self._search("Royal Castle", providers=[adventure, city])
        self.assertEqual("structured_city", city_only["results"][0]["authorityClass"])
        self.assertEqual(1, len(city_only["suppressedLowerAuthorityConflicts"]))

    def test_lower_identity_resolves_upward_without_merging_conflicting_title(self):
        world_record = _authority_record(
            "world-algren", "world_canon", "world_canon", "Algren Vayne"
        )
        world_record.update({"title": "Councilor", "shortDescriptor": "Approved councilor"})
        lower_record = _authority_record(
            "city-algren", "arentoria", "structured_city", "Chancellor Algren Vayne"
        )
        lower_record.update({"title": "Chancellor", "shortDescriptor": "Stale city title"})
        world = LocalJsonReferenceProvider(
            "world_canon", [world_record], authority_class="world_canon"
        )
        city = LocalJsonReferenceProvider(
            "arentoria", [lower_record], authority_class="structured_city"
        )

        result = search_campaign_reference(
            "Chancellor Algren Vayne",
            sources=["arentoria"],
            providers=[city, world],
        )

        resolved = result["results"][0]
        self.assertEqual("Algren Vayne", resolved["canonicalName"])
        self.assertEqual("world_canon", resolved["authorityClass"])
        self.assertEqual("Councilor", resolved["title"])
        self.assertEqual("Approved councilor", resolved["shortDescriptor"])
        self.assertNotIn("Stale city title", json.dumps(resolved))
        self.assertEqual(["world_canon"], result["canonicalizationProvidersQueried"])
        self.assertEqual(
            "canonical_name_containment",
            result["upwardCanonicalizations"][0]["reason"],
        )
        self.assertEqual(
            "Chancellor Algren Vayne",
            result["upwardCanonicalizations"][0]["lowerCanonicalName"],
        )

    def test_lower_entity_without_world_match_remains_lower_authority(self):
        world = LocalJsonReferenceProvider(
            "world_canon",
            [_authority_record("world-other", "world_canon", "world_canon", "Other Guide")],
            authority_class="world_canon",
        )
        city = LocalJsonReferenceProvider(
            "arentoria",
            [_authority_record("city-local", "arentoria", "structured_city", "Local Guide")],
            authority_class="structured_city",
        )
        result = search_campaign_reference(
            "Local Guide", sources=["arentoria"], providers=[city, world]
        )
        self.assertEqual("Local Guide", result["results"][0]["canonicalName"])
        self.assertEqual("structured_city", result["results"][0]["authorityClass"])
        self.assertEqual([], result["upwardCanonicalizations"])

    def test_similar_but_different_identity_is_not_collapsed(self):
        world = LocalJsonReferenceProvider(
            "world_canon",
            [_authority_record("world-vayner", "world_canon", "world_canon", "Algren Vayner")],
            authority_class="world_canon",
        )
        city = LocalJsonReferenceProvider(
            "arentoria",
            [_authority_record(
                "city-vayne", "arentoria", "structured_city", "Chancellor Algren Vayne"
            )],
            authority_class="structured_city",
        )
        result = search_campaign_reference(
            "Chancellor Algren Vayne",
            sources=["arentoria"],
            providers=[city, world],
        )
        self.assertEqual("Chancellor Algren Vayne", result["results"][0]["canonicalName"])
        self.assertEqual("structured_city", result["results"][0]["authorityClass"])
        self.assertEqual([], result["upwardCanonicalizations"])

    def test_upward_resolution_precedes_final_result_cap(self):
        world = LocalJsonReferenceProvider(
            "world_canon",
            [_authority_record("world-algren", "world_canon", "world_canon", "Algren Vayne")],
            authority_class="world_canon",
        )
        lower_records = [
            _authority_record(
                f"city-{index}", "arentoria", "structured_city",
                f"{name} Algren",
            )
            for index, name in enumerate(("Alpha", "Bravo", "Charlie", "Delta", "Echo"))
        ]
        lower_records.append(_authority_record(
            "city-algren", "arentoria", "structured_city", "Zed Algren Vayne",
        ))
        city = LocalJsonReferenceProvider(
            "arentoria", lower_records, authority_class="structured_city"
        )

        result = search_campaign_reference(
            "Algren", sources=["arentoria"], limit=5, providers=[city, world]
        )

        self.assertEqual(5, result["resultCount"])
        self.assertEqual("Algren Vayne", result["results"][0]["canonicalName"])
        self.assertEqual("world_canon", result["results"][0]["authorityClass"])
        self.assertEqual(6, result["candidateCounts"]["arentoria"])

    def test_legacy_is_off_by_default_and_opt_in_only(self):
        legacy = LocalJsonReferenceProvider(
            "legacy",
            [_authority_record("legacy-1", "legacy", "legacy", "Old Vayne")],
            authority_class="legacy",
        )
        default = self._search("Old Vayne", providers=[legacy])
        explicit = search_campaign_reference(
            "Old Vayne", providers=[legacy], include_legacy=True
        )
        self.assertEqual([], default["results"])
        self.assertFalse(default["legacyEnabled"])
        self.assertEqual("Old Vayne", explicit["results"][0]["canonicalName"])

    def test_campaign_without_world_keeps_non_world_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            server, "CAMPAIGNS_DIR", os.path.join(temp_dir, "campaigns")
        ), mock.patch.object(server, "WORLDS_DIR", os.path.join(temp_dir, "worlds")):
            campaign = server.write_campaign("worldless", {
                "campaignId": "worldless",
                "name": "Worldless",
                "worldId": None,
                "canonNames": [{"name": "Local Guide", "type": "npc"}],
            })
            resolution = server.resolve_campaign_world("worldless")
            context = server._reference_context_for_snapshot(server._build_context_snapshot(campaign))
            found = search_campaign_reference("Local Guide", providers=context["providers"])
        self.assertIsNone(resolution["worldId"])
        self.assertEqual([], resolution["errors"])
        self.assertEqual("Local Guide", found["results"][0]["canonicalName"])

    def test_approved_null_never_falls_back_to_reviewed_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            world_root = Path(temp_dir) / "null-world"
            world_root.mkdir(parents=True)
            (world_root / "candidate.json").write_text("not valid json", encoding="utf-8")
            (world_root / "world.manifest.json").write_text(json.dumps({
                "schemaVersion": 1,
                "worldId": "null-world",
                "canon": {
                    "master": {"approved": None, "reviewedCandidate": "candidate.json"},
                    "adventureReferences": [],
                },
            }), encoding="utf-8")
            resolution = load_world_reference_providers("null-world", temp_dir)
        self.assertEqual([], resolution["providers"])
        self.assertEqual("approved_master_unavailable", resolution["errors"][0]["code"])

    def test_invalid_or_missing_world_fails_closed_without_guessing_paths(self):
        invalid = load_world_reference_providers("../arentor", WORLDS_ROOT)
        missing = load_world_reference_providers("not-installed", WORLDS_ROOT)
        self.assertEqual([], invalid["providers"])
        self.assertEqual("invalid_world_id", invalid["errors"][0]["code"])
        self.assertEqual([], missing["providers"])
        self.assertEqual("world_manifest_missing", missing["errors"][0]["code"])

    def test_broken_adventure_does_not_erase_valid_master(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            world_root = Path(temp_dir) / "fixture-world"
            master_path = world_root / "canon" / "master.json"
            master_path.parent.mkdir(parents=True)
            master_path.write_text(ARENTOR_MASTER.read_text(encoding="utf-8"), encoding="utf-8")
            (world_root / "world.manifest.json").write_text(json.dumps({
                "schemaVersion": 1,
                "worldId": "fixture-world",
                "canon": {
                    "master": {"approved": "canon/master.json"},
                    "adventureReferences": [{
                        "id": "missing",
                        "status": "current",
                        "path": "canon/adventures/missing.json",
                    }],
                },
            }), encoding="utf-8")
            resolution = load_world_reference_providers("fixture-world", temp_dir)
            result = search_campaign_reference(
                "Moon Castle",
                providers=resolution["providers"],
                world_id="fixture-world",
                provider_errors=resolution["errors"],
            )
        self.assertEqual("Moon Castle", result["results"][0]["canonicalName"])
        self.assertEqual("adventure_reference_invalid", result["providerErrors"][0]["code"])


class ReferenceToolLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.uploads_dir = os.path.join(self.temp_dir.name, "uploads")
        self.env_path = os.path.join(self.temp_dir.name, "missing.env")
        self.patch_uploads = mock.patch.object(server, "UPLOADS_DIR", self.uploads_dir)
        self.patch_env = mock.patch.object(server, "ENV_PATH", self.env_path)
        self.patch_uploads.start()
        self.patch_env.start()
        self.env = mock.patch.dict(os.environ, {
            "ENABLE_STRUCTURED_RECONCILIATION": "1",
            "ENABLE_RECONCILIATION_REFERENCE_RETRIEVAL": "1",
            "RECONCILIATION_REFERENCE_MAX_SEARCHES": "5",
            "RECONCILIATION_REFERENCE_RESULTS_PER_SEARCH": "5",
            "ARENTORIA_DB_PATH": os.path.join(self.temp_dir.name, "missing.sqlite"),
        })
        self.env.start()
        self.session_id = "22345678"

    def tearDown(self):
        self.env.stop()
        self.patch_env.stop()
        self.patch_uploads.stop()
        self.temp_dir.cleanup()

    def _ready_session(self):
        server.init_session(self.session_id)
        server._set_session_status_fields(self.session_id, {
            "contextSnapshot": {
                "campaignId": "test-campaign",
                "campaignName": "Test Campaign",
                "canonNames": [{
                    "name": "Captain Elowen",
                    "aliases": "The Ash Captain",
                    "type": "npc",
                    "descriptor": "city official",
                    "visibility": "player_known",
                }],
                "prepContext": {},
            }
        })
        server.update_status_for_chunk(self.session_id, 0, "chunk_0000.wav", 100)
        server._append_transcript(
            self.session_id, 0, "I ask Captin Elowin to open the moon gate."
        )
        finalization = server._request_session_finalization(self.session_id, 0)
        self.assertEqual("ready_for_reconciliation", finalization["finalization"]["state"])

    def _audit_rows(self, filename):
        path = os.path.join(self.uploads_dir, self.session_id, filename)
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _no_match_search(query, **kwargs):
        return {
            "query": query,
            "campaignId": kwargs.get("campaign_id") or "",
            "worldId": kwargs.get("world_id"),
            "visibilityMode": kwargs.get("visibility_mode") or "dm",
            "resolution": "no_match",
            "results": [],
            "resultCount": 0,
            "providersQueried": list(kwargs.get("sources") or []),
            "candidateCounts": {
                source: 0 for source in kwargs.get("sources") or []
            },
            "authorityClassesQueried": ["campaign_reference"],
            "suppressedLowerAuthorityConflicts": [],
            "legacyEnabled": bool(kwargs.get("include_legacy")),
            "providerErrors": [],
            "authoritativeStorageChanged": False,
        }

    def test_reconciliation_transport_has_no_hidden_retry(self):
        with mock.patch.object(server, "_openai_api_key", return_value="test-key"), mock.patch.object(
            server,
            "urlopen",
            side_effect=server.URLError("offline"),
        ) as opened:
            with self.assertRaisesRegex(RuntimeError, "connection error"):
                server._request_structured_reconciliation_model({"model": "offline-test"})
        self.assertEqual(1, opened.call_count)

    def test_tool_loop_is_bounded_audited_and_usage_is_counted(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response([{
                    "query": "Captin Elowin",
                    "sources": ["campaign_files"],
                    "entity_types": ["npc"],
                    "limit": 5,
                }])
            return _final_response([_event(0)])

        with mock.patch.object(
            server,
            "_request_structured_reconciliation_model",
            side_effect=AssertionError("network model client must not be used"),
        ):
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )

        self.assertEqual(2, len(requests))
        self.assertIn("tools", requests[0])
        self.assertIsInstance(requests[1]["input"], list)
        tool_output = requests[1]["input"][-1]
        self.assertEqual("function_call_output", tool_output["type"])
        self.assertIn("Captain Elowen", tool_output["output"])
        diagnostics = result["diagnostics"]["referenceRetrieval"]
        self.assertEqual(1, diagnostics["searchCount"])
        self.assertEqual(2, diagnostics["modelRequestCount"])
        self.assertEqual(["campaign_files"], diagnostics["providersQueried"])
        self.assertGreater(diagnostics["approximateReferenceTokensInserted"], 0)
        self.assertEqual(2, result["usage"]["requests"])
        self.assertEqual(4, diagnostics["maximumModelRequests"])
        self.assertIn("estimatedMaximumToolLoopCostAtCaps", result["diagnostics"]["costSafety"])

        audit_path = os.path.join(
            self.uploads_dir,
            self.session_id,
            server.RECONCILIATION_REFERENCE_AUDIT_FILENAME,
        )
        with open(audit_path, encoding="utf-8") as handle:
            audit = json.loads(handle.readline())
        self.assertEqual(2, audit["schemaVersion"])
        self.assertEqual("Captin Elowin", audit["query"])
        self.assertEqual("Captain Elowen", audit["returnedReferences"][0]["canonicalName"])
        self.assertIn("reconciliationId", audit)
        self.assertEqual("test-campaign", audit["campaignId"])
        self.assertIsNone(audit["worldId"])
        self.assertEqual("dm", audit["visibilityMode"])
        self.assertFalse(audit["legacyEnabled"])
        self.assertEqual({"campaign_files": 1}, audit["candidateCounts"])
        self.assertEqual(["campaign_reference"], audit["authorityClassesQueried"])
        self.assertIn("providerErrors", audit)
        self.assertIn("suppressedLowerAuthorityConflicts", audit)
        self.assertIn("canonicalizationProvidersQueried", audit)
        self.assertIn("upwardCanonicalizations", audit)
        self.assertNotIn("shortDescriptor", audit["returnedReferences"][0])

    def test_default_search_budget_is_five(self):
        with mock.patch.dict(
            os.environ, {"RECONCILIATION_REFERENCE_MAX_SEARCHES": ""}
        ):
            self.assertEqual(5, server._reconciliation_reference_max_searches())
        self.assertEqual(5, server.RECONCILIATION_REFERENCE_MAX_SEARCHES_DEFAULT)

    def test_exact_search_budget_executes_all_calls(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three", "four", "five"),
                    call_id_prefix="exact",
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ) as searched:
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        retrieval = result["diagnostics"]["referenceRetrieval"]
        self.assertEqual(5, searched.call_count)
        self.assertEqual(5, retrieval["searchCount"])
        self.assertEqual(5, retrieval["proposedSearchCount"])
        self.assertEqual(0, retrieval["suppressedSearchCount"])
        self.assertEqual(2, len(requests))

    def test_over_budget_first_batch_executes_first_five_and_suppresses_two(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three", "four", "five", "six", "seven"),
                    call_id_prefix="over",
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ) as searched:
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )

        outputs = [
            item for item in requests[1]["input"]
            if item.get("type") == "function_call_output"
        ]
        suppressed = [json.loads(item["output"]) for item in outputs[5:]]
        self.assertEqual(5, searched.call_count)
        self.assertEqual(7, len(outputs))
        self.assertEqual(
            ["search_budget_exhausted", "search_budget_exhausted"],
            [item["status"] for item in suppressed],
        )
        self.assertTrue(all(item["remainingSearches"] == 0 for item in suppressed))
        retrieval = result["diagnostics"]["referenceRetrieval"]
        self.assertEqual(5, retrieval["executedSearchCount"])
        self.assertEqual(2, retrieval["suppressedSearchCount"])

    def test_search_budget_is_global_across_model_requests(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three"), "resp_first", "first"
                )
            if len(requests) == 2:
                return _tool_response(
                    _search_calls("four", "five", "six", "seven"), "resp_second", "second"
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ) as searched:
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        retrieval = result["diagnostics"]["referenceRetrieval"]
        self.assertEqual(5, searched.call_count)
        self.assertEqual(7, retrieval["proposedSearchCount"])
        self.assertEqual(5, retrieval["executedSearchCount"])
        self.assertEqual(2, retrieval["suppressedSearchCount"])
        self.assertEqual(3, retrieval["modelRequestCount"])

    def test_zero_remaining_budget_returns_tool_result_without_provider_call(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three", "four", "five"),
                    "resp_full", "full",
                )
            if len(requests) == 2:
                return _tool_response(
                    _search_calls("six"), "resp_exhausted", "exhausted"
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ) as searched:
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        outputs = [
            item for item in requests[2]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(5, searched.call_count)
        self.assertEqual("search_budget_exhausted", json.loads(outputs[-1]["output"])["status"])
        self.assertEqual(
            1, result["diagnostics"]["referenceRetrieval"]["suppressedSearchCount"]
        )

    def test_model_request_cap_remains_four(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            ordinal = len(requests)
            return _tool_response(
                _search_calls(f"query {ordinal}"),
                f"resp_{ordinal}",
                f"cap_{ordinal}",
            )

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ) as searched:
            with self.assertRaisesRegex(ValueError, "model request-count limit"):
                server.run_structured_reconciliation(
                    self.session_id, confirm=True, model_client=client
                )
        self.assertEqual(4, len(requests))
        self.assertEqual(4, searched.call_count)
        audits = self._audit_rows(server.RECONCILIATION_REFERENCE_AUDIT_FILENAME)
        self.assertEqual("executed", audits[-1]["callStatus"])

    def test_raw_response_is_preserved_before_provider_failure(self):
        self._ready_session()
        requests = []
        provider_calls = []

        def failing_search(query, **kwargs):
            provider_calls.append(query)
            if len(provider_calls) == 2:
                raise RuntimeError("provider exploded")
            return self._no_match_search(query, **kwargs)

        def client(payload):
            requests.append(payload)
            return _tool_response(
                _search_calls("one", "two", "three", "four", "five"),
                call_id_prefix="failure",
            )

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=failing_search
        ):
            with self.assertRaisesRegex(RuntimeError, "provider exploded"):
                server.run_structured_reconciliation(
                    self.session_id, confirm=True, model_client=client
                )
        model_audits = self._audit_rows(
            server.RECONCILIATION_MODEL_RESPONSE_AUDIT_FILENAME
        )
        response_audit = next(
            item for item in model_audits if item["recordType"] == "model_response"
        )
        self.assertEqual(5, response_audit["proposedFunctionCallCount"])
        self.assertEqual("resp_tool", response_audit["rawResponse"]["id"])
        self.assertEqual(
            ["one", "two", "three", "four", "five"],
            [
                item["parsedArguments"]["query"]
                for item in response_audit["proposedFunctionCalls"]
            ],
        )
        retrieval_audits = self._audit_rows(
            server.RECONCILIATION_REFERENCE_AUDIT_FILENAME
        )
        self.assertEqual(
            [
                "executed",
                "failed_provider",
                "not_executed_after_failure",
                "not_executed_after_failure",
                "not_executed_after_failure",
            ],
            [item["callStatus"] for item in retrieval_audits],
        )

    def test_over_budget_execution_uses_model_emitted_order(self):
        self._ready_session()
        requests = []
        executed_queries = []

        def ordered_search(query, **kwargs):
            executed_queries.append(query)
            return self._no_match_search(query, **kwargs)

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("A", "B", "C", "D", "E", "F", "G"),
                    call_id_prefix="order",
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=ordered_search
        ):
            server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        self.assertEqual(["A", "B", "C", "D", "E"], executed_queries)

    def test_budget_suppression_is_not_reported_as_no_match(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three", "four", "five", "six"),
                    call_id_prefix="distinguish",
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ):
            server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        outputs = [
            json.loads(item["output"])
            for item in requests[1]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual("no_match", outputs[4]["resolution"])
        self.assertEqual("search_budget_exhausted", outputs[5]["status"])
        self.assertNotIn("resolution", outputs[5])

    def test_search_audit_counts_proposed_executed_and_suppressed(self):
        self._ready_session()
        requests = []

        def client(payload):
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(
                    _search_calls("one", "two", "three", "four", "five", "six", "seven"),
                    call_id_prefix="audit",
                )
            return _final_response([_event(0)])

        with mock.patch.object(
            server, "search_campaign_reference", side_effect=self._no_match_search
        ):
            result = server.run_structured_reconciliation(
                self.session_id, confirm=True, model_client=client
            )
        retrieval = result["diagnostics"]["referenceRetrieval"]
        self.assertEqual(
            (7, 5, 2),
            (
                retrieval["proposedSearchCount"],
                retrieval["executedSearchCount"],
                retrieval["suppressedSearchCount"],
            ),
        )
        model_audits = self._audit_rows(
            server.RECONCILIATION_MODEL_RESPONSE_AUDIT_FILENAME
        )
        first_outcome = next(
            item for item in model_audits
            if item["recordType"] == "tool_call_batch_outcome"
            and item["proposedSearchCount"] == 7
        )
        self.assertEqual(5, first_outcome["executedSearchCount"])
        self.assertEqual(2, first_outcome["suppressedSearchCount"])
        self.assertEqual(
            [
                "executed", "executed", "executed", "executed", "executed",
                "suppressed_budget", "suppressed_budget",
            ],
            [item["callStatus"] for item in first_outcome["calls"]],
        )

    def test_clean_benchmark_keeps_answer_key_out_and_legacy_retrieval_off(self):
        self._ready_session()
        session_dir = os.path.join(self.uploads_dir, self.session_id)
        server.write_text(
            os.path.join(session_dir, server.RECONCILIATION_BENCHMARK_FILENAME),
            json.dumps({"schemaVersion": 1, "mode": "clean", "excludeReferenceCanon": True}),
        )
        server.write_text(
            os.path.join(session_dir, "notes_overrides.json"),
            json.dumps({"0": {"editedText": "ANSWER KEY"}}),
        )
        with mock.patch.object(
            server,
            "_reviewed_timeline_context_text",
            side_effect=AssertionError("review data was accessed"),
        ):
            preview = server.run_structured_reconciliation(self.session_id, dry_run=True)
        self.assertEqual(0, preview["diagnostics"]["contextContributions"]["humanReviewEvidence"]["bytes"])
        self.assertFalse(preview["diagnostics"]["referenceRetrieval"]["enabled"])
        self.assertEqual(1, preview["diagnostics"]["referenceRetrieval"]["maximumModelRequests"])

        server.write_text(
            os.path.join(session_dir, server.RECONCILIATION_BENCHMARK_FILENAME),
            json.dumps({
                "schemaVersion": 1,
                "mode": "clean",
                "excludeReferenceCanon": False,
                "enableReferenceRetrieval": True,
            }),
        )
        with mock.patch.object(
            server,
            "_reviewed_timeline_context_text",
            side_effect=AssertionError("review data was accessed"),
        ):
            retrieval_preview = server.run_structured_reconciliation(
                self.session_id, dry_run=True
            )
        self.assertTrue(retrieval_preview["diagnostics"]["referenceRetrieval"]["enabled"])
        self.assertEqual(0, retrieval_preview["diagnostics"]["contextContributions"]["humanReviewEvidence"]["bytes"])
        self.assertEqual(4, retrieval_preview["diagnostics"]["referenceRetrieval"]["maximumModelRequests"])


if __name__ == "__main__":
    unittest.main()
