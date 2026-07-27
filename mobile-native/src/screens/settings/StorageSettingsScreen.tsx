import { useCallback, useEffect, useRef, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Directory, File, Paths } from "expo-file-system";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  confirm,
  SettingsButton,
  SettingsSelect,
  SettingsSlider,
  SettingsSwitch,
  SelectOption
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { AutoDownloadPolicy, CACHE_LIMIT_MAX_MB, CACHE_LIMIT_MIN_MB, MediaQuality } from "../../settings/schema";
import { useTheme } from "../../theme/ThemeContext";

const CACHE_LIMIT_STEP_MB = 128;

/**
 * Ceiling on entries visited while measuring. A cache with tens of thousands of
 * files would otherwise pin the JS thread on synchronous native calls for
 * seconds; a "700 MB or more" answer within a frame budget beats an exact one
 * that freezes the screen.
 */
const MAX_MEASURED_ENTRIES = 5000;

const AUTO_DOWNLOAD_OPTIONS: SelectOption<AutoDownloadPolicy>[] = [
  { value: "always", label: "Wi-Fi and mobile data", description: "Fastest, uses the most data.", icon: "cellular-outline" },
  { value: "wifi", label: "Wi-Fi only", description: "Downloads while you're on Wi-Fi; taps to load elsewhere.", icon: "wifi-outline" },
  { value: "never", label: "Never", description: "Nothing downloads until you open it.", icon: "close-circle-outline" }
];

const MEDIA_QUALITY_OPTIONS: SelectOption<MediaQuality>[] = [
  { value: "auto", label: "Automatic", description: "Matches quality to your current connection speed.", icon: "speedometer-outline" },
  { value: "high", label: "High quality", description: "Full-resolution photos and video. Noticeably more data.", icon: "sparkles-outline" },
  { value: "data_saver", label: "Data saver", description: "Compressed media everywhere. Best on a metered plan.", icon: "leaf-outline" }
];

