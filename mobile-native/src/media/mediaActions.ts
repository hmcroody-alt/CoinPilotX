/**
 * Shared media actions — Stages 7, 8 and 39.
 *
 * Save to Photos and share-as-file, owned once. Before this module neither
 * existed: `expo-media-library` was not a dependency, so nothing in PulseSoc
 * could write to the camera roll, and every `handleSave` in the app is a
 * bookmark-to-collection, not a download. `nativeShare` could share a *link*
 * but never a file, so "share this photo" put a URL in Messages and the
 * recipient got a login wall instead of a picture.
 *
 * ## Why these return a result instead of throwing
 *
 * Stage 7 ends with "never falsely report Saved", and that is harder than it
 * sounds, because the honest outcomes are not two but six: saved; the user
 * declined Photos access; the user granted *limited* access; the file could not
 * be fetched; the file is a type Photos will not accept; the write itself
 * failed. A boolean collapses five of those into "didn't work" and a thrown
 * error tempts every call site into `catch { toast("Saved") }` — which is the
 * exact lie the stage forbids.
 *
 * So the contract is a discriminated union. A caller cannot render a success
 * message without having matched `status === "saved"`, and every failure arrives
 * with a message already written for it.
 *
 * ## Why "limited" is a success and "denied" is not
 *
 * On iOS the user can grant access to *selected photos only*. For reading that
 * is a real restriction. For adding — which is all we do — the add-only
 * entitlement still permits the write, so treating `limited` as a failure would
 * refuse to save for a user who never denied us anything. The distinction is
 * kept in the result so telemetry can tell the two apart.
 *
 * ## Ordering
 *
 * Stage 39 fixes the action order across surfaces as React, Reply, Forward,
 * Share, Save. `MEDIA_ACTION_ORDER` is that order as data, so a new surface
 * inherits it instead of re-deciding it, and the regression test has something
 * to assert against.
 */
import * as FileSystem from "expo-file-system/legacy";
import * as MediaLibrary from "expo-media-library";
import * as Sharing from "expo-sharing";

import { sharePulseObject, type PulseShareMetadata } from "../sharing/nativeShare";
import { downloadMedia, downloadMessageFor, MediaDownloadError, type MediaDownloadKind } from "./mediaDownloader";
import { mediaFailureReason, trackMediaEvent, type MediaFailureReason } from "./mediaTelemetry";

export const MEDIA_ACTION_ORDER = ["react", "reply", "forward", "share", "save"] as const;
export type MediaActionName = (typeof MEDIA_ACTION_ORDER)[number];

export type MediaActionTarget = {
  url: string;
  mediaId?: number | string | null;
  kind?: MediaDownloadKind;
  mimeType?: string;
  expectedBytes?: number;
  surface?: string;
  /** Canonical PulseSoc link, used when sharing a *post* rather than a *file*. */
  sourceUrl?: string;
  title?: string;
  description?: string;
  author?: string;
  thumbnailUrl?: string;
  /**
   * Re-mint `url` when the server rejects the transfer as unauthorized.
   *
   * Save and Share differ from rendering in one way that matters: rendering
   * happens the instant the grant is issued, and these happen whenever the user
   * decides to tap. A messenger access URL is a fifteen-minute credential, so a
   * viewer left open past that renders a perfectly good decoded image and then
   * fails to save it. Passed straight through to `downloadMedia`, which spends it
   * at most once.
   */
  refreshUrl?: () => Promise<string>;
};

export type MediaSaveResult =
  | { status: "saved"; limited: boolean }
  | { status: "permission_denied"; message: string }
  | { status: "unsupported"; message: string }
  | { status: "failed"; reason: MediaFailureReason; message: string };

export type MediaShareResult =
  | { status: "shared"; mode: "file" | "link" }
  | { status: "failed"; reason: MediaFailureReason; message: string };

export type MediaOpenResult =
  | { status: "opened" }
  | { status: "unsupported"; message: string }
  | { status: "failed"; reason: MediaFailureReason; message: string };

/** Photos accepts pictures and movies. Audio and documents go to the share sheet. */
const SAVEABLE_KINDS = new Set<MediaDownloadKind>(["image", "video"]);

