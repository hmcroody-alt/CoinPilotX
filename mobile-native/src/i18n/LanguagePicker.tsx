import { Ionicons } from "@expo/vector-icons";
import { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  NativeModules,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { useTheme } from "../theme/ThemeContext";
import { useI18n } from "./I18nContext";
import { getCoverage } from "./coverage";
import { useDirection } from "./hooks";
import { LocaleDefinition, searchLocales } from "./locales";

/**
 * The language picker.
 *
 * Design decisions worth stating, because each one is a requirement rather than
 * a preference:
 *
 *   - The **native name leads** each row. Someone who landed in a language they
 *     cannot read finds their way out by recognising "Español" or "日本語", not
 *     by reading an English gloss. The English name sits underneath as a second
 *     chance for everyone else.
 *   - **Search matches both**, plus transliterations and ISO codes, and it is
 *     accent-insensitive — "espanol" and "français" both find French/Spanish.
 *   - Tapping a row **applies immediately**. There is no Save button, because a
 *     language change is instantly self-evident: if the wrong one was tapped,
 *     the list is still on screen and still readable via native names.
 *   - **"Use device language"** sits at the top as a first-class choice, not
 *     buried, so following the phone is as easy to return to as it was to leave.
 *   - A **coverage badge** appears only below 100%, so a partly-translated
 *     language warns the user before they pick it instead of after.
 */

/** True when the running build can reload itself to finish native RTL mirroring. */
function canReloadApp(): boolean {
  const devSettings = (NativeModules as { DevSettings?: { reload?: () => void } }).DevSettings;
  return typeof devSettings?.reload === "function";
}

function reloadApp(): void {
  const devSettings = (NativeModules as { DevSettings?: { reload?: () => void } }).DevSettings;
  try {
    devSettings?.reload?.();
  } catch {
    // The prompt stays on screen; the user can close and reopen the app instead.
  }
}

export interface LanguagePickerProps {
  /** Renders the "Use device language" row. Off for onboarding, where the
   *  device language is already the default and the row would be a no-op. */
  showFollowDevice?: boolean;
  /** Called after a language is applied — used to dismiss a modal presentation. */
  onChanged?: (locale: string) => void;
  testID?: string;
}

export function LanguagePicker({ showFollowDevice = true, onChanged, testID = "language-picker" }: LanguagePickerProps) {
  const theme = useTheme();
  const dir = useDirection();
  const {
    t,
    locale,
    available,
    followingDevice,
    detectionSource,
    switching,
    restartRecommended,
    definition,
    setLanguage
  } = useI18n();

  const [query, setQuery] = useState("");
  /** The row awaiting `setLanguage`, so only that row shows a spinner. */
  const [applying, setApplying] = useState<string | null>(null);
  /** Whether the user has changed the language since this screen opened. The
   *  live region stays empty until then, so mounting announces nothing. */
  const [changed, setChanged] = useState(false);

  const results = useMemo<readonly LocaleDefinition[]>(
    () => (query.trim() ? searchLocales(query) : available),
    [query, available]
  );

  const apply = useCallback(
    async (next: string | null) => {
      setApplying(next ?? "__device__");
      try {
        await setLanguage(next);
        setChanged(true);
        if (next) onChanged?.(next);
      } finally {
        setApplying(null);
      }
    },
    [setLanguage, onChanged]
  );

  // Read from `definition` rather than echoing the tapped code: choosing
  // "use device language" resolves to whatever the phone reports, and that
  // resolved language is the one to announce. Built after the switch, so the
  // confirmation is itself already in the new language.
  const liveMessage = switching
    ? t("settings:language.changing", { language: definition.nativeName })
    : changed
      ? t("settings:language.changed", { language: definition.nativeName })
      : "";

  return (
    <View testID={testID} style={styles.container}>
      <View
        style={[
          styles.search,
          dir.paddingX(12, 10),
          { backgroundColor: theme.colors.surfaceRaised, borderColor: theme.colors.border, borderRadius: theme.metrics.radius }
        ]}
      >
        <Ionicons name="search" size={theme.scaleFont(17)} color={theme.colors.muted} />
        <TextInput
          testID={`${testID}-search`}
          value={query}
          onChangeText={setQuery}
          placeholder={t("settings:language.searchPlaceholder")}
          placeholderTextColor={theme.colors.muted}
          accessibilityLabel={t("settings:language.searchPlaceholder")}
          autoCorrect={false}
          autoCapitalize="none"
          returnKeyType="search"
          clearButtonMode="while-editing"
          style={[
            styles.searchInput,
            dir.align(),
            dir.writingDirection(),
            { color: theme.colors.text, fontSize: theme.scaleFont(16) }
          ]}
        />
        {query.length > 0 && Platform.OS !== "ios" ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("common:actions.clear")}
            hitSlop={10}
            onPress={() => setQuery("")}
          >
            <Ionicons name="close-circle" size={theme.scaleFont(17)} color={theme.colors.muted} />
          </Pressable>
        ) : null}
      </View>

      {showFollowDevice && !query.trim() ? (
        <LanguageRow
          testID={`${testID}-device`}
          title={t("settings:language.systemDefault")}
          subtitle={
            followingDevice && detectionSource !== "fallback"
              ? t("settings:language.detectedAutomatically")
              : t("settings:language.systemDefaultHint")
          }
          selected={followingDevice}
          busy={applying === "__device__"}
          disabled={switching}
          icon="phone-portrait-outline"
          onPress={() => void apply(null)}
        />
      ) : null}

      {results.length === 0 ? (
        <Text
          testID={`${testID}-empty`}
          style={[styles.empty, dir.align(), { color: theme.colors.muted, fontSize: theme.scaleFont(14) }]}
        >
          {t("settings:language.noMatches", { query: query.trim() })}
        </Text>
      ) : (
        results.map((entry) => {
          const coverage = getCoverage(entry.code);
          const active = entry.code === locale && !followingDevice;
          return (
            <LanguageRow
              key={entry.code}
              testID={`${testID}-${entry.code}`}
              // The endonym leads; see the component note above.
              title={entry.nativeName}
              subtitle={entry.englishName}
              // Rendered in the language's own direction so an Arabic endonym
              // is not visually reordered by the surrounding LTR list.
              titleDirection={entry.direction}
              note={coverage.percent < 100 ? t("settings:language.translationCoverage", { percent: coverage.percent }) : undefined}
              selected={active}
              busy={applying === entry.code}
              disabled={switching}
              onPress={() => void apply(entry.code)}
            />
          );
        })
      )}

      {/* Only meaningful after a change, and only for Arabic in the shipped set. */}
      {restartRecommended ? (
        <View
          testID={`${testID}-restart`}
          style={[styles.notice, { backgroundColor: theme.colors.surfaceRaised, borderColor: theme.colors.border, borderRadius: theme.metrics.radius }]}
        >
          <Text style={[dir.align(), { color: theme.colors.text, fontSize: theme.scaleFont(14), lineHeight: theme.scaleFont(20) }]}>
            {t("settings:language.restartForRtl")}
          </Text>
          {canReloadApp() ? (
            <Pressable
              accessibilityRole="button"
              onPress={reloadApp}
              style={[styles.noticeAction, { backgroundColor: theme.colors.accent, borderRadius: theme.metrics.radius }]}
            >
              <Text style={{ color: theme.colors.background, fontWeight: "700", fontSize: theme.scaleFont(14) }}>
                {t("settings:language.restartNow")}
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {/* A polite live region, so screen-reader users get spoken confirmation of
          a change whose visual proof (the whole UI re-rendering) they cannot see. */}
      <Text accessibilityLiveRegion="polite" testID={`${testID}-status`} style={styles.visuallyHidden}>
        {liveMessage}
      </Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Row
 * ------------------------------------------------------------------ */

function LanguageRow({
  title,
  subtitle,
  note,
  selected,
  busy,
  disabled,
  icon,
  titleDirection,
  onPress,
  testID
}: {
  title: string;
  subtitle?: string;
  note?: string;
  selected: boolean;
  busy: boolean;
  disabled: boolean;
  icon?: keyof typeof Ionicons.glyphMap;
  titleDirection?: "ltr" | "rtl";
  onPress: () => void;
  testID: string;
}) {
  const theme = useTheme();
  const dir = useDirection();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ selected, disabled: disabled && !busy, busy }}
      accessibilityLabel={subtitle ? `${title}, ${subtitle}` : title}
      accessibilityHint={note}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        dir.row(),
        dir.paddingX(14, 12),
        {
          backgroundColor: pressed ? theme.colors.surfaceRaised : "transparent",
          borderBottomColor: theme.colors.border,
          opacity: disabled && !busy ? 0.5 : 1
        }
      ]}
    >
      {icon ? (
        <View style={dir.marginX(0, 12)}>
          <Ionicons name={icon} size={theme.scaleFont(20)} color={theme.colors.muted} />
        </View>
      ) : null}

      <View style={styles.rowBody}>
        <Text
          numberOfLines={1}
          style={[
            dir.align(),
            titleDirection ? { writingDirection: titleDirection } : dir.writingDirection(),
            { color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: selected ? "700" : theme.metrics.titleWeight }
          ]}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text
            numberOfLines={1}
            style={[dir.align(), { color: theme.colors.muted, fontSize: theme.scaleFont(13), marginTop: 2 }]}
          >
            {subtitle}
          </Text>
        ) : null}
        {note ? (
          <Text
            numberOfLines={1}
            style={[dir.align(), { color: theme.colors.warning, fontSize: theme.scaleFont(12), marginTop: 2 }]}
          >
            {note}
          </Text>
        ) : null}
      </View>

      {busy ? (
        <ActivityIndicator color={theme.colors.accent} />
      ) : selected ? (
        <Ionicons name="checkmark" size={theme.scaleFont(20)} color={theme.colors.accent} />
      ) : (
        <View style={{ width: theme.scaleFont(20) }} />
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { width: "100%" },
  search: { alignItems: "center", borderWidth: StyleSheet.hairlineWidth, flexDirection: "row", gap: 8, marginBottom: 8, paddingVertical: 10 },
  searchInput: { flex: 1, padding: 0 },
  row: { alignItems: "center", borderBottomWidth: StyleSheet.hairlineWidth, minHeight: 56, paddingVertical: 10 },
  rowBody: { flex: 1 },
  empty: { paddingHorizontal: 4, paddingVertical: 24, textAlign: "center" },
  notice: { borderWidth: StyleSheet.hairlineWidth, gap: 12, marginTop: 12, padding: 14 },
  noticeAction: { alignSelf: "flex-start", paddingHorizontal: 18, paddingVertical: 9 },
  // Kept in the tree for the screen reader but out of the visual layout.
  visuallyHidden: { height: 0, opacity: 0, position: "absolute", width: 0 }
});
