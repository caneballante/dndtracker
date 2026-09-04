import { z } from "zod";

export const postKindSchema = z.enum([
  "session",
  "npc",
  "item",
  "location",
  "lore",
  "note",
]);

export const postStatusSchema = z.enum(["draft", "published", "archived"]);

export const createCampaignSchema = z
  .object({
    name: z.string().trim().min(1).max(160),
    slug: z.string().trim().max(120).default(""),
    eyebrow: z.string().trim().max(160).default("An ongoing campaign"),
    tagline: z.string().trim().max(300).default(""),
    summary: z.string().trim().max(3000).default(""),
    accent: z.enum(["oxblood", "forest", "indigo", "brass"]).default("oxblood"),
  })
  .transform((input) => ({
    ...input,
    slug: safeSlug(input.slug || input.name),
  }))
  .refine((input) => input.slug.length > 0, {
    message: "Campaign name must contain at least one letter or number.",
    path: ["name"],
  });

export const mediaItemSchema = z.object({
  id: z.string().min(1).max(120),
  postId: z.string().min(1).max(120),
  objectKey: z.string().min(1).max(600),
  url: z.string().url().or(z.string().startsWith("/")),
  altText: z.string().max(500).default(""),
  caption: z.string().max(1000).default(""),
  displayOrder: z.number().int().min(0).max(10000),
});

export const createPostSchema = z.object({
  campaignId: z.string().min(1).max(120),
  kind: postKindSchema.default("note"),
  title: z.string().trim().min(1).max(240),
  body: z.string().max(50000).default(""),
  eventDate: z.iso.date(),
  source: z.enum(["manager", "tracker", "maker"]).default("manager"),
  sourceRef: z.string().trim().max(240).default(""),
});

export const updatePostSchema = z
  .object({
    campaignId: z.string().min(1).max(120).optional(),
    kind: postKindSchema.optional(),
    status: postStatusSchema.optional(),
    title: z.string().trim().min(1).max(240).optional(),
    body: z.string().max(50000).optional(),
    eventDate: z.iso.date().optional(),
    displayOrder: z
      .number()
      .int()
      .min(-2147483648)
      .max(2147483647)
      .optional(),
    pinned: z.boolean().optional(),
    media: z.array(mediaItemSchema).max(24).optional(),
  })
  .strict();

export const ingestPostSchema = createPostSchema
  .omit({ source: true, campaignId: true })
  .extend({
    campaignId: z.string().min(1).max(120).optional(),
    campaignSlug: z.string().trim().min(1).max(120).optional(),
    sourceRef: z.string().trim().min(1).max(240),
    media: z
      .array(
        z.object({
          url: z.string().url(),
          objectKey: z.string().max(600).default(""),
          altText: z.string().max(500).default(""),
          caption: z.string().max(1000).default(""),
        }),
      )
      .max(24)
      .default([]),
  })
  .refine((value) => value.campaignId || value.campaignSlug, {
    message: "campaignId or campaignSlug is required",
    path: ["campaignId"],
  });

export const uploadRequestSchema = z.object({
  postId: z.string().min(1).max(120),
  fileName: z.string().trim().min(1).max(255),
  contentType: z.enum([
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
  ]),
  size: z.number().int().positive().max(12 * 1024 * 1024),
});

export const reorderSchema = z.object({
  campaignId: z.string().min(1).max(120),
  postIds: z.array(z.string().min(1).max(120)).min(1).max(500),
});

export function safeSlug(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}
