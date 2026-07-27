import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, StyleSheet, Text, View } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  SettingsEmptyState,
  SettingsHeader,
  SettingsSection,
  SettingsShell
} from "../../settings/components/SettingsShell";
import {
  SettingsBadge,
  SettingsRow,
  SettingsSwitch,
  SettingsValue,
  confirm
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup, usePreferences } from "../../settings/store";
import { configurePerfTracing, type PerfSample } from "../../core/perfTrace";
import { getSessionEnvelope } from "../../session/sessionStore";
import { useAuth } from "../../session/auth";
import { PULSE_API_BASE_URL } from "../../api/config";
import { useTheme } from "../../theme/ThemeContext";

/**
 * Every AsyncStorage key the settings platform owns. Cleared as a namespace
 * rather than by exact key so snapshots left behind by an older schema version
 * (`…settings.v1` today, `.v2` tomorrow) are swept up too.
 */
const SETTINGS_CACHE_PREFIX = "pulsesoc.native.settings";

/**
 * Sink used by `verboseApiLogging`.
 *
 * `pulseApi` already opens an `api.request` span per call, so verbose logging is
 * a matter of listening rather than instrumenting. Defined at module scope so
 * the identity is stable — re-registering a new closure on every render would
 * churn the tracer's sink slot for no reason.
 */
function verboseApiSink(sample: PerfSample): void {
  if (!sample.name.startsWith("api.")) return;
  console.log(`[pulse-api] ${sample.name} ${sample.durationMs}ms`, sample.attributes);
}

