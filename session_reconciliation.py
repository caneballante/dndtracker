"""Prompt, schema, validation, and size safety for structured reconciliation."""

import copy
import hashlib
import json
import math


EVENT_TYPES = (
    "npc_interaction",
    "discovery",
    "decision",
    "quest_change",
    "location_change",
    "combat_outcome",
    "item_change",
    "relationship_change",
    "world_state",
    "consequence",
    "unresolved_hook",
    "resolved_hook",
    "ruling",
    "other",
)
EVENT_STATUSES = ("active", "unresolved", "resolved")
IMPORTANCE_LEVELS = ("low", "medium", "high", "critical")
CONFIDENCE_LEVELS = ("unknown", "low", "medium", "high")
OPERATION_TYPES = (
    "CREATE_EVENT",
    "UPDATE_EVENT",
    "MERGE_EVENTS",
    "MARK_RESOLVED",
    "MARK_SUPERSEDED",
)
HIGHLIGHT_CATEGORIES = (
    "humor",
    "combat",
    "dramatic_roll",
    "clever_solution",
    "character_moment",
    "social_moment",
    "failure",
    "triumph",
    "memorable_action",
    "other",
)


RECONCILIATION_INSTRUCTIONS = """You maintain a durable structured understanding of one D&D session.

Determine what actually happened from the complete ordered transcript evidence. Do not write a recap and do not turn transcript sentences into independent notes. Return two complementary outputs: durable event operations for what changed in the game world, and session highlight operations for memorable table moments. Consolidate repeated discussion of the same occurrence. Use later evidence to interpret earlier evidence. Frequency of discussion is not importance; importance and confidence are separate judgments.

The input contains three deliberately separate context classes:
1. sessionEvidence contains the ordered transcript with chunk provenance, session tracker evidence, CANON/MARK evidence markers, and may contain explicitly supplied reviewer-confirmed corrections. Each transcript chunk is labelled prior_session_recap, current_session_play, or uncertain; only current-session occurrence evidence may establish what occurred tonight.
2. orientationContext is small background: identity, current roster, adventure-so-far orientation, and a few curated entities. It helps interpret evidence but cannot prove a current-session event.
3. referenceCanon is authoritative world background that may include DM-only secrets. It can help identify or spell entities and understand significance, but cannot prove a current-session event or player knowledge.

Authority boundary:
- Reference and orientation material describe world/campaign context. They do not prove that an event occurred during this session.
- Only session evidence may establish that the party encountered, learned, discovered, decided, acquired, visited, fought, changed, or resolved something tonight.
- Never infer that players know a secret merely because it appears in canon. visibility=dm_only must not become player knowledge; visibility=unknown is also not proof of player knowledge.
- Example: if reference canon says Rathgar secretly works for the Dawnfire cult but session evidence only shows the party meeting Rathgar, do not claim that the party discovered his affiliation.
- Every created or updated event must cite supplied transcript source chunk indexes.
- The current roster in orientationContext is authoritative for participant identity, not for proving actions.
- Reviewer-confirmed corrections, when supplied, and DM evidence markers are high-authority session evidence. Tracker data is separately labelled supporting session evidence.
- Spoken recollection of earlier sessions is historical orientation, not proof that the recalled event occurred tonight. prior_session_recap chunks may explain goals, unresolved situations, or starting state, but must not independently create or update a current-session event.
- A current_session_play label means the words were spoken during current play; it does not turn a character's recollection of an earlier event into a current occurrence. Distinguish the time of the statement from the time of the described action.
- uncertain transcript chunks cannot independently establish a current-session occurrence. Corroborate them with current_session_play evidence, tracker evidence, or an explicit session marker.
- Every created or updated event must cite at least one current_session_play transcript chunk that supports an action, decision, discovery, change, or consequence occurring during this session.
- Existing structured events have durable IDs. Reference only IDs supplied in existingStructuredEvents.
- Existing session highlights have durable IDs. Reference only IDs supplied in existingSessionHighlights.
- The adventure-so-far summary is orientation only. Do not treat it as recursive memory or regenerate it in this operation.

Two complementary memories:
- Durable events answer "what changed?" They preserve decisions, discoveries, outcomes, relationships, obligations, and world or quest state that matter after the moment passes.
- Session highlights answer "what will the players remember?" They preserve selective table memory: funny moments, clutch or creative actions, dramatic rolls or failures, clever improvisation, strong character or social moments, and memorable combat contributions.
- A moment may be both a durable event and a highlight, one, or neither. Do not force a one-to-one mapping and do not put transient table color into the event store merely because it is memorable.
- Before returning, consider four separate questions: What changed? What did the characters do? What will the players remember? What will be useful next session?

Event granularity:
- The unit is one underlying meaningful session event, not one transcript observation and not one entire scene.
- Consolidate repeated descriptions and details that share the same objective, decision, consequence, and durable state change.
- Split developments when they have independently meaningful objectives, decisions, consequences, relationship changes, conflict-state changes, quest-state changes, location or world-state boundaries, unresolved hooks, commitments, or refusals. Events in the same scene need not be the same event.
- Do not make a mega-event whose compression hides a development that a DM would reasonably review, revise, resolve, or reference independently later.
- Do not fragment ordinary tactics, repeated discussion, or descriptive observations into standalone events. There is no fixed event quota; evidence determines the event count.

High-value session information:
- Explicit player decisions are durable information, including agreements, refusals, attempted or reversed decisions, choices to attack or stand down, lies, alliances, releases, commitments, requests for intervention, and abandoned plans. Preserve a serious decision even if it is prevented, reversed, or never becomes a completed physical action.
- Consider whether session evidence changed the party's understanding of, attitude toward, or relationship with an NPC or faction. Evidence-supported trust, distrust, perceived mistreatment, betrayal, reconciliation, alliance, hostility, obligation, protection, suspicion, and newly understood relationships must not disappear inside a broader scene summary.
- Within a consolidated event, retain significant actions whose removal would materially worsen a DM's later understanding: consequential disguises or deceptions, claims or bluffs, situation-changing spells or abilities, clear preparation for violence, releases or captures, legal commitments or refusals, meaningful acquisition or loss, explicit threats, and consequential persuasion.
- Do not preserve every die roll, attack, joke, minor tactic, or incidental object as a durable event. Prefer material understanding over exhaustive narration.

Highlight selection:
- Highlights are curated table memory, not a transcript log and not a second event summary. Select only moments likely to remain memorable or enrich a later recap.
- Humor requires evidence of a genuinely memorable beat, such as sustained or repeated laughter/reaction, multiple participants building on it, or later callbacks. Do not capture every joke or merely label a line funny.
- Use this humor/memorability test: would the players be disappointed if a recap completely forgot the moment even though it did not materially change the plot?
- Combat highlights are combat memory, not a combat log. Preserve distinctive, clutch, creative, dramatic, costly, or situation-changing contributions and name the participating characters when supported. Significant killing blows, rescues, near-deaths, decisive spells or tactics, dramatic saves, retreats, surrenders, or escapes are candidates. Do not enumerate routine attacks, damage, initiative, or every turn.
- A natural 20 or natural 1 is only a candidate highlight; preserve it when it materially shaped or memorably defined the scene. Ask what the group would probably mention if they reminisced about the fight six months later.
- A highlight may carry multiple categories when the evidence supports multiple qualities, such as combat plus triumph or humor plus character_moment.
- Every created or updated highlight must cite supplied transcript source chunk indexes. Confidence describes the evidence for the remembered moment.
- Highlights must be moments that occurred during this session. A funny, dramatic, or memorable story retold in an opening recap is not a new highlight for tonight.
- relatedEventIds are optional. They may reference only durable IDs supplied in existingStructuredEvents. Omit a link to a CREATE_EVENT in the same response because storage assigns that event's ID after validation.
- CREATE_HIGHLIGHT never includes a highlight ID; storage assigns it. UPDATE_HIGHLIGHT may reference only an ID supplied in existingSessionHighlights. Human keep/edit/reject review happens outside this model operation.

Names and corrections:
- When session evidence establishes an actionable NPC, organization, destination, location, item, document, quest, or contact by name, retain that supported name in facts and entities rather than replacing it only with a generic description.
- Do not invent or silently normalize an uncertain name. If the evidence does not resolve it, preserve the uncertainty and lower confidence as appropriate.
- search_campaign_reference is only for resolving identity, canonical spelling, aliases, or entity type already raised by session evidence. Search only when that uncertainty matters.
- A reference result can normalize a well-supported identity, but cannot add an occurrence, action, dialogue, discovery, player knowledge, or relationship change. Never create an event or highlight from a reference result alone.
- If retrieval reports ambiguous or no_match, retain the transcript's uncertainty; do not silently select the nearest canon record. A dm_only result is not evidence that players know any returned fact.
- Read evidence chronologically across the complete session. A later explicit correction or clear clarification generally supersedes an earlier mistaken attribution, duration, identity, name, quantity, location, wording, or relationship.
- When conflicting evidence remains unresolved, do not choose the first plausible version and mark it high confidence. State the uncertainty when material and lower confidence.

Confidence:
- Confidence measures reliability of the supporting evidence, not event importance. An important event may have low or medium confidence.
- Unresolved conflicts about attribution, duration, identity, wording, or other material facts should reduce confidence even when the broad event certainly occurred.

Operation rules:
- CREATE_EVENT never includes an event ID; storage assigns it.
- UPDATE_EVENT, MERGE_EVENTS, MARK_RESOLVED, and MARK_SUPERSEDED reference valid existing durable IDs.
- Prefer a manageable set of meaningful interactions, discoveries, decisions, quest or location changes, combat outcomes, item changes, relationship or faction changes, world-state facts, consequences, hooks, and rulings.
- Preserve uncertainty with confidence and unresolved status rather than inventing certainty.
- Before returning operations, silently perform a final coverage check over all session evidence. Ensure the proposed set has not lost materially important discoveries, decisions, consequences, relationship changes, quest changes, unresolved hooks, significant NPC interactions, acquired or lost items, legal or organizational obligations, meaningful location discoveries, actionable names, or explicit corrections. Add or enrich meaningful events when needed, without creating trivial events merely to satisfy categories.
- Do not output chain-of-thought, the coverage check, commentary, or a recap.
- Return only event and highlight operations matching the required schema."""


