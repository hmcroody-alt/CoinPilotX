/**
 * The conversation media gallery's state, owned above the message list.
 *
 * Three things live here that cannot live in a message bubble:
 *
 * 1. **The collection.** Server-paged, so it is the whole conversation's media
 *    rather than the mounted window's. A bubble does not know what the 17th
 *    photo is; only something holding the full ordered list does.
 *
 * 2. **The active item, tracked by key rather than by index.** This is the
 *    single decision that makes "new media must not move the item you are
 *    looking at" true. An index is a position in an array that other people are
 *    splicing into; a key is the photo. Older pages loading in shift every
 *    numeric index and change nothing about what is on screen.
 *
 * 3. **Access-URL resolution for the active window.** Delivery URLs expire.
 *    Resolving them per-item, on demand, near the active item is what lets a
 *    six-month-old photo open at all — and doing it for the *window* rather
 *    than the collection is what stops a 400-photo thread from minting 400
 *    grants to show one picture.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchConversationMedia } from "../api/conversationMedia";
import {
  ConversationMediaItem,
  MEDIA_PREFETCH_RADIUS,
  conversationMediaKey,
  indexOfConversationMedia,
  mergeConversationMedia,
  prefetchNeighbours,
  removeConversationMedia
} from "./conversationMediaCollection";
import {
  grantMessengerMediaAccess,
  isProtectedMessengerMediaUrl,
  resolveCanonicalMessengerMediaId
} from "./messengerMediaAccess";
import { absoluteApiUrl } from "../api/config";

const PAGE_SIZE = 60;

export type ConversationMediaGalleryState = {
  /** The canonical ordered collection. Never derived from mounted cells. */
  items: ConversationMediaItem[];
  /** -1 when closed. Derived from `activeKey`, so it survives merges. */
  index: number;
  visible: boolean;
  /** Total gallery-eligible media in the conversation, for "12 of 43". */
  total: number;
  loading: boolean;
  /**
   * Per-key resolved playback/preview URLs for the active window. A key absent
   * here has not been resolved yet; a key present with `url: ""` is known to be
   * unavailable, which the viewer must render as a stated condition rather than
   * as a spinner that never ends.
   */
  resolved: Record<string, { url: string; thumbnailUrl: string; unavailable: boolean }>;
  open: (seed: ConversationMediaItem) => void;
  close: () => void;
  setIndex: (next: number) => void;
  /** A message was deleted — drop its media and land somewhere sensible. */
  dropMessage: (messageId: number) => void;
};

