import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState, AppStateStatus, Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as Notifications from "expo-notifications";
import { animateNextLayout, SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsButton, SettingsRow, SettingsSlider, SettingsSwitch } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import {
  CategoryChannels,
  ChannelKey,
  NOTIFICATION_CATEGORIES,
  NOTIFICATION_CATEGORY_LABELS,
  NotificationCategory
} from "../../settings/schema";
import { registerPushDevice } from "../../api/push";
import { useTheme } from "../../theme/ThemeContext";

const CHANNELS: { key: ChannelKey; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { key: "push", label: "Push", icon: "phone-portrait-outline" },
  { key: "email", label: "Email", icon: "mail-outline" },
  { key: "inApp", label: "In-app", icon: "notifications-outline" }
];

/**
 * Twelve categories x three channels is 36 controls. Flat, that is an unusable
 * wall; grouped by what the notification is *about*, the user only ever reads
 * the two-to-four rows that matter to them. Each row stays collapsed to a
 * one-line summary until it is opened.
 */
const CATEGORY_GROUPS: { title: string; description?: string; categories: NotificationCategory[] }[] = [
  { title: "Conversations", description: "The ones people expect you to answer.", categories: ["messages", "calls"] },
  { title: "Your content", categories: ["likes", "comments", "mentions", "reels"] },
  { title: "People & communities", categories: ["follows", "live", "groups", "marketplace"] },
  { title: "Account & updates", categories: ["security", "product"] }
];

// A category added to the schema must never silently vanish from this screen,
// so anything not explicitly placed above still gets rendered.
const CATEGORY_SECTIONS = (() => {
  const placed = new Set<NotificationCategory>(CATEGORY_GROUPS.flatMap((group) => group.categories));
  const orphans = NOTIFICATION_CATEGORIES.filter((category) => !placed.has(category));
  return orphans.length ? [...CATEGORY_GROUPS, { title: "More", categories: orphans }] : CATEGORY_GROUPS;
})();

const MINUTES_IN_DAY = 24 * 60;
const QUIET_HOURS_STEP = 15;

function parseTimeToMinutes(time: string, fallback: number): number {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(time).trim());
  if (!match) return fallback;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return fallback;
  return hours * 60 + minutes;
}

function wrapMinutes(total: number): number {
  return ((Math.round(total) % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
}

/** Emits the `HH:MM` 24-hour form the schema stores and validates. */
function minutesToTime(total: number): string {
  const wrapped = wrapMinutes(total);
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
}

function formatClock(total: number, use24h: boolean): string {
  const wrapped = wrapMinutes(total);
  const hours = Math.floor(wrapped / 60);
  const minutes = String(wrapped % 60).padStart(2, "0");
  if (use24h) return `${String(hours).padStart(2, "0")}:${minutes}`;
  return `${hours % 12 === 0 ? 12 : hours % 12}:${minutes} ${hours < 12 ? "AM" : "PM"}`;
}

function describeQuietWindow(startMinutes: number, endMinutes: number): string {
  const span = wrapMinutes(endMinutes - startMinutes);
  if (span === 0) return "Start and end are the same time, so nothing is muted. Pick different times.";
  const hours = Math.floor(span / 60);
  const minutes = span % 60;
  const length = [hours ? `${hours} hr` : "", minutes ? `${minutes} min` : ""].filter(Boolean).join(" ");
  return `Notifications are muted for ${length} every day.`;
}

/* -------------------------------------------------------------------------- */
/*                              Permission banner                              */
/* -------------------------------------------------------------------------- */

/**
 * Nothing on this screen can produce a push notification while the OS is
 * blocking us, so this sits above the controls rather than beside them — a
 * quiet inline hint next to the push switch reliably goes unread.
 */
function PermissionBanner({
  canAskAgain,
  busy,
  error,
  onAllow,
  onOpenSettings
}: {
  canAskAgain: boolean;
  busy: boolean;
  error: string | null;
  onAllow: () => void;
  onOpenSettings: () => void;
}) {
  const theme = useTheme();
  return (
    <View
      accessibilityLiveRegion="polite"
      style={[
        styles.banner,
        {
          backgroundColor: theme.colors.warningSoft,
          borderColor: theme.colors.warning,
          borderRadius: theme.metrics.radius,
          marginTop: theme.metrics.sectionGap
        }
      ]}
    >
      <View style={styles.bannerHead}>
        <Ionicons name="notifications-off-outline" size={theme.scaleFont(20)} color={theme.colors.warning} />
        <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: theme.metrics.titleWeight, flex: 1 }}>
          Your device is blocking PulseSoc notifications
        </Text>
      </View>
      <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14), lineHeight: theme.scaleFont(20), marginTop: 6 }}>
        Everything you choose below is saved, but no push notification can reach this phone until PulseSoc is allowed to send
        them.
      </Text>
      {error ? (
        <Text style={{ color: theme.colors.danger, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18), marginTop: 8 }}>
          {error}
        </Text>
      ) : null}
      <View style={styles.bannerActions}>
        {canAskAgain ? (
          <SettingsButton
            testID="notifications-permission-allow"
            label="Allow notifications"
            icon="notifications-outline"
            busy={busy}
            onPress={onAllow}
          />
        ) : null}
        <SettingsButton
          testID="notifications-permission-open-settings"
          label="Open device settings"
          icon="open-outline"
          variant={canAskAgain ? "secondary" : "primary"}
          onPress={onOpenSettings}
        />
      </View>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                             Per-category controls                           */
