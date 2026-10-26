import assert from "node:assert/strict";
import test from "node:test";

import { betterAuth } from "better-auth";
import { memoryAdapter } from "better-auth/adapters/memory";

import { previewTrustedOrigins } from "../src/lib/auth-origins.ts";

const productionOrigin = "https://dungeonshare.vercel.app";
const previewHost = "dungeonshare-test-preview.vercel.app";
const previewOrigin = `https://${previewHost}`;
const credentials = {
  email: "manager@example.test",
  password: "local-fixture-password-only",
};

test("only the current preview origin is added", () => {
  assert.deepEqual(
    previewTrustedOrigins({ VERCEL_ENV: "preview", VERCEL_URL: previewHost }),
    [previewOrigin],
  );
});

test("production and development never add preview origins", () => {
  for (const environment of ["production", "development", undefined]) {
    assert.deepEqual(
      previewTrustedOrigins({ VERCEL_ENV: environment, VERCEL_URL: previewHost }),
      [],
    );
  }
});

test("missing deployment URLs fail closed", () => {
  assert.deepEqual(previewTrustedOrigins({ VERCEL_ENV: "preview" }), []);
  assert.deepEqual(
    previewTrustedOrigins({ VERCEL_ENV: "preview", VERCEL_URL: "" }),
    [],
  );
});

async function fixture(trustedOrigins) {
  const auth = betterAuth({
    baseURL: productionOrigin,
    secret: "isolated-auth-origin-fixture-secret-not-for-deployment",
    database: memoryAdapter({ user: [], account: [], session: [], verification: [] }),
    trustedOrigins,
    emailAndPassword: { enabled: true },
    rateLimit: { enabled: false },
    logger: { disabled: true },
    telemetry: { enabled: false },
    advanced: { useSecureCookies: true },
  });
  await auth.api.signUpEmail({
    body: { ...credentials, name: "Isolated Test Manager" },
  });
  return auth;
}

function signIn(auth, origin, password = credentials.password) {
  return auth.handler(
    new Request(`${previewOrigin}/api/auth/sign-in/email`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
      },
      body: JSON.stringify({ ...credentials, password }),
    }),
  );
}

test("the original origin mismatch reproduces before the fix", async () => {
  const auth = await fixture([]);
  const response = await signIn(auth, previewOrigin);
  assert.equal(response.status, 403);
  assert.equal((await response.json()).code, "INVALID_ORIGIN");
});

test("preview password sign-in succeeds without trusting other sites", async () => {
  const auth = await fixture(
    previewTrustedOrigins({ VERCEL_ENV: "preview", VERCEL_URL: previewHost }),
  );
  const response = await signIn(auth, previewOrigin);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("set-cookie"), /HttpOnly/i);
  assert.match(response.headers.get("set-cookie"), /Secure/i);

  const badPassword = await signIn(auth, previewOrigin, "incorrect-test-password");
  assert.equal(badPassword.status, 401);

  for (const origin of [
    "https://unrelated.example.test",
    "https://other-project.vercel.app",
    "https://dungeonshare-another-preview.vercel.app",
  ]) {
    const rejected = await signIn(auth, origin);
    assert.equal(rejected.status, 403);
    assert.equal((await rejected.json()).code, "INVALID_ORIGIN");
  }
});

test("production retains its original origin restrictions", async () => {
  const auth = await fixture(
    previewTrustedOrigins({ VERCEL_ENV: "production", VERCEL_URL: previewHost }),
  );
  assert.equal((await signIn(auth, productionOrigin)).status, 200);
  const rejected = await signIn(auth, previewOrigin);
  assert.equal(rejected.status, 403);
  assert.equal((await rejected.json()).code, "INVALID_ORIGIN");
});
