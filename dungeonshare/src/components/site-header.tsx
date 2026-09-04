import { ScrollText } from "lucide-react";
import Link from "next/link";

export function SiteHeader({
  campaignName,
}: {
  campaignName?: string;
}) {
  return (
    <header className="site-header">
      <Link className="brand" href="/">
        <span className="brand-mark" aria-hidden="true">
          <ScrollText size={19} />
        </span>
        <span>
          Dungeon Share
          {campaignName ? <small>{campaignName}</small> : null}
        </span>
      </Link>
      <nav aria-label="Main navigation">
        <Link href="/#campaigns">Campaigns</Link>
        <Link href="/manage">Manager</Link>
      </nav>
    </header>
  );
}
