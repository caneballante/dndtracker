import { sessionMemorySourceRef } from "./session-memory.ts";
import type { SessionMemoryPublishPayload } from "@/lib/session-memory";
import type { Campaign, JournalPost } from "@/lib/types";

export function materializePublishedSessionMemoryPost({
  campaign,
  payload,
  existing,
  id,
  displayOrder,
  now,
}: {
  campaign: Pick<Campaign, "id" | "slug" | "name">;
  payload: SessionMemoryPublishPayload;
  existing: JournalPost | null;
  id: string;
  displayOrder: number;
  now: string;
}): JournalPost {
  return {
    id: existing?.id ?? id,
    campaignId: campaign.id,
    campaignSlug: campaign.slug,
    campaignName: campaign.name,
    kind: "session",
    status: "published",
    title: payload.session.title,
    body: existing?.body ?? "",
    eventDate: payload.session.date,
    displayOrder: existing?.displayOrder ?? displayOrder,
    pinned: existing?.pinned ?? false,
    source: "tracker",
    sourceRef: sessionMemorySourceRef(payload),
    sessionMemory: payload,
    media: existing?.media ?? [],
    publishedAt: payload.publication.publishedAt,
    archivedAt: null,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };
}
