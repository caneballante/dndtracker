import { randomUUID } from "node:crypto";

import {
  accessErrorResponse,
  requireIngestSource,
} from "@/lib/auth";
import {
  createPost,
  findCampaignIdBySlug,
  getPostBySourceRef,
  updatePost,
} from "@/lib/journal";
import { ingestOptionsResponse, withIngestCors } from "@/lib/cors";
import { ingestPostSchema } from "@/lib/validation";

export async function POST(request: Request) {
  const respond = (body: unknown, init?: ResponseInit) =>
    withIngestCors(Response.json(body, init), request);
  try {
    const source = requireIngestSource(request);
    const input = ingestPostSchema.parse(await request.json());
    const campaignId =
      input.campaignId ||
      (input.campaignSlug
        ? await findCampaignIdBySlug(input.campaignSlug)
        : null);
    if (!campaignId) {
      return respond(
        { ok: false, error: "Campaign not found." },
        { status: 404 },
      );
    }

    const existing = await getPostBySourceRef(source, input.sourceRef);
    if (existing && existing.status !== "draft") {
      return respond(
        {
          ok: false,
          error:
            "This source item has already left the draft inbox. Create a manual copy before replacing it.",
        },
        { status: 409 },
      );
    }
    const post = existing
      ? await updatePost(
          existing.id,
          {
            campaignId,
            kind: input.kind,
            title: input.title,
            body: input.body,
            eventDate: input.eventDate,
          },
          `${source}:${input.sourceRef}`,
        )
      : await createPost({
          campaignId,
          kind: input.kind,
          title: input.title,
          body: input.body,
          eventDate: input.eventDate,
          source,
          sourceRef: input.sourceRef,
        });

    const media = input.media.map((item, index) => ({
      id: randomUUID(),
      postId: post.id,
      objectKey: item.objectKey,
      url: item.url,
      altText: item.altText,
      caption: item.caption,
      displayOrder: index,
    }));
    const completed = media.length
      ? await updatePost(post.id, { media }, `${source}:${input.sourceRef}`)
      : post;

    return respond(
      {
        ok: true,
        post: completed,
        state: "draft",
        action: existing ? "updated" : "created",
      },
      { status: existing ? 200 : 201 },
    );
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return withIngestCors(accessResponse, request);
    const message = error instanceof Error ? error.message : "Unable to ingest post.";
    return respond({ ok: false, error: message }, { status: 400 });
  }
}

export function OPTIONS(request: Request) {
  return ingestOptionsResponse(request);
}