RECONCILIATION_MAX_MODEL_REQUESTS = 4


def _string_array_schema(max_items):
    return {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": max_items,
    }


def _source_chunks_schema():
    return {
        "type": "array",
        "items": {"type": "integer", "minimum": 0},
        "minItems": 1,
        "maxItems": 200,
    }


def _event_payload_schema():
    properties = {
        "type": {"type": "string", "enum": list(EVENT_TYPES)},
        "status": {"type": "string", "enum": list(EVENT_STATUSES)},
        "importance": {"type": "string", "enum": list(IMPORTANCE_LEVELS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "summary": {"type": "string", "maxLength": 2000},
        "facts": _string_array_schema(30),
        "entities": _string_array_schema(30),
        "sourceChunks": _source_chunks_schema(),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _highlight_payload_schema():
    properties = {
        "categories": {
            "type": "array",
            "items": {"type": "string", "enum": list(HIGHLIGHT_CATEGORIES)},
            "minItems": 1,
            "maxItems": 4,
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "summary": {"type": "string", "maxLength": 1200},
        "participants": _string_array_schema(30),
        "sourceChunks": _source_chunks_schema(),
        "relatedEventIds": {
            "type": "array",
            "items": {"type": "string", "pattern": "^evt_[0-9a-f]{32}$"},
            "maxItems": 20,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def reconciliation_output_schema():
    event_schema = _event_payload_schema()
    highlight_schema = _highlight_payload_schema()
    event_id = {"type": "string", "pattern": "^evt_[0-9a-f]{32}$"}
    highlight_id = {"type": "string", "pattern": "^hlt_[0-9a-f]{32}$"}
    reason = {"type": "string", "maxLength": 500}
    operation_variants = [
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["CREATE_EVENT"]},
                "event": copy.deepcopy(event_schema),
                "reason": reason,
            },
            "required": ["operation", "event", "reason"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["UPDATE_EVENT"]},
                "eventId": event_id,
                "changes": copy.deepcopy(event_schema),
                "reason": reason,
            },
            "required": ["operation", "eventId", "changes", "reason"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["MERGE_EVENTS"]},
                "targetEventId": event_id,
                "sourceEventIds": {
                    "type": "array",
                    "items": event_id,
                    "minItems": 1,
                    "maxItems": 50,
                },
                "changes": copy.deepcopy(event_schema),
                "reason": reason,
            },
            "required": ["operation", "targetEventId", "sourceEventIds", "changes", "reason"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["MARK_RESOLVED"]},
                "eventId": event_id,
                "reason": reason,
            },
            "required": ["operation", "eventId", "reason"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["MARK_SUPERSEDED"]},
                "eventId": event_id,
                "supersededBy": {"anyOf": [event_id, {"type": "null"}]},
                "reason": reason,
            },
            "required": ["operation", "eventId", "supersededBy", "reason"],
            "additionalProperties": False,
        },
    ]
    highlight_operation_variants = [
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["CREATE_HIGHLIGHT"]},
                "highlight": copy.deepcopy(highlight_schema),
                "reason": reason,
            },
            "required": ["operation", "highlight", "reason"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["UPDATE_HIGHLIGHT"]},
                "highlightId": highlight_id,
                "changes": copy.deepcopy(highlight_schema),
                "reason": reason,
            },
            "required": ["operation", "highlightId", "changes", "reason"],
            "additionalProperties": False,
        },
    ]
    return {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {"anyOf": operation_variants},
                "maxItems": 200,
            },
            "highlightOperations": {
                "type": "array",
                "items": {"anyOf": highlight_operation_variants},
                "maxItems": 100,
            },
        },
        "required": ["operations", "highlightOperations"],
        "additionalProperties": False,
    }


