import { timingSafeEqual } from "node:crypto";

import "server-only";

import { passkey } from "@better-auth/passkey";
import { betterAuth } from "better-auth";
import { APIError, createAuthMiddleware } from "better-auth/api";
import { Kysely, PostgresDialect } from "kysely";
import { headers } from "next/headers";
import { Pool } from "pg";

import type { ManagerAccess } from "@/lib/types";

export class AccessDeniedError extends Error {
  status: number;

  constructor(message: string, status = 401) {
    super(message);
    this.name = "AccessDeniedError";
    this.status = status;
  }
}

export function authIsConfigured(): boolean {
  return Boolean(
    process.env.DATABASE_URL &&
      process.env.BETTER_AUTH_SECRET &&
      process.env.BETTER_AUTH_URL &&
      process.env.DUNGEONSHARE_ADMIN_EMAILS,
  );
}

export function isLocalDemo(): boolean {
  return process.env.NODE_ENV !== "production" && !process.env.DATABASE_URL;
}

function passkeyOrigin(): string {
  return process.env.BETTER_AUTH_URL ?? "http://localhost:3000";
}

function passkeyRpId(): string {
  try {
    return new URL(passkeyOrigin()).hostname;
  } catch {
    return "localhost";
  }
}

function createAuthInstance() {
  const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 5,
  });
  const database = new Kysely({
    dialect: new PostgresDialect({ pool }),
  }).withSchema("dungeonshare_auth");

  return betterAuth({
    appName: "Dungeon Share",
    database: {
      db: database,
      type: "postgres",
    },
    secret: process.env.BETTER_AUTH_SECRET,
    baseURL: process.env.BETTER_AUTH_URL,
    emailAndPassword: {
      enabled: true,
      disableSignUp:
        process.env.DUNGEONSHARE_ALLOW_ADMIN_SIGNUP !== "true",
    },
    rateLimit: {
      enabled: true,
      window: 60,
      max: 12,
    },
    hooks: {
      before: createAuthMiddleware(async (context) => {
        if (context.path !== "/sign-up/email") return;
        const email = String(context.body?.email ?? "").trim().toLowerCase();
        const candidate =
          context.headers?.get("x-dungeonshare-bootstrap") ?? "";
        const expected = process.env.DUNGEONSHARE_BOOTSTRAP_TOKEN ?? "";
        const allowed =
          process.env.DUNGEONSHARE_ALLOW_ADMIN_SIGNUP === "true" &&
          adminEmails().has(email) &&
          expected.length >= 32 &&
          tokenMatches(candidate, expected);
        if (!allowed) {
          throw new APIError("FORBIDDEN", {
            message: "Administrator setup is not authorized.",
          });
        }
      }),
    },
    plugins: [
      passkey({
        rpID: passkeyRpId(),
        rpName: "Dungeon Share",
        origin: passkeyOrigin(),
      }),
    ],
    advanced: {
      cookiePrefix: "dungeonshare",
      useSecureCookies: process.env.NODE_ENV === "production",
    },
  });
}

type AuthInstance = ReturnType<typeof createAuthInstance>;
let authInstance: AuthInstance | null = null;

export function getAuth(): AuthInstance {
  if (!authIsConfigured()) {
    throw new Error("Manager authentication is not configured.");
  }
  if (!authInstance) authInstance = createAuthInstance();
  return authInstance;
}

function adminEmails(): Set<string> {
  return new Set(
    String(process.env.DUNGEONSHARE_ADMIN_EMAILS ?? "")
      .split(",")
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean),
  );
}

export async function getManagerAccess(): Promise<ManagerAccess> {
  if (isLocalDemo()) {
    return {
      state: "ready",
      demo: true,
      user: {
        id: "local-demo-admin",
        email: "local-demo@dungeonshare.test",
        name: "Local Demo",
      },
    };
  }

  if (!authIsConfigured()) return { state: "unconfigured", demo: false };

  const session = await getAuth().api.getSession({
    headers: await headers(),
  });
  const user = session?.user;
  if (!user?.email) return { state: "signed-out", demo: false };

  if (!adminEmails().has(user.email.toLowerCase())) {
    return { state: "forbidden", demo: false, email: user.email };
  }

  return {
    state: "ready",
    demo: false,
    user: {
      id: user.id,
      email: user.email,
      name: user.name || user.email,
    },
  };
}

export async function requireManager(): Promise<
  Extract<ManagerAccess, { state: "ready" }>
> {
  const access = await getManagerAccess();
  if (access.state === "ready") return access;
  if (access.state === "forbidden") {
    throw new AccessDeniedError("This account is not an administrator.", 403);
  }
  if (access.state === "unconfigured") {
    throw new AccessDeniedError("Manager authentication is not configured.", 503);
  }
  throw new AccessDeniedError("Sign in is required.", 401);
}

function tokenMatches(candidate: string, expected: string): boolean {
  const a = Buffer.from(candidate);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function requireIngestSource(
  request: Request,
): "tracker" | "maker" {
  const authorization = request.headers.get("authorization") ?? "";
  const candidate = authorization.startsWith("Bearer ")
    ? authorization.slice(7).trim()
    : "";

  const configured = [
    {
      source: "tracker" as const,
      token: process.env.DUNGEONSHARE_TRACKER_TOKEN ?? "",
    },
    {
      source: "maker" as const,
      token: process.env.DUNGEONSHARE_MAKER_TOKEN ?? "",
    },
  ].filter((item) => item.token.length >= 32);

  const match = configured.find((item) =>
    tokenMatches(candidate, item.token),
  );
  if (!match) {
    throw new AccessDeniedError("A valid publishing token is required.", 401);
  }
  return match.source;
}

export function accessErrorResponse(error: unknown): Response | null {
  if (!(error instanceof AccessDeniedError)) return null;
  return Response.json({ ok: false, error: error.message }, { status: error.status });
}
