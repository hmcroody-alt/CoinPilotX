import { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Screen } from "../components/Screen";
import { getDeviceTimeZone } from "../core/localTime";
import { useLocalTime, useTimeZonePreference } from "../core/TimeZoneContext";
import { colors } from "../theme/colors";
import { getAccountLanguage, updateAccountLanguage } from "../api/account";

const COMMON_LANGUAGES = [
  { id: "en", label: "English" },
  { id: "es", label: "Español" },
  { id: "fr", label: "Français" },
  { id: "ht", label: "Kreyòl ayisyen" },
  { id: "pt", label: "Português" },
  { id: "de", label: "Deutsch" },
  { id: "ar", label: "العربية" },
  { id: "hi", label: "हिन्दी" },
  { id: "ja", label: "日本語" },
  { id: "ko", label: "한국어" },
  { id: "zh", label: "中文" }
];

// A curated set of common IANA zones for manual override. Automatic detection
// still covers every zone the device reports; this list is only for the manual
// picker so users can pin a specific city when they choose to.
const COMMON_ZONES: { id: string; label: string }[] = [
  { id: "Pacific/Honolulu", label: "Honolulu" },
  { id: "America/Anchorage", label: "Anchorage" },
  { id: "America/Los_Angeles", label: "Los Angeles" },
  { id: "America/Denver", label: "Denver" },
  { id: "America/Chicago", label: "Chicago" },
  { id: "America/New_York", label: "New York" },
  { id: "America/Sao_Paulo", label: "São Paulo" },
  { id: "Europe/London", label: "London" },
  { id: "Europe/Paris", label: "Paris" },
  { id: "Africa/Lagos", label: "Lagos" },
  { id: "Europe/Moscow", label: "Moscow" },
  { id: "Asia/Dubai", label: "Dubai" },
  { id: "Asia/Kolkata", label: "Kolkata" },
  { id: "Asia/Singapore", label: "Singapore" },
  { id: "Asia/Tokyo", label: "Tokyo" },
  { id: "Australia/Sydney", label: "Sydney" },
  { id: "Pacific/Auckland", label: "Auckland" },
  { id: "UTC", label: "UTC" }
];

