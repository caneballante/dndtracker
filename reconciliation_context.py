"""Typed reconciliation context packets and provider-neutral canon search."""

import copy
import difflib
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path


SCHEMA_VERSION = 1
VISIBILITIES = {"dm_only", "player_known", "unknown"}
TRANSCRIPT_EVIDENCE_PHASES = (
    "prior_session_recap",
    "current_session_play",
    "uncertain",
)
_PHASE_AUTHORITY = {
    "prior_session_recap": "historical_orientation_only",
    "current_session_play": "may_support_current_session_occurrence",
    "uncertain": "cannot_independently_establish_current_session_occurrence",
}
_OPENING_SCAN_CHUNKS = 12
_RECAP_CUE_RE = re.compile(
    r"\b(last (?:time|session|week)|previously|previous session|"
    r"when (?:last )?we left off|recap(?:ping)?)\b",
    re.IGNORECASE,
)
_EXPLICIT_PLAY_CUE_RE = re.compile(
    r"\b(back in play|back to (?:the )?(?:game|action|present)|"
    r"resume (?:the )?(?:game|session|play)|we(?:'re| are) back in game)\b",
    re.IGNORECASE,
)
_PLAY_INVITATION_CUE_RE = re.compile(
    r"\b(what (?:do you do|are (?:you|everybody|everyone) doing)|"
    r"roll initiative|make (?:an?|the) [a-z -]{0,30}check)\b",
    re.IGNORECASE,
)
_CURRENT_ACTION_CUE_RE = re.compile(
    r"\bi (?:cast|attack|open|use|move|draw|search|investigate|walk|run|head)\b",
    re.IGNORECASE,
)
_SUMMARY_STOPWORDS = {
    "about", "after", "again", "also", "been", "before", "being", "could",
    "from", "have", "into", "more", "party", "session", "their", "there",
    "these", "they", "this", "through", "were", "what", "when", "where",
    "which", "with", "would", "your",
}
_REFERENCE_ENTITY_TYPE_ALIASES = {
    "person": "npc",
    "people": "npc",
    "organization": "faction",
    "organisation": "faction",
    "building": "location",
    "business": "location",
    "place": "location",
    "road": "street",
    "streets": "street",
    "districts": "district",
    "infrastructure": "landmark",
}
REFERENCE_AUTHORITY_ORDER = (
    "session_evidence",
    "campaign_continuity",
    "world_canon",
    "structured_city",
    "current_adventure",
    "campaign_reference",
    "unclassified_reference",
    "legacy",
)
_REFERENCE_AUTHORITY_RANK = {
    authority: len(REFERENCE_AUTHORITY_ORDER) - index
    for index, authority in enumerate(REFERENCE_AUTHORITY_ORDER)
}
_REFERENCE_SOURCE_AUTHORITIES = {
    "world_canon": "world_canon",
    "arentoria": "structured_city",
    "current_adventure": "current_adventure",
    "dungeon_maker": "unclassified_reference",
    "campaign_files": "campaign_reference",
    "legacy": "legacy",
}
_CURRENT_ADVENTURE_STATUSES = {"current", "active", "approved"}
_WORLD_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_INTERNAL_REFERENCE_CANDIDATE_LIMIT = 25


def normalize_visibility(value, default="unknown"):
    visibility = str(value or "").strip().lower()
    return visibility if visibility in VISIBILITIES else default


def _clip_text(value, maximum):
    text = str(value or "").strip()
    if len(text) <= maximum:
        return text
    return text[: maximum - 1].rstrip() + "…"


def _transcript_text(entry):
    return str(entry.get("speakerText") or entry.get("text") or entry.get("transcript") or "").strip()


def _summary_terms(text):
    return {
        term for term in re.findall(r"[a-z0-9']+", str(text or "").lower())
        if len(term) >= 4 and term not in _SUMMARY_STOPWORDS
    }


def _prior_summary_signal(opening_text, prior_session_summary):
    summary_terms = _summary_terms(prior_session_summary)
    opening_terms = _summary_terms(opening_text)
    overlap = sorted(summary_terms & opening_terms)
    summary_coverage = len(overlap) / max(1, len(summary_terms))
    opening_coverage = len(overlap) / max(1, len(opening_terms))
    return {
        "available": bool(str(prior_session_summary or "").strip()),
        "method": "normalized_term_overlap",
        "advisoryOnly": True,
        "overlapTermCount": len(overlap),
        "summaryTermCoverage": round(summary_coverage, 3),
        "openingTermCoverage": round(opening_coverage, 3),
        "supportsRecapInference": (
            len(overlap) >= 4 and summary_coverage >= 0.2 and opening_coverage >= 0.03
        ),
    }


