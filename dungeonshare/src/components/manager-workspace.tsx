"use client";

import {
  Archive,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  CircleAlert,
  CloudUpload,
  ImagePlus,
  KeyRound,
  LoaderCircle,
  LogOut,
  Plus,
  RotateCcw,
  Save,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { upload } from "@vercel/blob/client";
import Link from "next/link";
import { useMemo, useState } from "react";

import { authClient } from "@/lib/auth-client";
import type {
  Campaign,
  JournalPost,
  MediaItem,
  PostKind,
  PostStatus,
} from "@/lib/types";

const kindLabels: Record<PostKind, string> = {
  session: "Session recap",
  npc: "NPC",
  item: "Item",
  location: "Location",
  lore: "Lore",
  note: "Note",
};

const statusLabels: Record<PostStatus, string> = {
  draft: "Draft inbox",
  published: "Published",
  archived: "Archive",
};

function today() {
  const date = new Date();
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function resequenceMedia(media: MediaItem[]) {
  return media.map((item, index) => ({ ...item, displayOrder: index }));
}

function orderedMedia(media: MediaItem[]) {
  return resequenceMedia(
    media.slice().sort((a, b) => a.displayOrder - b.displayOrder),
  );
}

export function ManagerWorkspace({
  campaigns: initialCampaigns,
  initialPosts,
  user,
  demo,
}: {
  campaigns: Campaign[];
  initialPosts: JournalPost[];
  user: { email: string; name: string };
  demo: boolean;
}) {
  const [campaigns, setCampaigns] = useState(initialCampaigns);
  const [posts, setPosts] = useState(initialPosts);
  const [campaignId, setCampaignId] = useState(
    initialCampaigns[0]?.id ?? "",
  );
  const [status, setStatus] = useState<PostStatus>("draft");
  const [activeId, setActiveId] = useState(
    initialPosts.find((post) => post.status === "draft")?.id ??
      initialPosts[0]?.id ??
      "",
  );
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [showCampaignForm, setShowCampaignForm] = useState(false);
  const [campaignNotice, setCampaignNotice] = useState("");
  const [newCampaign, setNewCampaign] = useState({
    name: "",
    tagline: "",
    summary: "",
    accent: "oxblood" as Campaign["accent"],
  });

  const visiblePosts = useMemo(
    () =>
      posts
        .filter(
          (post) =>
            post.campaignId === campaignId && post.status === status,
        )
        .sort(
          (a, b) =>
            Number(b.pinned) - Number(a.pinned) ||
            b.displayOrder - a.displayOrder ||
            b.eventDate.localeCompare(a.eventDate),
        ),
    [posts, campaignId, status],
  );

  const active =
    visiblePosts.find((post) => post.id === activeId) ??
    visiblePosts[0] ??
    null;

  function selectStatus(nextStatus: PostStatus) {
    setStatus(nextStatus);
    const next = posts.find(
      (post) =>
        post.campaignId === campaignId && post.status === nextStatus,
    );
    if (next) setActiveId(next.id);
  }

  function updateActive(patch: Partial<JournalPost>) {
    if (!active) return;
    setPosts((current) =>
      current.map((post) =>
        post.id === active.id ? { ...post, ...patch } : post,
      ),
    );
    setDirty((current) => new Set(current).add(active.id));
  }

  function replacePost(updated: JournalPost) {
    setPosts((current) =>
      current.map((post) => (post.id === updated.id ? updated : post)),
    );
    setDirty((current) => {
      const next = new Set(current);
      next.delete(updated.id);
      return next;
    });
  }

  async function requestJson(url: string, init?: RequestInit) {
    const response = await fetch(url, init);
    const json = await response.json().catch(() => ({}));
    if (!response.ok || !json.ok) {
      throw new Error(json.error || `Request failed (${response.status}).`);
    }
    return json;
  }

  async function createEntry() {
    if (!campaignId) return;
    setPending(true);
    setNotice("");
    try {
      const json = await requestJson("/api/manage/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaignId,
          kind: "note",
          title: "Untitled note",
          body: "",
          eventDate: today(),
        }),
      });
      setPosts((current) => [json.post, ...current]);
      setStatus("draft");
      setActiveId(json.post.id);
      setNotice("New draft created.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create draft.");
    } finally {
      setPending(false);
    }
  }

  async function createNewCampaign(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setCampaignNotice("");
    try {
      const json = await requestJson("/api/manage/campaigns", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newCampaign),
      });
      setCampaigns((current) => [json.campaign, ...current]);
      setCampaignId(json.campaign.id);
      setStatus("draft");
      setActiveId("");
      setNewCampaign({
        name: "",
        tagline: "",
        summary: "",
        accent: "oxblood",
      });
      setShowCampaignForm(false);
      setCampaignNotice(`${json.campaign.name} is ready for entries.`);
    } catch (error) {
      setCampaignNotice(
        error instanceof Error ? error.message : "Unable to create campaign.",
      );
    } finally {
      setPending(false);
    }
  }

  async function saveActive(statusOverride?: PostStatus) {
    if (!active) return;
    setPending(true);
    setNotice("");
    try {
      const nextStatus = statusOverride ?? active.status;
      const json = await requestJson(`/api/manage/posts/${active.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaignId: active.campaignId,
          kind: active.kind,
          status: nextStatus,
          title: active.title,
          body: active.body,
          eventDate: active.eventDate,
          displayOrder: active.displayOrder,
          pinned: active.pinned,
          media: orderedMedia(active.media),
        }),
      });
      replacePost(json.post);
      setStatus(nextStatus);
      setNotice(
        nextStatus === "published"
          ? "Entry published to the player journal."
          : nextStatus === "archived"
            ? "Entry moved to the archive."
            : "Draft saved.",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to save entry.");
    } finally {
      setPending(false);
    }
  }

  async function archiveActive() {
    if (!active) return;
    if (!window.confirm(`Archive “${active.title}”? You can restore it later.`)) {
      return;
    }
    await saveActive("archived");
  }

  async function movePost(id: string, direction: -1 | 1) {
    const index = visiblePosts.findIndex((post) => post.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= visiblePosts.length) return;
    const reordered = visiblePosts.slice();
    [reordered[index], reordered[target]] = [
      reordered[target],
      reordered[index],
    ];
    const orderById = new Map(
      reordered.map((post, idx) => [
        post.id,
        (reordered.length - idx) * 1024,
      ]),
    );
    setPosts((current) =>
      current.map((post) =>
        orderById.has(post.id)
          ? { ...post, displayOrder: orderById.get(post.id)! }
          : post,
      ),
    );
    try {
      await requestJson("/api/manage/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaignId,
          postIds: reordered.map((post) => post.id),
        }),
      });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to reorder.");
    }
  }

  async function uploadFiles(files: FileList | null, replaceId?: string) {
    if (!active || !files?.length) return;
    setPending(true);
    setNotice("");
    try {
      const nextMedia = active.media.slice();
      for (const file of Array.from(files)) {
        let media: MediaItem;
        if (demo) {
          media = {
            id: crypto.randomUUID(),
            postId: active.id,
            objectKey: `local-demo/${file.name}`,
            url: URL.createObjectURL(file),
            altText: file.name.replace(/\.[^.]+$/, ""),
            caption: "",
            displayOrder: nextMedia.length,
          };
        } else {
          const uploaded = await upload(
            `journal/${active.id}/${file.name}`,
            file,
            {
              access: "public",
              handleUploadUrl: "/api/uploads",
              clientPayload: JSON.stringify({
                postId: active.id,
                fileName: file.name,
                contentType: file.type,
                size: file.size,
              }),
              multipart: file.size > 5 * 1024 * 1024,
            },
          );
          media = {
            id: crypto.randomUUID(),
            postId: active.id,
            objectKey: uploaded.pathname,
            url: uploaded.url,
            altText: file.name.replace(/\.[^.]+$/, ""),
            caption: "",
            displayOrder: nextMedia.length,
          };
        }

        if (replaceId) {
          const replaceIndex = nextMedia.findIndex(
            (item) => item.id === replaceId,
          );
          if (replaceIndex >= 0) {
            media.displayOrder = nextMedia[replaceIndex].displayOrder;
            nextMedia[replaceIndex] = media;
          }
        } else {
          nextMedia.push(media);
        }
      }
      updateActive({ media: orderedMedia(nextMedia) });
      setNotice("Photo ready. Save the entry to keep this change.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Photo upload failed.");
    } finally {
      setPending(false);
    }
  }

  function removeMedia(id: string) {
    if (!active) return;
    updateActive({
      media: orderedMedia(active.media.filter((item) => item.id !== id)),
    });
  }

  function moveMedia(id: string, direction: -1 | 1) {
    if (!active) return;
    const media = orderedMedia(active.media);
    const index = media.findIndex((item) => item.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= media.length) return;
    [media[index], media[target]] = [media[target], media[index]];
    updateActive({ media: resequenceMedia(media) });
  }

  async function addPasskey() {
    setPending(true);
    setNotice("");
    try {
      const result = await authClient.passkey.addPasskey({
        name: "Dungeon Share manager",
      });
      if (result?.error) throw new Error(result.error.message);
      setNotice("Passkey added to this administrator account.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to add passkey.");
    } finally {
      setPending(false);
    }
  }

  async function signOut() {
    await authClient.signOut();
    window.location.assign("/sign-in");
  }

  return (
    <div className="manager-shell">
      <header className="manager-topbar">
        <div>
          <p className="eyebrow">Dungeon Share</p>
          <h1>Journal manager</h1>
        </div>
        <div className="manager-account">
          <span>
            {demo ? "Local preview" : user.name}
            <small>{demo ? "Changes reset with the server" : user.email}</small>
          </span>
          {!demo ? (
            <>
              <button className="icon-button labeled-icon" onClick={addPasskey}>
                <KeyRound size={16} /> Add passkey
              </button>
              <button className="icon-button" onClick={signOut} title="Sign out">
                <LogOut size={17} />
              </button>
            </>
          ) : null}
          <Link className="button button-quiet compact-button" href="/">
            View site
          </Link>
        </div>
      </header>

      <div className="manager-main">
        <aside className="manager-sidebar">
          <label className="manager-field">
            Campaign
            <select
              onChange={(event) => {
                const nextId = event.target.value;
                setCampaignId(nextId);
                const next = posts.find(
                  (post) => post.campaignId === nextId && post.status === status,
                );
                if (next) setActiveId(next.id);
              }}
              value={campaignId}
            >
              {campaigns.map((campaign) => (
                <option key={campaign.id} value={campaign.id}>
                  {campaign.name}
                </option>
              ))}
            </select>
          </label>

          <button
            className="button button-quiet full-button campaign-toggle"
            onClick={() => {
              setShowCampaignForm((current) => !current);
              setCampaignNotice("");
            }}
            type="button"
          >
            {showCampaignForm ? <X size={16} /> : <Plus size={16} />}
            {showCampaignForm ? "Cancel" : "New campaign"}
          </button>

          {showCampaignForm ? (
            <form
              className="campaign-create-panel"
              onSubmit={createNewCampaign}
            >
              <label className="manager-field">
                Campaign name
                <input
                  autoFocus
                  maxLength={160}
                  onChange={(event) =>
                    setNewCampaign((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  placeholder="The Shattered Crown"
                  required
                  value={newCampaign.name}
                />
              </label>
              <label className="manager-field">
                Short tagline
                <input
                  maxLength={300}
                  onChange={(event) =>
                    setNewCampaign((current) => ({
                      ...current,
                      tagline: event.target.value,
                    }))
                  }
                  placeholder="A promise made beneath a dying star."
                  value={newCampaign.tagline}
                />
              </label>
              <label className="manager-field">
                Player-facing overview
                <textarea
                  maxLength={3000}
                  onChange={(event) =>
                    setNewCampaign((current) => ({
                      ...current,
                      summary: event.target.value,
                    }))
                  }
                  placeholder="A short introduction for the campaign page."
                  rows={3}
                  value={newCampaign.summary}
                />
              </label>
              <label className="manager-field">
                Color
                <select
                  onChange={(event) =>
                    setNewCampaign((current) => ({
                      ...current,
                      accent: event.target.value as Campaign["accent"],
                    }))
                  }
                  value={newCampaign.accent}
                >
                  <option value="oxblood">Oxblood</option>
                  <option value="forest">Forest</option>
                  <option value="indigo">Indigo</option>
                  <option value="brass">Brass</option>
                </select>
              </label>
              <button
                className="button button-primary full-button"
                disabled={pending}
                type="submit"
              >
                {pending ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <Plus size={16} />
                )}
                Create campaign
              </button>
            </form>
          ) : null}

          {campaignNotice ? (
            <p
              className={`campaign-create-notice ${campaignNotice.toLowerCase().includes("unable") || campaignNotice.toLowerCase().includes("exists") ? "error-message" : ""}`}
              role="status"
            >
              {campaignNotice}
            </p>
          ) : null}

          <nav className="manager-status-nav" aria-label="Entry states">
            {(["draft", "published", "archived"] as PostStatus[]).map(
              (item) => {
                const count = posts.filter(
                  (post) =>
                    post.campaignId === campaignId && post.status === item,
                ).length;
                return (
                  <button
                    className={status === item ? "active" : ""}
                    key={item}
                    onClick={() => selectStatus(item)}
                  >
                    <span>{statusLabels[item]}</span>
                    <b>{count}</b>
                  </button>
                );
              },
            )}
          </nav>

          <button
            className="button button-primary full-button"
            disabled={pending}
            onClick={createEntry}
          >
            <Plus size={16} /> Add note
          </button>
        </aside>

        <section className="manager-list-panel">
          <div className="manager-panel-heading">
            <div>
              <p className="eyebrow">{statusLabels[status]}</p>
              <h2>
                {visiblePosts.length}{" "}
                {visiblePosts.length === 1 ? "entry" : "entries"}
              </h2>
            </div>
            {demo ? <span className="demo-chip">Demo data</span> : null}
          </div>

          <div className="manager-entry-list">
            {visiblePosts.map((post, index) => (
              <article
                className={`manager-entry-row ${active?.id === post.id ? "active" : ""}`}
                key={post.id}
              >
                <button
                  className="entry-select-button"
                  onClick={() => setActiveId(post.id)}
                >
                  <span>{kindLabels[post.kind]}</span>
                  <strong>{post.title}</strong>
                  <small>{post.eventDate}</small>
                  {dirty.has(post.id) ? <i>Unsaved</i> : null}
                </button>
                <div className="row-order-controls">
                  <button
                    aria-label={`Move ${post.title} up`}
                    disabled={index === 0}
                    onClick={() => movePost(post.id, -1)}
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    aria-label={`Move ${post.title} down`}
                    disabled={index === visiblePosts.length - 1}
                    onClick={() => movePost(post.id, 1)}
                  >
                    <ArrowDown size={14} />
                  </button>
                </div>
                <ChevronRight className="row-chevron" aria-hidden="true" />
              </article>
            ))}
            {!visiblePosts.length ? (
              <div className="manager-empty">
                <Check aria-hidden="true" />
                <h3>Nothing here yet.</h3>
                <p>
                  {status === "draft"
                    ? "New items from your apps will arrive in this inbox."
                    : "Entries moved into this state will appear here."}
                </p>
              </div>
            ) : null}
          </div>
        </section>

        <section className="manager-editor">
          {active ? (
            <>
              <header className="editor-heading">
                <div>
                  <p className="eyebrow">Edit entry</p>
                  <h2>{active.title}</h2>
                </div>
                <span className={`status-chip status-${active.status}`}>
                  {active.status}
                </span>
              </header>

              <div className="editor-fields">
                <div className="field-grid">
                  <label className="manager-field">
                    Type
                    <select
                      onChange={(event) =>
                        updateActive({ kind: event.target.value as PostKind })
                      }
                      value={active.kind}
                    >
                      {Object.entries(kindLabels).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="manager-field">
                    Display date
                    <input
                      onChange={(event) =>
                        updateActive({ eventDate: event.target.value })
                      }
                      type="date"
                      value={active.eventDate}
                    />
                  </label>
                </div>

                <label className="manager-field">
                  Heading
                  <input
                    onChange={(event) =>
                      updateActive({ title: event.target.value })
                    }
                    value={active.title}
                  />
                </label>

                <label className="manager-field">
                  Player-facing text
                  <textarea
                    className="editor-prose"
                    onChange={(event) =>
                      updateActive({ body: event.target.value })
                    }
                    placeholder="Write what the players should remember…"
                    value={active.body}
                  />
                </label>

                <label className="pin-toggle">
                  <input
                    checked={active.pinned}
                    onChange={(event) =>
                      updateActive({ pinned: event.target.checked })
                    }
                    type="checkbox"
                  />
                  Pin this entry above the campaign timeline
                </label>

                <section className="media-editor">
                  <div className="media-heading">
                    <div>
                      <h3>Photos</h3>
                      <p>Add, replace, caption, or rearrange images.</p>
                    </div>
                    <label className="button button-quiet compact-button upload-label">
                      <ImagePlus size={16} />
                      Add photos
                      <input
                        accept="image/jpeg,image/png,image/webp,image/gif"
                        multiple
                        onChange={(event) => uploadFiles(event.target.files)}
                        type="file"
                      />
                    </label>
                  </div>

                  {active.media.length ? (
                    <div className="media-list">
                      {orderedMedia(active.media).map((media, index) => (
                        <article className="media-row" key={media.id}>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={media.url} alt={media.altText} />
                          <div>
                            <input
                              aria-label="Image alt text"
                              onChange={(event) =>
                                updateActive({
                                  media: active.media.map((item) =>
                                    item.id === media.id
                                      ? { ...item, altText: event.target.value }
                                      : item,
                                  ),
                                })
                              }
                              placeholder="Describe this image"
                              value={media.altText}
                            />
                            <input
                              aria-label="Image caption"
                              onChange={(event) =>
                                updateActive({
                                  media: active.media.map((item) =>
                                    item.id === media.id
                                      ? { ...item, caption: event.target.value }
                                      : item,
                                  ),
                                })
                              }
                              placeholder="Optional caption"
                              value={media.caption}
                            />
                          </div>
                          <div className="media-actions">
                            <button
                              aria-label="Move photo earlier"
                              disabled={index === 0}
                              onClick={() => moveMedia(media.id, -1)}
                            >
                              <ArrowUp size={14} />
                            </button>
                            <button
                              aria-label="Move photo later"
                              disabled={index === active.media.length - 1}
                              onClick={() => moveMedia(media.id, 1)}
                            >
                              <ArrowDown size={14} />
                            </button>
                            <label title="Replace photo">
                              <RotateCcw size={14} />
                              <input
                                accept="image/jpeg,image/png,image/webp,image/gif"
                                onChange={(event) =>
                                  uploadFiles(event.target.files, media.id)
                                }
                                type="file"
                              />
                            </label>
                            <button
                              aria-label="Remove photo"
                              className="danger-icon"
                              onClick={() => removeMedia(media.id)}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <label className="photo-dropzone">
                      <CloudUpload aria-hidden="true" />
                      <strong>Choose photos to add</strong>
                      <span>JPEG, PNG, WebP, or GIF · up to 12 MB each</span>
                      <input
                        accept="image/jpeg,image/png,image/webp,image/gif"
                        multiple
                        onChange={(event) => uploadFiles(event.target.files)}
                        type="file"
                      />
                    </label>
                  )}
                </section>
              </div>

              {notice ? (
                <p
                  className={`manager-notice ${notice.toLowerCase().includes("unable") || notice.toLowerCase().includes("failed") ? "error-message" : ""}`}
                >
                  {notice.toLowerCase().includes("unable") ||
                  notice.toLowerCase().includes("failed") ? (
                    <CircleAlert size={15} />
                  ) : (
                    <Check size={15} />
                  )}
                  {notice}
                </p>
              ) : null}

              <footer className="editor-actions">
                <button
                  className="button button-quiet"
                  disabled={pending}
                  onClick={() => saveActive("draft")}
                >
                  {pending ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  Save draft
                </button>

                {active.status === "published" ? (
                  <button
                    className="button button-primary"
                    disabled={pending}
                    onClick={() => saveActive("draft")}
                  >
                    <X size={16} /> Unpublish
                  </button>
                ) : active.status === "archived" ? (
                  <button
                    className="button button-primary"
                    disabled={pending}
                    onClick={() => saveActive("draft")}
                  >
                    <RotateCcw size={16} /> Restore to drafts
                  </button>
                ) : (
                  <button
                    className="button button-primary"
                    disabled={pending}
                    onClick={() => saveActive("published")}
                  >
                    <Send size={16} /> Publish
                  </button>
                )}

                {active.status !== "archived" ? (
                  <button
                    className="button danger-button"
                    disabled={pending}
                    onClick={archiveActive}
                  >
                    <Archive size={16} /> Archive
                  </button>
                ) : null}
              </footer>
            </>
          ) : (
            <div className="manager-empty large-empty">
              <Plus aria-hidden="true" />
              <h2>Select an entry or add a note.</h2>
              <p>The editor will open here.</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