def campaign_reference_tool_schema(available_sources=None, max_results=5):
    sources = sorted({
        str(item or "").strip().lower()
        for item in available_sources or []
        if str(item or "").strip()
    })
    source_items = {"type": "string"}
    if sources:
        source_items["enum"] = sources
    return {
        "type": "function",
        "name": "search_campaign_reference",
        "description": (
            "Resolve an uncertain campaign-specific name, alias, spelling, or entity type that "
            "already appears in session evidence. Do not use for lore exploration, secrets, "
            "player knowledge, or deciding what happened. Empty sources/entity_types means all."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                "sources": {
                    "type": "array",
                    "items": source_items,
                    "maxItems": 3,
                },
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 40},
                    "maxItems": 8,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": max(1, min(5, int(max_results))),
                },
            },
            "required": ["query", "sources", "entity_types", "limit"],
            "additionalProperties": False,
        },
    }


def build_reconciliation_request(
    model,
    reasoning_effort,
    max_output_tokens,
    max_input_tokens,
    session_id,
    finalization_id,
    session_evidence,
    orientation_context,
    reference_canon,
    existing_events,
    existing_highlights=None,
    benchmark_mode="normal",
    reference_retrieval=None,
):
    session_evidence = session_evidence if isinstance(session_evidence, dict) else {}
    orientation_context = orientation_context if isinstance(orientation_context, dict) else {}
    reference_canon = reference_canon if isinstance(reference_canon, dict) else {}
    if session_evidence.get("contextClass") != "session_evidence":
        raise ValueError("sessionEvidence must be an explicit session_evidence packet.")
    if orientation_context.get("contextClass") != "orientation_context":
        raise ValueError("orientationContext must be an explicit orientation_context packet.")
    if reference_canon.get("contextClass") != "reference_canon":
        raise ValueError("referenceCanon must be an explicit reference_canon packet.")
    transcript = session_evidence.get("orderedTranscriptEvidence") or []
    indexes = [item["chunkIndex"] for item in transcript]
    if indexes != sorted(indexes):
        raise ValueError("Reconciliation transcript evidence is not ordered by chunkIndex.")
    if len(indexes) != len(set(indexes)):
        raise ValueError("Reconciliation transcript evidence contains duplicate chunk indexes.")

    reference_retrieval = reference_retrieval if isinstance(reference_retrieval, dict) else {}
    retrieval_enabled = bool(reference_retrieval.get("enabled"))
    max_searches = max(1, min(5, int(reference_retrieval.get("maxSearches") or 5)))
    max_results = max(1, min(5, int(reference_retrieval.get("maxResultsPerSearch") or 5)))
    available_source_names = [
        item.get("source")
        for item in reference_canon.get("availableSources") or []
        if isinstance(item, dict) and item.get("available") and item.get("source")
    ]
    input_document = {
        "schemaVersion": 5,
        "sessionId": str(session_id),
        "finalizationId": str(finalization_id),
        "benchmarkMode": str(benchmark_mode or "normal"),
        "authorityPolicy": {
            "currentSessionOccurrenceAuthority": "sessionEvidenceOnly",
            "transcriptPhasePolicy": "prior_session_recap_is_historical_orientation_only",
            "uncertainTranscriptPolicy": "requires_current_session_corroboration",
            "orientationAndReferenceRole": "backgroundInterpretationOnly",
            "unknownVisibilityMeansPlayerKnowledge": False,
            "referenceRetrievalRole": "identityNormalizationOnlyNeverOccurrenceEvidence",
        },
        "sessionEvidence": copy.deepcopy(session_evidence),
        "orientationContext": copy.deepcopy(orientation_context),
        "referenceCanon": copy.deepcopy(reference_canon),
        "existingStructuredEvents": existing_events if isinstance(existing_events, dict) else {},
        "existingSessionHighlights": (
            existing_highlights if isinstance(existing_highlights, dict) else {}
        ),
    }
    input_text = json.dumps(input_document, ensure_ascii=False, separators=(",", ":"))
    schema = reconciliation_output_schema()
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    reference_tool = (
        campaign_reference_tool_schema(available_source_names, max_results)
        if retrieval_enabled else None
    )
    tool_schema_bytes = (
        len(json.dumps(reference_tool, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if reference_tool else 0
    )
    estimated_bytes = (
        len(RECONCILIATION_INSTRUCTIONS.encode("utf-8"))
        + len(input_text.encode("utf-8"))
        + len(schema_text.encode("utf-8"))
        + tool_schema_bytes
    )
    estimated_tokens = int(math.ceil(estimated_bytes / 3.0))
    json_bytes = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    session_bytes = json_bytes(input_document["sessionEvidence"])
    transcript_bytes = json_bytes(transcript)
    tracker_bytes = json_bytes(session_evidence.get("trackerEvidence") or {})
    reviewer_bytes = (
        json_bytes(session_evidence["reviewerConfirmedCorrections"])
        if "reviewerConfirmedCorrections" in session_evidence else 0
    )
    orientation_bytes = json_bytes(input_document["orientationContext"])
    events_bytes = json_bytes(input_document["existingStructuredEvents"])
    highlights_bytes = json_bytes(input_document["existingSessionHighlights"])
    reference_bytes = json_bytes(input_document["referenceCanon"])
    included_reference = reference_canon.get("includedUpFront") or []
    included_reference_bytes = sum(json_bytes(item) for item in included_reference)
    components = {
        "sessionTranscriptEvidence": transcript_bytes,
        "trackerEvidence": tracker_bytes,
        "humanReviewEvidence": reviewer_bytes,
        "sessionEvidenceMetadataAndMarkers": max(0, session_bytes - transcript_bytes - tracker_bytes - reviewer_bytes),
        "orientationContext": orientation_bytes,
        "existingStructuredEvents": events_bytes,
        "existingSessionHighlights": highlights_bytes,
        "referenceCanonIncludedUpFront": included_reference_bytes,
        "referenceRetrievalMetadata": max(0, reference_bytes - included_reference_bytes),
        "referenceRetrievalToolSchema": tool_schema_bytes,
    }
    components["instructionsSchemaAndEnvelope"] = max(
        0, estimated_bytes - sum(components.values())
    )
    component_diagnostics = {
        key: {
            "bytes": value,
            "approximateTokens": int(math.ceil(value / 3.0)) if value else 0,
        }
        for key, value in components.items()
    }
    diagnostics = {
        "contextSchemaVersion": 5,
        "benchmarkMode": input_document["benchmarkMode"],
        "inputBytes": estimated_bytes,
        "approximateInputTokens": estimated_tokens,
        "maxInputTokens": int(max_input_tokens),
        "fitsConfiguredLimit": estimated_tokens <= int(max_input_tokens),
        "transcriptChunks": len(transcript),
        "firstChunk": indexes[0] if indexes else None,
        "lastChunk": indexes[-1] if indexes else None,
        "transcriptCharacters": sum(len(item["transcript"]) for item in transcript),
        "transcriptPhaseCounts": {
            phase: sum(1 for item in transcript if item.get("evidencePhase") == phase)
            for phase in ("prior_session_recap", "current_session_play", "uncertain")
        },
        "transcriptPhaseAssessment": copy.deepcopy(
            session_evidence.get("transcriptPhaseAssessment") or {}
        ),
        "existingEventCount": len(input_document["existingStructuredEvents"]),
        "existingHighlightCount": len(input_document["existingSessionHighlights"]),
        "referenceRecordCount": len(included_reference),
        "referenceRetrieval": {
            "enabled": retrieval_enabled,
            "availableProviders": available_source_names,
            "maxSearches": max_searches,
            "maxResultsPerSearch": max_results,
            "maximumModelRequests": RECONCILIATION_MAX_MODEL_REQUESTS if retrieval_enabled else 1,
            "potentialAdditionalModelRequests": RECONCILIATION_MAX_MODEL_REQUESTS - 1 if retrieval_enabled else 0,
            "searchCount": 0,
            "providersQueried": [],
            "resultCount": 0,
            "approximateReferenceTokensInserted": 0,
            "modelRequestCount": 0,
        },
        "contextContributions": component_diagnostics,
        "contextPacketHashes": {
            key: hashlib.sha256(
                json.dumps(input_document[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            for key in (
                "sessionEvidence",
                "orientationContext",
                "referenceCanon",
                "existingStructuredEvents",
                "existingSessionHighlights",
            )
        },
        "model": str(model),
        "reasoningEffort": str(reasoning_effort),
        "maxOutputTokens": int(max_output_tokens),
        "estimateMethod": "UTF-8 bytes divided by 3, including instructions and output schema",
    }
    request_payload = {
        "model": str(model),
        "instructions": RECONCILIATION_INSTRUCTIONS,
        "input": input_text,
        "reasoning": {"effort": str(reasoning_effort)},
        "max_output_tokens": int(max_output_tokens),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "session_reconciliation_operations",
                "strict": True,
                "schema": schema,
            }
        },
        "store": False,
        "truncation": "disabled",
    }
    if reference_tool:
        request_payload["tools"] = [reference_tool]
        request_payload["tool_choice"] = "auto"
    return request_payload, diagnostics, input_document


def extract_structured_output(response_payload):
    if not isinstance(response_payload, dict):
        raise ValueError("Reconciliation API response must be an object.")
    status = str(response_payload.get("status") or "completed").lower()
    if status != "completed":
        details = response_payload.get("incomplete_details") or response_payload.get("error") or status
        raise ValueError(f"Reconciliation response was not completed: {details}.")
    text = str(response_payload.get("output_text") or "").strip()
    refusal = ""
    if not text:
        for item in response_payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "refusal":
                    refusal = str(content.get("refusal") or "").strip()
                elif content.get("type") == "output_text":
                    text += str(content.get("text") or "")
    if refusal:
        raise ValueError(f"Reconciliation model refused the request: {refusal}")
    if not text.strip():
        raise ValueError("Reconciliation response contained no structured output.")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reconciliation response was not valid JSON: {exc.msg}.") from None
    return result


def _exact_fields(value, required, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    missing = set(required) - set(value)
    extra = set(value) - set(required)
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(sorted(missing))}.")
    if extra:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(extra))}.")


def _normalize_event_payload(
    value, valid_source_chunks, label, current_session_source_chunks=None
):
    fields = ("type", "status", "importance", "confidence", "summary", "facts", "entities", "sourceChunks")
    _exact_fields(value, fields, label)
    event_type = str(value["type"] or "").strip()
    status = str(value["status"] or "").strip().lower()
    importance = str(value["importance"] or "").strip().lower()
    confidence = str(value["confidence"] or "").strip().lower()
    summary = str(value["summary"] or "").strip()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"{label}.type is invalid.")
    if status not in EVENT_STATUSES:
        raise ValueError(f"{label}.status is invalid.")
    if importance not in IMPORTANCE_LEVELS:
        raise ValueError(f"{label}.importance is invalid.")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"{label}.confidence is invalid.")
    if not summary or len(summary) > 2000:
        raise ValueError(f"{label}.summary must contain 1-2000 characters.")
    if not isinstance(value["facts"], list) or len(value["facts"]) > 30:
        raise ValueError(f"{label}.facts must be a list with at most 30 entries.")
    if not isinstance(value["entities"], list) or len(value["entities"]) > 30:
        raise ValueError(f"{label}.entities must be a list with at most 30 entries.")
    if any(not isinstance(item, str) for item in value["facts"]):
        raise ValueError(f"{label}.facts entries must be strings.")
    if any(not isinstance(item, str) for item in value["entities"]):
        raise ValueError(f"{label}.entities entries must be strings.")
    facts = [str(item or "").strip() for item in value["facts"] if str(item or "").strip()]
    entities = [str(item or "").strip() for item in value["entities"] if str(item or "").strip()]
    source_chunks = value["sourceChunks"]
    if not isinstance(source_chunks, list) or not source_chunks:
        raise ValueError(f"{label}.sourceChunks must contain transcript chunk indexes.")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in source_chunks):
        raise ValueError(f"{label}.sourceChunks must contain integers.")
    source_chunks = sorted(set(source_chunks))
    unavailable = sorted(set(source_chunks) - set(valid_source_chunks))
    if unavailable:
        raise ValueError(f"{label} references unavailable transcript chunks: {unavailable}.")
    if current_session_source_chunks is not None and not (
        set(source_chunks) & set(current_session_source_chunks)
    ):
        raise ValueError(
            f"{label} must cite at least one current_session_play transcript chunk."
        )
    return {
        "type": event_type,
        "status": status,
        "importance": importance,
        "confidence": confidence,
        "summary": summary,
        "facts": facts,
        "entities": entities,
        "sourceChunks": source_chunks,
    }


