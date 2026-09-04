import { getAuth, authIsConfigured } from "@/lib/auth";

function unavailable() {
  return Response.json(
    { error: "Manager authentication is not configured." },
    { status: 503 },
  );
}

async function handle(request: Request) {
  if (!authIsConfigured()) return unavailable();
  return getAuth().handler(request);
}

export const GET = handle;
export const POST = handle;
