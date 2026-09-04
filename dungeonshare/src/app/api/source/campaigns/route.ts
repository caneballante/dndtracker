import {
  accessErrorResponse,
  requireIngestSource,
} from "@/lib/auth";
import { ingestOptionsResponse, withIngestCors } from "@/lib/cors";
import { listCampaigns } from "@/lib/journal";

export async function GET(request: Request) {
  const respond = (body: unknown, init?: ResponseInit) =>
    withIngestCors(Response.json(body, init), request);
  try {
    requireIngestSource(request);
    const campaigns = (await listCampaigns()).map(({ id, slug, name }) => ({
      id,
      slug,
      name,
    }));
    return respond({ ok: true, campaigns });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return withIngestCors(accessResponse, request);
    return respond(
      { ok: false, error: "Unable to load campaigns." },
      { status: 500 },
    );
  }
}

export function OPTIONS(request: Request) {
  return ingestOptionsResponse(request);
}
