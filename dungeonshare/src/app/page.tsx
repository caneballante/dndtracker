import { ArrowRight, BookOpenText, ImagePlus, ScrollText } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { SiteHeader } from "@/components/site-header";
import { listCampaigns } from "@/lib/journal";

export const dynamic = "force-dynamic";

export default async function Home() {
  const campaigns = await listCampaigns();
  const totalEntries = campaigns.reduce(
    (sum, campaign) => sum + campaign.publishedPostCount,
    0,
  );

  return (
    <main>
      <SiteHeader />

      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">A shared table memory</p>
          <h1>Every campaign deserves a chronicle.</h1>
          <p className="hero-lede">
            Session stories, discovered artifacts, and memorable faces—kept
            together for everyone who gathered around the table.
          </p>
          <div className="hero-actions">
            <a className="button button-primary" href="#campaigns">
              Explore campaigns <ArrowRight aria-hidden="true" size={17} />
            </a>
            <Link className="button button-quiet" href="/manage">
              Open manager
            </Link>
          </div>
          <dl className="hero-stats" aria-label="Journal overview">
            <div>
              <dt>{campaigns.length}</dt>
              <dd>campaigns</dd>
            </div>
            <div>
              <dt>{totalEntries}</dt>
              <dd>stories shared</dd>
            </div>
            <div>
              <dt>One</dt>
              <dd>living chronicle</dd>
            </div>
          </dl>
        </div>

        <div className="hero-art" aria-label="Dungeon Share campaign artwork">
          <div className="hero-art-frame">
            <Image
              src="/og.png"
              alt="An illustrated parchment map leading to a ruined keep"
              width={1200}
              height={630}
              priority
            />
          </div>
          <p className="hero-art-note">Collected at the edge of the map</p>
        </div>
      </section>

      <section className="campaign-section" id="campaigns">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Choose a chronicle</p>
            <h2>Campaign journals</h2>
          </div>
          <p>
            Each campaign keeps its own timeline while sharing the same quiet,
            readable home.
          </p>
        </div>

        <div className="campaign-grid">
          {campaigns.map((campaign, index) => (
            <Link
              className={`campaign-card accent-${campaign.accent}`}
              href={`/campaign/${campaign.slug}`}
              key={campaign.id}
            >
              <div className="campaign-number" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </div>
              <div className="campaign-card-copy">
                <p>{campaign.eyebrow}</p>
                <h3>{campaign.name}</h3>
                <blockquote>{campaign.tagline}</blockquote>
                <span>
                  {campaign.publishedPostCount}{" "}
                  {campaign.publishedPostCount === 1 ? "entry" : "entries"}
                </span>
              </div>
              <ArrowRight className="campaign-arrow" aria-hidden="true" />
            </Link>
          ))}
        </div>
      </section>

      <section className="source-strip" aria-label="Journal sources">
        <article>
          <ScrollText aria-hidden="true" />
          <div>
            <h3>DnD Tracker</h3>
            <p>Reviewed session summaries arrive as drafts.</p>
          </div>
        </article>
        <article>
          <ImagePlus aria-hidden="true" />
          <div>
            <h3>Dungeon Maker</h3>
            <p>NPCs, items, lore, and images join the same inbox.</p>
          </div>
        </article>
        <article>
          <BookOpenText aria-hidden="true" />
          <div>
            <h3>Dungeon Share</h3>
            <p>Only polished, player-safe entries are published.</p>
          </div>
        </article>
      </section>

      <footer className="site-footer">
        <p>Dungeon Share</p>
        <span>Made for stories that outlive the session.</span>
      </footer>
    </main>
  );
}