def classify_transcript_evidence_phases(
    ordered_entries,
    prior_session_summary="",
    phase_override=None,
):
    """Conservatively classify an opening recap without another model call.

    The assessment is chunk-level. A transition chunk is deliberately uncertain because
    audio chunks may contain both the end of a recap and the beginning of current play.
    """
    rows = [
        {"chunkIndex": int(entry["chunkIndex"]), "transcript": _transcript_text(entry)}
        for entry in ordered_entries or []
    ]
    indexes = [row["chunkIndex"] for row in rows]
    if indexes != sorted(indexes):
        raise ValueError("Transcript phase classification requires chunkIndex order.")
    if len(indexes) != len(set(indexes)):
        raise ValueError("Transcript phase classification requires unique chunk indexes.")

    override = phase_override if isinstance(phase_override, dict) else {}
    override_start = override.get("currentPlayStartsAtChunkIndex")
    uncertain_override = override.get("uncertainChunkIndexes") or []
    if override_start is not None:
        if isinstance(override_start, bool):
            raise ValueError("currentPlayStartsAtChunkIndex override must be an integer.")
        try:
            override_start = int(override_start)
            uncertain_override = {int(index) for index in uncertain_override}
        except (TypeError, ValueError):
            raise ValueError("Transcript phase override indexes must be integers.") from None
        unknown = sorted(uncertain_override - set(indexes))
        if unknown:
            raise ValueError(f"Transcript phase override references unavailable chunks: {unknown}.")
        if indexes and not indexes[0] <= override_start <= indexes[-1] + 1:
            raise ValueError("currentPlayStartsAtChunkIndex override is outside the transcript boundary.")
        phase_by_index = {
            index: (
                "uncertain" if index in uncertain_override
                else "prior_session_recap" if index < override_start
                else "current_session_play"
            )
            for index in indexes
        }
        reason_by_index = {
            index: ["human_boundary_override"] for index in indexes
        }
        method = "human_boundary_override"
        inference_status = "overridden"
        confidence = "high"
        reason_codes = ["human_boundary_override"]
    else:
        opening = rows[:_OPENING_SCAN_CHUNKS]
        recap_position = next(
            (position for position, row in enumerate(opening) if _RECAP_CUE_RE.search(row["transcript"])),
            None,
        )
        explicit_position = None
        invitation_position = None
        action_position = None
        if recap_position is not None:
            explicit_position = next(
                (
                    position for position, row in enumerate(rows[recap_position:], recap_position)
                    if _EXPLICIT_PLAY_CUE_RE.search(row["transcript"])
                ),
                None,
            )
            invitation_position = next(
                (
                    position for position, row in enumerate(rows[recap_position + 1:], recap_position + 1)
                    if _PLAY_INVITATION_CUE_RE.search(row["transcript"])
                ),
                None,
            )
            action_position = next(
                (
                    position for position, row in enumerate(rows[recap_position + 1:], recap_position + 1)
                    if _CURRENT_ACTION_CUE_RE.search(row["transcript"])
                ),
                None,
            )
        transition_start = explicit_position
        current_position = None
        transition_reason = ""
        if explicit_position is not None:
            later_invitation = (
                invitation_position
                if invitation_position is not None and invitation_position >= explicit_position
                else None
            )
            later_action = (
                action_position
                if action_position is not None and action_position >= explicit_position
                else None
            )
            if later_invitation is not None and (
                later_action is None or later_invitation <= later_action
            ):
                current_position = later_invitation + 1
                transition_reason = "explicit_play_transition_then_gameplay_invitation"
            elif later_action is not None:
                current_position = later_action
                transition_reason = "explicit_play_transition_then_current_action"
            else:
                current_position = explicit_position + 1
                transition_reason = "explicit_play_transition"
        elif invitation_position is not None and (
            action_position is None or invitation_position <= action_position
        ):
            transition_start = invitation_position
            current_position = invitation_position + 1
            transition_reason = "gameplay_invitation_transition"
        elif action_position is not None:
            transition_start = action_position
            current_position = action_position
            transition_reason = "current_action_transition"
        phase_by_index = {}
        reason_by_index = {}
        reason_codes = []
        if recap_position is None:
            for row in rows:
                phase_by_index[row["chunkIndex"]] = "current_session_play"
                reason_by_index[row["chunkIndex"]] = ["no_opening_recap_cue_detected"]
            inference_status = "inferred"
            confidence = "medium"
            reason_codes = ["no_opening_recap_cue_detected"]
        elif current_position is None:
            for position, row in enumerate(rows):
                phase = "prior_session_recap" if position == recap_position else "uncertain"
                phase_by_index[row["chunkIndex"]] = phase
                reason_by_index[row["chunkIndex"]] = [
                    "explicit_opening_recap_cue" if phase == "prior_session_recap"
                    else "recap_transition_not_identified"
                ]
            inference_status = "uncertain"
            confidence = "low"
            reason_codes = ["explicit_opening_recap_cue", "recap_transition_not_identified"]
        else:
            for position, row in enumerate(rows):
                if position < recap_position:
                    phase = "uncertain"
                    reasons = ["pre_recap_table_audio"]
                elif position < transition_start:
                    phase = "prior_session_recap"
                    reasons = ["inside_opening_recap"]
                elif position < current_position:
                    phase = "uncertain"
                    reasons = ["recap_to_play_transition_chunk"]
                else:
                    phase = "current_session_play"
                    reasons = ["after_recap_to_play_transition"]
                phase_by_index[row["chunkIndex"]] = phase
                reason_by_index[row["chunkIndex"]] = reasons
            inference_status = "inferred"
            confidence = "high" if explicit_position is not None else "medium"
            reason_codes = [
                "explicit_opening_recap_cue",
                transition_reason,
                "transition_chunks_kept_uncertain",
            ]
        method = "deterministic_opening_cue_inference"

    current_indexes = [index for index in indexes if phase_by_index.get(index) == "current_session_play"]
    prior_indexes = [index for index in indexes if phase_by_index.get(index) == "prior_session_recap"]
    uncertain_indexes = [index for index in indexes if phase_by_index.get(index) == "uncertain"]
    opening_end_position = next(
        (position for position, row in enumerate(rows) if phase_by_index.get(row["chunkIndex"]) == "current_session_play"),
        min(len(rows), _OPENING_SCAN_CHUNKS),
    )
    opening_text = "\n".join(row["transcript"] for row in rows[:opening_end_position])
    return {
        "schemaVersion": 1,
        "method": method,
        "inferenceStatus": inference_status,
        "confidence": confidence,
        "reasonCodes": reason_codes,
        "availableEvidencePhases": list(TRANSCRIPT_EVIDENCE_PHASES),
        "currentPlayBoundary": {
            "firstCurrentSessionPlayChunkIndex": current_indexes[0] if current_indexes else None,
            "lastPriorSessionRecapChunkIndex": prior_indexes[-1] if prior_indexes else None,
            "uncertainChunkIndexes": uncertain_indexes,
            "overrideSupported": True,
            "overrideField": "status.transcriptPhaseOverride.currentPlayStartsAtChunkIndex",
        },
        "priorApprovedSummarySignal": _prior_summary_signal(opening_text, prior_session_summary),
        "chunkPhases": [
            {
                "chunkIndex": index,
                "evidencePhase": phase_by_index[index],
                "occurrenceAuthority": _PHASE_AUTHORITY[phase_by_index[index]],
                "reasonCodes": reason_by_index[index],
            }
            for index in indexes
        ],
    }


