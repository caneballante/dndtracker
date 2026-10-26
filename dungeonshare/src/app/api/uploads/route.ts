import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";

import {
  accessErrorResponse,
  requireIngestSource,
  requireManager,
} from "@/lib/auth";
import { ingestOptionsResponse, withIngestCors } from "@/lib/cors";
import { uploadRequestSchema } from "@/lib/validation";

async function authorize(request: Request) {
  if (request.headers.get("authorization")?.startsWith("Bearer ")) {
    return requireIngestSource(request);
  }
  const access = await requireManager();
  return access.user.email;
}

export async function POST(request: Request) {
  const respond = (body: unknown, init?: ResponseInit) =>
    withIngestCors(Response.json(body, init), request);
  try {
    const body = (await request.json()) as HandleUploadBody;
    const response = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname, clientPayload) => {
        await authorize(request);
        const input = uploadRequestSchema.parse(
          JSON.parse(clientPayload ?? "{}"),
        );
        const expectedPrefix = `journal/${input.postId}/`;
        if (!pathname.startsWith(expectedPrefix)) {
          throw new Error("The upload path does not match this journal entry.");
        }

        return {
          allowedContentTypes: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
          ],
          maximumSizeInBytes: 12 * 1024 * 1024,
          addRandomSuffix: true,
          tokenPayload: JSON.stringify({
            postId: input.postId,
            requestedBy: await authorize(request),
          }),
        };
      },
      onUploadCompleted: async () => {
        // The manager saves the returned Blob URL with the rest of the entry.
      },
    });
    return respond(response);
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return withIngestCors(accessResponse, request);
    const message =
      error instanceof Error ? error.message : "Unable to prepare photo upload.";
    return respond({ ok: false, error: message }, { status: 400 });
  }
}

export function OPTIONS(request: Request) {
  return ingestOptionsResponse(request);
}