/**
 * Download if needed, then write to the device photo library.
 *
 * The download comes first and deliberately so: asking for Photos permission
 * before we know we have a file to write produces a permission prompt followed
 * by a failure, which reads to the user as "PulseSoc took my photo access and
 * then broke". Fetch, then ask, then write.
 */
export async function saveMediaToGallery(target: MediaActionTarget): Promise<MediaSaveResult> {
  const kind = target.kind || "file";
  if (!SAVEABLE_KINDS.has(kind)) {
    return {
      status: "unsupported",
      message: "Only photos and videos can be saved to your library. Use Share to send this file."
    };
  }

  let fileUri: string;
  try {
    const entry = await downloadMedia({
      url: target.url,
      mediaId: target.mediaId,
      mimeType: target.mimeType,
      kind,
      surface: target.surface,
      expectedBytes: target.expectedBytes,
      // The user asked for this specific file by tapping Save, Share or Open.
      retention: "explicit",
      refreshUrl: target.refreshUrl
    });
    fileUri = entry.fileUri;
  } catch (error) {
    const reason = error instanceof MediaDownloadError ? error.reason : mediaFailureReason(error);
    trackMediaEvent({ name: "MEDIA_SAVE_FAILED", kind, surface: target.surface, reason });
    return { status: "failed", reason, message: downloadMessageFor(reason) };
  }

  const permission = await requestSavePermission();
  if (!permission.allowed) {
    trackMediaEvent({
      name: "MEDIA_SAVE_FAILED",
      kind,
      surface: target.surface,
      reason: "permission_denied"
    });
    return {
      status: "permission_denied",
      message: permission.canAskAgain
        ? "PulseSoc needs access to your photo library to save this."
        : "Allow photo library access for PulseSoc in Settings to save media."
    };
  }

  let saveUri = fileUri;
  try {
    saveUri = await namedCopyOf(fileUri, kind, target.mimeType);
    await MediaLibrary.saveToLibraryAsync(saveUri);
  } catch (error) {
    const reason = mediaFailureReason(error);
    trackMediaEvent({ name: "MEDIA_SAVE_FAILED", kind, surface: target.surface, reason });
    return {
      status: "failed",
      reason,
      message: "PulseSoc could not save this to your library. Check available storage and try again."
    };
  } finally {
    // The copy exists only to give Photos a filename it will accept. The cache
    // still owns the real bytes, so keeping it would double the disk cost of
    // every save for no benefit.
    if (saveUri !== fileUri) {
      await FileSystem.deleteAsync(saveUri, { idempotent: true }).catch(() => undefined);
    }
  }

  trackMediaEvent({ name: "MEDIA_SAVE_SUCCEEDED", kind, surface: target.surface });
  return { status: "saved", limited: permission.limited };
}

async function requestSavePermission(): Promise<{ allowed: boolean; limited: boolean; canAskAgain: boolean }> {
  // `writeOnly: true` asks for the narrowest entitlement that can do the job.
  // Requesting full library read to save one photo is the kind of over-ask that
  // gets an app declined, and we genuinely do not need it.
  const current = await MediaLibrary.getPermissionsAsync(true).catch(() => null);
  const response = current?.granted ? current : await MediaLibrary.requestPermissionsAsync(true).catch(() => null);
  if (!response) return { allowed: false, limited: false, canAskAgain: false };
  const limited = response.accessPrivileges === "limited";
  return {
    allowed: Boolean(response.granted) || limited,
    limited,
    canAskAgain: response.canAskAgain !== false
  };
}

/**
 * iOS routes on the extension, not on the bytes — for both consumers.
 *
 * The photo library write decides image-versus-movie from the file name and
 * refuses a file with no extension at all, even a valid JPEG. The share sheet is
 * the same story with a different symptom: an extensionless file is offered as
 * an untyped document, so the recipient gets `cm58uqq` they cannot open and the
 * sheet drops every image-aware target. Declaring `mimeType` and `UTI` does not
 * rescue it; the file name wins.
 *
 * The download engine names its files now, but this is the boundary that
 * actually has the constraint, and it has to hold for two cases the engine
 * cannot reach: entries already sitting in the cache from before that fix, and
 * any future producer that resolves a file some other way.
 *
 * Returns the original URI unchanged when it already has an extension, so the
 * common path copies nothing. The basename stays the cache digest rather than
 * becoming the media's title — a shared filename is visible to the recipient,
 * and a chat photo's title is not ours to disclose.
 */
