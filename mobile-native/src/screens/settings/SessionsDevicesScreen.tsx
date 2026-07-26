import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, View } from "react-native";
import { SettingsEmptyState, SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  confirm,
  SettingsBadge,
  SettingsButton,
  SettingsRow
} from "../../settings/components/SettingsControls";
import { ActiveSession, fetchActiveSessions, revokeSession } from "../../settings/api";
import { signOutEverywhere, useAuth } from "../../session/auth";
import { useTheme } from "../../theme/ThemeContext";

/**
 * Relative time, written locally because the app ships no date library and one
 * screen does not justify adding ~70kb of `date-fns` to the bundle.
 *
 * Deliberately coarse: "3 days ago" is what a user needs to spot a session they
 * don't recognise. Minute-level precision on a week-old session is noise.
 */
function formatRelativeTime(iso: string | null): string {
  if (!iso) return "Last active unknown";
  // Bare SQL timestamps ("2026-07-25 11:02:00") are parsed as local time by
  // some engines and UTC by others; normalise to the ISO form the server means.
  const normalized = /\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(iso) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
    ? `${iso.replace(" ", "T")}Z`
    : iso;
  const timestamp = Date.parse(normalized);
  if (!Number.isFinite(timestamp)) return "Last active unknown";

  const seconds = Math.round((Date.now() - timestamp) / 1000);
  // Small clock skew between device and server can put "now" slightly ahead.
  if (seconds < 60) return "Active just now";

  const units: [limit: number, seconds: number, name: string][] = [
    [3600, 60, "minute"],
    [86400, 3600, "hour"],
    [604800, 86400, "day"],
    [2629800, 604800, "week"],
    [31557600, 2629800, "month"]
  ];
  for (const [limit, size, name] of units) {
    if (seconds < limit) {
      const count = Math.floor(seconds / size);
      return `${count} ${name}${count === 1 ? "" : "s"} ago`;
    }
  }
  const years = Math.floor(seconds / 31557600);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

/** "iOS · San Francisco · 2 hours ago", skipping whatever the server omitted. */
function describeSession(session: ActiveSession): string {
  return [session.platform, session.location, formatRelativeTime(session.lastActiveAt)]
    .map((part) => (part ? String(part).trim() : ""))
    .filter(Boolean)
    .join(" · ");
}

type LoadState = "loading" | "ready" | "error";

/**
 * Sessions & devices.
 *
 * Revocation is optimistic: the row disappears the instant the user confirms,
 * because the mental model is "this device is out". If the call fails we put
 * the row back in its original position and say so — a silently reappearing
 * session at the bottom of the list would read as a second, unknown login.
 */
export function SessionsDevicesScreen() {
  const theme = useTheme();
  const { authState, setAuthState } = useAuth();
  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [refreshing, setRefreshing] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [signingOutAll, setSigningOutAll] = useState(false);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async (mode: "initial" | "refresh") => {
    if (mode === "initial") setState("loading");
    else setRefreshing(true);
    try {
      const list = await fetchActiveSessions();
      if (!mounted.current) return;
      // Current session first, then most recently active — the two things a
      // user scanning for an intruder actually wants at the top.
      const ordered = [...list].sort((a, b) => {
        if (a.current !== b.current) return a.current ? -1 : 1;
        return (Date.parse(b.lastActiveAt || "") || 0) - (Date.parse(a.lastActiveAt || "") || 0);
      });
      setSessions(ordered);
      setState("ready");
    } catch {
      if (!mounted.current) return;
      setState("error");
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load("initial");
  }, [load]);

  const onRefresh = useCallback(async () => {
    await load("refresh");
  }, [load]);

  const handleRevoke = useCallback(
    async (session: ActiveSession) => {
      if (revokingId) return;
      const ok = await confirm({
        title: `Sign out ${session.deviceName}?`,
        message: "That device will need to sign in again. Anything it was doing in PulseSoc stops immediately.",
        confirmLabel: "Sign out",
        destructive: true
      });
      if (!ok) return;

      // Snapshot the index so a rollback restores order, not just membership.
      const index = sessions.findIndex((entry) => entry.id === session.id);
      setRevokingId(session.id);
      setSessions((current) => current.filter((entry) => entry.id !== session.id));
      try {
        await revokeSession(session.id);
      } catch (error) {
        if (!mounted.current) return;
        setSessions((current) => {
          if (current.some((entry) => entry.id === session.id)) return current;
          const restored = [...current];
          restored.splice(Math.max(0, index), 0, session);
          return restored;
        });
        Alert.alert(
          "Couldn't sign that device out",
          error instanceof Error ? error.message : "The session is still active. Check your connection and try again."
        );
      } finally {
        if (mounted.current) setRevokingId(null);
      }
    },
    [revokingId, sessions]
  );

  /**
   * The backend's revoke-all also invalidates the token this device is holding,
   * so the copy says "all devices, including this one" rather than "other
   * sessions" — promising otherwise would sign the user out unexpectedly.
   */
  const handleSignOutEverywhere = useCallback(async () => {
    if (signingOutAll) return;
    const ok = await confirm({
      title: "Sign out of all devices?",
      message: "Every PulseSoc session is revoked, including this one. You'll sign in again on this device.",
      confirmLabel: "Sign out everywhere",
      destructive: true
    });
    if (!ok) return;
    setSigningOutAll(true);
    try {
      const next = await signOutEverywhere();
      setAuthState(next);
    } catch (error) {
      if (!mounted.current) return;
      Alert.alert(
        "Couldn't sign out everywhere",
        error instanceof Error ? error.message : "Your sessions are unchanged. Check your connection and try again."
      );
    } finally {
      if (mounted.current) setSigningOutAll(false);
    }
  }, [setAuthState, signingOutAll]);

  const signedIn = authState.status === "signedIn";

  return (
    <SettingsShell bottomDock={false} onRefresh={onRefresh} refreshing={refreshing}>
      <SettingsHeader title="Sessions & devices" subtitle="Everywhere your PulseSoc account is currently signed in." />

      {state === "loading" ? (
        <View style={styles.loading} accessibilityRole="progressbar" accessibilityLabel="Loading your active sessions">
          <ActivityIndicator size="large" color={theme.colors.accent} />
          <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14), marginTop: 12 }}>
            Checking your active sessions…
          </Text>
        </View>
      ) : state === "error" ? (
        <SettingsEmptyState
          icon="cloud-offline-outline"
          title="Couldn't load your sessions"
          body="We couldn't reach PulseSoc. Your sessions are unchanged — pull down or try again."
          action={<SettingsButton testID="sessions-retry" label="Try again" icon="refresh" variant="secondary" full={false} onPress={() => void load("initial")} />}
        />
      ) : sessions.length === 0 ? (
        <SettingsEmptyState
          icon="phone-portrait-outline"
          title="No other sessions listed"
          body="PulseSoc isn't reporting any active sessions for this account right now. If you signed in somewhere else recently, pull down to refresh."
          action={<SettingsButton testID="sessions-refresh-empty" label="Refresh" icon="refresh" variant="secondary" full={false} onPress={() => void load("initial")} />}
        />
      ) : (
        <SettingsSection title={`${sessions.length} active ${sessions.length === 1 ? "session" : "sessions"}`}>
          {sessions.map((session) => (
            <SettingsRow
              key={session.id}
              testID={`sessions-row-${session.id}`}
              title={session.deviceName}
              subtitle={describeSession(session)}
              icon={session.current ? "phone-portrait" : "phone-portrait-outline"}
              busy={revokingId === session.id}
              onPress={session.current ? undefined : () => void handleRevoke(session)}
              accessibilityRole={session.current ? "none" : "button"}
              accessibilityState={{ selected: session.current, disabled: revokingId === session.id }}
              accessibilityHint={session.current ? undefined : `Signs ${session.deviceName} out of PulseSoc.`}
              accessory={
                session.current ? (
                  <SettingsBadge label="THIS DEVICE" tone="accent" />
                ) : (
                  <SettingsBadge label="SIGN OUT" tone="danger" />
                )
              }
            />
          ))}
        </SettingsSection>
      )}

      <SettingsSection
        title="Everywhere else"
        footnote="Use this if you've lost a device or think someone else has your password. Change your password afterwards."
      >
        <View style={styles.action}>
          <SettingsButton
            testID="sessions-sign-out-everywhere"
            label="Sign out of all devices"
            icon="log-out-outline"
            variant="destructive"
            busy={signingOutAll}
            disabled={!signedIn || signingOutAll}
            onPress={() => void handleSignOutEverywhere()}
          />
        </View>
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  loading: { alignItems: "center", paddingVertical: 56 },
  action: { padding: 16 }
});
