import { useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import {
  getNotificationExperience,
  getNotificationPreferences,
  NotificationPreferences,
  updateNotificationExperience,
  updateNotificationPreferences
} from "../api/notifications";
import { getPushPermissionState, registerPushDevice } from "../api/push";
import { colors } from "../theme/colors";

const DEFAULT_CATEGORIES = [
  ["chat_message", "Chat Messages"],
  ["comments", "Comments"],
  ["likes", "Likes"],
  ["mentions", "Mentions"],
  ["follows", "Follows"],
  ["live", "Live"],
  ["crypto", "Crypto Alerts"],
  ["intelligence", "Intelligence Alerts"],
  ["marketplace", "Marketplace"],
  ["purchase", "Purchases"],
  ["premium", "Premium"],
  ["security", "Security"]
] as const;

const CHANNELS = ["in_app", "push", "email", "sms"] as const;

export function NotificationPreferencesScreen() {
  const [preferences, setPreferences] = useState<NotificationPreferences>({});
  const [quietHours, setQuietHours] = useState(false);
  const [sound, setSound] = useState(true);
  const [vibration, setVibration] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [pushStatus, setPushStatus] = useState("Checking push permission...");

  async function load() {
    try {
      const [prefData, experienceData] = await Promise.all([getNotificationPreferences(), getNotificationExperience()]);
      const permission = await getPushPermissionState();
      setPreferences(seedPreferences(prefData.preferences || {}));
      const experience = experienceData.experience || {};
      setQuietHours(Boolean(experience.quiet_hours_enabled));
      setSound(experience.enable_notification_sound !== false);
      setVibration(experience.enable_notification_vibration !== false);
      setPushStatus(permission.message);
      setStatus("Preferences loaded.");
    } catch (error) {
      setPreferences(seedPreferences({}));
      setStatus(error instanceof Error ? error.message : "Notification preferences could not load.");
    }
  }

  async function enablePush() {
    const result = await registerPushDevice();
    setStatus(String(result.message || (result.ok === false ? "Push was not enabled." : "Push registration sent.")));
    const permission = await getPushPermissionState();
    setPushStatus(permission.message);
  }

  async function save() {
    setSaving(true);
    try {
      await updateNotificationPreferences(preferences);
      await updateNotificationExperience({
        quiet_hours_enabled: quietHours,
        enable_notification_sound: sound,
        enable_notification_vibration: vibration,
        notification_sound_type: sound ? "soft" : "silent"
      });
      setStatus("Notification preferences saved.");
    } catch (error) {
      Alert.alert("Preferences not saved", error instanceof Error ? error.message : "Notification preferences were not saved.");
    } finally {
      setSaving(false);
    }
  }

  function toggle(category: string, channel: (typeof CHANNELS)[number], value: boolean) {
    setPreferences((current) => ({
      ...current,
      [category]: {
        ...(current[category] || {}),
        [channel]: category === "security" && channel === "in_app" ? true : value
      }
    }));
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Notification Preferences</Text>
        <Text style={styles.subtitle}>Native controls backed by existing PulseSoc notification settings.</Text>
      </View>
      <View style={styles.panel}>
        <Text style={styles.panelTitle}>Push device</Text>
        <Text style={styles.muted}>Permission and token registration use the existing PulseSoc push subscription endpoint.</Text>
        <Text style={styles.status}>{pushStatus}</Text>
        <Pressable style={styles.button} onPress={enablePush}>
          <Text style={styles.buttonText}>Enable push</Text>
        </Pressable>
      </View>
      <View style={styles.panel}>
        <Text style={styles.panelTitle}>Experience</Text>
        <ToggleRow label="Quiet hours" value={quietHours} onValueChange={setQuietHours} />
        <ToggleRow label="Sound" value={sound} onValueChange={setSound} />
        <ToggleRow label="Vibration" value={vibration} onValueChange={setVibration} />
      </View>
      <View style={styles.panel}>
        <Text style={styles.panelTitle}>Categories</Text>
        {DEFAULT_CATEGORIES.map(([category, label]) => (
          <View key={category} style={styles.categoryBlock}>
            <Text style={styles.categoryTitle}>{label}</Text>
            <View style={styles.channelGrid}>
              {CHANNELS.map((channel) => (
                <ToggleRow
                  compact
                  key={`${category}-${channel}`}
                  label={channel.replace("_", " ")}
                  value={Boolean(preferences[category]?.[channel]) || (category === "security" && channel === "in_app")}
                  disabled={category === "security" && channel === "in_app"}
                  onValueChange={(value) => toggle(category, channel, value)}
                />
              ))}
            </View>
          </View>
        ))}
      </View>
      <Pressable disabled={saving} style={[styles.button, saving && styles.disabled]} onPress={save}>
        <Text style={styles.buttonText}>{saving ? "Saving..." : "Save preferences"}</Text>
      </Pressable>
      {status ? <Text style={styles.status}>{status}</Text> : null}
    </ScrollView>
  );
}

function ToggleRow({
  label,
  value,
  onValueChange,
  disabled,
  compact
}: {
  label: string;
  value: boolean;
  onValueChange: (value: boolean) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  return (
    <View style={[styles.toggleRow, compact && styles.compactRow]}>
      <Text style={[styles.toggleLabel, disabled && styles.disabledText]}>{label}</Text>
      <Switch value={value} disabled={disabled} onValueChange={onValueChange} trackColor={{ true: colors.accent, false: colors.border }} />
    </View>
  );
}

function seedPreferences(existing: NotificationPreferences) {
  const next: NotificationPreferences = {};
  DEFAULT_CATEGORIES.forEach(([category]) => {
    next[category] = {
      in_app: existing[category]?.in_app ?? true,
      push: existing[category]?.push ?? true,
      email: existing[category]?.email ?? true,
      sms: existing[category]?.sms ?? true
    };
  });
  next.security = { ...(next.security || {}), in_app: true };
  return next;
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  content: {
    gap: 12,
    padding: 16
  },
  header: {
    gap: 4
  },
  title: {
    color: colors.text,
    fontSize: 26,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 12
  },
  panelTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 14
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  },
  disabled: {
    opacity: 0.56
  },
  disabledText: {
    color: colors.muted
  },
  categoryBlock: {
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 8,
    paddingTop: 10
  },
  categoryTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  channelGrid: {
    gap: 4
  },
  toggleRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 44
  },
  compactRow: {
    minHeight: 38
  },
  toggleLabel: {
    color: colors.text,
    fontSize: 14,
    textTransform: "capitalize"
  },
  status: {
    color: colors.muted,
    fontSize: 13,
    textAlign: "center"
  }
});
