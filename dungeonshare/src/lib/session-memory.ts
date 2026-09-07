import { z } from "zod";

export const SESSION_MEMORY_SCHEMA_VERSION = 1 as const;
export const SESSION_MEMORY_KIND = "dungeontracker_session_memory" as const;

function nonBlankText(maxLength: number) {
  return z
    .string()
    .min(1)
    .max(maxLength)
    .refine((value) => value.trim().length > 0, "Text cannot be blank.");
}

const identifierSchema = z
  .string()
  .min(1)
  .max(100)
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9._-]*$/,
    "Identifiers may contain only letters, numbers, dots, underscores, and hyphens.",
  );
const confidenceSchema = z.enum(["unknown", "low", "medium", "high"]);
const eventStatusSchema = z.enum([
  "active",
  "unresolved",
  "resolved",
  "superseded",
]);
const importanceSchema = z.enum(["low", "medium", "high", "critical"]);
const highlightCategorySchema = z.enum([
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
]);

const eventSchema = z
  .object({
    id: identifierSchema,
    type: nonBlankText(80),
    status: eventStatusSchema,
    importance: importanceSchema,
    confidence: confidenceSchema,
    summary: nonBlankText(2000),
    facts: z.array(nonBlankText(1000)).max(50),
    entities: z.array(nonBlankText(240)).max(50),
  })
  .strict();

const highlightSchema = z
  .object({
    id: identifierSchema,
    categories: z.array(highlightCategorySchema).min(1).max(4),
    confidence: confidenceSchema,
    summary: nonBlankText(1200),
    participants: z.array(nonBlankText(240)).max(30),
    relatedEventIds: z.array(identifierSchema).max(20),
  })
  .strict();

export const sessionMemoryPublishSchema = z
  .object({
    schemaVersion: z.literal(SESSION_MEMORY_SCHEMA_VERSION),
    kind: z.literal(SESSION_MEMORY_KIND),
    campaign: z
      .object({
        id: identifierSchema,
        name: nonBlankText(160),
        dungeonShareSlug: z
          .string()
          .min(1)
          .max(120)
          .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
      })
      .strict(),
    world: z
      .object({
        id: identifierSchema,
        name: nonBlankText(160),
      })
      .strict()
      .nullable(),
    session: z
      .object({
        id: identifierSchema,
        title: nonBlankText(240),
        date: z.iso.date(),
      })
      .strict(),
    memory: z
      .object({
        revision: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
        events: z.array(eventSchema).max(200),
        highlights: z.array(highlightSchema).max(100),
      })
      .strict()
      .superRefine((memory, context) => {
        for (const [key, items] of [
          ["events", memory.events],
          ["highlights", memory.highlights],
        ] as const) {
          const seen = new Set<string>();
          items.forEach((item, index) => {
            if (seen.has(item.id)) {
              context.addIssue({
                code: "custom",
                message: `Duplicate ${key} ID.`,
                path: [key, index, "id"],
              });
            }
            seen.add(item.id);
          });
        }
      }),
    publication: z
      .object({
        publishedAt: z.iso.datetime({ offset: true }),
        source: z.literal("DungeonTracker"),
      })
      .strict(),
  })
  .strict();

export type SessionMemoryPublishPayload = z.infer<
  typeof sessionMemoryPublishSchema
>;

export function sessionMemorySourceRef(
  payload: SessionMemoryPublishPayload,
): string {
  return `session-memory:${payload.campaign.id}:${payload.session.id}`;
}

export function legacySessionSummarySourceRef(sessionId: string): string {
  return `session-${sessionId}-summary`;
}

export function selectSessionMemoryUpsertTarget<T>(
  stable: T | null | undefined,
  legacy: T | null | undefined,
): { existing: T | null; adoptedLegacy: boolean } {
  if (stable) return { existing: stable, adoptedLegacy: false };
  if (legacy) return { existing: legacy, adoptedLegacy: true };
  return { existing: null, adoptedLegacy: false };
}

type SessionMemoryOwnedPost = {
  source: string;
  sessionMemory: SessionMemoryPublishPayload | null;
};

const trackerOwnedContentFields = new Set([
  "campaignId",
  "kind",
  "title",
  "body",
  "eventDate",
]);

export function managerPatchMutatesTrackerMemory(
  post: SessionMemoryOwnedPost,
  patch: Record<string, unknown>,
): boolean {
  if (post.source !== "tracker" || !post.sessionMemory) return false;
  return Object.keys(patch).some((key) => trackerOwnedContentFields.has(key));
}