async function namedCopyOf(fileUri: string, kind: MediaDownloadKind, mimeType?: string): Promise<string> {
  const name = fileUri.split("/").pop() || "";
  if (/\.[A-Za-z0-9]{1,5}$/.test(name)) return fileUri;

  const extension = SAVE_EXTENSIONS[mimeType ? mimeType.split(";")[0].trim().toLowerCase() : ""]
    || (kind === "video" ? ".mp4" : ".jpg");
  const directory = `${FileSystem.cacheDirectory || ""}pulsesoc-save/`;
  await FileSystem.makeDirectoryAsync(directory, { intermediates: true }).catch(() => undefined);
  const target = `${directory}${name || `media-${Date.now()}`}${extension}`;
  await FileSystem.deleteAsync(target, { idempotent: true }).catch(() => undefined);
  await FileSystem.copyAsync({ from: fileUri, to: target });
  return target;
}

/**
 * Only the types Photos accepts. Anything else falls back on `kind`, because a
 * wrong still-image extension saves correctly — iOS decodes the real data — and
 * no extension does not.
 */
const SAVE_EXTENSIONS: Record<string, string> = {
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/png": ".png",
  "image/gif": ".gif",
  "image/heic": ".heic",
  "image/heif": ".heif",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov"
};

/**
 * Share the actual file through the OS share sheet, falling back to the
 * canonical PulseSoc link.
 *
 * Which one is correct depends on what is being shared, and the caller says so
 * by passing `preferLink`. A photo from a chat is a *file* — the recipient wants
 * the picture, and a link would hand them a login wall. A post or a reel is a
 * *link* — it has an OpenGraph preview, comments, and an author, none of which
 * survive being flattened into a JPEG. Stage 8's "do not force screenshots as
 * sharing" is the file path; "share canonical link when content semantics
 * require it" is the other.
 *
 * The link is also the fallback whenever the file cannot be produced, so a
 * failed download degrades to a working share rather than to nothing.
 */
export async function shareMedia(
  target: MediaActionTarget,
  options: { preferLink?: boolean; shareKind?: PulseShareMetadata["kind"] } = {}
): Promise<MediaShareResult> {
  const kind = target.kind || "file";

  if (!options.preferLink) {
    const shareable = await Sharing.isAvailableAsync().catch(() => false);
    if (shareable) {
      let shareUri = "";
      let sourceUri = "";
      try {
        const entry = await downloadMedia({
          url: target.url,
          mediaId: target.mediaId,
          mimeType: target.mimeType,
          kind,
          surface: target.surface,
          expectedBytes: target.expectedBytes,
          // The user asked for this specific file by tapping Save, Share or Open.
          retention: "explicit",
          // Share is reached whenever the user taps, which can be long after the
          // fifteen-minute access URL that painted the picture was minted. Without
          // this, sharing a photo that is on screen degrades to sharing a link.
          refreshUrl: target.refreshUrl
        });
        sourceUri = entry.fileUri;
        shareUri = await namedCopyOf(entry.fileUri, kind, entry.mimeType || target.mimeType);
        await Sharing.shareAsync(shareUri, {
          mimeType: entry.mimeType || target.mimeType,
          UTI: utiFor(kind, entry.mimeType || target.mimeType),
          dialogTitle: target.title || "Share media"
        });
        return { status: "shared", mode: "file" };
      } catch {
        // Fall through to the link. A share sheet the user dismissed and a
        // download that failed are indistinguishable here, and in both cases
        // offering the link is better than reporting an error.
      } finally {
        // `shareAsync` resolves once the sheet closes, so by here the OS is done
        // reading the file. The cache still owns the real bytes.
        if (shareUri && shareUri !== sourceUri) {
          await FileSystem.deleteAsync(shareUri, { idempotent: true }).catch(() => undefined);
        }
      }
    }
  }

  const url = target.sourceUrl || target.url;
  if (!url) {
    return { status: "failed", reason: "not_found", message: "There is nothing to share for this media." };
  }

  try {
    await sharePulseObject({
      kind: options.shareKind || "media",
      url,
      title: target.title,
      description: target.description,
      author: target.author,
      previewImageUrl: target.thumbnailUrl
    });
    return { status: "shared", mode: "link" };
  } catch (error) {
    const reason = mediaFailureReason(error);
    return { status: "failed", reason, message: "PulseSoc could not open the share sheet." };
  }
}

