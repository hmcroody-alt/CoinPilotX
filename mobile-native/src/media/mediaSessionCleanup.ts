import AsyncStorage from "@react-native-async-storage/async-storage";
import { resetMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { clearAllMediaCaches, setMediaCacheScope } from "./mediaCache";
import { resetMessengerMediaAccess } from "./messengerMediaAccess";

const USER_SCOPED_MEDIA_PREFIXES = [
  "pulsesoc.native.feed.",
  "pulsesoc.native.post.",
  "pulsesoc.native.reels.",
  "pulsesoc.native.status.",
  "pulsesoc.native.messenger.",
  /** Cache index and download-resume offsets — see `mediaCache`. */
  "pulsesoc.native.mediacache."
];

/**
 * Called on every sign-out path.
 *
 * The AsyncStorage sweep alone is not enough now that media is cached on disk:
 * clearing the *index* while leaving the files would hide user A's private
 * photos from the app but leave them readable on the filesystem, and a stale
 * index is not a security boundary. `clearAllMediaCaches` removes the bytes.
 *
 * It clears every account's cache, not just the one signing out — a handset that
 * has hosted three accounts should not still hold the first two's private media
 * because only the third bothered to sign out. This is the Stage 35 P0 test:
 * user A views private media, signs out, user B signs in, and B must find
 * nothing. Resetting the scope to anonymous afterwards means anything cached
 * between sign-out and the next sign-in lands in `anon`, never in A's directory.
 */
export async function clearUserScopedMediaState() {
  await resetMediaPlayback().catch(() => undefined);
  resetMessengerMediaAccess();
  await clearAllMediaCaches().catch(() => undefined);
  setMediaCacheScope(null);
  const keys = await AsyncStorage.getAllKeys().catch(() => [] as string[]);
  const scoped = keys.filter((key) => USER_SCOPED_MEDIA_PREFIXES.some((prefix) => key.startsWith(prefix)));
  if (scoped.length) await AsyncStorage.multiRemove(scoped).catch(() => undefined);
}
