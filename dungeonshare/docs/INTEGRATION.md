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

- create a draft;
- upload a supported image.

Only a signed-in, allowlisted manager can edit, publish, unpublish, reorder, or
archive content. Removed photo references are retained in Blob storage so an
accidental replacement does not immediately destroy the original file.