/* -------------------------------------------------------------------------- */

function ChannelChip({
  label,
  icon,
  active,
  disabled,
  disabledReason,
  accessibilityLabel,
  onToggle,
  testID
}: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  active: boolean;
  disabled: boolean;
  disabledReason: string | null;
  accessibilityLabel: string;
  onToggle: (next: boolean) => void;
  testID: string;
}) {
  const theme = useTheme();
  const tint = disabled ? theme.colors.disabled : active ? theme.colors.accent : theme.colors.muted;

  return (
    <Pressable
      testID={testID}
      accessibilityRole="checkbox"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ checked: active, disabled }}
      accessibilityHint={disabled ? disabledReason ?? undefined : undefined}
      disabled={disabled}
      onPress={() => {
        if (theme.hapticFeedback) void Haptics.selectionAsync().catch(() => undefined);
        onToggle(!active);
      }}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: active && !disabled ? theme.colors.signalDim : theme.colors.surface,
          borderColor: active && !disabled ? theme.colors.accent : theme.colors.border,
          opacity: disabled ? 0.45 : pressed ? 0.7 : 1
        }
      ]}
    >
      <Ionicons name={active && !disabled ? "checkmark-circle" : icon} size={theme.scaleFont(16)} color={tint} />
      <Text numberOfLines={1} style={{ color: tint, fontSize: theme.scaleFont(13), fontWeight: "700" }}>
        {label}
      </Text>
    </Pressable>
  );
}

