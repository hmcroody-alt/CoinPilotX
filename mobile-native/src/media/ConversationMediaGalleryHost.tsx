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

import { absoluteApiUrl, isAdaptiveManifest, isLoadableMediaUrl } from "../api/config";
import { NativeMediaViewer, NativeMediaViewerItem } from "../components/NativeMediaViewer";
import {
  grantMessengerMediaAccess,
  invalidateMessengerMediaAccess,
  messengerMediaCacheIdentity,
  resolveCanonicalMessengerMediaId
} from "./messengerMediaAccess";
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

/**
 * Mint a replacement access URL for one item, on demand (§8).
 *
 * Save to Photos and Share are the two actions that can happen arbitrarily long
 * after the grant that painted the picture. A messenger access URL is a
 * fifteen-minute credential, so a viewer left open past that shows a decoded
 * image and then refuses to save it — reported to the user as "You do not have
 * access to this media", about a photo they are looking at.
 *
 * The cached grant is dropped FIRST. Without that, the access module answers
 * from its own cache and hands back the same expired URL, which is a refresh
 * that refreshes nothing. Returns "" when the item has no resolvable foundation
 * id, which the downloader reads as "no refresh available" and reports the
 * original failure honestly rather than inventing a second one.
 */
function refreshAccessUrlFor(item: ConversationMediaItem): () => Promise<string> {
  return async () => {
    const canonical = resolveCanonicalMessengerMediaId(
      { mediaUploadId: item.mediaUploadId, attachmentId: item.attachmentId },
      item.url
    );
    if (!canonical.id) return "";
    invalidateMessengerMediaAccess(canonical.id);
    const grant = await grantMessengerMediaAccess(canonical);
    return grant.url || "";
  };
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
    // A streamed video's playback source and its downloadable file are two
    // different resources, so they are resolved separately here and never
    // allowed to swap places.
    //
    // PLAYBACK prefers the manifest. Everywhere else in this function the grant
    // wins, because it is the freshest credential — but a grant is a progressive
    // download of the whole file, and preferring it over an adaptive manifest
    // throws away the only mechanism that lets a video start on a first segment
    // instead of a complete transfer (§9/§21). The manifest is already a signed,
    // membership-gated URL by the time the server hands it over, so nothing is
    // given up by preferring it.
    //
    // DOWNLOAD must never be a manifest. `preferLoadable` alone would happily
    // return one, and the failure is silent: the transfer succeeds, a playlist
    // lands in the cache, and Photos rejects the write. Empty is the honest
    // answer when no file URL exists, and the viewer falls back to `url`.
    const manifest = isAdaptiveManifest(item.url) ? item.url : "";
    const downloadCandidate = preferLoadable(
      grant?.url,
      absoluteApiUrl(item.downloadUrl),
      absoluteApiUrl(item.url)
    );
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
      // Carried so Save and Share can give the cached file an extension. The
      // access URL's path ends in `/download`, so the MIME type is the only
      // thing that can name the file `.jpg`, and Photos routes on the extension
      // rather than on the bytes: without this, saving a photo the user is
      // looking at fails with "could not save this to your library".
      mimeType: item.mimeType,
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
      url: unavailable ? "" : manifest || preferLoadable(grant?.url, item.url),
      downloadUrl: unavailable || isAdaptiveManifest(downloadCandidate) ? "" : downloadCandidate,
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
      alt: label,
      // Save and Share go through the downloader, which spends this at most once
      // on a 401/403. Rendering never needs it — the resolve effect above already
      // holds a fresh grant by the time a frame is painted.
      refreshUrl: refreshAccessUrlFor(item)
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
