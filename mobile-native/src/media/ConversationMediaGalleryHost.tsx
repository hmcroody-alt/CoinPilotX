/**
 * The one media viewer a conversation has, and the way a bubble reaches it.
 *
 * Every photo bubble used to mount its own `<NativeMediaViewer items={[one]}>`.
 * That is why tapping the 17th photo opened a gallery of exactly one item with
 * no next and no previous: the bubble is the only thing that knew which photo
 * was tapped, and a bubble only knows about itself. Worse, the bubbles that know
 * anything are the ones React Native currently has mounted, so "the gallery" was
 * really "the four or five cells near the viewport".
 *
 * The fix is structural rather than careful. The collection and the viewer are
 * hoisted to the screen, where the whole conversation's media is fetchable, and
 * a bubble's only job is to say *which item* was tapped. It hands over a seed —
 * the thing it does know — and the host places that seed in the real collection.
 */

import { createContext, useContext, useMemo } from "react";
import { useTranslation } from "../i18n";

import { isLoadableMediaUrl } from "../api/config";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import { messengerMediaCacheIdentity } from "./messengerMediaAccess";
import { ConversationMediaItem } from "./conversationMediaCollection";
import { ConversationMediaGalleryState } from "./useConversationMediaGallery";

/**
 * First candidate a native loader can actually open, in the caller's order.
 *
 * Preference is expressed by argument order; loadability is the floor under it.
 * Falls back to the first non-empty candidate so an unrecognised-but-present URL
 * still reaches the loader and fails as a *reported* load error rather than
 * being silently blanked here.
 */
function preferLoadable(...candidates: Array<string | null | undefined>): string {
  for (const candidate of candidates) {
    if (isLoadableMediaUrl(candidate)) return String(candidate).trim();
  }
  for (const candidate of candidates) {
    if (String(candidate || "").trim()) return String(candidate).trim();
  }
  return "";
}

type GalleryHandle = Pick<ConversationMediaGalleryState, "open">;

const ConversationGalleryContext = createContext<GalleryHandle | null>(null);

/**
 * Null outside a host, deliberately.
 *
 * A media bubble rendered somewhere with no conversation gallery — a preview, a
 * test harness — should render its media and simply not open anything, rather
 * than throw. Callers check.
 */
export function useConversationGallery(): GalleryHandle | null {
  return useContext(ConversationGalleryContext);
}

export function ConversationGalleryProvider({ gallery, children }: { gallery: GalleryHandle; children: React.ReactNode }) {
  // `gallery.open` is a stable useCallback, so this memo keeps every media
  // bubble in a long thread from re-rendering whenever the collection grows.
  const value = useMemo(() => ({ open: gallery.open }), [gallery.open]);
  return <ConversationGalleryContext.Provider value={value}>{children}</ConversationGalleryContext.Provider>;
}

/**
 * Turn the collection into viewer items, substituting resolved delivery URLs.
 *
 * An item the window has not resolved yet keeps its seeded URL, which for the
 * photo the user actually tapped is already decoded and on screen — that is what
 * makes opening feel instant instead of showing a spinner over a photo the
 * device is holding in memory.
 *
 * An item resolved as unavailable gets an empty URL and says so. The viewer
 * renders an empty URL as its "media unavailable" state, which is the honest
 * end state for media that was deleted, expired beyond recovery, or is offline
 * and uncached. Requirement §9/§19: never an infinite spinner, never a black
 * frame.
 */
export function galleryViewerItems(
  items: ConversationMediaItem[],
  resolved: ConversationMediaGalleryState["resolved"],
  labels: { photo: (item: ConversationMediaItem, position: number) => string; video: (item: ConversationMediaItem, position: number) => string; unavailable: string }
): NativeMediaViewerItem[] {
  return items.map((item, position) => {
    const grant = resolved[item.key];
    const unavailable = Boolean(grant?.unavailable);
    const label = item.kind === "video" ? labels.video(item, position) : labels.photo(item, position);
    return {
      // The viewer's `id` becomes the playback-coordinator owner id, so it has
      // to separate two items. Messenger's row ids come from several tables and
      // collide, which is why the collection is keyed on a composite — the
      // position is what is unique here and cheap to hand over.
      id: item.attachmentId,
      // `id` above separates two items for the playback coordinator; it is not
      // safe as a cache key, because an attachment id and a media-upload id are
      // different tables' autoincrements and the same number names two files.
      // Save, share and open-document all key on this, so without it the gallery
      // silently falls back to URL keying.
      cacheIdentity: messengerMediaCacheIdentity({
        mediaUploadId: item.mediaUploadId,
        attachmentId: item.attachmentId
      }),
      kind: item.kind,
      // Never let an unloadable candidate displace a loadable one.
      //
      // This read `grant?.url || item.url`, which is the bug that made every
      // fullscreen item black. The seed carries the URL the chat bubble ALREADY
      // rendered from — absolute, warm, proven — and the grant arrived
      // site-relative. Truthiness ranked the broken one first, so tapping media
      // that was visibly on screen threw away the working URL and handed the
      // player one AVPlayer rejects outright (§3/§4: tapping content must never
      // make it disappear).
      //
      // Order still prefers the grant: it is the freshest credential and the
      // only one that can be re-minted when it expires. `isLoadableMediaUrl` is
      // the floor under that preference, not a replacement for it.
      url: unavailable ? "" : preferLoadable(grant?.url, item.url),
      // Poster order is INVERTED on purpose. The seed's thumbnail is the exact
      // bitmap already decoded in the thread, so preferring it means the viewer
      // opens on a picture instead of re-fetching an equivalent URL that differs
      // only in its access token — a different cache key, a second download, and
      // a black gap where the poster should be.
      thumbnailUrl: unavailable ? "" : preferLoadable(item.thumbnailUrl, grant?.thumbnailUrl),
      // `subtitle` is what the viewer reads out and displays under the title, so
      // the accessibility requirement ("Photo from Maria Cherie, 12 of 43") and
      // the visible counter are the same string rather than two that can drift.
      subtitle: unavailable ? `${label} — ${labels.unavailable}` : label,
      alt: label
    };
  });
}

/**
 * The screen's single viewer instance.
 *
 * Controlled: `index` comes from the gallery, which tracks the active item by
 * key. That is what keeps a swipe stable while older pages splice in underneath
 * — the position changes, the photo does not.
 */
export function ConversationMediaGalleryViewer({ gallery, online = true }: { gallery: ConversationMediaGalleryState; online?: boolean }) {
  const { t } = useTranslation();
  const total = Math.max(gallery.total, gallery.items.length);
  const viewerItems = useMemo(
    () =>
      galleryViewerItems(gallery.items, gallery.resolved, {
        photo: (item, position) =>
          t("messaging:chat.a11yGalleryPhotoPosition", {
            sender: item.senderName || t("messaging:chat.mediaViewerTitle"),
            position: position + 1,
            total
          }),
        video: (item, position) =>
          t("messaging:chat.a11yGalleryVideoPosition", {
            sender: item.senderName || t("messaging:chat.mediaViewerTitle"),
            position: position + 1,
            total
          }),
        unavailable: online ? t("messaging:chat.galleryUnavailable") : t("messaging:chat.galleryUnavailableOffline")
      }),
    [gallery.items, gallery.resolved, online, t, total]
  );
  return (
    <NativeMediaViewer
      visible={gallery.visible}
      items={viewerItems}
      index={gallery.index}
      onIndexChange={gallery.setIndex}
      totalCount={total}
      swipeToNavigate
      surface="messenger"
      title={t("messaging:chat.mediaViewerTitle")}
      onClose={gallery.close}
    />
  );
}
