import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AppState, AppStateStatus, Linking, Platform, StyleSheet, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
// SDK 54 note: `expo-camera` no longer exports the permission helpers at the
// module top level — they live on the `Camera` object, which also owns the
// microphone permission (there is no separate audio package to install).
import { Camera } from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import * as Notifications from "expo-notifications";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsButton, SettingsRow } from "../../settings/components/SettingsControls";

type PermissionKey = "camera" | "microphone" | "photos" | "notifications";

/** `undetermined` collapses to "we can still ask"; everything else is terminal. */
type PermissionState = "granted" | "limited" | "denied" | "undetermined" | "unknown";

type PermissionSnapshot = {
  state: PermissionState;
  /** False once the OS will no longer show a prompt — the only case for deep-linking to Settings. */
  canAskAgain: boolean;
};

type PermissionDescriptor = {
  key: PermissionKey;
  title: string;
  icon: keyof typeof Ionicons.glyphMap;
  /** Plain English: what PulseSoc does with it, and what breaks without it. */
  purpose: string;
  check: () => Promise<PermissionSnapshot>;
  request: () => Promise<PermissionSnapshot>;
};

const UNKNOWN: PermissionSnapshot = { state: "unknown", canAskAgain: false };

function toSnapshot(response: { status: string; canAskAgain: boolean; granted: boolean }): PermissionSnapshot {
  const state: PermissionState =
    response.granted || response.status === "granted"
      ? "granted"
      : response.status === "undetermined"
      ? "undetermined"
      : "denied";
  return { state, canAskAgain: Boolean(response.canAskAgain) };
}

/**
 * Photo access is tri-state on iOS 14+ and Android 14+: the user can grant a
 * hand-picked subset. Reporting that as "granted" would be a lie the first time
 * a picker comes back half-empty, so it gets its own state.
 */
function toPhotoSnapshot(response: ImagePicker.MediaLibraryPermissionResponse): PermissionSnapshot {
  const base = toSnapshot(response);
  if (base.state === "granted" && response.accessPrivileges === "limited") {
    return { ...base, state: "limited" };
  }
  return base;
}

/**
 * iOS provisional authorisation reports `granted: false` while notifications are
 * in fact being delivered quietly. Treat it as granted rather than nagging the
 * user to enable something that already works.
 */
function toNotificationSnapshot(response: Notifications.NotificationPermissionsStatus): PermissionSnapshot {
  if (response.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL) {
    return { state: "granted", canAskAgain: false };
  }
  return toSnapshot(response);
}

const PERMISSIONS: PermissionDescriptor[] = [
  {
    key: "camera",
    title: "Camera",
    icon: "camera-outline",
    purpose: "Record reels, go live, take profile photos, and scan QR codes. PulseSoc only opens the camera when you tap a capture control.",
    check: async () => toSnapshot(await Camera.getCameraPermissionsAsync()),
    request: async () => toSnapshot(await Camera.requestCameraPermissionsAsync())
  },
  {
    key: "microphone",
    title: "Microphone",
    icon: "mic-outline",
    purpose: "Capture audio in videos and reels, and carry your voice on calls and live broadcasts. Without it, video you record is silent.",
    check: async () => toSnapshot(await Camera.getMicrophonePermissionsAsync()),
    request: async () => toSnapshot(await Camera.requestMicrophonePermissionsAsync())
  },
  {
    key: "photos",
    title: "Photo library",
    icon: "images-outline",
    purpose: "Choose existing photos and videos to post, send in messages, or set as your avatar. PulseSoc reads only the items you pick.",
    check: async () => toPhotoSnapshot(await ImagePicker.getMediaLibraryPermissionsAsync()),
    request: async () => toPhotoSnapshot(await ImagePicker.requestMediaLibraryPermissionsAsync())
  },
  {
    key: "notifications",
    title: "Notifications",
    icon: "notifications-outline",
    purpose: "Deliver messages, call invites, and security alerts while PulseSoc is closed. What you actually receive is controlled in Notification settings.",
    check: async () => toNotificationSnapshot(await Notifications.getPermissionsAsync()),
    request: async () =>
      toNotificationSnapshot(
        await Notifications.requestPermissionsAsync({
          ios: { allowAlert: true, allowBadge: true, allowSound: true }
        })
      )
  }
];

const BADGE: Record<PermissionState, { label: string; tone: "accent" | "danger" | "warning" | "muted" }> = {
  granted: { label: "ALLOWED", tone: "accent" },
  limited: { label: "LIMITED", tone: "warning" },
  denied: { label: "BLOCKED", tone: "danger" },
  undetermined: { label: "NOT ASKED", tone: "muted" },
  unknown: { label: "UNAVAILABLE", tone: "muted" }
};

const SETTINGS_APP = Platform.OS === "ios" ? "Settings" : "Android settings";

type SnapshotMap = Record<PermissionKey, PermissionSnapshot>;

const INITIAL: SnapshotMap = {
  camera: UNKNOWN,
  microphone: UNKNOWN,
  photos: UNKNOWN,
  notifications: UNKNOWN
};

/**
 * Device permissions.
 *
 * The OS — not PulseSoc — owns this state, and the user can change it in the
 * Settings app at any time without the app being notified. So the screen
 * re-reads every permission on focus and on foreground rather than trusting a
 * value captured at mount, which is what makes stale "Blocked" badges appear
 * right after someone has just granted access.
 */
