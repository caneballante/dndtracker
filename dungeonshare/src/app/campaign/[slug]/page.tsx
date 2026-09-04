import { ArrowLeft, ArrowUpDown, BookOpen } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PostCard } from "@/components/post-card";
import { SiteHeader } from "@/components/site-header";
import { getCampaignBySlug, listPublishedPosts } from "@/lib/journal";

export const dynamic = "force-dynamic";

export default async function CampaignPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ order?: string }>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const order = query.order === "oldest" ? "oldest" : "newest";
  const campaign = await getCampaignBySlug(slug);
  if (!campaign) notFound();

  const posts = await listPublishedPosts(campaign.id, order);

  return (
    <main>
      <SiteHeader campaignName={campaign.name} />
      <section className={`campaign-hero accent-${campaign.accent}`}>
        <Link className="back-link" href="/">
          <ArrowLeft size={16} aria-hidden="true" /> All campaigns
        </Link>
        <div className="campaign-hero-grid">
          <div>
            <p className="eyebrow">{campaign.eyebrow}</p>
            <h1>{campaign.name}</h1>
            <blockquote>{campaign.tagline}</blockquote>
          </div>
          <p>{campaign.summary}</p>
        </div>
      </section>

      <section className="chronicle">
        <div className="chronicle-heading">
          <div>
            <p className="eyebrow">The chronicle</p>
            <h2>{posts.length} stories shared</h2>
          </div>
          <Link
            className="sort-link"
            href={`/campaign/${campaign.slug}?order=${order === "newest" ? "oldest" : "newest"}`}
          >
            <ArrowUpDown size={15} aria-hidden="true" />
            {order === "newest" ? "Newest first" : "Oldest first"}
          </Link>
        </div>

        {posts.length ? (
          <div className="journal-list">
            {posts.map((post) => (
              <PostCard post={post} key={post.id} />
            ))}
          </div>
        ) : (
          <div className="empty-chronicle">
            <BookOpen aria-hidden="true" />
            <h2>The first page is waiting.</h2>
            <p>Published entries for this campaign will appear here.</p>
          </div>
        )}
      </section>

      <footer className="site-footer">
        <p>{campaign.name}</p>
        <Link href="/">Return to Dungeon Share</Link>
      </footer>
    </main>
  );
}