type CacheUsage =
  | { state: "measuring" }
  /** `atLeast` is true when the entry budget cut the walk short. */
  | { state: "ready"; bytes: number; atLeast: boolean }
  | { state: "unavailable" };

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function formatMegabytes(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${Math.round(mb)} MB`;
}

/** Let the UI thread breathe between directory levels — `list()`/`size` are synchronous native calls. */
function yieldToUi(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Total bytes under the OS cache directory.
 *
 * `Directory.size` exists in expo-file-system 19 but is documented as nullable
 * and is not consistently recursive across platforms, so the tree is walked
 * explicitly. Unreadable subtrees are skipped rather than aborting the whole
 * measurement — a sandboxed subfolder should not turn a real number into an
 * error.
 */
async function measureCacheUsage(): Promise<{ bytes: number; atLeast: boolean } | null> {
  let root: Directory;
  try {
    root = Paths.cache;
    if (!root.exists) return null;
  } catch {
    return null;
  }

  let bytes = 0;
  let visited = 0;
  let atLeast = false;
  const queue: Directory[] = [root];
  let readAnything = false;

  while (queue.length) {
    const directory = queue.shift();
    if (!directory) break;

    let entries: (Directory | File)[];
    try {
      entries = directory.list();
      readAnything = true;
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (visited >= MAX_MEASURED_ENTRIES) {
        atLeast = true;
        queue.length = 0;
        break;
      }
      visited += 1;
      if (entry instanceof Directory) {
        queue.push(entry);
        continue;
      }
      try {
        bytes += entry.size ?? 0;
      } catch {
        // A file deleted between listing and sizing contributes nothing.
      }
    }

    await yieldToUi();
  }

  return readAnything ? { bytes, atLeast } : null;
}

/**
 * Storage & data.
 *
 * The download policies and quality settings are read by the media layer at
 * fetch time. The cache readout below is measured live rather than tracked
 * incrementally: the OS evicts cache files on its own, so any counter the app
 * maintained would drift away from the truth within days.
 */
export function StorageSettingsScreen() {
  const theme = useTheme();
  const { value, setGroup, pending } = usePreferenceGroup("storage");
  const [usage, setUsage] = useState<CacheUsage>({ state: "measuring" });
  const [clearing, setClearing] = useState(false);
  const mounted = useRef(true);

  const refreshUsage = useCallback(async () => {
    setUsage({ state: "measuring" });
    const result = await measureCacheUsage().catch(() => null);
    if (!mounted.current) return;
    setUsage(result ? { state: "ready", bytes: result.bytes, atLeast: result.atLeast } : { state: "unavailable" });
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refreshUsage();
    return () => {
      mounted.current = false;
    };
  }, [refreshUsage]);

  const clearCache = useCallback(async () => {
    const confirmed = await confirm({
      title: "Clear cached media?",
      message:
        "Photos, videos, and audio will be downloaded again next time you open them. Your posts, drafts, and messages are not affected.",
      confirmLabel: "Clear",
      destructive: true
    });
    if (!confirmed) return;

    setClearing(true);
    try {
      // Entries are deleted individually instead of removing the cache
      // directory itself: the OS owns that folder, and a file currently open by
      // the media player must not take the whole operation down with it.
      const entries = Paths.cache.list();
      for (const entry of entries) {
        try {
          entry.delete();
        } catch {
          // Locked or already gone — the next sweep will pick it up.
        }
        await yieldToUi();
      }
    } catch {
      // Nothing readable to clear; the re-measure below reports the real state.
    } finally {
      if (mounted.current) setClearing(false);
      await refreshUsage();
    }
  }, [refreshUsage]);

  const limitBytes = value.cacheLimitMb * 1024 * 1024;
  const usedBytes = usage.state === "ready" ? usage.bytes : 0;
  const fillRatio = usage.state === "ready" ? Math.min(1, usedBytes / Math.max(limitBytes, 1)) : 0;
  const overLimit = usage.state === "ready" && usedBytes > limitBytes;

  const usageLabel =
    usage.state === "measuring"
      ? "Measuring…"
      : usage.state === "unavailable"
        ? "Not available on this device"
        : `${usage.atLeast ? "At least " : ""}${formatBytes(usedBytes)} of ${formatMegabytes(value.cacheLimitMb)}`;

  return (
    // Pull-to-refresh re-measures on demand; the shell owns the spinner while
    // `refreshUsage` is awaited, so no extra refreshing flag is needed here.
    <SettingsShell bottomDock={false} onRefresh={refreshUsage}>
      <SettingsHeader title="Storage & data" subtitle="Control what PulseSoc downloads and how much of your phone it keeps." />

      <SettingsSection
        title="Cache"
        description="Media PulseSoc keeps on this device so it loads instantly the second time."
        footnote={
          usage.state === "unavailable"
            ? "PulseSoc couldn't read the cache folder on this device, so the size is unknown. Clearing still works."
            : overLimit
              ? "You're above your cache limit. Clearing now frees the difference immediately."
              : undefined
        }
        busy={pending}
      >
        <View style={[styles.usage, { padding: theme.metrics.rowPaddingHorizontal }]}>
          <View style={styles.usageHeader}>
            <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: "600" }}>Cached media</Text>
            <Text
              testID="storage-cache-usage"
              accessibilityLiveRegion="polite"
              style={{
                color: overLimit ? theme.colors.warning : theme.colors.accent,
                fontSize: theme.scaleFont(15),
                fontWeight: "700"
              }}
            >
              {usageLabel}
            </Text>
          </View>
          {usage.state === "ready" ? (
            <View
              accessible
              accessibilityRole="progressbar"
              accessibilityLabel="Cache usage against your limit"
              accessibilityValue={{ min: 0, max: value.cacheLimitMb, now: Math.round(usedBytes / (1024 * 1024)), text: usageLabel }}
              style={[styles.track, { backgroundColor: theme.colors.border }]}
            >
              <View
                style={[
                  styles.fill,
                  { backgroundColor: overLimit ? theme.colors.warning : theme.colors.accent, width: `${fillRatio * 100}%` }
                ]}
              />
            </View>
          ) : null}
        </View>
        <View style={{ padding: theme.metrics.rowPaddingHorizontal }}>
          <SettingsButton
            testID="storage-clear-cache"
            label={clearing ? "Clearing…" : "Clear cache"}
            icon="trash-outline"
            variant="destructive"
            busy={clearing}
            disabled={usage.state === "measuring"}
            onPress={() => void clearCache()}
          />
        </View>
      </SettingsSection>

      <SettingsSection title="Cache limit" footnote="PulseSoc keeps recently viewed media up to this size. Your device may still reclaim it when storage runs low.">
        <SettingsSlider
          testID="storage-cache-limit"
          title="Maximum size"
          subtitle="A larger cache means less re-downloading on a slow connection."
          value={value.cacheLimitMb}
          minimumValue={CACHE_LIMIT_MIN_MB}
          maximumValue={CACHE_LIMIT_MAX_MB}
          step={CACHE_LIMIT_STEP_MB}
          onChange={(next) => void setGroup({ cacheLimitMb: next })}
          formatValue={formatMegabytes}
        />
        <SettingsSwitch
          testID="storage-auto-clear-cache"
          title="Trim automatically"
          subtitle="Drops the least recently viewed media once the cache passes the limit above, without asking."
          icon="refresh-outline"
          value={value.autoClearCache}
          onValueChange={(next) => void setGroup({ autoClearCache: next })}
        />
      </SettingsSection>

      <SettingsSection title="Media quality">
        <SettingsSelect
          options={MEDIA_QUALITY_OPTIONS}
          value={value.mediaQuality}
          onChange={(next) => void setGroup({ mediaQuality: next })}
          testID="storage-media-quality"
        />
      </SettingsSection>

      <SettingsSection title="Auto-download photos">
        <SettingsSelect
          options={AUTO_DOWNLOAD_OPTIONS}
          value={value.autoDownloadPhotos}
          onChange={(next) => void setGroup({ autoDownloadPhotos: next })}
          testID="storage-auto-photos"
        />
      </SettingsSection>

      <SettingsSection title="Auto-download videos">
        <SettingsSelect
          options={AUTO_DOWNLOAD_OPTIONS}
          value={value.autoDownloadVideos}
          onChange={(next) => void setGroup({ autoDownloadVideos: next })}
          testID="storage-auto-videos"
        />
      </SettingsSection>

      <SettingsSection
        title="Auto-download audio"
        footnote="Voice notes are always downloaded when you play them, regardless of this setting."
      >
        <SettingsSelect
          options={AUTO_DOWNLOAD_OPTIONS}
          value={value.autoDownloadAudio}
          onChange={(next) => void setGroup({ autoDownloadAudio: next })}
          testID="storage-auto-audio"
        />
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  usage: { width: "100%" },
  usageHeader: { alignItems: "center", flexDirection: "row", gap: 12, justifyContent: "space-between" },
  track: { borderRadius: 3, height: 6, marginTop: 12, overflow: "hidden", width: "100%" },
  fill: { height: "100%" }
});