def _normalize_highlight_payload(
    value,
    valid_source_chunks,
    valid_event_ids,
    label,
    current_session_source_chunks=None,
):
    fields = (
        "categories",
        "confidence",
        "summary",
        "participants",
        "sourceChunks",
        "relatedEventIds",
    )
    _exact_fields(value, fields, label)
    categories = value["categories"]
    if not isinstance(categories, list) or not 1 <= len(categories) <= 4:
        raise ValueError(f"{label}.categories must contain 1-4 entries.")
    if any(not isinstance(item, str) for item in categories):
        raise ValueError(f"{label}.categories entries must be strings.")
    categories = [item.strip().lower() for item in categories]
    if len(categories) != len(set(categories)) or any(
        item not in HIGHLIGHT_CATEGORIES for item in categories
    ):
        raise ValueError(f"{label}.categories contains an invalid or duplicate category.")

    confidence = str(value["confidence"] or "").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"{label}.confidence is invalid.")
    summary = str(value["summary"] or "").strip()
    if not summary or len(summary) > 1200:
        raise ValueError(f"{label}.summary must contain 1-1200 characters.")

    participants = value["participants"]
    if not isinstance(participants, list) or len(participants) > 30:
        raise ValueError(f"{label}.participants must be a list with at most 30 entries.")
    if any(not isinstance(item, str) for item in participants):
        raise ValueError(f"{label}.participants entries must be strings.")
    participants = list(dict.fromkeys(
        item.strip() for item in participants if item.strip()
    ))

    source_chunks = value["sourceChunks"]
    if not isinstance(source_chunks, list) or not source_chunks:
        raise ValueError(f"{label}.sourceChunks must contain transcript chunk indexes.")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in source_chunks):
        raise ValueError(f"{label}.sourceChunks must contain integers.")
    source_chunks = sorted(set(source_chunks))
    unavailable_chunks = sorted(set(source_chunks) - set(valid_source_chunks))
    if unavailable_chunks:
        raise ValueError(
            f"{label} references unavailable transcript chunks: {unavailable_chunks}."
        )
    if current_session_source_chunks is not None and not (
        set(source_chunks) & set(current_session_source_chunks)
    ):
        raise ValueError(
            f"{label} must cite at least one current_session_play transcript chunk."
        )

    related_event_ids = value["relatedEventIds"]
    if not isinstance(related_event_ids, list) or len(related_event_ids) > 20:
        raise ValueError(f"{label}.relatedEventIds must be a list with at most 20 entries.")
    if any(not isinstance(item, str) for item in related_event_ids):
        raise ValueError(f"{label}.relatedEventIds entries must be strings.")
    related_event_ids = [item.strip() for item in related_event_ids]
    if len(related_event_ids) != len(set(related_event_ids)):
        raise ValueError(f"{label}.relatedEventIds must be unique.")
    unknown_event_ids = sorted(set(related_event_ids) - set(valid_event_ids))
    if unknown_event_ids:
        raise ValueError(
            f"{label} references unknown related event IDs: {unknown_event_ids}."
        )
    return {
        "categories": categories,
        "confidence": confidence,
        "summary": summary,
        "participants": participants,
        "sourceChunks": source_chunks,
        "relatedEventIds": related_event_ids,
    }