function CategoryRow({
  category,
  channels,
  expanded,
  blockedReasons,
  onToggleExpanded,
  onChangeChannel
}: {
  category: NotificationCategory;
  channels: CategoryChannels;
  expanded: boolean;
  blockedReasons: Partial<Record<ChannelKey, string | null>>;
  onToggleExpanded: () => void;
  onChangeChannel: (channel: ChannelKey, next: boolean) => void;
}) {
  const theme = useTheme();
  const meta = NOTIFICATION_CATEGORY_LABELS[category];

  // The summary reports what will actually be delivered, so a category whose
  // push flag is on but globally muted reads "Off" rather than lying.
  const summary = useMemo(() => {
    const live = CHANNELS.filter((channel) => channels[channel.key] && !blockedReasons[channel.key]);
    return live.length ? live.map((channel) => channel.label).join(" · ") : "Off";
  }, [blockedReasons, channels]);

  const reasons = useMemo(
    () => Array.from(new Set(CHANNELS.map((channel) => blockedReasons[channel.key]).filter(Boolean) as string[])),
    [blockedReasons]
  );

  return (
    <>
      <SettingsRow
        testID={`notifications-category-${category}`}
        title={meta.title}
        subtitle={summary}
        onPress={onToggleExpanded}
        accessibilityRole="button"
        accessibilityState={{ selected: expanded }}
        accessibilityHint={expanded ? "Hides the channel options for this category." : "Shows push, email, and in-app options for this category."}
        accessory={
          <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={theme.scaleFont(17)} color={theme.colors.muted} />
        }
      />
      {expanded ? (
        <View
          style={[
            styles.panel,
            { backgroundColor: theme.colors.surfaceRaised, paddingHorizontal: theme.metrics.rowPaddingHorizontal }
          ]}
        >
          <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18) }}>
            {meta.description}
          </Text>
          <View style={styles.chipRow}>
            {CHANNELS.map((channel) => {
              const reason = blockedReasons[channel.key] ?? null;
              return (
                <ChannelChip
                  key={channel.key}
                  testID={`notifications-category-${category}-${channel.key}`}
                  label={channel.label}
                  icon={channel.icon}
                  active={channels[channel.key]}
                  disabled={Boolean(reason)}
                  disabledReason={reason}
                  accessibilityLabel={`${channel.label} notifications for ${meta.title}`}
                  onToggle={(next) => onChangeChannel(channel.key, next)}
                />
              );
            })}
          </View>
          {reasons.map((reason) => (
            <Text
              key={reason}
              style={{ color: theme.colors.warning, fontSize: theme.scaleFont(12), lineHeight: theme.scaleFont(17), marginTop: 8 }}
            >
              {reason}
            </Text>
          ))}
        </View>
      ) : null}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * Notifications.
 *
 * Two tiers, in the order a user reasons about them: the channels PulseSoc may
 * use at all, then what each kind of activity is allowed to use. A channel
 * switched off at the top is switched off *and* explained everywhere below, so
 * the screen can never show a toggle whose state is a fiction.
 */
