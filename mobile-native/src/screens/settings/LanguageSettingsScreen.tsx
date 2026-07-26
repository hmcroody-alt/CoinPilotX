import { useCallback, useEffect, useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsRow, SettingsSelect, SettingsSwitch, SelectOption } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { TimeFormat } from "../../settings/schema";
import { SUPPORTED_LOCALES } from "../../i18n/locales";
import { LanguagePicker } from "../../i18n/LanguagePicker";
import { regionDisplayName, useDirection, useFormatters, useI18n, useTranslation } from "../../i18n";
import { useTheme } from "../../theme/ThemeContext";

/**
 * The language list is read from `src/i18n/locales.ts`, never redeclared here.
 *
 * That file is the single source of truth for what the app ships, and
 * `catalogs/index.ts` asserts at module load that every entry has a translation
 * bundle. A hand-maintained second list on this screen drifts in both
 * directions and both are silent failures: offering a language with no catalog
 * makes `resolveSupportedLocale` return null and drops the user to English with
 * no explanation, while omitting one we do ship simply hides it.
 */
const LANGUAGES = SUPPORTED_LOCALES.map((locale) => ({
  tag: locale.code,
  label: locale.englishName,
  endonym: locale.nativeName
}));

/**
 * ISO 3166-1 codes only — the *names* are resolved at render time.
 *
 * Storing English labels here would leave nine untranslated country names on an
 * otherwise fully translated screen. `regionDisplayName` reads them from CLDR,
 * which carries every territory in every language we ship and stays current
 * through renames without us re-translating anything.
 */
const REGION_CODES = ["US", "GB", "DE", "ES", "BR", "IN", "AE", "JP", "KE"] as const;

/**
 * Language & region.
 *
 * Two different questions live on this screen and are deliberately kept apart:
 * the app language is what PulseSoc's own chrome speaks, `contentLanguages` is
 * what the feed is allowed to show you. Users routinely want an English
 * interface with posts in three other languages, and collapsing those into one
 * control is what makes most apps' language settings useless.
 */
