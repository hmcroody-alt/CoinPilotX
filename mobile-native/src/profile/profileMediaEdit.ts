/**
 * The one path a profile photo or cover takes from the picker to the server.
 *
 * Both entry points the product requires — the camera badge on the profile hero
 * and the Edit Profile form — call through here, so there is a single crop
 * ratio, a single filename rule and a single cache-invalidation broadcast. The
 * upload itself still belongs to `api/profile`, which owns the endpoints.
 */

import * as ImagePicker from "expo-image-picker";
import { PulseProfile, uploadProfileAvatar, uploadProfileCover } from "../api/profile";
import { invalidateNativeSync } from "../core/eventSync";
import { haptic } from "../native/haptics";

export type ProfileImageKind = "avatar" | "cover";

/**
 * Crop ratios offered in the picker.
 *
 * The cover ratio tracks what the hero actually displays (full width against
 * `PROFILE_HERO_HEIGHT`, drawn with `resizeMode="cover"`). The 16:6 panorama
 * this replaces was nearly twice as wide as the hero, so the hero centre-cropped
 * away the sides: whatever the owner framed in the crop box was not what their
 * profile showed.
 */
const ASPECT: Record<ProfileImageKind, [number, number]> = {
  avatar: [1, 1],
  cover: [3, 2]
};

/**
 * What the upload endpoints accept. The server re-checks the magic bytes, so
 * this is not the security boundary — it is here so the filename we send agrees
 * with the bytes we send.
 */
const EXTENSION_BY_MIME: Record<string, { mimeType: string; extension: string }> = {
  "image/jpeg": { mimeType: "image/jpeg", extension: "jpg" },
  "image/jpg": { mimeType: "image/jpeg", extension: "jpg" },
  "image/png": { mimeType: "image/png", extension: "png" },
  "image/webp": { mimeType: "image/webp", extension: "webp" }
};

export type ProfileImageAsset = { uri: string; name: string; mimeType: string };

export type ProfileImagePick =
  | ({ status: "picked" } & ProfileImageAsset)
  | { status: "cancelled" }
  | { status: "denied" }
  | { status: "unsupported"; mimeType: string };

export function profileImageLabel(kind: ProfileImageKind) {
  return kind === "avatar" ? "Profile photo" : "Cover photo";
}

/**
 * Normalise the picker's answer into something the endpoint will accept.
 *
 * The filename is derived from the MIME type rather than taken from the asset.
 * iOS hands back `IMG_0001.HEIC` alongside `image/jpeg` once `allowsEditing`
 * has re-encoded the crop, and the server checks the *extension* against its
 * allowlist before it looks at anything else — so passing the original name
 * through failed an upload whose bytes were perfectly valid.
 */
function describeAsset(kind: ProfileImageKind, asset: { uri: string; mimeType?: string | null }): ProfileImagePick {
  const declared = String(asset.mimeType || "").toLowerCase().split(";")[0].trim();
  // An absent type is the picker not telling us, which is common and benign;
  // a *recognised but unsupported* type is a real answer we must not paper over.
  const resolved = declared ? EXTENSION_BY_MIME[declared] : EXTENSION_BY_MIME["image/jpeg"];
  if (!resolved) return { status: "unsupported", mimeType: declared };
  return {
    status: "picked",
    uri: asset.uri,
    name: `${kind}.${resolved.extension}`,
    mimeType: resolved.mimeType
  };
}

export async function pickProfileImage(kind: ProfileImageKind): Promise<ProfileImagePick> {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) return { status: "denied" };
  const result = await ImagePicker.launchImageLibraryAsync({
    allowsEditing: true,
    mediaTypes: ImagePicker.MediaTypeOptions.Images,
    quality: 0.88,
    aspect: ASPECT[kind]
  });
  const asset = result.canceled ? null : result.assets?.[0];
  if (!asset) return { status: "cancelled" };
  return describeAsset(kind, asset);
}

/**
 * Upload, then tell the rest of the app the viewer's identity media moved.
 *
 * The returned profile is already cache-busted by the endpoint and written to
 * the `me` profile cache by `api/profile`. The broadcast is what reaches the
 * surfaces that hold their own copy — the global header avatar above all, which
 * otherwise keeps whatever it fetched when the app launched.
 */
export async function saveProfileImage(kind: ProfileImageKind, asset: ProfileImageAsset): Promise<PulseProfile> {
  const profile = kind === "avatar" ? await uploadProfileAvatar(asset) : await uploadProfileCover(asset);
  await announceProfileMediaChange(kind);
  haptic("success");
  return profile;
}

export async function announceProfileMediaChange(kind: ProfileImageKind | "identity") {
  await invalidateNativeSync(["profile"], `profile_${kind}_updated`, [
    {
      event_type: `pulse_profile_${kind}_updated`,
      entity_type: "profile",
      invalidates: ["profile"],
      metadata: { source: "native_profile" }
    }
  ]).catch(() => undefined);
}

/** Message for the states where nothing was uploaded and the user needs to know why. */
export function describeProfilePick(kind: ProfileImageKind, pick: ProfileImagePick) {
  if (pick.status === "denied") {
    return `PulseSoc needs photo access to change your ${profileImageLabel(kind).toLowerCase()}.`;
  }
  if (pick.status === "unsupported") {
    return `That image format is not supported. Choose a JPEG, PNG or WebP.`;
  }
  return "";
}
