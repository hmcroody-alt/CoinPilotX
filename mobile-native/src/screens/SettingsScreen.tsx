/**
 * Settings index.
 *
 * This screen renders nothing of its own: it is a projection of
 * `src/settings/registry.ts`. Adding a settings page is a single entry there,
 * which simultaneously makes it appear in the right section, become searchable
 * by its keywords, and gain a `pulsesoc://settings/<id>` deep link. Nothing on
 * this screen needs editing to add a destination, which is the whole point —
 * the previous version was a hand-maintained wall of 26 `Pressable`s where the
 * list, the search, and the deep-link table had already drifted apart.
 *
 * There is deliberately no WebView fallback anywhere in this tree. Every row
 * below resolves to a native screen; the three `openSupportWebFallback` calls
 * that used to sit at the bottom of this file (privacy policy, terms, Telegram
 * setup) are now `LegalSettings` and `HelpSettings` respectively.
 */

import { useCallback, useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import {
  SettingsEmptyState,
  SettingsHeader,
  SettingsSearchField,
  SettingsSection,
  SettingsShell,
  animateNextLayout
} from "../settings/components/SettingsShell";
import { SettingsButton, SettingsRow, confirm } from "../settings/components/SettingsControls";
import {
  SettingsEntry,
  groupBySection,
  searchSettings,
  settingsSectionTitle,
  settingsSubtitle,
  settingsTitle,
  visibleSettings
} from "../settings/registry";
import { usePreferenceGroup } from "../settings/store";
import { RootStackParamList } from "../navigation/types";
import { signOut, signOutEverywhere, useAuth } from "../session/auth";
import { useTranslation } from "../i18n";
import { useTheme } from "../theme/ThemeContext";

type Nav = NativeStackNavigationProp<RootStackParamList>;

export function SettingsScreen() {
  const theme = useTheme();
  const navigation = useNavigation<Nav>();
  const { authState, setAuthState } = useAuth();
  const { value: developer } = usePreferenceGroup("developer");
  const { t } = useTranslation();

  const [query, setQuery] = useState("");
  const [signingOut, setSigningOut] = useState(false);

  const authenticated = authState.status === "signedIn";

  /**
   * Two-stage filter: visibility first (auth state, developer options), search
   * second. Doing it in this order means a signed-out user can never surface an
   * authenticated-only page by typing its name, and a hidden developer page
   * stays hidden rather than appearing only when searched for.
   */
  const entries = useMemo(
    () => visibleSettings({ authenticated, developerEnabled: developer.enabled }),
    [authenticated, developer.enabled]
  );

  // `t` is in the dependency list because `searchSettings` matches against the
  // *translated* index: switching language has to rebuild the results, not just
  // relabel them.
  const results = useMemo(() => searchSettings(query, entries), [entries, query, t]);
  const searching = query.trim().length > 0;
  const sections = useMemo(() => groupBySection(results), [results]);

  const open = useCallback(
    (entry: SettingsEntry) => {
      // The registry types `params` as `Record<string, unknown>` because it has
      // to cover every route's shape at once, which no single overload of the
      // typed `navigate` accepts. Widening the *function* rather than the
      // arguments keeps the erasure to this one line — `entry.route` is still
      // `keyof RootStackParamList`, so a typo in the registry is a build error,
      // and only the params payload goes unchecked.
      (navigation.navigate as unknown as (screen: string, params?: Record<string, unknown>) => void)(
        entry.route,
        entry.params
      );
    },
    [navigation]
  );

  const onChangeQuery = useCallback(
    (next: string) => {
      // Animate the list settling as results narrow — but only when the layout
      // is actually about to change shape, not on every keystroke.
      animateNextLayout(theme.reduceMotion);
      setQuery(next);
    },
    [theme.reduceMotion]
  );

  const handleSignOut = useCallback(async () => {
    if (signingOut) return;
    const ok = await confirm({
      title: t("settings:index.signOutConfirmTitle"),
      message: t("settings:index.signOutConfirmBody"),
      confirmLabel: t("settings:index.signOut")
    });
    if (!ok) return;
    setSigningOut(true);
    try {
      setAuthState(await signOut());
    } finally {
      setSigningOut(false);
    }
  }, [setAuthState, signingOut, t]);

  const handleSignOutEverywhere = useCallback(async () => {
    if (signingOut) return;
    const ok = await confirm({
      title: t("settings:index.signOutEverywhereConfirmTitle"),
      message: t("settings:index.signOutEverywhereConfirmBody"),
      confirmLabel: t("settings:index.signOutEverywhere"),
      destructive: true
    });
    if (!ok) return;
    setSigningOut(true);
    try {
      setAuthState(await signOutEverywhere());
    } catch (error) {
      // Sign-out-everywhere is a security action; a silent failure would leave
      // the user believing rogue sessions were killed when they weren't.
      await confirm({
        title: t("settings:index.signOutEverywhereFailedTitle"),
        message:
          error instanceof Error && error.message
            ? error.message
            : t("settings:index.signOutEverywhereFailedBody"),
        confirmLabel: t("common:actions.done"),
        cancelLabel: ""
      });
    } finally {
      setSigningOut(false);
    }
  }, [setAuthState, signingOut, t]);

  return (
    <SettingsShell>
      <SettingsHeader
        title={t("settings:root.title")}
        subtitle={authenticated ? t("settings:index.subtitle") : t("settings:index.subtitleSignedOut")}
      />

      <SettingsSearchField
        value={query}
        onChangeText={onChangeQuery}
        placeholder={t("settings:index.searchPlaceholder")}
      />

      {searching && results.length === 0 ? (
        <SettingsEmptyState
          icon="search-outline"
          title={t("settings:index.noMatchTitle")}
          body={t("settings:index.noMatchBody", { query: query.trim() })}
          action={
            <SettingsButton
              testID="settings-clear-search"
              label={t("common:actions.clear")}
              variant="secondary"
              full={false}
              onPress={() => onChangeQuery("")}
            />
          }
        />
      ) : (
        sections.map((section) => (
          <SettingsSection
            key={section.id}
            // While searching, section headings are noise — results are already
            // ranked by relevance, and a heading implies a completeness the
            // filtered list doesn't have.
            title={searching ? undefined : settingsSectionTitle(section.id)}
          >
            {section.entries.map((entry) => (
              <SettingsRow
                key={entry.id}
                testID={`settings-entry-${entry.id}`}
                title={settingsTitle(entry)}
                subtitle={settingsSubtitle(entry)}
                icon={entry.icon}
                chevron
                onPress={() => open(entry)}
                accessibilityHint={t("settings:index.openHint", { title: settingsTitle(entry) })}
              />
            ))}
          </SettingsSection>
        ))
      )}

      {authenticated && !searching ? (
        <SettingsSection
          title={t("settings:index.sessionTitle")}
          footnote={t("settings:index.signedInAs", {
            account: authState.user?.username
              ? `@${authState.user.username}`
              : authState.user?.email || t("settings:index.yourAccount")
          })}
        >
          <SettingsRow
            testID="settings-sign-out"
            title={t("settings:index.signOut")}
            subtitle={t("settings:index.signOutSubtitle")}
            icon="log-out-outline"
            busy={signingOut}
            onPress={() => void handleSignOut()}
          />
          <SettingsRow
            testID="settings-sign-out-everywhere"
            title={t("settings:index.signOutEverywhere")}
            subtitle={t("settings:index.signOutEverywhereSubtitle")}
            icon="power-outline"
            tone="danger"
            busy={signingOut}
            onPress={() => void handleSignOutEverywhere()}
          />
        </SettingsSection>
      ) : null}

      {!searching ? (
        <View style={styles.footer}>
          {/* Pluralized by the engine, not by a ternary: "1 setting / 2
              settings" is an English rule, and Arabic, Russian and Welsh all
              need forms this expression cannot produce. */}
          <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), textAlign: "center" }}>
            {t("settings:index.count", { count: results.length })}
          </Text>
        </View>
      ) : null}
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  footer: { marginTop: 24, paddingBottom: 8 }
});
