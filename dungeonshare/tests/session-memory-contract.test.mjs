import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  legacySessionSummarySourceRef,
  managerPatchMutatesTrackerMemory,
  selectSessionMemoryUpsertTarget,
  sessionMemoryPublishSchema,
  sessionMemorySourceRef,
} from "../src/lib/session-memory.ts";
import { sessionMemoryDisplayModel } from "../src/lib/session-memory-view.ts";
import { materializePublishedSessionMemoryPost } from "../src/lib/session-memory-post.ts";

const fixtureUrl = new URL("./fixtures/session-memory-v1.json", import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, "utf8"));

test("the representative version-1 fixture validates", () => {
  const parsed = sessionMemoryPublishSchema.parse(fixture);
  assert.deepEqual(parsed, fixture);
  assert.equal(parsed.memory.revision, 3);
});

test("unknown fields are rejected at every contract level", () => {
  assert.equal(
    sessionMemoryPublishSchema.safeParse({ ...fixture, transcript: "secret" })
      .success,
    false,
  );

  const eventWithUnknownField = structuredClone(fixture);
  eventWithUnknownField.memory.events[0].sourceChunks = [1];
  assert.equal(
    sessionMemoryPublishSchema.safeParse(eventWithUnknownField).success,
    false,
  );
});

test("unsupported versions and malformed items are rejected", () => {
  assert.equal(
    sessionMemoryPublishSchema.safeParse({ ...fixture, schemaVersion: 2 })
      .success,
    false,
  );

  const missingSummary = structuredClone(fixture);
  delete missingSummary.memory.highlights[0].summary;
  assert.equal(sessionMemoryPublishSchema.safeParse(missingSummary).success, false);

  const duplicateEvent = structuredClone(fixture);
  duplicateEvent.memory.events.push(structuredClone(duplicateEvent.memory.events[0]));
  assert.equal(sessionMemoryPublishSchema.safeParse(duplicateEvent).success, false);
});

test("stable identity uses tracker campaign and session IDs", () => {
  const parsed = sessionMemoryPublishSchema.parse(fixture);
  assert.equal(
    sessionMemorySourceRef(parsed),
    "session-memory:campaign-three-friends:1788720000000",
  );
  assert.equal(
    legacySessionSummarySourceRef(parsed.session.id),
    "session-1788720000000-summary",
  );
});

test("upsert targeting prefers stable identity and adopts legacy otherwise", () => {
  assert.deepEqual(selectSessionMemoryUpsertTarget("stable", "legacy"), {
    existing: "stable",
    adoptedLegacy: false,
  });
  assert.deepEqual(selectSessionMemoryUpsertTarget(null, "legacy"), {
    existing: "legacy",
    adoptedLegacy: true,
  });
  assert.deepEqual(selectSessionMemoryUpsertTarget(null, null), {
    existing: null,
    adoptedLegacy: false,
  });
});

test("republishing materializes one published post and preserves its identity", () => {
  const parsed = sessionMemoryPublishSchema.parse(fixture);
  const campaign = {
    id: "dungeonshare-campaign-id",
    slug: "three-friends",
    name: "The Three Friends",
  };
  const legacy = {
    id: "existing-post-id",
    campaignId: campaign.id,
    campaignSlug: campaign.slug,
    campaignName: campaign.name,
    kind: "session",
    status: "published",
    title: "Legacy recap",
    body: "The old recap remains in the revision snapshot.",
    eventDate: "2026-09-05",
    displayOrder: 2048,
    pinned: true,
    source: "tracker",
    sourceRef: "session-1788720000000-summary",
    sessionMemory: null,
    media: [],
    publishedAt: "2026-09-05T19:45:00.000Z",
    archivedAt: null,
    createdAt: "2026-09-05T19:45:00.000Z",
    updatedAt: "2026-09-05T19:45:00.000Z",
  };
  const updated = materializePublishedSessionMemoryPost({
    campaign,
    payload: parsed,
    existing: legacy,
    id: "unused-new-id",
    displayOrder: 9999,
    now: "2026-09-06T19:46:00.000Z",
  });

  assert.equal(updated.id, legacy.id);
  assert.equal(updated.status, "published");
  assert.equal(
    updated.sourceRef,
    "session-memory:campaign-three-friends:1788720000000",
  );
  assert.equal(updated.sessionMemory.memory.revision, 3);
  assert.equal(updated.displayOrder, legacy.displayOrder);
  assert.equal(updated.pinned, true);
});

test("manager content patches cannot mutate tracker-owned Session Memory", () => {
  const parsed = sessionMemoryPublishSchema.parse(fixture);
  const post = { source: "tracker", sessionMemory: parsed };
  assert.equal(managerPatchMutatesTrackerMemory(post, { title: "Changed" }), true);
  assert.equal(managerPatchMutatesTrackerMemory(post, { body: "Changed" }), true);
  assert.equal(managerPatchMutatesTrackerMemory(post, { status: "archived" }), false);
  assert.equal(
    managerPatchMutatesTrackerMemory(
      { source: "manager", sessionMemory: parsed },
      { title: "Allowed" },
    ),
    false,
  );
});

test("display model keeps structured Highlights before Events", () => {
  const parsed = sessionMemoryPublishSchema.parse(fixture);
  const model = sessionMemoryDisplayModel(parsed);
  assert.equal(model.highlights[0].summary, fixture.memory.highlights[0].summary);
  assert.deepEqual(model.highlights[0].categoryLabels, [
    "memorable action",
    "character moment",
  ]);
  assert.equal(model.events[0].summary, fixture.memory.events[0].summary);
  assert.deepEqual(model.events[0].facts, fixture.memory.events[0].facts);
  assert.equal(model.revision, 3);
});