export function LanguageSettingsScreen() {
  const theme = useTheme();
  const dir = useDirection();
  const { t } = useTranslation();
  const fmt = useFormatters();
  const { locale } = useI18n();
  const { value, setGroup, pending } = usePreferenceGroup("language");
  /** Explains a rejected removal. Cleared by any successful change. */
  const [notice, setNotice] = useState<string | null>(null);

  const selected = value.contentLanguages;

  /**
   * Mirrors the live interface language into the preference store.
   *
   * The i18n provider is the source of truth while the app is running — it also
   * owns "follow the device", which has no representation in this schema (the
   * tag validator rejects a sentinel like "auto"). What the stored value is
   * *for* is everything rendered outside the running app: server-sent email and
   * push notifications read `appLanguage` off the account. So the resolved tag
   * is written back whether the user picked it explicitly or the device did.
   * The equality guard is what keeps this from looping.
   */
  useEffect(() => {
    if (locale && locale !== value.appLanguage) void setGroup({ appLanguage: locale });
  }, [locale, value.appLanguage, setGroup]);

  const selectedSummary = useMemo(() => {
    const names = selected.map((tag) => LANGUAGES.find((language) => language.tag === tag)?.endonym ?? tag.toUpperCase());
    // `fmt.list` applies the locale's own conjunction and separator — Arabic's
    // "،" and Japanese's "、" are not commas, and "and" is not universal.
    return fmt.list(names, { max: 3 });
  }, [selected, fmt]);

  const regionOptions = useMemo<SelectOption<string>[]>(
    () => [
      {
        value: "auto",
        label: t("settings:region.matchDevice"),
        description: t("settings:region.matchDeviceHint"),
        icon: "phone-portrait-outline"
      },
      ...REGION_CODES.map((code) => ({ value: code, label: regionDisplayName(code) }))
    ],
    [t, fmt.locale]
  );

  const timeFormatOptions = useMemo<SelectOption<TimeFormat>[]>(() => {
    // The sample times are formatted, not written: "1:30 PM" and "13:30" are
    // themselves English conventions, and the point of this control is to show
    // the user what their own timestamps will look like.
    const sample = new Date(2024, 0, 1, 13, 30);
    return [
      {
        value: "12h",
        label: t("settings:region.timeFormat12"),
        description: fmt.time(sample, { hour12: true }),
        icon: "sunny-outline"
      },
      {
        value: "24h",
        label: t("settings:region.timeFormat24"),
        description: fmt.time(sample, { hour12: false }),
        icon: "time-outline"
      }
    ];
  }, [t, fmt]);

  const toggleContentLanguage = useCallback(
    (tag: string) => {
      const active = selected.includes(tag);
      if (active && selected.length === 1) {
        // An empty list would silently filter the entire feed to nothing, which
        // reads as "PulseSoc is broken" rather than "I removed my last language".
        setNotice(t("settings:contentLanguages.keepOne"));
        return;
      }
      setNotice(null);
      // Append rather than rebuilding from `LANGUAGES`. Rebuilding would quietly
      // drop any stored tag this build doesn't render a row for — a language set
      // on the web, or one from a newer release — turning "add a language" into
      // silent data loss on the rest of the list.
      const next = active ? selected.filter((entry) => entry !== tag) : [...selected, tag];
      void setGroup({ contentLanguages: next });
    },
    [selected, setGroup, t]
  );

  /**
   * The time-format preview shows the *current* time, so it has to tick.
   * Re-rendering on the minute boundary rather than every second keeps this to
   * roughly one render per minute while the screen is open, and lands the change
   * at the moment the displayed digits actually change.
   */
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      const current = new Date();
      setNow(current);
      const msToNextMinute = 60_000 - (current.getSeconds() * 1000 + current.getMilliseconds());
      timer = setTimeout(schedule, msToNextMinute);
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title={t("settings:language.screenTitle")} subtitle={t("settings:language.screenSubtitle")} />

      <SettingsSection
        title={t("settings:language.appLanguage")}
        description={t("settings:language.appLanguageDescription")}
        busy={pending}
      >
        {/* The picker applies immediately and owns its own persistence. The
            preference store is updated by the mirroring effect above rather
            than from an `onChanged` callback, so the "use device language" row
            — which resolves to a language rather than naming one — travels the
            same path as an explicit pick. */}
        <LanguagePicker testID="language-app" />
      </SettingsSection>

      <SettingsSection
        title={t("settings:contentLanguages.title")}
        description={t("settings:contentLanguages.description", { list: selectedSummary })}
        footnote={t("settings:contentLanguages.footnote")}
      >
        {LANGUAGES.map((language) => {
          const active = selected.includes(language.tag);
          const isLastRemaining = active && selected.length === 1;
          return (
            <SettingsRow
              key={language.tag}
              testID={`language-content-${language.tag}`}
              // Endonym leads, matching the picker: someone who has landed in a
              // language they cannot read recognises "日本語", not "Japanese".
              title={language.endonym}
              subtitle={language.label}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityHint={
                isLastRemaining
                  ? t("settings:contentLanguages.hintLocked")
                  : active
                    ? t("settings:contentLanguages.hintRemove")
                    : t("settings:contentLanguages.hintAdd")
              }
              onPress={() => toggleContentLanguage(language.tag)}
              accessory={
                active ? (
                  <Ionicons name="checkmark" size={theme.scaleFont(20)} color={theme.colors.accent} />
                ) : (
                  <View style={{ width: theme.scaleFont(20) }} />
                )
              }
            />
          );
        })}
      </SettingsSection>

      {notice ? (
        <Text
          accessibilityLiveRegion="polite"
          testID="language-content-notice"
          style={[
            styles.notice,
            dir.align(),
            { color: theme.colors.warning, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18) }
          ]}
        >
          {notice}
        </Text>
      ) : null}

      <SettingsSection title={t("settings:contentLanguages.translationTitle")}>
        <SettingsSwitch
          testID="language-auto-translate"
          title={t("settings:contentLanguages.autoTranslate")}
          subtitle={t("settings:contentLanguages.autoTranslateHint")}
          icon="language-outline"
          value={value.autoTranslate}
          onValueChange={(next) => void setGroup({ autoTranslate: next })}
        />
      </SettingsSection>

      <SettingsSection title={t("settings:region.title")} description={t("settings:region.regionDescription")}>
        <SettingsSelect
          options={regionOptions}
          value={value.region}
          onChange={(next) => {
            setNotice(null);
            void setGroup({ region: next });
          }}
          testID="language-region"
        />
      </SettingsSection>

      <SettingsSection title={t("settings:region.timeFormat")} footnote={t("settings:region.timeFormatFootnote")}>
        <SettingsSelect
          options={timeFormatOptions}
          value={value.timeFormat}
          onChange={(next) => void setGroup({ timeFormat: next })}
          testID="language-time-format"
        />
        <View style={[styles.preview, { padding: theme.metrics.rowPaddingHorizontal }]}>
          <Text style={[dir.align(), { color: theme.colors.muted, fontSize: theme.scaleFont(13) }]}>
            {t("settings:region.timePreviewLabel")}
          </Text>
          <Text
            style={[
              dir.align(),
              {
                color: theme.colors.text,
                fontSize: theme.scaleFont(22),
                fontWeight: theme.metrics.titleWeight,
                marginTop: 2
              }
            ]}
          >
            {fmt.time(now, { hour12: value.timeFormat === "12h" })}
          </Text>
        </View>
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  notice: { marginTop: 10, paddingHorizontal: 4 },
  preview: { width: "100%" }
});