export function RegionTimeScreen() {
  const { timeZone, locale, automatic, manualTimeZone, setTimeZoneOverride, setLocaleOverride } = useTimeZonePreference();
  const localTime = useLocalTime();
  const [now, setNow] = useState(() => new Date());
  const [language, setLanguage] = useState(locale);
  const [languageBusy, setLanguageBusy] = useState(false);
  const [languageError, setLanguageError] = useState("");

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    let mounted = true;
    getAccountLanguage().then(async (response) => {
      if (!mounted) return;
      const preferred = response.preferred_language || response.language || locale;
      setLanguage(preferred);
      await setLocaleOverride(preferred);
    }).catch(() => {
      if (mounted) setLanguage(locale);
    });
    return () => {
      mounted = false;
    };
  }, []);

  async function chooseLanguage(nextLanguage: string) {
    if (!nextLanguage || nextLanguage === language || languageBusy) return;
    const previous = language;
    setLanguage(nextLanguage);
    setLanguageBusy(true);
    setLanguageError("");
    try {
      const response = await updateAccountLanguage(nextLanguage);
      const confirmed = response.preferred_language || response.language || nextLanguage;
      setLanguage(confirmed);
      await setLocaleOverride(confirmed);
    } catch {
      setLanguage(previous);
      setLanguageError("Language could not be saved. Your previous preference is still active.");
    } finally {
      setLanguageBusy(false);
    }
  }

  const deviceZone = getDeviceTimeZone();
  const sampleTime = useMemo(
    () => localTime.absolute(now, { withTime: true, withZoneName: true }),
    [localTime, now]
  );
  const zoneCityLabel = timeZone.split("/").pop()?.replace(/_/g, " ") || timeZone;

  return (
    <Screen
      title="Language, Region & Time"
      subtitle="PulseSOC shows every date and time in your local zone. Times adjust automatically as you travel."
    >
      <View style={styles.panel}>
        <Text style={styles.panelLabel}>Active time zone</Text>
        <Text style={styles.activeZone}>{zoneCityLabel}</Text>
        <Text style={styles.muted}>{timeZone}</Text>
        <Text style={styles.sample}>{sampleTime}</Text>
        <Text style={styles.mutedSmall}>
          {automatic ? `Automatic · following this device (${deviceZone})` : "Manual override active"}
        </Text>
        <Text style={styles.mutedSmall}>Locale {locale} · clock format follows your device settings</Text>
      </View>

      <Text style={styles.sectionHeading}>Language</Text>
      <View style={styles.panel}>
        <Text style={styles.mutedSmall}>
          Your selection is saved to your PulseSoc account and follows you across devices.
        </Text>
        {languageError ? <Text style={styles.error}>{languageError}</Text> : null}
        {COMMON_LANGUAGES.map((item) => {
          const selected = language.toLowerCase().split("-")[0] === item.id;
          return (
            <Pressable
              key={item.id}
              accessibilityRole="button"
              accessibilityState={{ selected, disabled: languageBusy }}
              accessibilityLabel={`${item.label}${selected ? ", selected" : ""}`}
              disabled={languageBusy}
              style={[styles.row, selected && styles.rowSelected]}
              onPress={() => chooseLanguage(item.id)}
            >
              <Text style={styles.rowTitle}>{item.label}</Text>
              {selected ? <Text style={styles.check}>✓</Text> : null}
            </Pressable>
          );
        })}
      </View>

      <View style={styles.panel}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: automatic }}
          accessibilityLabel={`Automatic time zone${automatic ? ", selected" : ""}`}
          style={[styles.row, automatic && styles.rowSelected]}
          onPress={() => setTimeZoneOverride(null)}
        >
          <View style={styles.rowText}>
            <Text style={styles.rowTitle}>Automatic</Text>
            <Text style={styles.mutedSmall}>Use this device&apos;s time zone ({deviceZone})</Text>
          </View>
          {automatic ? <Text style={styles.check}>✓</Text> : null}
        </Pressable>
      </View>

      <Text style={styles.sectionHeading}>Set a specific time zone</Text>
      <View style={styles.panel}>
        <ScrollView style={styles.zoneList} nestedScrollEnabled>
          {COMMON_ZONES.map((zone) => {
            const selected = !automatic && manualTimeZone === zone.id;
            const zoneTime = localTime.absolute(now, { timeZone: zone.id, withTime: true });
            return (
              <Pressable
                key={zone.id}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                accessibilityLabel={`${zone.label}, ${zoneTime}${selected ? ", selected" : ""}`}
                style={[styles.row, selected && styles.rowSelected]}
                onPress={() => setTimeZoneOverride(zone.id)}
              >
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>{zone.label}</Text>
                  <Text style={styles.mutedSmall}>
                    {zone.id} · {zoneTime}
                  </Text>
                </View>
                {selected ? <Text style={styles.check}>✓</Text> : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  panel: {
    backgroundColor: colors.surface,
    borderRadius: 18,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: 16,
    gap: 6,
    marginBottom: 14
  },
  panelLabel: { color: colors.muted, fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase" },
  activeZone: { color: colors.text, fontSize: 22, fontWeight: "700" },
  sample: { color: colors.accent, fontSize: 16, fontWeight: "600", marginTop: 4 },
  muted: { color: colors.muted, fontSize: 13 },
  mutedSmall: { color: colors.muted, fontSize: 12 },
  error: { color: colors.danger, fontSize: 12 },
  sectionHeading: { color: colors.text, fontSize: 15, fontWeight: "700", marginBottom: 8, marginLeft: 4 },
  zoneList: { maxHeight: 360 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 12,
    paddingHorizontal: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: 12
  },
  rowSelected: { backgroundColor: colors.surfaceRaised, borderRadius: 12 },
  rowText: { flex: 1, gap: 2 },
  rowTitle: { color: colors.text, fontSize: 15, fontWeight: "600" },
  check: { color: colors.accent, fontSize: 18, fontWeight: "800" }
});
