import { LockKeyhole, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";

import { ManagerWorkspace } from "@/components/manager-workspace";
import { getManagerAccess } from "@/lib/auth";
import { listCampaigns, listManagerPosts } from "@/lib/journal";

export const dynamic = "force-dynamic";

export default async function ManagePage() {
  const access = await getManagerAccess();

  if (access.state === "signed-out") redirect("/sign-in");

  if (access.state === "unconfigured") {
    return (
      <main className="centered-page manager-locked-page">
        <LockKeyhole aria-hidden="true" />
        <p className="eyebrow">Manager locked safely</p>
        <h1>Connect Neon before managing the journal.</h1>
        <p>
          The public preview is ready, but destructive controls remain disabled
          until the database and administrator authentication are configured.
        </p>
        <Link className="button button-primary" href="/">
          View the public journal
        </Link>
      </main>
    );
  }

  if (access.state === "forbidden") {
    return (
      <main className="centered-page manager-locked-page">
        <ShieldAlert aria-hidden="true" />
        <p className="eyebrow">Access denied</p>
        <h1>This account is not a Dungeon Share administrator.</h1>
        <p>{access.email}</p>
        <Link className="button button-primary" href="/">
          Return to the journal
        </Link>
      </main>
    );
  }

  const [campaigns, posts] = await Promise.all([
    listCampaigns(),
    listManagerPosts(),
  ]);

  return (
    <ManagerWorkspace
      campaigns={campaigns}
      demo={access.demo}
      initialPosts={posts}
      user={access.user}
    />
  );
}
