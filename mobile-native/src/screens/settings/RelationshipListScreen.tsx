/**
 * Shared implementation behind Blocked accounts and Muted accounts.
 *
 * The two screens are the same interaction with different words and a different
 * endpoint: fetch a relationship list, show who is on it and since when, and let
 * the user take exactly one action per row that removes them from it. Keeping a
 * single implementation means a fix to the optimistic-removal or rollback logic
 * lands in both places at once — the previous duplicated screens had drifted to
 * the point where only one of them rolled back on failure.
 *
 * Everything that differs between the two lives in `RelationshipListConfig`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import {
  SettingsEmptyState,
  SettingsHeader,
  SettingsSearchField,
  SettingsSection,
  SettingsShell,
  animateNextLayout
} from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsButton, confirm } from "../../settings/components/SettingsControls";
import type { RelationshipUser } from "../../settings/api";
import { useTheme } from "../../theme/ThemeContext";

/** Above this many rows the list stops being scannable, so search appears. */
const SEARCH_THRESHOLD = 8;

export type RelationshipListConfig = {
  /** Page title, e.g. "Blocked accounts". */
  title: string;
  /**
   * Header copy. Blocking and muting are routinely confused, so each screen
   * spends its subtitle explaining what this list actually does and what the
   * other one would do instead.
   */
  subtitle: string;
  fetch: () => Promise<RelationshipUser[]>;
  /** Called with `false` to remove the relationship (unblock / unmute). */
  mutate: (userId: number, active: boolean) => Promise<void>;
  /** Label on the per-row action button, e.g. "Unblock". */
  actionLabel: string;
  /** Verb used in the "…ed 3 days ago" timestamp line, e.g. "Blocked". */
  sinceVerb: string;
  /** Section heading above the list, e.g. "Blocked accounts". */
  sectionTitle: string;
  /** Explains the consequence of the action; shown under the list. */
  sectionFootnote: string;
  emptyIcon: keyof typeof Ionicons.glyphMap;
  emptyTitle: string;
  emptyBody: string;
  searchPlaceholder: string;
  confirmTitle: (user: RelationshipUser) => string;
  confirmMessage: (user: RelationshipUser) => string;
  /** Prefix for every testID on the screen, e.g. "blocked-users". */
  testIDPrefix: string;
};

/* -------------------------------------------------------------------------- */
/*                               Relative time                                 */
/* -------------------------------------------------------------------------- */

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
}

/**
 * Coarse relative formatter.
 *
 * Deliberately hand-written: the app ships no date library, and "how long ago
 * did I block this person" needs one significant unit, not calendar-accurate
 * arithmetic. Anything unparseable degrades to an empty string so the caller can
 * simply omit the line rather than render "Invalid Date".
 */
export function relativeTimeFrom(iso: string | null): string {
  if (!iso) return "";
  const timestamp = Date.parse(iso);
  if (!Number.isFinite(timestamp)) return "";

  const seconds = Math.round((Date.now() - timestamp) / 1000);
  // A clock skew between device and server can make `since` look like the
  // future; treat anything non-positive as "just now" rather than "-2 days ago".
  if (seconds < 45) return "just now";
  if (seconds < HOUR) return plural(Math.round(seconds / MINUTE), "minute");
  if (seconds < DAY) return plural(Math.round(seconds / HOUR), "hour");
  if (seconds < WEEK) return plural(Math.round(seconds / DAY), "day");
  if (seconds < MONTH) return plural(Math.round(seconds / WEEK), "week");
  if (seconds < YEAR) return plural(Math.round(seconds / MONTH), "month");
  return plural(Math.round(seconds / YEAR), "year");
}

/* -------------------------------------------------------------------------- */
/*                                   Avatar                                    */
/* -------------------------------------------------------------------------- */

/** Palette roles reused as avatar tints so the fallback re-themes with the app. */
const AVATAR_ROLES = ["accent", "accentStrong", "intelligence", "creator", "economy", "safety", "crypto"] as const;

function initialFor(user: RelationshipUser): string {
  const source = user.displayName || user.username || "";
  const first = Array.from(source.trim())[0];
  return (first || "?").toUpperCase();
}

/** Stable per-user tint: the same account always gets the same colour. */
function tintIndexFor(user: RelationshipUser): number {
  const seed = user.username || String(user.id);
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) % 100003;
  }
  return hash % AVATAR_ROLES.length;
}

