import json
import os
import threading
import time


SCHEMA_VERSION = 1
USAGE_FILENAME = "ai_usage.jsonl"
KNOWN_STAGES = (
    "transcription",
    "live_notes",
    "notes_rebuild",
    "notes_lab",
    "clean_transcript",
    "session_reconciliation",
    "final_recap",
    "narrative",
)

_USAGE_LOCK = threading.Lock()


def _nonnegative_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _nonnegative_float(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _first_present(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def extract_response_usage(response_payload):
    """Normalize usage fields without retaining prompts, responses, or credentials."""
    payload = response_payload if isinstance(response_payload, dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}

    input_raw = _first_present(usage, ("input_tokens", "prompt_tokens"))
    output_raw = _first_present(usage, ("output_tokens", "completion_tokens"))
    total_raw = _first_present(usage, ("total_tokens",))

    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = usage.get("prompt_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    cached_raw = _first_present(input_details, ("cached_tokens",))

    duration_raw = _first_present(payload, ("duration", "audio_duration"))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if duration_raw is None:
        duration_raw = _first_present(metadata, ("duration", "audio_duration"))
    if duration_raw is None:
        duration_raw = _first_present(usage, ("seconds", "duration", "audio_seconds"))

    # A total without the input/output split cannot be priced safely because the
    # two directions can have different rates.
    token_usage_available = input_raw is not None or output_raw is not None
    return {
        "inputTokens": _nonnegative_int(input_raw),
        "outputTokens": _nonnegative_int(output_raw),
        "cachedInputTokens": _nonnegative_int(cached_raw),
        "totalTokens": _nonnegative_int(total_raw),
        "audioSeconds": _nonnegative_float(duration_raw),
        "tokenUsageAvailable": bool(token_usage_available),
    }


def load_pricing_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "currency": "USD",
            "tokenUnit": 1_000_000,
            "providers": {},
        }
    if not isinstance(config, dict):
        raise ValueError("AI pricing configuration must be a JSON object.")
    return config


def _model_pricing(config, provider, model):
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_config = providers.get(str(provider or "").strip().lower())
    if not isinstance(provider_config, dict):
        return None
    models = provider_config.get("models") if isinstance(provider_config.get("models"), dict) else {}
    pricing = models.get(str(model or "").strip())
    return pricing if isinstance(pricing, dict) else None


def estimate_cost(config, provider, model, usage):
    pricing = _model_pricing(config, provider, model)
    if pricing is None:
        return None

    token_unit = _nonnegative_float(config.get("tokenUnit") or 1_000_000) or 1_000_000
    token_usage_available = bool(usage.get("tokenUsageAvailable"))
    input_tokens = _nonnegative_int(usage.get("inputTokens"))
    output_tokens = _nonnegative_int(usage.get("outputTokens"))
    cached_tokens = min(input_tokens, _nonnegative_int(usage.get("cachedInputTokens")))
    uncached_tokens = max(0, input_tokens - cached_tokens)
    audio_seconds = _nonnegative_float(usage.get("audioSeconds"))

    total = 0.0
    used_metric = False
    if token_usage_available:
        for amount, price_key in (
            (uncached_tokens, "inputPerMillionTokens"),
            (cached_tokens, "cachedInputPerMillionTokens"),
            (output_tokens, "outputPerMillionTokens"),
        ):
            if amount <= 0:
                continue
            rate = pricing.get(price_key)
            if not isinstance(rate, (int, float)):
                return None
            total += (amount / token_unit) * float(rate)
            used_metric = True
        if input_tokens == 0 and output_tokens == 0:
            used_metric = True

    if audio_seconds > 0 and not token_usage_available:
        audio_rate = pricing.get("audioPerMinute")
        if not isinstance(audio_rate, (int, float)):
            return None
        total += (audio_seconds / 60.0) * float(audio_rate)
        used_metric = True

    if not used_metric:
        return None
    return round(total, 8)


def build_usage_event(stage, model, provider, response_payload, pricing_config, metadata=None):
    usage = extract_response_usage(response_payload)
    estimate = estimate_cost(pricing_config, provider, model, usage)
    pricing = _model_pricing(pricing_config, provider, model)
    if estimate is not None:
        pricing_status = "estimated"
    elif pricing is None:
        pricing_status = "unconfigured"
    else:
        pricing_status = "usage_unavailable"

    event = {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": int(time.time()),
        "stage": str(stage or "unknown").strip() or "unknown",
        "provider": str(provider or "unknown").strip().lower() or "unknown",
        "model": str(model or "unknown").strip() or "unknown",
        "requests": 1,
        **usage,
        "estimatedCost": estimate,
        "pricingStatus": pricing_status,
        "pricingVersion": str(pricing_config.get("version") or ""),
    }
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = dict(metadata)
    return event


def record_response_usage(session_dir, stage, model, provider, response_payload, pricing_path, metadata=None):
    os.makedirs(session_dir, exist_ok=True)
    pricing_config = load_pricing_config(pricing_path)
    event = build_usage_event(
        stage=stage,
        model=model,
        provider=provider,
        response_payload=response_payload,
        pricing_config=pricing_config,
        metadata=metadata,
    )
    path = os.path.join(session_dir, USAGE_FILENAME)
    with _USAGE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
    return event


def read_usage_events(session_dir):
    path = os.path.join(session_dir, USAGE_FILENAME)
    if not os.path.isfile(path):
        return []
    events = []
    with _USAGE_LOCK:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    return events


def _empty_bucket(stage="", model="", provider=""):
    return {
        "stage": stage,
        "model": model,
        "provider": provider,
        "requests": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedInputTokens": 0,
        "audioSeconds": 0.0,
        "estimatedCost": 0.0,
        "knownEstimatedCost": 0.0,
        "unestimatedRequests": 0,
        "tokenUsageMissingRequests": 0,
    }


def _add_event(bucket, event, current_estimate):
    requests = max(1, _nonnegative_int(event.get("requests")))
    bucket["requests"] += requests
    bucket["inputTokens"] += _nonnegative_int(event.get("inputTokens"))
    bucket["outputTokens"] += _nonnegative_int(event.get("outputTokens"))
    bucket["cachedInputTokens"] += _nonnegative_int(event.get("cachedInputTokens"))
    bucket["audioSeconds"] += _nonnegative_float(event.get("audioSeconds"))
    if not bool(event.get("tokenUsageAvailable")):
        bucket["tokenUsageMissingRequests"] += requests
    if current_estimate is None:
        bucket["unestimatedRequests"] += requests
    else:
        bucket["knownEstimatedCost"] += float(current_estimate)


def _finalize_bucket(bucket):
    out = dict(bucket)
    out["audioSeconds"] = round(out["audioSeconds"], 3)
    out["knownEstimatedCost"] = round(out["knownEstimatedCost"], 8)
    out["estimatedCost"] = None if out["unestimatedRequests"] else out["knownEstimatedCost"]
    return out


def summarize_usage(session_dir, pricing_path):
    config = load_pricing_config(pricing_path)
    events = read_usage_events(session_dir)
    total = _empty_bucket(model="*")
    stage_buckets = {stage: _empty_bucket(stage=stage, model="*") for stage in KNOWN_STAGES}
    model_buckets = {}

    for event in events:
        stage = str(event.get("stage") or "unknown").strip() or "unknown"
        provider = str(event.get("provider") or "unknown").strip().lower() or "unknown"
        model = str(event.get("model") or "unknown").strip() or "unknown"
        current_estimate = estimate_cost(config, provider, model, event)
        stage_bucket = stage_buckets.setdefault(stage, _empty_bucket(stage=stage, model="*"))
        model_key = (stage, provider, model)
        model_bucket = model_buckets.setdefault(
            model_key,
            _empty_bucket(stage=stage, model=model, provider=provider),
        )
        _add_event(total, event, current_estimate)
        _add_event(stage_bucket, event, current_estimate)
        _add_event(model_bucket, event, current_estimate)

    stages = []
    ordered_stage_names = list(KNOWN_STAGES) + sorted(set(stage_buckets) - set(KNOWN_STAGES))
    for stage in ordered_stage_names:
        stage_summary = _finalize_bucket(stage_buckets[stage])
        stage_summary["models"] = [
            _finalize_bucket(model_buckets[key])
            for key in sorted(model_buckets)
            if key[0] == stage
        ]
        stages.append(stage_summary)

    review_after = str(config.get("reviewAfter") or "").strip()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "currency": str(config.get("currency") or "USD"),
        "pricingVersion": str(config.get("version") or ""),
        "pricingEffectiveDate": str(config.get("effectiveDate") or ""),
        "pricingReviewAfter": review_after,
        "pricingReviewRecommended": bool(review_after and time.strftime("%Y-%m-%d") >= review_after),
        "total": _finalize_bucket(total),
        "stages": stages,
    }
