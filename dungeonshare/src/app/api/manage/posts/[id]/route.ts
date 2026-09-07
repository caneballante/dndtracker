import {
  accessErrorResponse,
  requireManager,
} from "@/lib/auth";
import { getPostById, updatePost } from "@/lib/journal";
import { managerPatchMutatesTrackerMemory } from "@/lib/session-memory";
import { updatePostSchema } from "@/lib/validation";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const access = await requireManager();
    const { id } = await context.params;
    const patch = updatePostSchema.parse(await request.json());
    const current = await getPostById(id);
    if (!current) {
      return Response.json(
        { ok: false, error: "Post not found." },
        { status: 404 },
      );
    }
    if (managerPatchMutatesTrackerMemory(current, patch)) {
      return Response.json(
        {
          ok: false,
          error:
            "Published Session Memory is owned by DungeonTracker. Correct it there and republish.",
        },
        { status: 409 },
      );
    }
    const post = await updatePost(id, patch, access.user.email);
    return Response.json({ ok: true, post });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return accessResponse;
    const message = error instanceof Error ? error.message : "Unable to update post.";
    const status = message === "Post not found." ? 404 : 400;
    return Response.json({ ok: false, error: message }, { status });
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const access = await requireManager();
    const { id } = await context.params;
    const post = await updatePost(
      id,
      { status: "archived" },
      access.user.email,
    );
    return Response.json({ ok: true, post });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return accessResponse;
    const message = error instanceof Error ? error.message : "Unable to archive post.";
    return Response.json({ ok: false, error: message }, { status: 400 });
  }
}