export function useConversationMediaGallery(
  conversationId: number,
  options: { online?: boolean } = {}
): ConversationMediaGalleryState {
  const online = options.online !== false;
  const [items, setItems] = useState<ConversationMediaItem[]>([]);
  const [activeKey, setActiveKey] = useState("");
  const [visible, setVisible] = useState(false);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [resolved, setResolved] = useState<ConversationMediaGalleryState["resolved"]>({});
  const edgesRef = useRef({ hasOlder: false, hasNewer: false, oldestId: 0, newestId: 0 });
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // A new conversation is a new collection. Without this, opening conversation B
  // after A would show A's photos under B's title until the first page landed.
  useEffect(() => {
    setItems([]);
    setActiveKey("");
    setVisible(false);
    setTotal(0);
    setResolved({});
    edgesRef.current = { hasOlder: false, hasNewer: false, oldestId: 0, newestId: 0 };
  }, [conversationId]);

  const index = useMemo(() => indexOfConversationMedia(items, activeKey), [items, activeKey]);

  const absorb = useCallback((page: Awaited<ReturnType<typeof fetchConversationMedia>>, direction: "older" | "newer" | "center") => {
    if (!mountedRef.current) return;
    setItems((current) => mergeConversationMedia(current, page.items));
    setTotal((current) => Math.max(current, page.total));
    const edges = edgesRef.current;
    if (direction === "older" || direction === "center") edges.hasOlder = page.hasOlder;
    if (direction === "newer" || direction === "center") edges.hasNewer = page.hasNewer;
    if (page.oldestId && (!edges.oldestId || page.oldestId < edges.oldestId)) edges.oldestId = page.oldestId;
    if (page.newestId > edges.newestId) edges.newestId = page.newestId;
  }, []);

  /**
   * Open on the tapped item, seeded from the bubble.
   *
   * The seed is what makes the viewer feel immediate: the photo the user tapped
   * is already decoded in the thread, so it goes into the collection and becomes
   * the active key synchronously. The fetch that follows fills in the rest of
   * the conversation around it. If that fetch never lands — offline, server
   * down — the user still gets the item they tapped rather than a spinner, and
   * swiping simply has nowhere to go.
   *
   * The pages are requested in BOTH directions from the tapped item, not from
   * the top. Paging down from the newest to reach the 17th-from-oldest photo
   * would be 6 round trips and a visibly empty gallery in a long thread.
   */
  const open = useCallback((seed: ConversationMediaItem) => {
    setItems((current) => mergeConversationMedia(current, [seed]));
    setActiveKey(seed.key);
    setVisible(true);
    if (!online || !conversationId) return;
    setLoading(true);
    Promise.allSettled([
      fetchConversationMedia(conversationId, { beforeId: seed.attachmentId, limit: PAGE_SIZE }),
      fetchConversationMedia(conversationId, { afterId: seed.attachmentId, limit: PAGE_SIZE })
    ])
      .then(([older, newer]) => {
        if (older.status === "fulfilled") absorb(older.value, "older");
        if (newer.status === "fulfilled") absorb(newer.value, "newer");
      })
      .finally(() => { if (mountedRef.current) setLoading(false); });
  }, [absorb, conversationId, online]);

  const close = useCallback(() => {
    setVisible(false);
    setActiveKey("");
  }, []);

  /**
   * Move to a numeric position, translating it straight back into a key.
   *
   * The viewer thinks in positions because swiping is positional. Storing that
   * position would reintroduce the bug this hook exists to prevent, so it is
   * converted at the boundary and never kept.
   */
  const setIndexByPosition = useCallback((next: number) => {
    const clamped = Math.max(0, Math.min(next, items.length - 1));
    const target = items[clamped];
    if (target) setActiveKey(target.key);
  }, [items]);

  const dropMessage = useCallback((messageId: number) => {
    const doomed = items.filter((item) => item.messageId === messageId);
    if (!doomed.length) return;
    let next = items;
    let position = indexOfConversationMedia(items, activeKey);
    for (const item of doomed) {
      const result = removeConversationMedia(next, item.key, position);
      next = result.items;
      position = result.index;
    }
    setItems(next);
    if (position < 0 || !next[position]) {
      setVisible(false);
      setActiveKey("");
      return;
    }
    setActiveKey(next[position].key);
  }, [activeKey, items]);

  // Page outward when the active item nears an edge, so a swipe never lands on
  // "nothing here" while the server still has media in that direction.
  useEffect(() => {
    if (!visible || !online || !conversationId || loading || index < 0) return;
    const edges = edgesRef.current;
    if (index <= MEDIA_PREFETCH_RADIUS && edges.hasOlder && edges.oldestId) {
      setLoading(true);
      fetchConversationMedia(conversationId, { beforeId: edges.oldestId, limit: PAGE_SIZE })
        .then((page) => absorb(page, "older"))
        .catch(() => undefined)
        .finally(() => { if (mountedRef.current) setLoading(false); });
      return;
    }
    if (index >= items.length - 1 - MEDIA_PREFETCH_RADIUS && edges.hasNewer && edges.newestId) {
      setLoading(true);
      fetchConversationMedia(conversationId, { afterId: edges.newestId, limit: PAGE_SIZE })
        .then((page) => absorb(page, "newer"))
        .catch(() => undefined)
        .finally(() => { if (mountedRef.current) setLoading(false); });
    }
  }, [absorb, conversationId, index, items.length, loading, online, visible]);

  /**
   * Resolve the active item and its immediate neighbours, and only those.
   *
   * Requirement, stated as two halves that pull against each other: N+1 and N-1
   * aggressively, and NOT the whole conversation at full resolution. Resolving
   * the window honours both — the next swipe is already warm, and a long thread
   * never mints hundreds of grants for photos nobody asked to see.
   */
  useEffect(() => {
    if (!visible || index < 0) return;
    const active = items[index];
    if (!active) return;
    const window = [active, ...prefetchNeighbours(items, index)];
    let cancelled = false;
    for (const item of window) {
      if (resolved[item.key]) continue;
      if (!isProtectedMessengerMediaUrl(item.url)) {
        // Already loadable. Recorded anyway so the window is not re-examined
        // every render, and so "resolved" means one thing for every item.
        // Absolutized on the way in for the same reason the granted URLs are:
        // a site-relative URL that never needed a grant is still unloadable by
        // a native player, and fails as a black rectangle rather than an error.
        setResolved((current) => current[item.key]
          ? current
          : {
              ...current,
              [item.key]: {
                url: absoluteApiUrl(item.url),
                thumbnailUrl: absoluteApiUrl(item.thumbnailUrl),
                unavailable: !item.url
              }
            });
        continue;
      }
      /**
       * Ask for the id the access endpoint is actually keyed on.
       *
       * `mediaUploadId || attachmentId` looks equivalent and is not. The two are
       * autoincrements from different tables whose ranges overlap, so a falsy
       * `media_upload_id` silently promotes a transport row id into a foundation
       * media id — which either 404s or, worse, resolves to somebody else's
       * attachment. `resolveCanonicalMessengerMediaId` also mines the id out of
       * the protected URL we just matched, which is direct evidence rather than
       * a sibling integer that happens to be truthy.
       */
      const canonical = resolveCanonicalMessengerMediaId(
        { mediaUploadId: item.mediaUploadId, attachmentId: item.attachmentId },
        item.url
      );
      if (!canonical.id) {
        // Unreachable while the URL matched the protected pattern -- that match
        // is itself where the id comes from. Recorded rather than skipped
        // anyway: `continue` here would leave the key absent from `resolved`
        // forever, which the viewer renders as a permanent spinner over a black
        // frame and reports nowhere. An item we cannot name is unavailable, and
        // saying so is the only honest end state.
        setResolved((current) => current[item.key]
          ? current
          : { ...current, [item.key]: { url: "", thumbnailUrl: "", unavailable: true } });
        continue;
      }
      // `grant…` not `resolve…`: this is the call that carries the one bounded
      // recovery (expired grant -> re-mint and retry once; wrong id -> try a
      // proven alternate). The gallery is where expiry is MOST likely, because
      // it is the surface that opens six-month-old media.
      grantMessengerMediaAccess(canonical)
        .then((grant) => {
          if (cancelled || !mountedRef.current) return;
          setResolved((current) => ({
            ...current,
            [item.key]: {
              url: grant.url,
              thumbnailUrl: grant.thumbnailUrl || absoluteApiUrl(item.thumbnailUrl),
              unavailable: !grant.url
            }
          }));
        })
        .catch(() => {
          if (cancelled || !mountedRef.current) return;
          // A stated failure, not an eternal spinner. The viewer renders this
          // as "Not available", which is the honest thing to show for media
          // that is deleted, expired beyond recovery, or offline and uncached.
          setResolved((current) => ({ ...current, [item.key]: { url: "", thumbnailUrl: "", unavailable: true } }));
        });
    }
    return () => { cancelled = true; };
  }, [index, items, resolved, visible]);

  return { items, index, visible, total, loading, resolved, open, close, setIndex: setIndexByPosition, dropMessage };
}

/** Build the collection item for a message bubble the user just tapped. */
export function gallerySeedFromMessage(input: {
  messageId: number;
  attachmentId: number;
  mediaUploadId?: number;
  kind: "image" | "video";
  url: string;
  thumbnailUrl?: string;
  mimeType?: string;
  width?: number;
  height?: number;
  durationSeconds?: number;
  senderId?: number;
  senderName?: string;
  createdAt?: string;
}): ConversationMediaItem {
  return {
    key: conversationMediaKey(input.messageId, input.attachmentId),
    attachmentId: input.attachmentId,
    mediaUploadId: input.mediaUploadId || 0,
    messageId: input.messageId,
    kind: input.kind,
    url: input.url,
    thumbnailUrl: input.thumbnailUrl || "",
    mimeType: input.mimeType || "",
    width: input.width || 0,
    height: input.height || 0,
    durationSeconds: input.durationSeconds || 0,
    senderId: input.senderId || 0,
    senderName: input.senderName || "",
    createdAt: input.createdAt || ""
  };
}
