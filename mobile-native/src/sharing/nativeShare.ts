import { Share, ShareAction } from "react-native";

export type PulseShareKind =
  | "post"
  | "reel"
  | "status"
  | "live"
  | "profile"
  | "marketplace"
  | "business"
  | "event"
  | "music"
  | "media";

export type PulseShareMetadata = {
  kind: PulseShareKind;
  url: string;
  title?: string;
  description?: string;
  author?: string;
  previewImageUrl?: string;
};

export type NativeSharePayload = {
  title: string;
  message: string;
  url: string;
};

const KIND_LABELS: Record<PulseShareKind, string> = {
  post: "Post",
  reel: "Reel",
  status: "Status",
  live: "Live",
  profile: "Profile",
  marketplace: "Marketplace listing",
  business: "Business",
  event: "Event",
  music: "Music",
  media: "Media"
};

function cleanLine(value?: string, maxLength = 280) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, maxLength);
}

export function buildNativeSharePayload(metadata: PulseShareMetadata): NativeSharePayload {
  const fallbackTitle = `PulseSoc ${KIND_LABELS[metadata.kind]}`;
  const title = cleanLine(metadata.title, 120) || fallbackTitle;
  const author = cleanLine(metadata.author, 100);
  const description = cleanLine(metadata.description, 320);
  const url = String(metadata.url || "").trim();
  const message = [
    title,
    author ? `By ${author}` : "",
    description && description !== title ? description : "",
    url
  ].filter(Boolean).join("\n");

  return { title, message, url };
}

/**
 * Opens the operating system share sheet with human-readable object metadata
 * and the canonical PulseSoc universal link. The linked web object owns its
 * OpenGraph preview; installed native clients receive the same URL through
 * their platform association.
 */
export async function sharePulseObject(metadata: PulseShareMetadata): Promise<ShareAction> {
  const payload = buildNativeSharePayload(metadata);
  return Share.share(
    payload,
    {
      dialogTitle: `Share ${KIND_LABELS[metadata.kind]}`,
      subject: payload.title
    }
  );
}