def _reason(value, label):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError(f"{label}.reason must be a string with at most 500 characters.")
    return value.strip()


def validate_reconciliation_output(
    output,
    existing_event_ids,
    valid_source_chunks,
    current_session_source_chunks=None,
):
    _exact_fields(output, ("operations",), "reconciliation output")
    operations = output["operations"]
    if not isinstance(operations, list) or len(operations) > 200:
        raise ValueError("reconciliation output.operations must be a list with at most 200 entries.")
    existing = {str(event_id) for event_id in existing_event_ids}
    valid_chunks = {int(index) for index in valid_source_chunks}
    normalized = []
    for index, item in enumerate(operations):
        if not isinstance(item, dict):
            raise ValueError(f"operations[{index}] must be an object.")
        operation = str(item.get("operation") or "").strip()
        label = f"operations[{index}]"
        if operation == "CREATE_EVENT":
            _exact_fields(item, ("operation", "event", "reason"), label)
            normalized.append({
                "operation": operation,
                "event": _normalize_event_payload(
                    item["event"], valid_chunks, f"{label}.event", current_session_source_chunks
                ),
                "reason": _reason(item["reason"], label),
            })
            continue
        if operation == "UPDATE_EVENT":
            _exact_fields(item, ("operation", "eventId", "changes", "reason"), label)
            event_id = str(item["eventId"] or "").strip()
            if event_id not in existing:
                raise ValueError(f"{label} references unknown eventId: {event_id}.")
            normalized.append({
                "operation": operation,
                "eventId": event_id,
                "changes": _normalize_event_payload(
                    item["changes"], valid_chunks, f"{label}.changes", current_session_source_chunks
                ),
                "reason": _reason(item["reason"], label),
            })
            continue
        if operation == "MERGE_EVENTS":
            _exact_fields(item, ("operation", "targetEventId", "sourceEventIds", "changes", "reason"), label)
            target_id = str(item["targetEventId"] or "").strip()
            source_ids = [str(event_id or "").strip() for event_id in item["sourceEventIds"]] if isinstance(item["sourceEventIds"], list) else []
            unknown = sorted(({target_id} | set(source_ids)) - existing)
            if unknown:
                raise ValueError(f"{label} references unknown event IDs: {unknown}.")
            if not source_ids or target_id in source_ids or len(source_ids) != len(set(source_ids)):
                raise ValueError(f"{label} must contain distinct source IDs separate from its target.")
            normalized.append({
                "operation": operation,
                "targetEventId": target_id,
                "sourceEventIds": source_ids,
                "changes": _normalize_event_payload(
                    item["changes"], valid_chunks, f"{label}.changes", current_session_source_chunks
                ),
                "reason": _reason(item["reason"], label),
            })
            continue
        if operation in {"MARK_RESOLVED", "MARK_SUPERSEDED"}:
            fields = ("operation", "eventId", "reason") if operation == "MARK_RESOLVED" else (
                "operation", "eventId", "supersededBy", "reason"
            )
            _exact_fields(item, fields, label)
            event_id = str(item["eventId"] or "").strip()
            if event_id not in existing:
                raise ValueError(f"{label} references unknown eventId: {event_id}.")
            normalized_item = {
                "operation": operation,
                "eventId": event_id,
                "reason": _reason(item["reason"], label),
            }
            if operation == "MARK_SUPERSEDED":
                replacement = str(item.get("supersededBy") or "").strip() or None
                if replacement is not None and replacement not in existing:
                    raise ValueError(f"{label} references unknown supersededBy eventId: {replacement}.")
                if replacement == event_id:
                    raise ValueError(f"{label}.supersededBy cannot reference the same event.")
                normalized_item["supersededBy"] = replacement
            normalized.append(normalized_item)
            continue
        raise ValueError(f"{label}.operation is invalid: {operation or '(empty)' }.")
    return normalized