export function PermissionsSettingsScreen() {
  const [snapshots, setSnapshots] = useState<SnapshotMap>(INITIAL);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<PermissionKey | null>(null);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refreshAll = useCallback(async () => {
    const results = await Promise.all(
      PERMISSIONS.map(async (permission) => {
        try {
          return [permission.key, await permission.check()] as const;
        } catch {
          // A missing native module (Expo Go, web) must not blank the screen —
          // the row reports "unavailable" and offers the Settings deep link.
          return [permission.key, UNKNOWN] as const;
        }
      })
    );
    if (!mounted.current) return;
    setSnapshots((current) => {
      const next = { ...current };
      results.forEach(([key, snapshot]) => {
        next[key] = snapshot;
      });
      return next;
    });
    setLoading(false);
  }, []);

  // Re-read whenever the screen comes back into view (returning from the
  // Settings app usually lands here via a focus event, not a remount).
  useFocusEffect(
    useCallback(() => {
      void refreshAll();
    }, [refreshAll])
  );

  // ...and on foreground, which is what fires when the user leaves via the OS
  // task switcher instead of navigating inside the app.
  useEffect(() => {
    const handler = (status: AppStateStatus) => {
      if (status === "active") void refreshAll();
    };
    const subscription = AppState.addEventListener("change", handler);
    return () => subscription.remove();
  }, [refreshAll]);

  const openSystemSettings = useCallback(async (title: string) => {
    try {
      await Linking.openSettings();
    } catch {
      Alert.alert(
        `Open ${SETTINGS_APP}`,
        `We couldn't open your device settings automatically. Go to ${SETTINGS_APP} › PulseSoc to change the ${title.toLowerCase()} permission.`
      );
    }
  }, []);

  const handleAction = useCallback(
    async (permission: PermissionDescriptor) => {
      const snapshot = snapshots[permission.key];
      // The OS only shows its prompt once. After that, the Settings app is the
      // only place the answer can change — sending a second request would look
      // like a dead button.
      if (snapshot.state !== "undetermined" || !snapshot.canAskAgain) {
        await openSystemSettings(permission.title);
        return;
      }
      setBusyKey(permission.key);
      try {
        const result = await permission.request();
        if (!mounted.current) return;
        setSnapshots((current) => ({ ...current, [permission.key]: result }));
        if (result.state === "denied") {
          Alert.alert(
            `${permission.title} not allowed`,
            `You can change this any time in ${SETTINGS_APP} › PulseSoc.`
          );
        }
      } catch (error) {
        if (!mounted.current) return;
        Alert.alert(
          `Couldn't request ${permission.title.toLowerCase()} access`,
          error instanceof Error ? error.message : `Open ${SETTINGS_APP} › PulseSoc to grant it manually.`
        );
      } finally {
        if (mounted.current) setBusyKey(null);
      }
    },
    [openSystemSettings, snapshots]
  );

  const actionLabel = (snapshot: PermissionSnapshot): string => {
    if (snapshot.state === "undetermined" && snapshot.canAskAgain) return "Allow";
    if (snapshot.state === "granted") return "Manage";
    if (snapshot.state === "limited") return "Change selection";
    return `Open ${SETTINGS_APP}`;
  };

  const statusLine = (snapshot: PermissionSnapshot, title: string): string => {
    if (snapshot.state === "granted") return `${title} access is allowed.`;
    if (snapshot.state === "limited") return `${title} access is limited to the items you selected.`;
    if (snapshot.state === "denied") return `${title} access is blocked. Only ${SETTINGS_APP} can re-enable it.`;
    if (snapshot.state === "undetermined") return `PulseSoc hasn't asked for ${title.toLowerCase()} access yet.`;
    return `${title} access can't be checked on this device.`;
  };

  return (
    <SettingsShell bottomDock={false} onRefresh={refreshAll} refreshing={loading}>
      <SettingsHeader
        title="Device permissions"
        subtitle="What PulseSoc is allowed to reach on this device. Your device — not PulseSoc — has the final say."
      />

      {PERMISSIONS.map((permission) => {
        const snapshot = snapshots[permission.key];
        const badge = BADGE[snapshot.state];
        return (
          <SettingsSection key={permission.key} title={permission.title} busy={busyKey === permission.key}>
            <SettingsRow
              testID={`permission-${permission.key}-status`}
              title={statusLine(snapshot, permission.title)}
              subtitle={permission.purpose}
              icon={permission.icon}
              accessory={<SettingsBadge label={badge.label} tone={badge.tone} />}
            />
            <View style={styles.action}>
              <SettingsButton
                testID={`permission-${permission.key}-action`}
                label={actionLabel(snapshot)}
                variant={snapshot.state === "denied" ? "destructive" : snapshot.state === "undetermined" ? "primary" : "secondary"}
                icon={snapshot.state === "undetermined" && snapshot.canAskAgain ? "checkmark-circle-outline" : "open-outline"}
                busy={busyKey === permission.key}
                onPress={() => void handleAction(permission)}
              />
            </View>
          </SettingsSection>
        );
      })}

      <SettingsSection
        title="All permissions"
        footnote="Revoking a permission never deletes anything you've already shared with PulseSoc. It only stops future access."
      >
        <SettingsRow
          testID="permissions-open-system-settings"
          title={`Open PulseSoc in ${SETTINGS_APP}`}
          subtitle="See every permission this device manages for PulseSoc, including ones not listed here."
          icon="settings-outline"
          chevron
          onPress={() => void openSystemSettings("all permissions")}
          accessibilityRole="button"
          accessibilityHint="Opens your device settings for PulseSoc."
        />
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  action: { padding: 16, paddingTop: 4 }
});
