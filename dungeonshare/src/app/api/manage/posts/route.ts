import {
  accessErrorResponse,
  requireManager,
} from "@/lib/auth";
import { createPost, listManagerPosts } from "@/lib/journal";
import { createPostSchema } from "@/lib/validation";

export async function GET() {
  try {
    await requireManager();
    return Response.json({ ok: true, posts: await listManagerPosts() });
  } catch (error) {
    return (
      accessErrorResponse(error) ??
      Response.json(
        { ok: false, error: "Unable to load posts." },
        { status: 500 },
      )
    );
  }
}

export async function POST(request: Request) {
  try {
    const access = await requireManager();
    const input = createPostSchema.parse(await request.json());
    const post = await createPost({
      ...input,
      source: "manager",
      sourceRef: access.user.id,
    });
    return Response.json({ ok: true, post }, { status: 201 });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return accessResponse;
    const message = error instanceof Error ? error.message : "Unable to create post.";
    return Response.json({ ok: false, error: message }, { status: 400 });
  }
}
