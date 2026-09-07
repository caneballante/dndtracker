import type { SessionMemoryPublishPayload } from "@/lib/session-memory";
import { sessionMemoryDisplayModel } from "@/lib/session-memory-view";

function displayPublishedAt(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

export function SessionMemoryContent({
  payload,
}: {
  payload: SessionMemoryPublishPayload;
}) {
  const memory = sessionMemoryDisplayModel(payload);

  return (
    <section className="session-memory" aria-label="Published Session Memory">
      <div className="session-memory-meta">
        <span>Revision {memory.revision}</span>
        <span>Published {displayPublishedAt(memory.publishedAt)}</span>
        {memory.worldName ? <span>{memory.worldName}</span> : null}
      </div>

      <section className="session-memory-section">
        <div className="session-memory-heading">
          <h3 className="eyebrow">Highlights</h3>
          <span>{memory.highlights.length}</span>
        </div>
        {memory.highlights.length ? (
          <div className="session-memory-list">
            {memory.highlights.map((highlight) => (
              <article className="session-memory-highlight" key={highlight.id}>
                <div className="session-memory-tags">
                  {highlight.categoryLabels.map((category, index) => (
                    <span key={`${highlight.id}-category-${index}`}>
                      {category}
                    </span>
                  ))}
                  <span>{highlight.confidenceLabel} confidence</span>
                </div>
                <p>{highlight.summary}</p>
                {highlight.participants.length ? (
                  <small>
                    Featuring {highlight.participants.join(", ")}
                  </small>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="session-memory-empty">No highlights were published.</p>
        )}
      </section>

      <section className="session-memory-section">
        <div className="session-memory-heading">
          <h3 className="eyebrow">Events</h3>
          <span>{memory.events.length}</span>
        </div>
        {memory.events.length ? (
          <div className="session-memory-list">
            {memory.events.map((event) => (
              <article className="session-memory-event" key={event.id}>
                <div className="session-memory-tags">
                  <span>{event.typeLabel}</span>
                  <span>{event.statusLabel}</span>
                  <span>{event.importanceLabel} importance</span>
                  <span>{event.confidenceLabel} confidence</span>
                </div>
                <p>{event.summary}</p>
                {event.facts.length ? (
                  <ul>
                    {event.facts.map((fact, index) => (
                      <li key={`${event.id}-fact-${index}`}>{fact}</li>
                    ))}
                  </ul>
                ) : null}
                {event.entities.length ? (
                  <small>People and places: {event.entities.join(", ")}</small>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="session-memory-empty">No events were published.</p>
        )}
      </section>
    </section>
  );
}