def validate_highlight_operations(
    operations,
    existing_highlight_ids,
    existing_event_ids,
    valid_source_chunks,
    current_session_source_chunks=None,
):
    if not isinstance(operations, list) or len(operations) > 100:
        raise ValueError("reconciliation output.highlightOperations must be a list with at most 100 entries.")
    existing_highlights = {str(highlight_id) for highlight_id in existing_highlight_ids}
    existing_events = {str(event_id) for event_id in existing_event_ids}
    valid_chunks = {int(index) for index in valid_source_chunks}
    normalized = []
    for index, item in enumerate(operations):
        label = f"highlightOperations[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object.")
        operation = str(item.get("operation") or "").strip()
        if operation == "CREATE_HIGHLIGHT":
            _exact_fields(item, ("operation", "highlight", "reason"), label)
            normalized.append({
                "operation": operation,
                "highlight": _normalize_highlight_payload(
                    item["highlight"], valid_chunks, existing_events, f"{label}.highlight",
                    current_session_source_chunks,
                ),
                "reason": _reason(item["reason"], label),
            })
            continue
        if operation == "UPDATE_HIGHLIGHT":
            _exact_fields(item, ("operation", "highlightId", "changes", "reason"), label)
            highlight_id = str(item["highlightId"] or "").strip()
            if highlight_id not in existing_highlights:
                raise ValueError(f"{label} references unknown highlightId: {highlight_id}.")
            normalized.append({
                "operation": operation,
                "highlightId": highlight_id,
                "changes": _normalize_highlight_payload(
                    item["changes"], valid_chunks, existing_events, f"{label}.changes",
                    current_session_source_chunks,
                ),
                "reason": _reason(item["reason"], label),
            })
            continue
        raise ValueError(f"{label}.operation is invalid: {operation or '(empty)' }.")
    return normalized


def validate_reconciliation_result(
    output,
    existing_event_ids,
    existing_highlight_ids,
    valid_source_chunks,
    current_session_source_chunks=None,
):
    """Validate the complete two-stream strict result before either store is changed."""
    _exact_fields(output, ("operations", "highlightOperations"), "reconciliation output")
    existing_event_ids = list(existing_event_ids)
    return {
        "operations": validate_reconciliation_output(
            {"operations": output["operations"]},
            existing_event_ids=existing_event_ids,
            valid_source_chunks=valid_source_chunks,
            current_session_source_chunks=current_session_source_chunks,
        ),
        "highlightOperations": validate_highlight_operations(
            output["highlightOperations"],
            existing_highlight_ids=existing_highlight_ids,
            existing_event_ids=existing_event_ids,
            valid_source_chunks=valid_source_chunks,
            current_session_source_chunks=current_session_source_chunks,
        ),
    }
