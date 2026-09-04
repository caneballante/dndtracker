import { redirect } from "next/navigation";
import Link from "next/link";

import { SignInForm } from "@/components/sign-in-form";
import { getManagerAccess } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function SignInPage() {
  const access = await getManagerAccess();
  if (access.state === "ready") redirect("/manage");

  return (
    <main className="auth-page">
      <div className="auth-backdrop" aria-hidden="true" />
      {access.state === "unconfigured" ? (
        <div className="sign-in-card setup-card">
          <p className="eyebrow">One setup step remains</p>
          <h1>Connect manager authentication</h1>
          <p>
            Add the Neon database and Better Auth environment values in Vercel,
            then return here to create the administrator.
          </p>
          <Link className="button button-primary" href="/">
            Return to the journal
          </Link>
        </div>
      ) : (
        <SignInForm
          allowSignup={
            process.env.DUNGEONSHARE_ALLOW_ADMIN_SIGNUP === "true" &&
            Boolean(process.env.DUNGEONSHARE_BOOTSTRAP_TOKEN)
          }
        />
      )}
    </main>
  );
}