export function NotificationSettingsScreen() {
  const theme = useTheme();
  const { value, setGroup, pending } = usePreferenceGroup("notifications");
  const language = usePreferenceGroup("language");
  const use24h = language.value.timeFormat === "24h";

  const [expanded, setExpanded] = useState<NotificationCategory | null>(null);
  const [osGranted, setOsGranted] = useState(true);
  const [canAskAgain, setCanAskAgain] = useState(true);
  const [permissionChecked, setPermissionChecked] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const mounted = useRef(true);

  const readPermission = useCallback(async () => {
    try {
      const permission = await Notifications.getPermissionsAsync();
      if (!mounted.current) return Boolean(permission.granted);
      setOsGranted(Boolean(permission.granted));
      setCanAskAgain(permission.canAskAgain !== false);
      setPermissionChecked(true);
      return Boolean(permission.granted);
    } catch {
      // A platform that cannot report permissions (web, an unsupported build)
      // must not produce a banner whose button would do nothing.
      if (mounted.current) {
        setOsGranted(true);
        setPermissionChecked(true);
      }
      return true;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void readPermission();
    // The user grants permission in the OS, not here, so the only reliable
    // moment to re-read it is when they come back to the app.
    const handleAppState = (state: AppStateStatus) => {
      if (state === "active") void readPermission();
    };
    const subscription = AppState.addEventListener("change", handleAppState);
    return () => {
      mounted.current = false;
      subscription.remove();
    };
  }, [readPermission]);

  const requestPermission = useCallback(async () => {
    setRequesting(true);
    setPermissionError(null);
    try {
      // `registerPushDevice` prompts *and* publishes the endpoint, so the
      // backend can reach this device in the same pass the user says yes.
      const result = await registerPushDevice();
      const granted = await readPermission();
      if (!mounted.current) return;
      if (!granted || result.ok === false) {
        setPermissionError(result.message || "Notifications are still blocked for PulseSoc on this device.");
      }
    } catch (error) {
      if (!mounted.current) return;
      setPermissionError(
        error instanceof Error && error.message
          ? error.message
          : "PulseSoc couldn't register this device for notifications. Check your connection and try again."
      );
    } finally {
      if (mounted.current) setRequesting(false);
    }
  }, [readPermission]);

  const openDeviceSettings = useCallback(() => {
    void Linking.openSettings().catch(() => {
      if (mounted.current) setPermissionError("PulseSoc couldn't open your device settings. Open them manually and allow notifications.");
    });
  }, []);

  const setPushEnabled = useCallback(
    (next: boolean) => {
      void setGroup({ pushEnabled: next });
      // The preference alone does not create a delivery endpoint; turning push
      // on is exactly when this device needs to be registered. A failure here
      // has to surface: the preference would read "on" while the backend has no
      // way to reach the device, which looks exactly like notifications being
      // silently broken.
      if (!next) return;
      void (async () => {
        try {
          const result = await registerPushDevice();
          const granted = await readPermission();
          if (!mounted.current) return;
          if (!granted || result.ok === false) {
            setPermissionError(result.message || "Notifications are still blocked for PulseSoc on this device.");
          }
        } catch (error) {
          if (!mounted.current) return;
          setPermissionError(
            error instanceof Error && error.message
              ? error.message
              : "PulseSoc couldn't register this device for push. Push is on, but this device may not receive alerts until you retry."
          );
        }
      })();
    },
    [readPermission, setGroup]
  );

  const setCategoryChannel = useCallback(
    (category: NotificationCategory, channel: ChannelKey, next: boolean) => {
      void setGroup({
        categories: { ...value.categories, [category]: { ...value.categories[category], [channel]: next } }
      });
    },
    [setGroup, value.categories]
  );

  const toggleExpanded = useCallback(
    (category: NotificationCategory) => {
      animateNextLayout(theme.reduceMotion);
      // One open row at a time — the point of collapsing is to keep the list
      // scannable, which twelve open panels would immediately undo.
      setExpanded((current) => (current === category ? null : category));
    },
    [theme.reduceMotion]
  );

  const blockedReasons = useMemo<Partial<Record<ChannelKey, string | null>>>(
    () => ({
      push: !value.pushEnabled
        ? "Push is turned off for all of PulseSoc. Turn it on under Channels to use it here."
        : !osGranted
          ? "Your device is blocking PulseSoc notifications, so push can't be delivered."
          : null,
      email: value.emailEnabled ? null : "Email is turned off for all of PulseSoc. Turn it on under Channels to use it here.",
      inApp: null
    }),
    [osGranted, value.emailEnabled, value.pushEnabled]
  );

  const quietStart = parseTimeToMinutes(value.quietHoursStart, 22 * 60);
  const quietEnd = parseTimeToMinutes(value.quietHoursEnd, 7 * 60);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title="Notifications" subtitle="Choose how PulseSoc reaches you, and for what." />

      {permissionChecked && !osGranted ? (
        <PermissionBanner
          canAskAgain={canAskAgain}
          busy={requesting}
          error={permissionError}
          onAllow={() => void requestPermission()}
          onOpenSettings={openDeviceSettings}
        />
      ) : null}

      <SettingsSection
        title="Channels"
        description="Master switches. Anything off here stays off for every category below."
        busy={pending}
      >
        <SettingsSwitch
          testID="notifications-push-enabled"
          title="Push notifications"
          subtitle={osGranted ? "Alerts on this device." : "Allowed in PulseSoc, but still blocked by your device."}
          icon="phone-portrait-outline"
          value={value.pushEnabled}
          onValueChange={setPushEnabled}
        />
        <SettingsSwitch
          testID="notifications-email-enabled"
          title="Email"
          subtitle="Sent to the address on your account."
          icon="mail-outline"
          value={value.emailEnabled}
          onValueChange={(next) => void setGroup({ emailEnabled: next })}
        />
        <SettingsSwitch
          testID="notifications-sms-enabled"
          title="SMS"
          subtitle="Text messages for time-critical alerts only. Carrier rates may apply."
          icon="chatbubble-ellipses-outline"
          value={value.smsEnabled}
          onValueChange={(next) => void setGroup({ smsEnabled: next })}
        />
      </SettingsSection>

      <SettingsSection title="How push arrives" footnote="Sound and vibration follow your device's ringer and Do Not Disturb settings.">
        <SettingsSwitch
          testID="notifications-sound"
          title="Sound"
          subtitle="Play a tone when a push notification arrives."
          icon="volume-high-outline"
          value={value.sound}
          disabled={!value.pushEnabled}
          onValueChange={(next) => void setGroup({ sound: next })}
        />
        <SettingsSwitch
          testID="notifications-vibration"
          title="Vibration"
          subtitle="Vibrate on arrival."
          icon="pulse-outline"
          value={value.vibration}
          disabled={!value.pushEnabled}
          onValueChange={(next) => void setGroup({ vibration: next })}
        />
        <SettingsSwitch
          testID="notifications-preview-text"
          title="Show message preview"
          subtitle="Off means the notification shows only the sender, never the message body — safer on a locked screen."
          icon="eye-off-outline"
          value={value.previewText}
          disabled={!value.pushEnabled}
          onValueChange={(next) => void setGroup({ previewText: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Quiet hours"
        description="Hold notifications overnight, or during any window you choose."
        footnote={value.quietHoursEnabled ? describeQuietWindow(quietStart, quietEnd) : undefined}
      >
        <SettingsSwitch
          testID="notifications-quiet-hours-enabled"
          title="Quiet hours"
          subtitle={
            value.quietHoursEnabled
              ? `${formatClock(quietStart, use24h)} to ${formatClock(quietEnd, use24h)}`
              : "Notifications arrive at any time of day."
          }
          icon="moon-outline"
          value={value.quietHoursEnabled}
          onValueChange={(next) => void setGroup({ quietHoursEnabled: next })}
        />
        {/* Sliders rather than a picker: the app ships no time-picker module,
            and a stepped minutes-of-day slider cannot produce an invalid time. */}
        <SettingsSlider
          testID="notifications-quiet-hours-start"
          title="Starts"
          value={quietStart}
          minimumValue={0}
          maximumValue={MINUTES_IN_DAY - QUIET_HOURS_STEP}
          step={QUIET_HOURS_STEP}
          disabled={!value.quietHoursEnabled}
          onChange={(next) => void setGroup({ quietHoursStart: minutesToTime(next) })}
          formatValue={(minutes) => formatClock(minutes, use24h)}
        />
        <SettingsSlider
          testID="notifications-quiet-hours-end"
          title="Ends"
          subtitle="An end time earlier than the start simply carries the window over midnight."
          value={quietEnd}
          minimumValue={0}
          maximumValue={MINUTES_IN_DAY - QUIET_HOURS_STEP}
          step={QUIET_HOURS_STEP}
          disabled={!value.quietHoursEnabled}
          onChange={(next) => void setGroup({ quietHoursEnd: minutesToTime(next) })}
          formatValue={(minutes) => formatClock(minutes, use24h)}
        />
      </SettingsSection>

      <View style={{ marginTop: theme.metrics.sectionGap }}>
        <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(20), fontWeight: theme.metrics.titleWeight }}>
          What you're notified about
        </Text>
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14), lineHeight: theme.scaleFont(20), marginTop: 4 }}>
          Tap a category to pick its channels. In-app alerts always appear inside PulseSoc, even when push and email are off.
        </Text>
      </View>

      {CATEGORY_SECTIONS.map((group) => (
        <SettingsSection key={group.title} title={group.title} description={group.description}>
          {group.categories.map((category) => (
            <CategoryRow
              key={category}
              category={category}
              channels={value.categories[category]}
              expanded={expanded === category}
              blockedReasons={blockedReasons}
              onToggleExpanded={() => toggleExpanded(category)}
              onChangeChannel={(channel, next) => setCategoryChannel(category, channel, next)}
            />
          ))}
        </SettingsSection>
      ))}
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  banner: { borderWidth: StyleSheet.hairlineWidth, padding: 14, width: "100%" },
  bannerHead: { alignItems: "center", flexDirection: "row", gap: 10 },
  bannerActions: { gap: 10, marginTop: 14 },
  panel: { paddingBottom: 14, paddingTop: 2, width: "100%" },
  chipRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  chip: {
    alignItems: "center",
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 8
  }
});