def build_session_evidence_packet(
    ordered_entries,
    tracker_state=None,
    tracker_events=None,
    reviewer_corrections="",
    evidence_markers=None,
    include_reviewer_evidence=True,
    prior_session_summary="",
    transcript_phase_override=None,
):
    base_transcript = [
        {
            "chunkIndex": int(entry["chunkIndex"]),
            "transcript": _transcript_text(entry),
        }
        for entry in ordered_entries or []
    ]
    indexes = [item["chunkIndex"] for item in base_transcript]
    if indexes != sorted(indexes):
        raise ValueError("Reconciliation transcript evidence is not ordered by chunkIndex.")
    if len(indexes) != len(set(indexes)):
        raise ValueError("Reconciliation transcript evidence contains duplicate chunk indexes.")
    phase_assessment = classify_transcript_evidence_phases(
        base_transcript,
        prior_session_summary=prior_session_summary,
        phase_override=transcript_phase_override,
    )
    phase_by_index = {
        item["chunkIndex"]: item for item in phase_assessment["chunkPhases"]
    }
    transcript = [
        {
            **item,
            "evidencePhase": phase_by_index[item["chunkIndex"]]["evidencePhase"],
            "occurrenceAuthority": phase_by_index[item["chunkIndex"]]["occurrenceAuthority"],
        }
        for item in base_transcript
    ]
    current_chunks = [
        item["chunkIndex"] for item in transcript
        if item["evidencePhase"] == "current_session_play"
    ]
    packet = {
        "contextClass": "session_evidence",
        "authority": "mixed_by_transcript_evidence_phase",
        "orderedTranscriptEvidenceSource": "transcripts.jsonl ordered by chunkIndex",
        "orderedTranscriptEvidence": transcript,
        "transcriptPhaseAssessment": phase_assessment,
        "currentSessionOccurrenceChunkIndexes": current_chunks,
        "historicalOrientationChunkIndexes": [
            item["chunkIndex"] for item in transcript
            if item["evidencePhase"] == "prior_session_recap"
        ],
        "uncertainOccurrenceChunkIndexes": [
            item["chunkIndex"] for item in transcript
            if item["evidencePhase"] == "uncertain"
        ],
        "trackerEvidence": {
            "authority": "session_supporting_evidence",
            "source": "tracking_events.jsonl plus derived current state",
            "currentState": tracker_state if isinstance(tracker_state, dict) else {},
            "operationHistory": tracker_events if isinstance(tracker_events, list) else [],
        },
        "dmEvidenceMarkers": {
            "authority": "dm_emphasized_session_evidence",
            "source": "future session marker stream",
            "supportedMarkerTypes": ["CANON", "MARK"],
            "markers": evidence_markers if isinstance(evidence_markers, list) else [],
        },
    }
    if include_reviewer_evidence:
        packet["reviewerConfirmedCorrections"] = {
            "authority": "human_confirmed_session_evidence",
            "source": "reviewed notes overrides",
            "text": str(reviewer_corrections or ""),
        }
    else:
        packet["benchmarkExclusions"] = [
            "human_edits",
            "promoted_notes",
            "deemphasized_notes",
            "rejected_notes",
            "reviewer_guidance",
            "reviewer_confirmed_corrections",
            "current_session_final_recap",
        ]
    return packet


def build_orientation_context(snapshot=None, party_roster="", snapshot_provenance="locked_session_snapshot"):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    prep = snapshot.get("prepContext") if isinstance(snapshot.get("prepContext"), dict) else {}
    dungeon = prep.get("dungeon") if isinstance(prep.get("dungeon"), dict) else {}
    campaign_summary = _clip_text(snapshot.get("campaignSummaryText"), 6000)
    recent_summaries = _clip_text(snapshot.get("recentSessionSummariesText"), 4000)
    adventure_parts = [part for part in (campaign_summary, recent_summaries) if part]
    canon_names = snapshot.get("canonNames") if isinstance(snapshot.get("canonNames"), list) else []
    relevant_entities = []
    for item in canon_names[:12]:
        if not isinstance(item, dict):
            continue
        relevant_entities.append({
            "name": str(item.get("name") or "").strip(),
            "aliases": str(item.get("aliases") or "").strip(),
            "entityType": str(item.get("type") or "npc").strip().lower(),
            "descriptor": _clip_text(item.get("descriptor"), 500),
            "visibility": normalize_visibility(item.get("visibility")),
        })
    return {
        "contextClass": "orientation_context",
        "authority": "background_only_not_current_session_evidence",
        "source": str(snapshot_provenance or "unknown"),
        "campaign": {
            "campaignId": str(snapshot.get("campaignId") or ""),
            "campaignName": str(snapshot.get("campaignName") or ""),
            "visibility": "player_known",
        },
        "currentPartyRoster": {
            "text": str(party_roster or ""),
            "authority": "current_session_roster",
            "visibility": "player_known",
        },
        "currentAdventure": {
            "name": str(dungeon.get("name") or "").strip(),
            "subtitle": str(dungeon.get("subtitle") or "").strip(),
            "source": f"{snapshot_provenance}.prepContext.dungeon" if dungeon else "unknown",
            "visibility": "unknown",
        },
        "adventureSoFar": {
            "text": _clip_text("\n\n".join(adventure_parts), 8000),
            "sourceKinds": [
                kind for kind, value in (
                    ("campaign_summary", campaign_summary),
                    ("prior_session_summaries", recent_summaries),
                ) if value
            ],
            "visibility": "unknown",
        },
        "activeMajorObjectives": [],
        "relevantCurrentEntities": relevant_entities,
        "dmEstablishedContext": {
            "text": "",
            "visibility": "dm_only",
        },
    }


def reference_source_catalog(
    snapshot=None,
    snapshot_provenance="locked_session_snapshot",
    arentoria_available=False,
    world_resolution=None,
):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    prep = snapshot.get("prepContext") if isinstance(snapshot.get("prepContext"), dict) else {}
    dungeon_types = {
        key: len(value)
        for key, value in prep.items()
        if isinstance(value, list) and key in {"rooms", "monsters", "npcs", "encounters", "items"}
    }
    campaign_available = bool(
        snapshot.get("campaignId")
        or snapshot.get("canonNames")
        or snapshot.get("campaignSummaryText")
        or snapshot.get("recentSessionSummariesText")
    )
    world_resolution = world_resolution if isinstance(world_resolution, dict) else {}
    resolved_world_sources = world_resolution.get("resolvedSources")
    resolved_world_sources = resolved_world_sources if isinstance(resolved_world_sources, list) else []
    catalog = [
        {
            "source": "world_canon",
            "available": any(item.get("source") == "world_canon" for item in resolved_world_sources),
            "canonicalAuthority": "approved World Master Canon",
            "authorityClass": "world_canon",
            "worldId": world_resolution.get("worldId"),
            "localRepresentation": next(
                (
                    item.get("path") for item in resolved_world_sources
                    if item.get("source") == "world_canon"
                ),
                "not resolved",
            ),
        },
        {
            "source": "current_adventure",
            "available": any(item.get("source") == "current_adventure" for item in resolved_world_sources),
            "canonicalAuthority": "manifest-current Adventure Reference",
            "authorityClass": "current_adventure",
            "worldId": world_resolution.get("worldId"),
            "localRepresentation": [
                item.get("path") for item in resolved_world_sources
                if item.get("source") == "current_adventure"
            ],
            "defaultVisibility": "dm_only",
        },
        {
            "source": "dungeon_maker",
            "available": bool(prep),
            "canonicalAuthority": "Gary / Dungeon Maker",
            "authorityClass": "unclassified_reference",
            "localRepresentation": f"{snapshot_provenance}.prepContext",
            "defaultVisibility": "dm_only",
            "entityCounts": dungeon_types,
        },
        {
            "source": "campaign_files",
            "available": campaign_available,
            "canonicalAuthority": "local campaign files",
            "authorityClass": "campaign_reference",
            "localRepresentation": str(snapshot_provenance or "unknown"),
            "defaultVisibility": "unknown",
        },
        {
            "source": "arentoria",
            "available": bool(arentoria_available),
            "canonicalAuthority": "Arentoria",
            "authorityClass": "structured_city",
            "localRepresentation": "read_only_local_database" if arentoria_available else "not connected",
            "defaultVisibility": "unknown",
        },
    ]
    return catalog