function RelationshipAvatar({ user }: { user: RelationshipUser }) {
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  const size = Math.max(40, theme.scaleFont(40));

  // A remote avatar that 404s must not leave a blank hole in the row, so a
  // failed load falls through to the same initial-letter treatment as a null URL.
  if (user.avatarUrl && !failed) {
    return (
      <Image
        source={{ uri: user.avatarUrl }}
        onError={() => setFailed(true)}
        accessibilityIgnoresInvertColors
        // Decorative: the row already announces the name and handle.
        accessibilityElementsHidden
        importantForAccessibility="no"
        style={[styles.avatar, { backgroundColor: theme.colors.surfaceRaised, borderRadius: size / 2, height: size, width: size }]}
      />
    );
  }

  const tint = theme.colors[AVATAR_ROLES[tintIndexFor(user)]];
  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no"
      style={[
        styles.avatar,
        styles.avatarFallback,
        { backgroundColor: theme.colors.surfaceRaised, borderColor: tint, borderRadius: size / 2, height: size, width: size }
      ]}
    >
      <Text style={{ color: tint, fontSize: theme.scaleFont(17), fontWeight: "900" }}>{initialFor(user)}</Text>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                    Row                                      */
/* -------------------------------------------------------------------------- */

/**
 * Purpose-built row rather than `SettingsRow`: this needs an avatar (the shared
 * row only takes an Ionicon) and a trailing action button that is independently
 * focusable. Padding and separators still come from the enclosing
 * `SettingsSection`, so it lines up with every other settings surface.
 */
function RelationshipRow({
  user,
  actionLabel,
  sinceVerb,
  busy,
  onAction,
  testID
}: {
  user: RelationshipUser;
  actionLabel: string;
  sinceVerb: string;
  busy: boolean;
  onAction: () => void;
  testID: string;
}) {
  const theme = useTheme();
  const relative = relativeTimeFrom(user.since);
  const handle = user.username ? `@${user.username}` : `User ${user.id}`;

  return (
    <View
      style={[
        styles.row,
        {
          minHeight: Math.max(56, theme.metrics.rowMinHeight),
          paddingHorizontal: theme.metrics.rowPaddingHorizontal,
          paddingVertical: theme.metrics.rowPaddingVertical
        }
      ]}
    >
      <RelationshipAvatar user={user} />

      <View style={styles.rowBody}>
        <Text numberOfLines={1} style={{ color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: "700" }}>
          {user.displayName}
        </Text>
        <Text numberOfLines={1} style={{ color: theme.colors.muted, fontSize: theme.scaleFont(13), marginTop: 1 }}>
          {handle}
        </Text>
        {relative ? (
          <Text numberOfLines={1} style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), marginTop: 3 }}>
            {sinceVerb} {relative}
          </Text>
        ) : null}
      </View>

      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={`${actionLabel} ${user.displayName}`}
        accessibilityHint={`Removes ${handle} from this list.`}
        accessibilityState={{ disabled: busy, busy }}
        disabled={busy}
        hitSlop={6}
        onPress={onAction}
        style={({ pressed }) => [
          styles.action,
          {
            backgroundColor: pressed ? theme.colors.signalDim : "transparent",
            borderColor: theme.colors.border,
            minHeight: Math.max(36, theme.scaleFont(36)),
            opacity: busy ? 0.6 : 1
          }
        ]}
      >
        {busy ? (
          <ActivityIndicator size="small" color={theme.colors.accent} />
        ) : (
          <Text style={{ color: theme.colors.accent, fontSize: theme.scaleFont(14), fontWeight: "800" }}>{actionLabel}</Text>
        )}
      </Pressable>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

type LoadState = "loading" | "ready" | "error";

