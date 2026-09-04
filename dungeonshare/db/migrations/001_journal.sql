create extension if not exists pgcrypto;
create schema if not exists dungeonshare;

create table if not exists dungeonshare.campaigns (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  eyebrow text not null default '',
  tagline text not null default '',
  summary text not null default '',
  cover_url text,
  accent text not null default 'oxblood'
    check (accent in ('oxblood', 'forest', 'indigo', 'brass')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists dungeonshare.posts (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null
    references dungeonshare.campaigns(id) on delete restrict,
  kind text not null
    check (kind in ('session', 'npc', 'item', 'location', 'lore', 'note')),
  status text not null default 'draft'
    check (status in ('draft', 'published', 'archived')),
  title text not null,
  body text not null default '',
  event_date date not null,
  display_order integer not null default 0,
  pinned boolean not null default false,
  source text not null default 'manager'
    check (source in ('manager', 'tracker', 'maker')),
  source_ref text not null default '',
  published_at timestamptz,
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists dungeonshare.media (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null
    references dungeonshare.posts(id) on delete cascade,
  object_key text not null default '',
  url text not null,
  alt_text text not null default '',
  caption text not null default '',
  display_order integer not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists dungeonshare.post_revisions (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null
    references dungeonshare.posts(id) on delete cascade,
  actor text not null,
  snapshot jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists dungeonshare_posts_public_feed_idx
  on dungeonshare.posts (campaign_id, status, pinned desc, event_date desc, display_order desc);
create index if not exists dungeonshare_posts_manager_idx
  on dungeonshare.posts (status, updated_at desc);
create index if not exists dungeonshare_posts_source_ref_idx
  on dungeonshare.posts (source, source_ref);
create unique index if not exists dungeonshare_posts_ingest_ref_uidx
  on dungeonshare.posts (source, source_ref)
  where source in ('tracker', 'maker') and source_ref <> '';
create index if not exists dungeonshare_media_post_idx
  on dungeonshare.media (post_id, display_order);
create index if not exists dungeonshare_revisions_post_idx
  on dungeonshare.post_revisions (post_id, created_at desc);