def build_reference_canon_packet(
    included_records=None,
    available_sources=None,
    retrieval_enabled=False,
):
    records = []
    for item in included_records if isinstance(included_records, list) else []:
        if not isinstance(item, dict):
            continue
        record = copy.deepcopy(item)
        record["visibility"] = normalize_visibility(record.get("visibility"))
        records.append(record)
    return {
        "contextClass": "reference_canon",
        "authority": "authoritative_background_only_not_current_session_evidence",
        "includedUpFront": records,
        "availableSources": copy.deepcopy(available_sources) if isinstance(available_sources, list) else [],
        "retrievalInterface": {
            "name": "search_campaign_reference",
            "parameters": ["query", "sources", "entity_types", "limit"],
            "status": (
                "available_on_demand_for_identity_resolution"
                if retrieval_enabled else "not_exposed_to_model"
            ),
        },
    }


def _reference_id(source, locator):
    digest = hashlib.sha256(f"{source}|{locator}".encode("utf-8")).hexdigest()[:24]
    return f"ref_{digest}"


def _split_aliases(value):
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;/|\n]+", str(value or ""))
    seen = set()
    aliases = []
    for item in raw:
        alias = str(item or "").strip()
        folded = _normalized_name(alias)
        if alias and folded and folded not in seen:
            seen.add(folded)
            aliases.append(alias[:120])
    return aliases[:12]


def _normalized_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9']+", text.casefold()))


def _compact_descriptor(item, fields):
    if not isinstance(item, dict):
        return ""
    parts = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, (str, int, float)) and str(value).strip():
            parts.append(str(value).strip())
    return _clip_text("; ".join(dict.fromkeys(parts)), 240)


def _normalized_reference_entity_type(value):
    entity_type = str(value or "").strip().lower()
    return _REFERENCE_ENTITY_TYPE_ALIASES.get(entity_type, entity_type)


def _normalize_reference_visibility_mode(value):
    mode = str(value or "dm").strip().lower().replace("-", "_")
    if mode in {"dm", "dm_only"}:
        return "dm"
    if mode in {"player", "player_safe", "non_dm", "public"}:
        return "player"
    raise ValueError("visibility_mode must be dm or player_safe.")


def _authority_class(source, explicit=None):
    value = str(explicit or "").strip().lower()
    return value or _REFERENCE_SOURCE_AUTHORITIES.get(
        str(source or "").strip().lower(), "unclassified_reference"
    )


def _authority_rank(value):
    return _REFERENCE_AUTHORITY_RANK.get(
        str(value or "").strip().lower(),
        _REFERENCE_AUTHORITY_RANK["unclassified_reference"],
    )


def _visible_reference_result(record, visibility_mode):
    mode = _normalize_reference_visibility_mode(visibility_mode)
    visibility = normalize_visibility(record.get("visibility"))
    if mode == "player" and visibility == "dm_only":
        return None
    result = copy.deepcopy(record)
    if mode == "player":
        for field in ("dmOnlyDetails", "dmOnlyNotes", "reviewNotes"):
            result.pop(field, None)
    return result


def _reference_record(
    source,
    entity_type,
    canonical_name,
    locator,
    visibility="unknown",
    aliases=None,
    short_descriptor="",
    authority_class=None,
):
    normalized_visibility = normalize_visibility(visibility)
    return {
        "referenceId": _reference_id(source, locator),
        "source": source,
        "provider": source,
        "authorityClass": _authority_class(source, authority_class),
        "entityType": _normalized_reference_entity_type(entity_type) or "other",
        "canonicalName": _clip_text(canonical_name or locator, 160),
        "aliases": _split_aliases(aliases),
        # DM-only records expose identity, not facts. This keeps hidden motives,
        # roles, and lore out of the model context even before prompt safeguards.
        "shortDescriptor": "" if normalized_visibility == "dm_only" else _clip_text(short_descriptor, 240),
        "visibility": normalized_visibility,
    }


def _match_record(query, record):
    query_name = _normalized_name(query)
    canonical = _normalized_name(record.get("canonicalName"))
    aliases = [
        _normalized_name(alias) for alias in record.get("aliases") or []
        if _normalized_name(alias)
    ]
    candidates = [("canonical_name", canonical)] + [("alias", alias) for alias in aliases]
    best = (0.0, "none", "")
    query_terms = set(query_name.split())
    for kind, candidate in candidates:
        if not candidate:
            continue
        if query_name == candidate:
            score = 1.0 if kind == "canonical_name" else 0.99
            match_kind = f"exact_{kind}"
        else:
            ratio = difflib.SequenceMatcher(None, query_name, candidate).ratio()
            candidate_terms = set(candidate.split())
            overlap = len(query_terms & candidate_terms) / max(1, len(query_terms | candidate_terms))
            token_ratio = max(
                (
                    difflib.SequenceMatcher(None, query_term, candidate_term).ratio()
                    for query_term in query_terms
                    for candidate_term in candidate_terms
                ),
                default=0.0,
            )
            contained = 0.94 if len(candidate) >= 4 and candidate in query_name else 0.0
            score = max(ratio * 0.96, overlap * 0.90, token_ratio * 0.82, contained)
            match_kind = "fuzzy_alias" if kind == "alias" else "fuzzy_canonical_name"
        if score > best[0]:
            best = (score, match_kind, candidate)
    if best[0] < 0.62:
        return None
    return {
        "score": round(best[0], 4),
        "kind": best[1],
        "matchedText": best[2],
    }


def _contains_name_tokens(container, contained):
    container_terms = _normalized_name(container).split()
    contained_terms = _normalized_name(contained).split()
    if len(contained_terms) < 2 or len(contained_terms) > len(container_terms):
        return False
    width = len(contained_terms)
    return any(
        container_terms[index:index + width] == contained_terms
        for index in range(len(container_terms) - width + 1)
    )


def _strong_reference_identity_match(lower_record, higher_record):
    """Return deterministic evidence that two authority records name one identity."""
    lower_type = _normalized_reference_entity_type(lower_record.get("entityType"))
    higher_type = _normalized_reference_entity_type(higher_record.get("entityType"))
    if lower_type and higher_type and lower_type != higher_type:
        return None

    lower_name = _normalized_name(lower_record.get("canonicalName"))
    higher_name = _normalized_name(higher_record.get("canonicalName"))
    if not lower_name or not higher_name:
        return None
    if lower_name == higher_name:
        return {"reason": "exact_canonical_name", "strength": 4}

    higher_aliases = {
        _normalized_name(alias) for alias in higher_record.get("aliases") or []
        if _normalized_name(alias)
    }
    lower_aliases = {
        _normalized_name(alias) for alias in lower_record.get("aliases") or []
        if _normalized_name(alias)
    }
    if lower_name in higher_aliases:
        return {"reason": "exact_approved_alias", "strength": 3}
    if higher_name in lower_aliases:
        return {"reason": "exact_lower_alias", "strength": 3}
    if (
        _contains_name_tokens(lower_name, higher_name)
        or _contains_name_tokens(higher_name, lower_name)
    ):
        return {"reason": "canonical_name_containment", "strength": 2}
    return None


