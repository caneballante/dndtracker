import type { SessionMemoryPublishPayload } from "@/lib/session-memory";

function words(value: string): string {
  return value.replaceAll("_", " ");
}

export function sessionMemoryDisplayModel(
  payload: SessionMemoryPublishPayload,
) {
  return {
    revision: payload.memory.revision,
    publishedAt: payload.publication.publishedAt,
    worldName: payload.world?.name ?? null,
    events: payload.memory.events.map((event) => ({
      ...event,
      typeLabel: words(event.type),
      statusLabel: words(event.status),
      importanceLabel: words(event.importance),
      confidenceLabel: words(event.confidence),
    })),
    highlights: payload.memory.highlights.map((highlight) => ({
      ...highlight,
      categoryLabels: highlight.categories.map(words),
      confidenceLabel: words(highlight.confidence),
    })),
  };
}