/**
 * Open a document in the OS document viewer.
 *
 * Distinct from `shareMedia` in one way that matters: there is no link
 * fallback. The user asked to read this file, and answering with a share sheet
 * for a URL their recipient would hit a login wall on is not the same thing —
 * a real error is more useful than a silent substitution.
 *
 * The bytes come through `downloadMedia`, which is where the media access grant,
 * the retry/backoff and the on-disk cache already live. Opening the same
 * attachment twice costs one download.
 */
export async function openDocument(target: MediaActionTarget): Promise<MediaOpenResult> {
  const available = await Sharing.isAvailableAsync().catch(() => false);
  if (!available) {
    return { status: "unsupported", message: "This device cannot open documents from PulseSoc." };
  }

  let fileUri: string;
  let mimeType: string | undefined;
  try {
    const entry = await downloadMedia({
      url: target.url,
      mediaId: target.mediaId,
      mimeType: target.mimeType,
      kind: "file",
      surface: target.surface,
      expectedBytes: target.expectedBytes,
      // The user asked for this specific file by tapping Save, Share or Open.
      retention: "explicit",
      refreshUrl: target.refreshUrl
    });
    fileUri = entry.fileUri;
    mimeType = entry.mimeType || target.mimeType;
  } catch (error) {
    const reason = error instanceof MediaDownloadError ? error.reason : mediaFailureReason(error);
    trackMediaEvent({ name: "MEDIA_OPEN_FAILED", kind: "file", surface: target.surface, reason });
    return { status: "failed", reason, message: downloadMessageFor(reason) };
  }

  try {
    await Sharing.shareAsync(fileUri, {
      mimeType,
      UTI: utiFor("file", mimeType),
      dialogTitle: target.title || "Open document"
    });
  } catch (error) {
    const reason = mediaFailureReason(error);
    trackMediaEvent({ name: "MEDIA_OPEN_FAILED", kind: "file", surface: target.surface, reason });
    return { status: "failed", reason, message: "PulseSoc could not open this document." };
  }
  trackMediaEvent({ name: "MEDIA_OPENED", kind: "file", surface: target.surface });
  return { status: "opened" };
}

/**
 * Documents need their *exact* UTI or iOS offers nothing that can read them —
 * `public.data` produces a sheet with no viewer. Media is the opposite: the
 * broad family type (`public.image`, not `public.jpeg`) lets every picture app
 * appear, which is what a user sharing a photo expects.
 */
const DOCUMENT_UTI: Record<string, string> = {
  "application/pdf": "com.adobe.pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "org.openxmlformats.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "org.openxmlformats.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "org.openxmlformats.presentationml.presentation",
  "application/msword": "com.microsoft.word.doc",
  "application/vnd.ms-excel": "com.microsoft.excel.xls",
  "application/vnd.ms-powerpoint": "com.microsoft.powerpoint.ppt",
  "text/plain": "public.plain-text",
  "text/csv": "public.comma-separated-values-text"
};

function utiFor(kind: MediaDownloadKind, mimeType?: string): string | undefined {
  const documentUti = DOCUMENT_UTI[String(mimeType || "").split(";")[0].trim().toLowerCase()];
  if (documentUti) return documentUti;
  switch (kind) {
    case "image":
      return "public.image";
    case "video":
      return "public.movie";
    case "audio":
      return "public.audio";
    default:
      return undefined;
  }
}
