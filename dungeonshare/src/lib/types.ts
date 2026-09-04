export type CampaignAccent = "oxblood" | "forest" | "indigo" | "brass";

export type Campaign = {
  id: string;
  slug: string;
  name: string;
  eyebrow: string;
  tagline: string;
  summary: string;
  coverUrl: string | null;
  accent: CampaignAccent;
  publishedPostCount: number;
  updatedAt: string;
};

export type CampaignDraftInput = Pick<
  Campaign,
  "slug" | "name" | "eyebrow" | "tagline" | "summary" | "accent"
>;

export type PostKind =
  | "session"
  | "npc"
  | "item"
  | "location"
  | "lore"
  | "note";

export type PostStatus = "draft" | "published" | "archived";

export type MediaItem = {
  id: string;
  postId: string;
  objectKey: string;
  url: string;
  altText: string;
  caption: string;
  displayOrder: number;
};

export type JournalPost = {
  id: string;
  campaignId: string;
  campaignSlug: string;
  campaignName: string;
  kind: PostKind;
  status: PostStatus;
  title: string;
  body: string;
  eventDate: string;
  displayOrder: number;
  pinned: boolean;
  source: "manager" | "tracker" | "maker";
  sourceRef: string;
  media: MediaItem[];
  publishedAt: string | null;
  archivedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PostDraftInput = {
  campaignId: string;
  kind: PostKind;
  title: string;
  body: string;
  eventDate: string;
  source?: JournalPost["source"];
  sourceRef?: string;
};

export type PostUpdateInput = Partial<
  Pick<
    JournalPost,
    | "campaignId"
    | "kind"
    | "status"
    | "title"
    | "body"
    | "eventDate"
    | "displayOrder"
    | "pinned"
    | "media"
  >
>;

export type ManagerAccess =
  | {
      state: "ready";
      user: { id: string; email: string; name: string };
      demo: boolean;
    }
  | { state: "signed-out"; demo: false }
  | { state: "forbidden"; demo: false; email: string }
  | { state: "unconfigured"; demo: false };
