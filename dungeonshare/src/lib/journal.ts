import { randomUUID } from "node:crypto";

import "server-only";

import { demoCampaigns, demoPosts } from "@/lib/demo-data";
import { getSql, hasDatabase } from "@/lib/db";
import type {
  Campaign,
  CampaignDraftInput,
  JournalPost,
  MediaItem,
  PostDraftInput,
  PostUpdateInput,
} from "@/lib/types";

type DemoStore = {
  campaigns: Campaign[];
  posts: JournalPost[];
};

declare global {
  var __dungeonShareDemoStore: DemoStore | undefined;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function demoStore(): DemoStore {
  if (!globalThis.__dungeonShareDemoStore) {
    globalThis.__dungeonShareDemoStore = {
      campaigns: clone(demoCampaigns),
      posts: clone(demoPosts),
    };
  }
  return globalThis.__dungeonShareDemoStore;
}

function iso(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string" && value) return new Date(value).toISOString();
  return new Date().toISOString();
}

function nullableIso(value: unknown): string | null {
  if (!value) return null;
  return iso(value);
}

function dateOnly(value: unknown): string {
  if (typeof value === "string") {
    const datePrefix = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
    if (datePrefix) return datePrefix;
  }

  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    const year = String(value.getFullYear()).padStart(4, "0");
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  throw new TypeError("Expected a valid database date");
}

function mapCampaign(row: Record<string, unknown>): Campaign {
  return {
    id: String(row.id),
    slug: String(row.slug),
    name: String(row.name),
    eyebrow: String(row.eyebrow ?? ""),
    tagline: String(row.tagline ?? ""),
    summary: String(row.summary ?? ""),
    coverUrl: row.cover_url ? String(row.cover_url) : null,
    accent: (row.accent as Campaign["accent"]) ?? "oxblood",
    publishedPostCount: Number(row.published_post_count ?? 0),
    updatedAt: iso(row.updated_at),
  };
}

function mapMedia(row: Record<string, unknown>): MediaItem {
  return {
    id: String(row.id),
    postId: String(row.post_id),
    objectKey: String(row.object_key ?? ""),
    url: String(row.url),
    altText: String(row.alt_text ?? ""),
    caption: String(row.caption ?? ""),
    displayOrder: Number(row.display_order ?? 0),
  };
}

function mapPost(
  row: Record<string, unknown>,
  media: MediaItem[],
): JournalPost {
  return {
    id: String(row.id),
    campaignId: String(row.campaign_id),
    campaignSlug: String(row.campaign_slug),
    campaignName: String(row.campaign_name),
    kind: row.kind as JournalPost["kind"],
    status: row.status as JournalPost["status"],
    title: String(row.title),
    body: String(row.body ?? ""),
    eventDate: dateOnly(row.event_date),
    displayOrder: Number(row.display_order ?? 0),
    pinned: Boolean(row.pinned),
    source: row.source as JournalPost["source"],
    sourceRef: String(row.source_ref ?? ""),
    media,
    publishedAt: nullableIso(row.published_at),
    archivedAt: nullableIso(row.archived_at),
    createdAt: iso(row.created_at),
    updatedAt: iso(row.updated_at),
  };
}

async function queryPostMedia(postIds: string[]): Promise<MediaItem[]> {
  if (postIds.length === 0) return [];
  const sql = getSql();
  const rows = await sql.query(
    `select id, post_id, object_key, url, alt_text, caption, display_order
       from dungeonshare.media
      where post_id = any($1::uuid[])
      order by display_order asc, created_at asc`,
    [postIds],
  );
  return (rows as Record<string, unknown>[]).map(mapMedia);
}

async function hydratePosts(
  rows: Record<string, unknown>[],
): Promise<JournalPost[]> {
  const media = await queryPostMedia(rows.map((row) => String(row.id)));
  const byPost = new Map<string, MediaItem[]>();
  for (const item of media) {
    const current = byPost.get(item.postId) ?? [];
    current.push(item);
    byPost.set(item.postId, current);
  }
  return rows.map((row) => mapPost(row, byPost.get(String(row.id)) ?? []));
}

export function isDemoData(): boolean {
  return !hasDatabase();
}

export async function createCampaign(
  input: CampaignDraftInput,
): Promise<Campaign> {
  const existing = await getCampaignBySlug(input.slug);
  if (existing) {
    throw new Error("A campaign with this name or web address already exists.");
  }

  if (!hasDatabase()) {
    const campaign: Campaign = {
      id: randomUUID(),
      ...input,
      coverUrl: null,
      publishedPostCount: 0,
      updatedAt: new Date().toISOString(),
    };
    demoStore().campaigns.unshift(campaign);
    return clone(campaign);
  }

  const sql = getSql();
  const rows = await sql`
    insert into dungeonshare.campaigns (
      slug, name, eyebrow, tagline, summary, accent
    )
    values (
      ${input.slug},
      ${input.name},
      ${input.eyebrow},
      ${input.tagline},
      ${input.summary},
      ${input.accent}
    )
    returning *, 0::int as published_post_count
  `;
  return mapCampaign(
    (rows as unknown as Record<string, unknown>[])[0],
  );
}

export async function listCampaigns(): Promise<Campaign[]> {
  if (!hasDatabase()) return clone(demoStore().campaigns);
  const sql = getSql();
  const rows = await sql`
    select
      c.*,
      count(p.id) filter (where p.status = 'published')::int as published_post_count
    from dungeonshare.campaigns c
    left join dungeonshare.posts p on p.campaign_id = c.id
    where c.is_active = true
    group by c.id
    order by c.updated_at desc
  `;
  return (rows as Record<string, unknown>[]).map(mapCampaign);
}

export async function getCampaignBySlug(
  slug: string,
): Promise<Campaign | null> {
  const campaigns = await listCampaigns();
  return campaigns.find((campaign) => campaign.slug === slug) ?? null;
}

export async function listPublishedPosts(
  campaignId: string,
  order: "newest" | "oldest" = "newest",
): Promise<JournalPost[]> {
  if (!hasDatabase()) {
    const direction = order === "newest" ? -1 : 1;
    return clone(
      demoStore()
        .posts.filter(
          (post) =>
            post.campaignId === campaignId && post.status === "published",
        )
        .sort((a, b) => {
          if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
          const manualOrder =
            (a.displayOrder - b.displayOrder) * direction;
          return (
            manualOrder ||
            a.eventDate.localeCompare(b.eventDate) * direction
          );
        }),
    );
  }

  const sql = getSql();
  const rows = await sql.query(
    `select p.*, c.slug as campaign_slug, c.name as campaign_name
       from dungeonshare.posts p
       join dungeonshare.campaigns c on c.id = p.campaign_id
      where p.campaign_id = $1::uuid
        and p.status = 'published'
      order by p.pinned desc,
               p.display_order ${order === "newest" ? "desc" : "asc"},
               p.event_date ${order === "newest" ? "desc" : "asc"}`,
    [campaignId],
  );
  return hydratePosts(rows as Record<string, unknown>[]);
}

export async function listManagerPosts(): Promise<JournalPost[]> {
  if (!hasDatabase()) {
    return clone(
      demoStore().posts.sort(
        (a, b) =>
          b.eventDate.localeCompare(a.eventDate) ||
          b.displayOrder - a.displayOrder,
      ),
    );
  }

  const sql = getSql();
  const rows = await sql`
    select p.*, c.slug as campaign_slug, c.name as campaign_name
      from dungeonshare.posts p
      join dungeonshare.campaigns c on c.id = p.campaign_id
     order by
       case p.status when 'draft' then 0 when 'published' then 1 else 2 end,
       p.updated_at desc
  `;
  return hydratePosts(rows as Record<string, unknown>[]);
}

export async function findCampaignIdBySlug(
  slug: string,
): Promise<string | null> {
  const campaign = await getCampaignBySlug(slug);
  return campaign?.id ?? null;
}

export async function createPost(input: PostDraftInput): Promise<JournalPost> {
  const now = new Date().toISOString();
  if (!hasDatabase()) {
    const campaign = demoStore().campaigns.find(
      (item) => item.id === input.campaignId,
    );
    if (!campaign) throw new Error("Campaign not found.");
    const dayOrder =
      Math.floor(new Date(`${input.eventDate}T00:00:00Z`).getTime() / 86400000) *
      1024;
    const sameDayCount = demoStore().posts.filter(
      (post) =>
        post.campaignId === input.campaignId &&
        post.eventDate === input.eventDate,
    ).length;
    const post: JournalPost = {
      id: randomUUID(),
      campaignId: campaign.id,
      campaignSlug: campaign.slug,
      campaignName: campaign.name,
      kind: input.kind,
      status: "draft",
      title: input.title,
      body: input.body,
      eventDate: input.eventDate,
      displayOrder: dayOrder + sameDayCount,
      pinned: false,
      source: input.source ?? "manager",
      sourceRef: input.sourceRef ?? "",
      media: [],
      publishedAt: null,
      archivedAt: null,
      createdAt: now,
      updatedAt: now,
    };
    demoStore().posts.unshift(post);
    return clone(post);
  }

  const sql = getSql();
  const rows = await sql`
    insert into dungeonshare.posts (
      campaign_id, kind, status, title, body, event_date,
      display_order, source, source_ref
    )
    values (
      ${input.campaignId}::uuid,
      ${input.kind},
      'draft',
      ${input.title},
      ${input.body},
      ${input.eventDate}::date,
      (
        (${input.eventDate}::date - date '1970-01-01') * 1024 +
        (
          select count(*)::int
          from dungeonshare.posts
         where campaign_id = ${input.campaignId}::uuid
           and event_date = ${input.eventDate}::date
        )
      ),
      ${input.source ?? "manager"},
      ${input.sourceRef ?? ""}
    )
    on conflict (source, source_ref)
      where source in ('tracker', 'maker') and source_ref <> ''
    do update set updated_at = dungeonshare.posts.updated_at
    returning id
  `;
  const id = String(
    ((rows as unknown as Record<string, unknown>[])[0]).id,
  );
  const created = await getPostById(id);
  if (!created) throw new Error("Post could not be loaded after creation.");
  return created;
}

export async function getPostById(id: string): Promise<JournalPost | null> {
  if (!hasDatabase()) {
    return clone(demoStore().posts.find((post) => post.id === id) ?? null);
  }
  const sql = getSql();
  const rows = await sql`
    select p.*, c.slug as campaign_slug, c.name as campaign_name
      from dungeonshare.posts p
      join dungeonshare.campaigns c on c.id = p.campaign_id
     where p.id = ${id}::uuid
     limit 1
  `;
  const postRows = rows as unknown as Record<string, unknown>[];
  if (!postRows[0]) return null;
  return (await hydratePosts(postRows))[0];
}

export async function getPostBySourceRef(
  source: Extract<JournalPost["source"], "tracker" | "maker">,
  sourceRef: string,
): Promise<JournalPost | null> {
  if (!hasDatabase()) {
    return clone(
      demoStore().posts.find(
        (post) => post.source === source && post.sourceRef === sourceRef,
      ) ?? null,
    );
  }
  const sql = getSql();
  const rows = await sql`
    select p.*, c.slug as campaign_slug, c.name as campaign_name
      from dungeonshare.posts p
      join dungeonshare.campaigns c on c.id = p.campaign_id
     where p.source = ${source}
       and p.source_ref = ${sourceRef}
     limit 1
  `;
  const postRows = rows as unknown as Record<string, unknown>[];
  if (!postRows[0]) return null;
  return (await hydratePosts(postRows))[0];
}

export async function updatePost(
  id: string,
  patch: PostUpdateInput,
  actor = "manager",
): Promise<JournalPost> {
  const current = await getPostById(id);
  if (!current) throw new Error("Post not found.");
  const publishedAt =
    patch.status === undefined
      ? current.publishedAt
      : patch.status === "published"
        ? (current.publishedAt ?? new Date().toISOString())
        : null;
  const archivedAt =
    patch.status === undefined
      ? current.archivedAt
      : patch.status === "archived"
        ? new Date().toISOString()
        : null;

  const next: JournalPost = {
    ...current,
    ...patch,
    media: patch.media ?? current.media,
    publishedAt,
    archivedAt,
    updatedAt: new Date().toISOString(),
  };

  if (!hasDatabase()) {
    const index = demoStore().posts.findIndex((post) => post.id === id);
    demoStore().posts[index] = clone(next);
    return clone(next);
  }

  const sql = getSql();
  await sql`
    insert into dungeonshare.post_revisions (post_id, actor, snapshot)
    values (${id}::uuid, ${actor}, ${JSON.stringify(current)}::jsonb)
  `;
  await sql`
    update dungeonshare.posts
       set campaign_id = ${next.campaignId}::uuid,
           kind = ${next.kind},
           status = ${next.status},
           title = ${next.title},
           body = ${next.body},
           event_date = ${next.eventDate}::date,
           display_order = ${next.displayOrder},
           pinned = ${next.pinned},
           published_at = ${next.publishedAt}::timestamptz,
           archived_at = ${next.archivedAt}::timestamptz,
           updated_at = now()
     where id = ${id}::uuid
  `;

  if (patch.media) {
    await sql`delete from dungeonshare.media where post_id = ${id}::uuid`;
    for (const item of patch.media) {
      await sql`
        insert into dungeonshare.media (
          id, post_id, object_key, url, alt_text, caption, display_order
        )
        values (
          ${item.id}::uuid,
          ${id}::uuid,
          ${item.objectKey},
          ${item.url},
          ${item.altText},
          ${item.caption},
          ${item.displayOrder}
        )
      `;
    }
  }

  const updated = await getPostById(id);
  if (!updated) throw new Error("Post could not be loaded after update.");
  return updated;
}

export async function reorderPosts(
  campaignId: string,
  postIds: string[],
): Promise<void> {
  if (!hasDatabase()) {
    postIds.forEach((id, index) => {
      const post = demoStore().posts.find(
        (item) => item.id === id && item.campaignId === campaignId,
      );
      if (post) post.displayOrder = (postIds.length - index) * 1024;
    });
    return;
  }
  const sql = getSql();
  for (const [index, id] of postIds.entries()) {
    await sql`
      update dungeonshare.posts
         set display_order = ${(postIds.length - index) * 1024},
             updated_at = now()
       where id = ${id}::uuid
         and campaign_id = ${campaignId}::uuid
    `;
  }
}