/* -------------------------------------------------------------------------- */
/*                                 Formatting                                  */
/* -------------------------------------------------------------------------- */

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** Countdown for a future timestamp; "expired" once it has passed. */
function formatExpiry(timestamp: number): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "not set";
  const remaining = timestamp - Date.now();
  if (remaining <= 0) return "expired";
  const minutes = Math.round(remaining / 60_000);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} d`;
}

type StorageStats = { keys: number; bytes: number; settingsKeys: number };

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * Developer options.
 *
 * Reachable only after unlocking it in About (tap the version seven times), and
 * everything on it is real: the switches drive live runtime configuration, the
 * diagnostics are read from the actual session and storage layers, and the
 * destructive actions go through the same store the rest of Settings uses.
 *
 * Nothing here prints a token, a cookie, or a refresh secret — expiry times and
 * presence flags are enough to debug an auth problem, and a screenshot of this
 * screen pasted into a bug report must not be a credential leak.
 */
export function DeveloperSettingsScreen() {
  const theme = useTheme();
  const { authState } = useAuth();
  const { value, setGroup, pending } = usePreferenceGroup("developer");
  const { resetAll } = usePreferences();

  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [envelope, setEnvelope] = useState<{ present: boolean; accessExpiry: string; refreshExpiry: string } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /**
   * Apply both developer toggles to the tracer in one place so the runtime
   * state can never disagree with the two switches.
   *
   * Note the coupling this inherits from `perfTrace`: `isPerfTracingEnabled()`
   * is true when tracing is enabled *or* a sink is registered, and `PerfOverlay`
   * renders on exactly that predicate. So verbose logging also collects samples
   * — which is intended, you cannot log what was never recorded. Separately,
   * `App.tsx` only mounts `PerfOverlay` at all in dev builds or when the
   * EXPO_PUBLIC_PULSESOC_PERF_OVERLAY flag is set, so on a plain Release build
   * this switch arms the overlay without being able to mount it.
   *
   * No cleanup on unmount: this is global runtime configuration and is supposed
   * to outlive the screen that set it.
   */
  useEffect(() => {
    configurePerfTracing({
      enabled: value.showPerfOverlay,
      sink: value.verboseApiLogging ? verboseApiSink : null
    });
  }, [value.showPerfOverlay, value.verboseApiLogging]);

  const loadDiagnostics = useCallback(async () => {
    const [keys, session] = await Promise.all([
      AsyncStorage.getAllKeys().catch(() => [] as readonly string[]),
      getSessionEnvelope().catch(() => null)
    ]);

    // multiGet on the whole keyspace is heavy, but this screen is opened by
    // engineers on demand and an approximate size is worthless without it.
    // Cast rather than annotate: AsyncStorage's own `KeyValuePair` is a mutable
    // tuple, and the union it forms with the catch fallback makes `reduce`
    // resolve to the no-seed overload.
    const entries = (await AsyncStorage.multiGet(keys as string[]).catch(() => [])) as ReadonlyArray<
      [string, string | null]
    >;
    const bytes = entries.reduce<number>((total, pair) => total + pair[0].length + (pair[1]?.length ?? 0), 0);

    if (!mounted.current) return;
    setStorage({
      keys: keys.length,
      bytes,
      settingsKeys: keys.filter((key) => key.startsWith(SETTINGS_CACHE_PREFIX)).length
    });
    setEnvelope({
      present: Boolean(session),
      accessExpiry: session ? formatExpiry(session.accessTokenExpiresAt) : "—",
      refreshExpiry: session ? formatExpiry(session.refreshTokenExpiresAt) : "—"
    });
  }, []);

  useEffect(() => {
    void loadDiagnostics();
  }, [loadDiagnostics]);

  const clearSettingsCache = useCallback(async () => {
    const ok = await confirm({
      title: "Clear local settings cache?",
      message:
        "Removes the on-device preference snapshot. Your settings stay on the server and re-download on the next launch; anything not yet synced is lost.",
      confirmLabel: "Clear",
      destructive: true
    });
    if (!ok) return;

    setBusyAction("cache");
    try {
      const keys = await AsyncStorage.getAllKeys();
      const targets = keys.filter((key) => key.startsWith(SETTINGS_CACHE_PREFIX));
      if (targets.length) await AsyncStorage.multiRemove(targets as string[]);
      await loadDiagnostics();
      Alert.alert(
        "Cache cleared",
        `${targets.length} ${targets.length === 1 ? "key" : "keys"} removed. Values currently in memory are unchanged until the app restarts.`
      );
    } catch (caught) {
      const detail = caught instanceof Error && caught.message ? caught.message : "Unknown error.";
      Alert.alert("Couldn't clear the cache", detail);
    } finally {
      if (mounted.current) setBusyAction(null);
    }
  }, [loadDiagnostics]);

  const resetPreferences = useCallback(async () => {
    const ok = await confirm({
      title: "Reset all preferences?",
      message:
        "Every setting — appearance, notifications, privacy, security, storage, and these developer options — returns to its shipped default and syncs to your account on all devices.",
      confirmLabel: "Reset",
      destructive: true
    });
    if (!ok) return;

    setBusyAction("reset");
    try {
      await resetAll();
      // resetAll restores `developer.enabled: false`, so this screen becomes
      // unreachable again; say so rather than leaving the user wondering where
      // the entry went.
      Alert.alert(
        "Preferences reset",
        "Everything is back to defaults. Developer options are off again — tap the version in About seven times to bring them back."
      );
    } catch (caught) {
      const detail = caught instanceof Error && caught.message ? caught.message : "Unknown error.";
      Alert.alert("Couldn't reset preferences", detail);
    } finally {
      if (mounted.current) setBusyAction(null);
    }
  }, [resetAll]);

  const disableDeveloper = useCallback(async () => {
    const ok = await confirm({
      title: "Disable developer options?",
      message: "This screen disappears from Settings. Tap the version in About seven times to bring it back.",
      confirmLabel: "Disable",
      destructive: true
    });
    if (!ok) return;
    // Turn the diagnostics off with the door, so a forgotten overlay or a
    // console full of request logs does not outlive the debugging session.
    await setGroup({ enabled: false, showPerfOverlay: false, verboseApiLogging: false });
  }, [setGroup]);

  if (!value.enabled) {
    return (
      <SettingsShell bottomDock={false}>
        <SettingsHeader title="Developer" subtitle="Diagnostics and runtime switches for debugging PulseSoc." />
        <SettingsEmptyState
          icon="lock-closed-outline"
          title="Developer options are off"
          body="Open Settings › About and tap the version row seven times to enable them."
        />
      </SettingsShell>
    );
  }

  const user = authState.user;

  return (
    <SettingsShell bottomDock={false} onRefresh={loadDiagnostics}>
      <SettingsHeader
        title="Developer"
        subtitle="Runtime switches and on-device diagnostics. These affect only this device and are never shown to other users."
      />

      <SettingsSection
        title="Instrumentation"
        footnote="Both switches take effect immediately and persist across launches. Verbose logging also enables sample collection, because the overlay and the log read the same buffer."
        busy={pending}
      >
        <SettingsSwitch
          testID="developer-perf-overlay"
          title="Performance overlay"
          subtitle="Shows the on-device HUD with p50/p95 timings for screens and API calls."
          icon="speedometer-outline"
          value={value.showPerfOverlay}
          onValueChange={(next) => void setGroup({ showPerfOverlay: next })}
        />
        <SettingsSwitch
          testID="developer-verbose-logging"
          title="Verbose API logging"
          subtitle="Logs every API request's route, method, and duration to the JS console. Never logs bodies, headers, or tokens."
          icon="terminal-outline"
          value={value.verboseApiLogging}
          onValueChange={(next) => void setGroup({ verboseApiLogging: next })}
        />
      </SettingsSection>

      <SettingsSection title="Environment">
        <SettingsRow
          testID="developer-api-base"
          title="API base URL"
          subtitle={PULSE_API_BASE_URL}
          icon="server-outline"
          accessory={
            <SettingsBadge
              label={/^https:\/\/pulsesoc\.com$/i.test(PULSE_API_BASE_URL) ? "Production" : "Non-production"}
              tone={/^https:\/\/pulsesoc\.com$/i.test(PULSE_API_BASE_URL) ? "accent" : "warning"}
            />
          }
        />
      </SettingsSection>

      <SettingsSection
        title="Session"
        description="Presence and expiry only — token values are deliberately not rendered."
      >
        <SettingsRow
          testID="developer-session-phase"
          title="Phase"
          subtitle="Terminal state resolved by the session bootstrap."
          icon="pulse-outline"
          accessory={<SettingsValue>{authState.phase}</SettingsValue>}
        />
        <SettingsRow
          testID="developer-session-user"
          title="Signed in as"
          subtitle={user?.username ? `@${user.username}` : "No user on this session"}
          icon="person-outline"
          accessory={<SettingsValue>{user?.user_id ? `#${user.user_id}` : "—"}</SettingsValue>}
        />
        <SettingsRow
          testID="developer-session-envelope"
          title="Credential envelope"
          subtitle={envelope?.present ? "Refresh token held in the keychain." : "No refresh token stored (cookie-only or signed out)."}
          icon="key-outline"
          accessory={<SettingsBadge label={envelope?.present ? "Present" : "Absent"} tone={envelope?.present ? "accent" : "muted"} />}
        />
        <SettingsRow
          testID="developer-session-access-expiry"
          title="Access token expires"
          icon="time-outline"
          accessory={<SettingsValue>{envelope?.accessExpiry ?? "…"}</SettingsValue>}
        />
        <SettingsRow
          testID="developer-session-refresh-expiry"
          title="Refresh token expires"
          icon="refresh-outline"
          accessory={<SettingsValue>{envelope?.refreshExpiry ?? "…"}</SettingsValue>}
        />
      </SettingsSection>

      <SettingsSection title="Local storage" footnote="Size is the sum of key and value lengths in AsyncStorage — an approximation, not the on-disk footprint.">
        <SettingsRow
          testID="developer-storage-keys"
          title="Keys stored"
          icon="albums-outline"
          accessory={<SettingsValue>{storage ? String(storage.keys) : "…"}</SettingsValue>}
        />
        <SettingsRow
          testID="developer-storage-size"
          title="Approximate size"
          icon="pie-chart-outline"
          accessory={<SettingsValue>{storage ? formatBytes(storage.bytes) : "…"}</SettingsValue>}
        />
        <SettingsRow
          testID="developer-storage-settings-keys"
          title="Settings cache keys"
          subtitle={`Keys under ${SETTINGS_CACHE_PREFIX}`}
          icon="options-outline"
          accessory={<SettingsValue>{storage ? String(storage.settingsKeys) : "…"}</SettingsValue>}
        />
      </SettingsSection>

      <SettingsSection title="Maintenance">
        <SettingsRow
          testID="developer-clear-settings-cache"
          title="Clear local settings cache"
          subtitle="Drops the on-device preference snapshot and forces a fresh fetch on next launch."
          icon="trash-outline"
          tone="danger"
          busy={busyAction === "cache"}
          accessibilityRole="button"
          accessibilityHint="Asks for confirmation first."
          onPress={() => void clearSettingsCache()}
        />
        <SettingsRow
          testID="developer-reset-preferences"
          title="Reset all preferences"
          subtitle="Restores every setting in the app to its shipped default and syncs the reset."
          icon="refresh-circle-outline"
          tone="danger"
          busy={busyAction === "reset"}
          accessibilityRole="button"
          accessibilityHint="Asks for confirmation first."
          onPress={() => void resetPreferences()}
        />
      </SettingsSection>

      <SettingsSection title="Access">
        <SettingsRow
          testID="developer-disable"
          title="Disable developer options"
          subtitle="Hides this screen and turns off the overlay and verbose logging."
          icon="lock-closed-outline"
          tone="danger"
          accessibilityRole="button"
          accessibilityHint="Asks for confirmation first."
          onPress={() => void disableDeveloper()}
        />
      </SettingsSection>

      <View style={styles.note}>
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), lineHeight: theme.scaleFont(17), textAlign: "center" }}>
          Developer options are stored per account and sync between your devices.
        </Text>
      </View>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  note: { marginTop: 20, paddingHorizontal: 12 }
});
