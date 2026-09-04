function allowedOrigins(): Set<string> {
  return new Set(
    String(process.env.DUNGEONSHARE_ALLOWED_INGEST_ORIGINS ?? "")
      .split(",")
      .map((origin) => origin.trim().replace(/\/+$/, ""))
      .filter(Boolean),
  );
}

export function withIngestCors(
  response: Response,
  request: Request,
): Response {
  const origin = request.headers.get("origin")?.replace(/\/+$/, "");
  if (!origin || !allowedOrigins().has(origin)) return response;
  response.headers.set("Access-Control-Allow-Origin", origin);
  response.headers.set("Access-Control-Allow-Headers", "Authorization, Content-Type");
  response.headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  response.headers.append("Vary", "Origin");
  return response;
}

export function ingestOptionsResponse(request: Request): Response {
  const origin = request.headers.get("origin")?.replace(/\/+$/, "");
  if (!origin || !allowedOrigins().has(origin)) {
    return new Response(null, { status: 403 });
  }
  return withIngestCors(new Response(null, { status: 204 }), request);
}
