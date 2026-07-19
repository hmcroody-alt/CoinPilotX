import AsyncStorage from "@react-native-async-storage/async-storage";
import { resetMediaPlayback } from "../core/mediaPlaybackCoordinator";

const USER_SCOPED_MEDIA_PREFIXES = [
  "pulsesoc.native.feed.",
  "pulsesoc.native.post.",
  "pulsesoc.native.reels.",
  "pulsesoc.native.status.",
  "pulsesoc.native.messenger."
];

export async function clearUserScopedMediaState() {
  await resetMediaPlayback().catch(() => undefined);
  const keys = await AsyncStorage.getAllKeys().catch(() => [] as string[]);
  const scoped = keys.filter((key) => USER_SCOPED_MEDIA_PREFIXES.some((prefix) => key.startsWith(prefix)));
  if (scoped.length) await AsyncStorage.multiRemove(scoped).catch(() => undefined);
}
