# Dungeon Maker and DnD Tracker integration

Both source apps use the deployed Dungeon Share URL. Give each app only its own
token:

- DnD Tracker: `DUNGEONSHARE_TRACKER_TOKEN`
- Dungeon Maker: `DUNGEONSHARE_MAKER_TOKEN`

Add each local app's exact origin to
`DUNGEONSHARE_ALLOWED_INGEST_ORIGINS`. The DnD Tracker default is
`http://127.0.0.1:8000`.

## Send a text entry

`POST https://dungeonshare.vercel.app/api/ingest`

```json
{
  "campaignSlug": "three-friends",
  "kind": "session",
  "title": "The Bell Beneath Briar Hollow",
  "body": "The party followed the bell into the flooded crypt...",
  "eventDate": "2026-07-25",
  "sourceRef": "tracker-session-2026-07-25",
  "media": []
}
```

Headers:

```text
Authorization: Bearer <the source app's token>
Content-Type: application/json
```

`sourceRef` must be a stable unique ID from the source app. Repeating the same
source and reference does not create another entry.

Kinds are `session`, `npc`, `item`, `location`, `lore`, and `note`.

## Publish canonical Session Memory

DungeonTracker publishes a reviewed Session Memory snapshot directly to:

`POST https://dungeonshare.vercel.app/api/session-memory`

Use the same `DUNGEONSHARE_TRACKER_TOKEN` bearer header. The request body is
the strict version-1 contract represented by
`tests/fixtures/session-memory-v1.json`. Unknown fields, malformed records,
and unsupported schema versions are rejected.

The consumer derives identity from the tracker campaign ID plus session ID.
Publishing that identity again updates the existing published post instead of
creating a duplicate. On first publish, an older tracker post with the legacy
`session-<sessionId>-summary` source reference is adopted in place when one
exists.

Unlike the generic ingest route, this endpoint publishes immediately. Its
authority is narrowly limited to structured Session Memory sent with the
tracker token. DungeonShare stores the reviewed snapshot and its publication
revision; canonical editing and edit history remain in DungeonTracker.

## Upload a photo from a browser app

Install `@vercel/blob` in the source app and use its client uploader. The custom
authorization header is checked before Dungeon Share issues the short-lived
upload token.

```ts
import { upload } from "@vercel/blob/client";

const token = "<the source app's token>";
const postId = "<draft post id>";
const blob = await upload(`journal/${postId}/${file.name}`, file, {
  access: "public",
  handleUploadUrl: "https://dungeonshare.vercel.app/api/uploads",
  headers: {
    Authorization: `Bearer ${token}`,
  },
  clientPayload: JSON.stringify({
    postId,
    fileName: file.name,
    contentType: file.type,
    size: file.size,
  }),
  multipart: file.size > 5 * 1024 * 1024,
});
```

Use `blob.pathname` as `objectKey` and `blob.url` as `url` in a subsequent
ingest request. For the smoothest Dungeon Maker workflow, first create the text
draft, upload photos using the returned `post.id`, then send one final version
using the same `sourceRef`.

The current upload limit is 12 MB per JPEG, PNG, WebP, or GIF.

## Safety boundary

The source endpoints can only:

- create a generic draft;
- publish or update a tracker-owned structured Session Memory snapshot;
- upload a supported image.

The manager cannot change the title, date, or structured content of a
tracker-owned Session Memory snapshot. Correct it in DungeonTracker and
republish. Existing generic entries retain the manager editing workflow.

Only a signed-in, allowlisted manager can edit, publish, unpublish, reorder, or
archive content. Removed photo references are retained in Blob storage so an
accidental replacement does not immediately destroy the original file.
