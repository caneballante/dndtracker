import {
  accessErrorResponse,
  requireManager,
} from "@/lib/auth";
import { reorderPosts } from "@/lib/journal";
import { reorderSchema } from "@/lib/validation";

export async function POST(request: Request) {
  try {
    await requireManager();
    const input = reorderSchema.parse(await request.json());
    await reorderPosts(input.campaignId, input.postIds);
    return Response.json({ ok: true });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return accessResponse;
    const message = error instanceof Error ? error.message : "Unable to reorder posts.";
    return Response.json({ ok: false, error: message }, { status: 400 });
  }
}
