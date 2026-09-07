import { ZodError } from "zod";

import {
  AccessDeniedError,
  accessErrorResponse,
  requireIngestSource,
} from "@/lib/auth";
import { ingestOptionsResponse, withIngestCors } from "@/lib/cors";
import {
  findCampaignIdBySlug,
  upsertPublishedSessionMemory,
} from "@/lib/journal";
import { sessionMemoryPublishSchema } from "@/lib/session-memory";

export async function POST(request: Request) {
  const respond = (body: unknown, init?: ResponseInit) =>
    withIngestCors(Response.json(body, init), request);

  try {
    const source = requireIngestSource(request);
    if (source !== "tracker") {
      throw new AccessDeniedError(
        "Session Memory publishing is available only to DungeonTracker.",
        403,
      );
    }

    const input = sessionMemoryPublishSchema.parse(await request.json());
    const campaignId = await findCampaignIdBySlug(
      input.campaign.dungeonShareSlug,
    );
    if (!campaignId) {
      return respond(
        { ok: false, error: "Campaign not found." },
        { status: 404 },
      );
    }

    const result = await upsertPublishedSessionMemory(campaignId, input);
    return respond(
      {
        ok: true,
        action: result.action,
        state: "published",
        post: result.post,
        acceptedSchemaVersion: input.schemaVersion,
        memoryRevision: input.memory.revision,
      },
      { status: result.action === "created" ? 201 : 200 },
    );
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return withIngestCors(accessResponse, request);
    if (error instanceof ZodError) {
      const issue = error.issues[0];
      const location = issue?.path.length ? issue.path.join(".") : "payload";
      return respond(
        {
          ok: false,
          error: `Invalid Session Memory payload at ${location}: ${issue?.message ?? "validation failed"}`,
        },
        { status: 400 },
      );
    }
    if (error instanceof SyntaxError) {
      return respond(
        { ok: false, error: "Session Memory payload must be valid JSON." },
        { status: 400 },
      );
    }
    return respond(
      { ok: false, error: "DungeonShare could not store Session Memory." },
      { status: 500 },
    );
  }
}

export function OPTIONS(request: Request) {
  return ingestOptionsResponse(request);
}
