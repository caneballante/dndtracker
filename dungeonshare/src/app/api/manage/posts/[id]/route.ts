import {
  accessErrorResponse,
  requireManager,
} from "@/lib/auth";
import { updatePost } from "@/lib/journal";
import { updatePostSchema } from "@/lib/validation";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const access = await requireManager();
    const { id } = await context.params;
    const patch = updatePostSchema.parse(await request.json());
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