class LocalJsonReferenceProvider:
    """Searches an in-memory view while leaving its canonical JSON untouched."""

    def __init__(self, source, records, authority_class=None):
        self.source = str(source)
        self.authority_class = _authority_class(self.source, authority_class)
        self._records = copy.deepcopy(records if isinstance(records, list) else [])

    def _search_records(self, query, entity_types=None, limit=5, visibility_mode="dm"):
        if not _normalized_name(query):
            return []
        mode = _normalize_reference_visibility_mode(visibility_mode)
        wanted_types = {
            _normalized_reference_entity_type(item)
            for item in entity_types or []
            if str(item).strip()
        }
        matches = []
        for record in self._records:
            if wanted_types and record.get("entityType") not in wanted_types:
                continue
            match = _match_record(query, record)
            if match:
                result = _visible_reference_result(record, mode)
                if result is None:
                    continue
                result["authorityClass"] = _authority_class(
                    result.get("source"), result.get("authorityClass") or self.authority_class
                )
                result["match"] = match
                matches.append(result)
        matches.sort(key=lambda item: (
            -item["match"]["score"],
            _normalized_name(item.get("canonicalName")),
            item["referenceId"],
        ))
        return matches[: max(1, int(limit))]

    def search(self, query, entity_types=None, limit=5, visibility_mode="dm"):
        return self._search_records(
            query,
            entity_types=entity_types,
            limit=max(1, min(5, int(limit))),
            visibility_mode=visibility_mode,
        )

    def search_candidates(
        self, query, entity_types=None, limit=_INTERNAL_REFERENCE_CANDIDATE_LIMIT,
        visibility_mode="dm",
    ):
        """Return a bounded internal candidate pool before global authority resolution."""
        return self._search_records(
            query,
            entity_types=entity_types,
            limit=max(1, min(_INTERNAL_REFERENCE_CANDIDATE_LIMIT, int(limit))),
            visibility_mode=visibility_mode,
        )

    def identity_matches(self, lower_record, visibility_mode="dm"):
        """Find strong identity links without treating the lower record as canonical."""
        mode = _normalize_reference_visibility_mode(visibility_mode)
        matches = []
        for record in self._records:
            identity_match = _strong_reference_identity_match(lower_record, record)
            if identity_match is None:
                continue
            result = _visible_reference_result(record, mode)
            if result is None:
                continue
            result["authorityClass"] = _authority_class(
                result.get("source"), result.get("authorityClass") or self.authority_class
            )
            result["identityResolution"] = identity_match
            matches.append(result)
        matches.sort(key=lambda item: (
            -int((item.get("identityResolution") or {}).get("strength") or 0),
            _normalized_name(item.get("canonicalName")),
            item.get("referenceId") or "",
        ))
        return matches