export function RelationshipListScreen(config: RelationshipListConfig) {
  const theme = useTheme();
  const [users, setUsers] = useState<RelationshipUser[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [query, setQuery] = useState("");
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const mounted = useRef(true);

  /**
   * The callers spread their config (`<RelationshipListScreen {...config} />`),
   * so `config` is a freshly allocated props object on every render even though
   * the caller memoised it. Depending on it directly would make `load` unstable,
   * and `load` feeding a `useEffect` that calls `setUsers` would refetch forever.
   * Everything below therefore depends on the individual fields — which are
   * stable — and never on the props object itself.
   */
  const { fetch: fetchList, mutate, actionLabel, confirmTitle, confirmMessage } = config;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const list = await fetchList();
      if (!mounted.current) return;
      setUsers(list);
      setState("ready");
    } catch {
      // Reads on this surface throw on transport failure precisely so we can
      // land here: an empty list must mean "nobody is on this list", never
      // "we couldn't ask".
      if (!mounted.current) return;
      setState("error");
    }
  }, [fetchList]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setState((current) => (current === "error" ? "loading" : current));
    await load();
  }, [load]);

  const remove = useCallback(
    async (user: RelationshipUser) => {
      const ok = await confirm({
        title: confirmTitle(user),
        message: confirmMessage(user),
        confirmLabel: actionLabel,
        cancelLabel: "Cancel"
      });
      if (!ok) return;

      setActionError(null);
      setPendingId(user.id);
      animateNextLayout(theme.reduceMotion);

      // Optimistic: the row leaves immediately. The index is captured from the
      // live list inside the updater — not from a closed-over `users` — so a
      // failure can put the user back exactly where they were without making
      // this callback depend on (and churn with) the list itself.
      let index = -1;
      setUsers((current) => {
        index = current.findIndex((entry) => entry.id === user.id);
        return current.filter((entry) => entry.id !== user.id);
      });

      try {
        await mutate(user.id, false);
      } catch (caught) {
        if (!mounted.current) return;
        animateNextLayout(theme.reduceMotion);
        setUsers((current) => {
          // Another row may have been removed meanwhile; rebuild from the
          // current list rather than blindly restoring the old snapshot.
          if (current.some((entry) => entry.id === user.id)) return current;
          const restored = current.slice();
          restored.splice(Math.min(Math.max(index, 0), restored.length), 0, user);
          return restored;
        });
        const message = caught instanceof Error && caught.message ? caught.message : "Please try again.";
        setActionError(`Couldn't ${actionLabel.toLowerCase()} ${user.displayName}. ${message}`);
      } finally {
        if (mounted.current) setPendingId(null);
      }
    },
    [actionLabel, confirmMessage, confirmTitle, mutate, theme.reduceMotion]
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return users;
    return users.filter(
      (user) => user.displayName.toLowerCase().includes(needle) || user.username.toLowerCase().includes(needle)
    );
  }, [query, users]);

  const showSearch = users.length >= SEARCH_THRESHOLD;

  return (
    <SettingsShell bottomDock={false} onRefresh={refresh} refreshing={false}>
      <SettingsHeader title={config.title} subtitle={config.subtitle} />

      {showSearch ? (
        <SettingsSearchField value={query} onChangeText={setQuery} placeholder={config.searchPlaceholder} />
      ) : null}

      {actionError ? (
        <View
          accessibilityLiveRegion="polite"
          style={[styles.banner, { backgroundColor: theme.colors.dangerSoft, borderColor: theme.colors.danger }]}
        >
          <Ionicons name="alert-circle" size={theme.scaleFont(16)} color={theme.colors.danger} />
          <Text style={{ color: theme.colors.text, flex: 1, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18) }}>
            {actionError}
          </Text>
          <Pressable
            testID={`${config.testIDPrefix}-dismiss-error`}
            accessibilityRole="button"
            accessibilityLabel="Dismiss error"
            hitSlop={10}
            onPress={() => setActionError(null)}
          >
            <Ionicons name="close" size={theme.scaleFont(16)} color={theme.colors.muted} />
          </Pressable>
        </View>
      ) : null}

      {state === "loading" ? (
        <View style={styles.loading} accessibilityLabel="Loading" accessibilityRole="progressbar">
          <ActivityIndicator color={theme.colors.accent} />
          <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14), marginTop: 10 }}>Loading your list…</Text>
        </View>
      ) : state === "error" ? (
        <SettingsEmptyState
          icon="cloud-offline-outline"
          title="Couldn't load this list"
          body="We couldn't reach PulseSoc. Check your connection and try again."
          action={
            <SettingsButton
              testID={`${config.testIDPrefix}-retry`}
              label="Try again"
              icon="refresh"
              variant="secondary"
              full={false}
              onPress={() => void refresh()}
            />
          }
        />
      ) : users.length === 0 ? (
        <SettingsEmptyState icon={config.emptyIcon} title={config.emptyTitle} body={config.emptyBody} />
      ) : filtered.length === 0 ? (
        <SettingsEmptyState
          icon="search-outline"
          title="No matches"
          body={`No account on this list matches “${query.trim()}”.`}
          action={
            <SettingsButton
              testID={`${config.testIDPrefix}-clear-search`}
              label="Clear search"
              variant="secondary"
              full={false}
              onPress={() => setQuery("")}
            />
          }
        />
      ) : (
        <SettingsSection
          title={config.sectionTitle}
          footnote={config.sectionFootnote}
          busy={pendingId !== null}
        >
          {filtered.map((user) => (
            <RelationshipRow
              key={user.id}
              user={user}
              actionLabel={config.actionLabel}
              sinceVerb={config.sinceVerb}
              busy={pendingId === user.id}
              onAction={() => void remove(user)}
              testID={`${config.testIDPrefix}-action-${user.id}`}
            />
          ))}
        </SettingsSection>
      )}

      {state === "ready" && users.length ? (
        <View style={styles.count}>
          <SettingsBadge
            label={`${users.length} ${users.length === 1 ? "account" : "accounts"}`}
            tone="muted"
          />
        </View>
      ) : null}
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  row: { alignItems: "center", flexDirection: "row", gap: 12 },
  rowBody: { flex: 1, justifyContent: "center" },
  avatar: { alignItems: "center", justifyContent: "center" },
  avatarFallback: { borderWidth: StyleSheet.hairlineWidth },
  action: {
    alignItems: "center",
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minWidth: 84,
    paddingHorizontal: 12
  },
  banner: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  loading: { alignItems: "center", paddingVertical: 48 },
  count: { alignItems: "center", marginTop: 18 }
});
