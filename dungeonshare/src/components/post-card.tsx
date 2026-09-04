import {
  BookOpenText,
  Gem,
  MapPinned,
  ScrollText,
  StickyNote,
  UserRound,
} from "lucide-react";

import { MarkdownBody } from "@/components/markdown-body";
import type { JournalPost, PostKind } from "@/lib/types";

const labels: Record<PostKind, string> = {
  session: "Session chronicle",
  npc: "Person encountered",
  item: "Artifact discovered",
  location: "Place visited",
  lore: "Lore recovered",
  note: "Campaign note",
};

const icons = {
  session: BookOpenText,
  npc: UserRound,
  item: Gem,
  location: MapPinned,
  lore: ScrollText,
  note: StickyNote,
};

function displayDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T12:00:00Z`));
}

export function PostCard({ post }: { post: JournalPost }) {
  const Icon = icons[post.kind];

  return (
    <article className={`journal-entry entry-${post.kind}`} id={post.id}>
      <div className="entry-rail" aria-hidden="true">
        <span>
          <Icon size={18} />
        </span>
      </div>
      <div className="entry-card">
        <header className="entry-header">
          <div>
            <p className="entry-kind">{labels[post.kind]}</p>
            <h2>{post.title}</h2>
          </div>
          <time dateTime={post.eventDate}>{displayDate(post.eventDate)}</time>
        </header>

        <div className="entry-prose">
          <MarkdownBody>{post.body}</MarkdownBody>
        </div>

        {post.media.length > 0 ? (
          <div
            className={`entry-gallery gallery-${Math.min(post.media.length, 3)}`}
          >
            {post.media.map((media) => (
              <figure key={media.id}>
                {/* User-uploaded Blob URLs are not known at build time. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={media.url} alt={media.altText} loading="lazy" />
                {media.caption ? <figcaption>{media.caption}</figcaption> : null}
              </figure>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