class ArentoriaSqliteReferenceProvider(LocalJsonReferenceProvider):
    """Read-only, minimized identity view over Arentoria's canonical SQLite data."""

    source = "arentoria"
    authority_class = "structured_city"

    def __init__(self, database_path):
        resolved = os.path.abspath(str(database_path or ""))
        if not os.path.isfile(resolved):
            raise ValueError("Arentoria database is unavailable.")
        self.database_path = resolved

    def _load_records(self):
        uri = Path(self.database_path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            queries = (
                ("npc", """
                    SELECT p.stable_id, p.display_name,
                           COALESCE(r.ancestry, ''), COALESCE(r.occupation_daily_role, ''),
                           COALESCE(r.home_district, '')
                    FROM person p LEFT JOIN resident_profile r ON r.person_id = p.stable_id
                    WHERE p.display_name IS NOT NULL AND trim(p.display_name) <> ''
                """),
                ("location", """
                    SELECT b.stable_id, COALESCE(b.name, b.address),
                           COALESCE(b.address, ''), COALESCE(b.district_name, ''),
                           COALESCE(c.specific_use, '')
                    FROM building b LEFT JOIN building_census c ON c.building_id = b.stable_id
                    WHERE COALESCE(b.name, b.address, '') <> ''
                """),
                ("faction", """
                    SELECT stable_id, name, COALESCE(faction_type, ''), COALESCE(public_face, ''), ''
                    FROM faction WHERE trim(name) <> ''
                """),
                ("street", """
                    SELECT stable_id, name, COALESCE(generation_zone, ''), '', ''
                    FROM road WHERE trim(name) <> ''
                """),
                ("district", """
                    SELECT stable_id, name, '', '', '' FROM district WHERE trim(name) <> ''
                """),
                ("landmark", """
                    SELECT stable_id, name, COALESCE(location, ''), '', ''
                    FROM infrastructure WHERE trim(name) <> ''
                """),
            )
            records = []
            for entity_type, sql in queries:
                for stable_id, name, descriptor_a, descriptor_b, descriptor_c in connection.execute(sql):
                    records.append(_reference_record(
                        self.source,
                        entity_type,
                        name,
                        f"{entity_type}:{stable_id}",
                        "unknown",
                        short_descriptor=_clip_text(
                            "; ".join(
                                str(value).strip()
                                for value in (descriptor_a, descriptor_b, descriptor_c)
                                if str(value or "").strip()
                            ),
                            240,
                        ),
                    ))
            return records
        finally:
            connection.close()

    def _adapter(self):
        return LocalJsonReferenceProvider(
            self.source, self._load_records(), authority_class=self.authority_class
        )

    def search(self, query, entity_types=None, limit=5, visibility_mode="dm"):
        return self._adapter().search(
            query,
            entity_types=entity_types,
            limit=limit,
            visibility_mode=visibility_mode,
        )

    def search_candidates(
        self, query, entity_types=None, limit=_INTERNAL_REFERENCE_CANDIDATE_LIMIT,
        visibility_mode="dm",
    ):
        return self._adapter().search_candidates(
            query,
            entity_types=entity_types,
            limit=limit,
            visibility_mode=visibility_mode,
        )

    def identity_matches(self, lower_record, visibility_mode="dm"):
        return self._adapter().identity_matches(
            lower_record, visibility_mode=visibility_mode
        )


class WorldMasterCanonReferenceProvider(LocalJsonReferenceProvider):
    """Compact entity view over one manifest-approved World Master Canon."""

    source = "world_canon"
    authority_class = "world_canon"

    def __init__(self, canon_path, world_id, manifest_locator, source_locator):
        path = Path(canon_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Approved World Master Canon is unreadable: {exc}.") from None
        if not isinstance(payload, dict) or payload.get("status") != "approved":
            raise ValueError("Approved World Master Canon must be an approved JSON object.")
        entities = payload.get("entities")
        if not isinstance(entities, list):
            raise ValueError("Approved World Master Canon has no entity collection.")

        records = []
        entity_ids = set()
        for entity in entities:
            if not isinstance(entity, dict):
                raise ValueError("Approved World Master Canon contains a malformed entity.")
            entity_id = str(entity.get("id") or "").strip()
            canonical_name = str(entity.get("canonicalName") or "").strip()
            if not entity_id or not canonical_name or entity_id in entity_ids:
                raise ValueError("Approved World Master Canon entity IDs and names must be unique and non-empty.")
            entity_ids.add(entity_id)
            visibility = normalize_visibility(entity.get("visibility"))
            descriptor = _clip_text(entity.get("shortDescription"), 240)
            record = _reference_record(
                self.source,
                entity.get("type") or "other",
                canonical_name,
                entity_id,
                visibility,
                aliases=entity.get("aliases"),
                short_descriptor=descriptor,
                authority_class=self.authority_class,
            )
            # World Canon explicitly marks fact-level secrets. Keep them available
            # only to DM-mode filtering; never expose review notes as canon facts.
            record["shortDescriptor"] = descriptor
            record["entityId"] = entity_id
            record["worldId"] = str(world_id)
            record["provenance"] = {
                "worldId": str(world_id),
                "manifest": str(manifest_locator),
                "masterCanon": str(source_locator),
            }
            dm_only_notes = entity.get("dmOnlyNotes")
            if isinstance(dm_only_notes, list):
                details = [
                    _clip_text(item, 240)
                    for item in dm_only_notes[:5]
                    if str(item or "").strip()
                ]
                if details:
                    record["dmOnlyDetails"] = details
            records.append(record)
        self.canon_path = str(path)
        self.world_id = str(world_id)
        self.source_locator = str(source_locator)
        super().__init__(self.source, records, authority_class=self.authority_class)


class AdventureReferenceProvider(LocalJsonReferenceProvider):
    """DM-safe identity view over one curated, manifest-current adventure reference."""

    source = "current_adventure"
    authority_class = "current_adventure"

    def __init__(self, reference_path, world_id, manifest_locator, manifest_entry):
        path = Path(reference_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Current Adventure Reference is unreadable: {exc}.") from None
        if not isinstance(payload, dict):
            raise ValueError("Current Adventure Reference must be a JSON object.")
        if str(payload.get("authorityClass") or "") != self.authority_class:
            raise ValueError("Adventure Reference authorityClass is not current_adventure.")
        local_entities = payload.get("localEntities")
        if not isinstance(local_entities, list):
            raise ValueError("Current Adventure Reference has no local entity collection.")

        adventure_id = str(manifest_entry.get("id") or "").strip()
        source_locator = str(manifest_entry.get("path") or "").strip()
        records = []
        for entity in local_entities:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or "").strip()
            canonical_name = str(entity.get("canonicalName") or "").strip()
            if not entity_id or not canonical_name:
                continue
            record = _reference_record(
                self.source,
                entity.get("type") or "other",
                canonical_name,
                f"{adventure_id}:{entity_id}",
                "dm_only",
                aliases=entity.get("aliases"),
                short_descriptor=entity.get("shortDescription"),
                authority_class=self.authority_class,
            )
            record["shortDescriptor"] = _clip_text(entity.get("shortDescription"), 240)
            record["entityId"] = entity_id
            record["worldId"] = str(world_id)
            record["adventureId"] = adventure_id
            record["preparedReferenceOnly"] = True
            record["provenance"] = {
                "worldId": str(world_id),
                "manifest": str(manifest_locator),
                "adventureReference": source_locator,
                "sourceSection": _clip_text(entity.get("sourceSection"), 160),
            }
            master_link = str(entity.get("masterCanonLink") or "").strip()
            if master_link:
                record["masterCanonLink"] = _clip_text(master_link, 160)
            records.append(record)
        self.reference_path = str(path)
        self.world_id = str(world_id)
        self.adventure_id = adventure_id
        self.source_locator = source_locator
        super().__init__(self.source, records, authority_class=self.authority_class)


def _manifest_json_path(world_root, relative_path):
    relative = str(relative_path or "").strip().replace("\\", "/")
    if not relative or Path(relative).is_absolute() or not relative.lower().endswith(".json"):
        raise ValueError("Manifest runtime source must be a relative JSON path.")
    root = Path(world_root).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("Manifest runtime source escapes its World directory.") from None
    return target, relative


def load_world_reference_providers(world_id, worlds_dir):
    """Discover runtime World providers exclusively through world.manifest.json."""
    requested_id = str(world_id or "").strip().lower()
    result = {
        "worldId": requested_id or None,
        "manifest": None,
        "providers": [],
        "resolvedSources": [],
        "errors": [],
    }
    if not requested_id or len(requested_id) > 64 or not _WORLD_ID_RE.fullmatch(requested_id):
        result["errors"].append({
            "code": "invalid_world_id",
            "source": "world",
            "message": "Campaign worldId is invalid.",
        })
        return result

    world_root = Path(worlds_dir).resolve() / requested_id
    manifest_path = world_root / "world.manifest.json"
    manifest_locator = f"worlds/{requested_id}/world.manifest.json"
    result["manifest"] = manifest_locator
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result["errors"].append({
            "code": "world_manifest_missing",
            "source": "world",
            "message": "World manifest is unavailable.",
        })
        return result
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append({
            "code": "world_manifest_invalid",
            "source": "world",
            "message": _clip_text(f"World manifest could not be read: {exc}", 240),
        })
        return result
    if not isinstance(manifest, dict) or str(manifest.get("worldId") or "").strip().lower() != requested_id:
        result["errors"].append({
            "code": "world_manifest_mismatch",
            "source": "world",
            "message": "World manifest worldId does not match the campaign worldId.",
        })
        return result

    canon = manifest.get("canon") if isinstance(manifest.get("canon"), dict) else {}
    master = canon.get("master") if isinstance(canon.get("master"), dict) else {}
    approved = master.get("approved")
    if approved is None or not str(approved).strip():
        result["errors"].append({
            "code": "approved_master_unavailable",
            "source": "world_canon",
            "message": "World manifest has no approved Master Canon; reviewed candidates are not runtime fallbacks.",
        })
    else:
        try:
            master_path, master_locator = _manifest_json_path(world_root, approved)
            provider = WorldMasterCanonReferenceProvider(
                master_path, requested_id, manifest_locator, master_locator
            )
            result["providers"].append(provider)
            result["resolvedSources"].append({
                "source": provider.source,
                "authorityClass": provider.authority_class,
                "path": master_locator,
            })
        except (OSError, ValueError) as exc:
            result["errors"].append({
                "code": "approved_master_invalid",
                "source": "world_canon",
                "message": _clip_text(exc, 240),
            })

    adventure_references = canon.get("adventureReferences")
    if not isinstance(adventure_references, list):
        adventure_references = []
    for entry in adventure_references:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status not in _CURRENT_ADVENTURE_STATUSES:
            continue
        try:
            reference_path, reference_locator = _manifest_json_path(world_root, entry.get("path"))
            provider = AdventureReferenceProvider(
                reference_path, requested_id, manifest_locator, entry
            )
            result["providers"].append(provider)
            result["resolvedSources"].append({
                "source": provider.source,
                "authorityClass": provider.authority_class,
                "adventureId": provider.adventure_id,
                "path": reference_locator,
            })
        except (OSError, ValueError) as exc:
            result["errors"].append({
                "code": "adventure_reference_invalid",
                "source": "current_adventure",
                "adventureId": str(entry.get("id") or ""),
                "message": _clip_text(exc, 240),
            })
    return result


def local_campaign_reference_providers(campaign):
    """Build local search adapters over imported canon; never changes canonical data."""
    campaign = campaign if isinstance(campaign, dict) else {}
    dungeon_maker = campaign.get("dungeonMakerJson") or campaign.get("prepContext")
    dungeon_maker = dungeon_maker if isinstance(dungeon_maker, dict) else {}
    dungeon_records = []
    dungeon = dungeon_maker.get("dungeon")
    if isinstance(dungeon, dict) and dungeon:
        dungeon_records.append(_reference_record(
            "dungeon_maker", "adventure", dungeon.get("name") or "Adventure",
            "dungeonMakerJson.dungeon", dungeon.get("visibility") or "dm_only",
            aliases=dungeon.get("aliases"),
            short_descriptor=_compact_descriptor(dungeon, ("subtitle", "type", "setting")),
        ))
    type_map = {
        "rooms": "location",
        "monsters": "monster",
        "npcs": "npc",
        "encounters": "encounter",
        "items": "item",
    }
    for collection, entity_type in type_map.items():
        for index, item in enumerate(dungeon_maker.get(collection) or []):
            if not isinstance(item, dict):
                continue
            title = item.get("name") or item.get("short_name") or item.get("title") or item.get("number")
            dungeon_records.append(_reference_record(
                "dungeon_maker", entity_type, title or f"{entity_type} {index + 1}",
                f"dungeonMakerJson.{collection}[{index}]", item.get("visibility") or "dm_only",
                aliases=item.get("aliases") or item.get("alias") or item.get("aka"),
                short_descriptor=_compact_descriptor(
                    item,
                    ("type", "role", "species", "race", "location", "room_type", "descriptor", "short_description"),
                ),
            ))

    campaign_records = []
    for index, item in enumerate(campaign.get("canonNames") or []):
        if not isinstance(item, dict):
            continue
        campaign_records.append(_reference_record(
            "campaign_files", item.get("type") or "entity", item.get("name") or "Canonical name",
            f"canonNames[{index}]", item.get("visibility") or "unknown",
            aliases=item.get("aliases"),
            short_descriptor=item.get("descriptor"),
        ))
    return [
        LocalJsonReferenceProvider("dungeon_maker", dungeon_records),
        LocalJsonReferenceProvider("campaign_files", campaign_records),
    ]


def _reference_match_tier(record):
    match = record.get("match") if isinstance(record.get("match"), dict) else {}
    kind = str(match.get("kind") or "")
    score = float(match.get("score") or 0)
    if kind.startswith("exact_"):
        return 3
    if score >= 0.82:
        return 2
    return 1


def _reference_identity_keys(record):
    values = [record.get("canonicalName"), record.get("masterCanonLink")]
    values.extend(record.get("aliases") or [])
    return {_normalized_name(value) for value in values if _normalized_name(value)}


def _reference_result_sort_key(item):
    return (
        -_reference_match_tier(item),
        -_authority_rank(item.get("authorityClass")),
        -float((item.get("match") or {}).get("score") or 0),
        _normalized_name(item.get("canonicalName")),
        item.get("referenceId") or "",
    )


def _collapse_reference_results(results):
    selected = []
    suppressed = []
    for candidate in sorted(results, key=_reference_result_sort_key):
        candidate_keys = _reference_identity_keys(candidate)
        collision = next(
            (
                winner for winner in selected
                if candidate_keys & _reference_identity_keys(winner)
            ),
            None,
        )
        reason = "same_resolved_identity"
        if collision is None and selected:
            top = selected[0]
            top_match = top.get("match") or {}
            candidate_match = candidate.get("match") or {}
            same_exact_query = (
                str(top_match.get("kind") or "").startswith("exact_")
                and str(candidate_match.get("kind") or "").startswith("exact_")
                and _normalized_name(top_match.get("matchedText"))
                == _normalized_name(candidate_match.get("matchedText"))
            )
            if (
                same_exact_query
                and _authority_rank(candidate.get("authorityClass"))
                < _authority_rank(top.get("authorityClass"))
            ):
                collision = top
                reason = "lower_authority_exact_conflict"
        if collision is None:
            selected.append(candidate)
            continue
        if _authority_rank(candidate.get("authorityClass")) <= _authority_rank(
            collision.get("authorityClass")
        ):
            suppressed.append({
                "referenceId": candidate.get("referenceId"),
                "canonicalName": candidate.get("canonicalName"),
                "authorityClass": candidate.get("authorityClass"),
                "suppressedByReferenceId": collision.get("referenceId"),
                "suppressedByAuthorityClass": collision.get("authorityClass"),
                "reason": reason,
            })
        else:
            selected.remove(collision)
            selected.append(candidate)
            suppressed.append({
                "referenceId": collision.get("referenceId"),
                "canonicalName": collision.get("canonicalName"),
                "authorityClass": collision.get("authorityClass"),
                "suppressedByReferenceId": candidate.get("referenceId"),
                "suppressedByAuthorityClass": candidate.get("authorityClass"),
                "reason": reason,
            })
    selected.sort(key=_reference_result_sort_key)
    return selected, suppressed


def _resolve_upward_to_world_canon(results, providers, visibility_mode):
    """Canonicalize discovered lower-authority identities against approved World Canon."""
    world_providers = [
        provider for provider in providers
        if _authority_class(
            getattr(provider, "source", ""), getattr(provider, "authority_class", None)
        ) == "world_canon"
        and callable(getattr(provider, "identity_matches", None))
    ]
    if not world_providers:
        return list(results), [], [], [], []

    resolved_results = []
    resolutions = []
    suppressed = []
    queried = []
    errors = []
    world_rank = _authority_rank("world_canon")
    for lower in results:
        if _authority_rank(lower.get("authorityClass")) >= world_rank:
            resolved_results.append(lower)
            continue

        candidates = []
        for provider in world_providers:
            source = str(getattr(provider, "source", "") or "").strip().lower()
            queried.append(source)
            try:
                candidates.extend(provider.identity_matches(
                    lower, visibility_mode=visibility_mode
                ))
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append({
                    "code": "identity_resolution_failed",
                    "source": source,
                    "authorityClass": "world_canon",
                    "message": _clip_text(exc, 240),
                })

        if not candidates:
            resolved_results.append(lower)
            continue
        candidates.sort(key=lambda item: (
            -int((item.get("identityResolution") or {}).get("strength") or 0),
            _normalized_name(item.get("canonicalName")),
            item.get("referenceId") or "",
        ))
        best_strength = int(
            (candidates[0].get("identityResolution") or {}).get("strength") or 0
        )
        best = [
            item for item in candidates
            if int((item.get("identityResolution") or {}).get("strength") or 0)
            == best_strength
        ]
        best_ids = {item.get("referenceId") for item in best}
        if len(best_ids) != 1:
            resolved_results.append(lower)
            continue

        promoted = copy.deepcopy(best[0])
        identity_resolution = promoted.pop("identityResolution", {})
        promoted["match"] = copy.deepcopy(lower.get("match") or {})
        resolution = {
            "lowerReferenceId": lower.get("referenceId"),
            "lowerCanonicalName": lower.get("canonicalName"),
            "lowerProvider": lower.get("provider"),
            "lowerAuthorityClass": lower.get("authorityClass"),
            "resolvedReferenceId": promoted.get("referenceId"),
            "resolvedCanonicalName": promoted.get("canonicalName"),
            "resolvedProvider": promoted.get("provider"),
            "resolvedAuthorityClass": promoted.get("authorityClass"),
            "reason": identity_resolution.get("reason"),
        }
        resolutions.append(resolution)
        suppressed.append({
            "referenceId": lower.get("referenceId"),
            "canonicalName": lower.get("canonicalName"),
            "authorityClass": lower.get("authorityClass"),
            "suppressedByReferenceId": promoted.get("referenceId"),
            "suppressedByAuthorityClass": promoted.get("authorityClass"),
            "reason": identity_resolution.get("reason"),
        })
        resolved_results.append(promoted)
    return (
        resolved_results,
        resolutions,
        suppressed,
        list(dict.fromkeys(queried)),
        errors,
    )


def search_campaign_reference(
    query,
    sources=None,
    entity_types=None,
    limit=5,
    providers=None,
    campaign_id=None,
    world_id=None,
    visibility_mode="dm",
    include_legacy=False,
    provider_errors=None,
):
    """Provider-neutral, authority-aware lookup suitable for a model-tool loop."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required.")
    if len(query) > 300:
        raise ValueError("query must be at most 300 characters.")
    try:
        limit = max(1, min(5, int(limit)))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer.") from None
    mode = _normalize_reference_visibility_mode(visibility_mode)
    wanted_sources = {str(item).strip().lower() for item in sources or [] if str(item).strip()}
    providers = list(providers or [])
    eligible_providers = [
        provider for provider in providers
        if include_legacy
        or _authority_class(
            getattr(provider, "source", ""), getattr(provider, "authority_class", None)
        ) != "legacy"
    ]
    allowed_sources = {
        str(getattr(provider, "source", "") or "").strip().lower()
        for provider in eligible_providers
        if str(getattr(provider, "source", "") or "").strip()
    }
    unsupported = sorted(wanted_sources - allowed_sources)
    if unsupported:
        raise ValueError(f"Unsupported or unavailable reference providers: {unsupported}.")
    results = []
    queried = []
    authority_classes = []
    candidate_counts = {}
    errors = copy.deepcopy(provider_errors) if isinstance(provider_errors, list) else []
    for provider in eligible_providers:
        source = str(getattr(provider, "source", "") or "").strip().lower()
        if wanted_sources and source not in wanted_sources:
            continue
        queried.append(source)
        authority = _authority_class(source, getattr(provider, "authority_class", None))
        authority_classes.append(authority)
        try:
            searcher = getattr(provider, "search_candidates", provider.search)
            provider_results = searcher(
                query,
                entity_types=entity_types,
                limit=_INTERNAL_REFERENCE_CANDIDATE_LIMIT,
                visibility_mode=mode,
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append({
                "code": "provider_search_failed",
                "source": source,
                "authorityClass": authority,
                "message": _clip_text(exc, 240),
            })
            candidate_counts[source] = 0
            continue
        candidate_counts[source] = candidate_counts.get(source, 0) + len(provider_results)
        results.extend(provider_results)
    (
        results,
        upward_resolutions,
        upward_suppressed,
        canonicalization_providers,
        canonicalization_errors,
    ) = _resolve_upward_to_world_canon(results, eligible_providers, mode)
    errors.extend(canonicalization_errors)
    results, collapsed_suppressed = _collapse_reference_results(results)
    collapsed_suppressed = [
        item for item in collapsed_suppressed
        if item.get("referenceId") != item.get("suppressedByReferenceId")
    ]
    suppressed = upward_suppressed + collapsed_suppressed
    results = results[:limit]
    if not results:
        resolution = "no_match"
    else:
        top = results[0]
        top_score = float((top.get("match") or {}).get("score") or 0)
        second_score = (
            float((results[1].get("match") or {}).get("score") or 0)
            if len(results) > 1 else 0.0
        )
        close_same_authority = (
            len(results) > 1
            and _authority_rank(results[1].get("authorityClass"))
            == _authority_rank(top.get("authorityClass"))
            and top_score - second_score < 0.08
        )
        resolution = (
            "strong_candidate"
            if top_score >= 0.82 and not close_same_authority
            else "ambiguous"
        )
    return {
        "query": query,
        "campaignId": str(campaign_id or ""),
        "worldId": str(world_id or "") or None,
        "visibilityMode": mode,
        "resolution": resolution,
        "results": results,
        "resultCount": len(results),
        "providersQueried": list(dict.fromkeys(queried)),
        "candidateCounts": candidate_counts,
        "authorityClassesQueried": list(dict.fromkeys(authority_classes)),
        "canonicalizationProvidersQueried": canonicalization_providers,
        "upwardCanonicalizations": upward_resolutions,
        "suppressedLowerAuthorityConflicts": suppressed,
        "legacyEnabled": bool(include_legacy),
        "providerErrors": errors,
        "authoritativeStorageChanged": False,
    }
