import AsyncStorage from "@react-native-async-storage/async-storage";
import { resetJsonCacheMemory } from "../core/cache";
import { resetMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { accountScopedKeys } from "../core/storageScope";
import { clearAllMediaCaches, setMediaCacheScope } from "./mediaCache";
import { resetMessengerMediaAccess } from "./messengerMediaAccess";

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
 *
 * The AsyncStorage half of this used to name six prefixes explicitly, which had
 * stopped describing the app long ago — profiles, the activity inbox, account
 * state, the saved library, recent searches, the radio queue and every composer
 * draft were all outside it. `accountScopedKeys` inverts the rule so the default
 * is deletion; see `core/storageScope` for what is deliberately kept and why.
 *
 * ON THE MEMORY TIER
 *
 * `resetJsonCacheMemory()` is not housekeeping. `core/cache` answers reads from
 * a module-level Map before it touches AsyncStorage, so removing a key from disk
 * while that Map survives leaves the value perfectly readable by the next
 * account — the only code path that reads it never gets as far as the storage we
 * cleared. Without this line the sweep above is close to decorative for any key
 * read during the outgoing session.
 */
export async function clearUserScopedMediaState() {
  await resetMediaPlayback().catch(() => undefined);
  resetMessengerMediaAccess();
  await clearAllMediaCaches().catch(() => undefined);
  setMediaCacheScope(null);
  const keys = await AsyncStorage.getAllKeys().catch(() => [] as string[]);
  const scoped = accountScopedKeys(keys);
  if (scoped.length) await AsyncStorage.multiRemove(scoped).catch(() => undefined);
  resetJsonCacheMemory();
}
