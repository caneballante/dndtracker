export function previewTrustedOrigins(
  environment: { VERCEL_ENV?: string; VERCEL_URL?: string } = {
    VERCEL_ENV: process.env.VERCEL_ENV,
    VERCEL_URL: process.env.VERCEL_URL,
  },
): string[] {
  if (environment.VERCEL_ENV !== "preview" || !environment.VERCEL_URL) {
    return [];
  }

  return [`https://${environment.VERCEL_URL}`];
}
