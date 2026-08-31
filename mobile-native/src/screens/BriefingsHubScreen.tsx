/**
 * Pulse Briefings hub — latest briefing, delivery status, settings, topics,
 * and paged history. Owner-only (the profile tile never renders for visitors).
 *
 * Three rules govern everything on this screen:
 *
 * 1. The server is the single authority. Every toggle here PATCHes the
 *    canonical preference row the scheduler reads, and the switch position is
 *    whatever the server echoed back — never an optimistic value left standing
 *    after a failed write.
 * 2. "Next check around X" is an estimate of the next *evaluation*, phrased
 *    that way on purpose: a window is a chance to receive a briefing, not a
 *    promise of one. Significance and dedupe gates may keep a window silent.
 * 3. A failed load is never presented as an empty history. The empty state
 *    renders only after a load that *succeeded* with zero rows; failures show
 *    the cached page (with an offline notice) or an explicit error + retry.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import {
  BRIEFING_HISTORY_PAGE_SIZE,
  BriefingDeliveryStatus,
  BriefingFrequency,
  BriefingListItem,
  BriefingPreferences,
  getBriefingPreferences,
  getBriefingStatus,
  listBriefings,
  loadCachedBriefingStatus,
  loadCachedBriefings,
  markBriefingsSeen,
  updateBriefingPreferences
} from "../api/briefings";
import { trackBriefings } from "../briefings/briefingsAnalytics";
import { Panel } from "../components/Panel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "BriefingsHub">;

/** "off" is the master switch, not a cadence — the picker never offers it. */
const FREQUENCY_CHOICES: BriefingFrequency[] = ["smart", "every_6h", "morning_evening", "daily", "important_only"];

/** Defensive timestamp formatter: never throws, never invents a value. */
function formatWhen(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

/** Shift an "HH:MM" quiet-hours boundary by whole hours, wrapping at 24. */
function shiftHour(value: string, delta: number): string {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value || "");
  const hour = match ? Number(match[1]) : 0;
  const minute = match ? match[2] : "00";
  const next = ((hour + delta) % 24 + 24) % 24;
  return `${String(next).padStart(2, "0")}:${minute}`;
}

export function BriefingsHubScreen({ navigation }: Props) {
  const { t } = useTranslation();

  const [items, setItems] = useState<BriefingListItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [prefs, setPrefs] = useState<BriefingPreferences | null>(null);
  const [status, setStatus] = useState<BriefingDeliveryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [fromCache, setFromCache] = useState(false);
  const [saveError, setSaveError] = useState(false);

  const loadAll = useCallback(async (mode: "initial" | "refresh") => {
    if (mode === "refresh") setRefreshing(true);
    setSaveError(false);

    // History: live first; on failure fall back to the cached first page so a
    // network blip never masquerades as "no briefings".
    try {
      const page = await listBriefings({ limit: BRIEFING_HISTORY_PAGE_SIZE, offset: 0 });
      setItems(page.briefings);
      setHasMore(page.has_more);
      setNextOffset(page.next_offset);
      setHistoryFailed(false);
      setFromCache(false);
      trackBriefings("briefings_history_page_loaded", { offset: 0, count: page.briefings.length });
    } catch {
      trackBriefings("briefings_load_failed", { surface: "history" });
      const cached = await loadCachedBriefings().catch(() => null);
      if (cached && cached.briefings.length) {
        setItems(cached.briefings);
        setHasMore(false);
        setNextOffset(null);
        setHistoryFailed(false);
        setFromCache(true);
      } else {
        setHistoryFailed(true);
        setFromCache(false);
      }
    }

    // Preferences and status are independent of history; each degrades alone.
    await Promise.all([
      getBriefingPreferences()
        .then(setPrefs)
        .catch(() => trackBriefings("briefings_load_failed", { surface: "preferences" })),
      getBriefingStatus()
        .then(setStatus)
        .catch(async () => {
          trackBriefings("briefings_load_failed", { surface: "status" });
          const cached = await loadCachedBriefingStatus().catch(() => null);
          if (cached) setStatus(cached);
        })
    ]);

    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    trackBriefings("briefings_hub_opened");
    loadAll("initial").catch(() => setLoading(false));
    // Clear the server-side unread cursor: the member is looking at the hub
    // now, so the tile's NEW label must not survive this visit.
    markBriefingsSeen()
      .then(() => trackBriefings("briefings_marked_seen"))
      .catch(() => undefined);
  }, [loadAll]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore || nextOffset === null) return;
    setLoadingMore(true);
    try {
      const page = await listBriefings({ limit: BRIEFING_HISTORY_PAGE_SIZE, offset: nextOffset });
      setItems((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...page.briefings.filter((item) => !seen.has(item.id))];
      });
      setHasMore(page.has_more);
      setNextOffset(page.next_offset);
      trackBriefings("briefings_history_page_loaded", { offset: nextOffset, count: page.briefings.length });
    } catch {
      trackBriefings("briefings_load_failed", { surface: "history_more" });
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, hasMore, nextOffset]);

  /**
   * All preference writes funnel through here: PATCH, then adopt whatever the
   * server echoed. On failure the previous server state stays on screen.
   */
  const savePrefs = useCallback(async (patch: Partial<BriefingPreferences>) => {
    setSaving(true);
    setSaveError(false);
    try {
      const next = await updateBriefingPreferences(patch);
      setPrefs(next);
      // Delivery status derives from the same row; refresh so "next check"
      // and the master flag stay coherent with what was just saved.
      getBriefingStatus().then(setStatus).catch(() => undefined);
    } catch {
      setSaveError(true);
      trackBriefings("briefings_load_failed", { surface: "preferences_write" });
    } finally {
      setSaving(false);
    }
  }, []);

  const latest = items.length ? items[0] : null;
  const enabled = Boolean(prefs?.enabled);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("briefings:hub.loading")}</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => loadAll("refresh").catch(() => undefined)}
          tintColor={colors.accent}
        />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("briefings:hub.title")}</Text>
        <Text style={styles.subtitle}>{t("briefings:hub.subtitle")}</Text>
      </View>

      {fromCache ? <Text style={styles.notice}>{t("briefings:hub.offlineNotice")}</Text> : null}
      {saveError ? <Text style={styles.error}>{t("briefings:hub.saveError")}</Text> : null}

      {/* LATEST BRIEFING */}
      <Text style={styles.sectionLabel}>{t("briefings:hub.latestTitle")}</Text>
      <Panel>
        {latest ? (
          <Pressable
            accessibilityRole="button"
            accessibilityHint={t("briefings:hub.openHint")}
            onPress={() => {
              trackBriefings("briefing_opened", { briefing_id: latest.id, from: "latest" });
              navigation.navigate("BriefingDetail", { briefingId: latest.id, title: latest.title });
            }}
            style={styles.latest}
          >
            <Text style={styles.rowTitle}>{latest.title}</Text>
            <Text style={styles.rowMeta}>{formatWhen(latest.sent_at || latest.generated_at)}</Text>
            <Text style={styles.body} numberOfLines={3}>{latest.body}</Text>
          </Pressable>
        ) : historyFailed ? (
          <View style={styles.gap}>
            <Text style={styles.error}>{t("briefings:hub.loadError")}</Text>
            <Pressable
              accessibilityRole="button"
              style={styles.loadMore}
              onPress={() => loadAll("refresh").catch(() => undefined)}
            >
              <Text style={styles.loadMoreText}>{t("briefings:hub.retry")}</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.gap}>
            <Text style={styles.rowTitle}>{t("briefings:hub.empty.title")}</Text>
            <Text style={styles.muted}>{t("briefings:hub.empty.body")}</Text>
          </View>
        )}
      </Panel>

      {/* DELIVERY STATUS */}
      {status ? (
        <>
          <Text style={styles.sectionLabel}>{t("briefings:hub.statusTitle")}</Text>
          <Panel>
            <View style={styles.gap}>
              {status.enabled && status.next_check_local ? (
                <Text style={styles.statusLine}>
                  {t("briefings:status.nextCheck", { time: formatWhen(status.next_check_local) })}
                </Text>
              ) : (
                <Text style={styles.statusLine}>{t("briefings:status.nextCheckNone")}</Text>
              )}
              <Text style={styles.muted}>
                {t("briefings:status.quiet", { start: status.quiet_start, end: status.quiet_end })}
              </Text>
              <Text style={styles.muted}>{t("briefings:status.timezone", { zone: status.timezone })}</Text>
              <Text style={styles.muted}>
                {status.push_enabled ? t("briefings:status.pushOn") : t("briefings:status.pushOff")}
              </Text>
            </View>
          </Panel>
        </>
      ) : null}

      {/* SETTINGS */}
      {prefs ? (
        <>
          <Text style={styles.sectionLabel}>{t("briefings:hub.settingsTitle")}</Text>
          <Panel>
            <View style={styles.toggleRow}>
              <View style={styles.toggleCopy}>
                <Text style={styles.rowTitle}>{t("briefings:settings.master")}</Text>
                <Text style={styles.muted}>{t("briefings:settings.masterHint")}</Text>
              </View>
              <Switch
                value={enabled}
                disabled={saving}
                onValueChange={(value) => {
                  trackBriefings("briefing_master_toggled", { enabled: value });
                  savePrefs({ enabled: value }).catch(() => undefined);
                }}
                trackColor={{ true: colors.accent, false: colors.border }}
              />
            </View>

            <Text style={styles.groupLabel}>{t("briefings:settings.frequency")}</Text>
            {FREQUENCY_CHOICES.map((choice) => {
              const selected = prefs.frequency === choice;
              return (
                <Pressable
                  key={choice}
                  accessibilityRole="button"
                  accessibilityState={{ selected, disabled: !enabled || saving }}
                  disabled={!enabled || saving}
                  style={[styles.choiceRow, !enabled ? styles.disabled : undefined]}
                  onPress={() => {
                    if (selected) return;
                    trackBriefings("briefing_frequency_changed", { frequency: choice });
                    savePrefs({ frequency: choice }).catch(() => undefined);
                  }}
                >
                  <Ionicons
                    name={selected ? "checkmark-circle" : "ellipse-outline"}
                    size={20}
                    color={selected ? colors.accent : colors.muted}
                  />
                  <View style={styles.choiceCopy}>
                    <Text style={styles.rowTitle}>{t(`briefings:frequency.${choice}`)}</Text>
                    {choice === "smart" ? (
                      <Text style={styles.recommended}>{t("briefings:frequency.recommended")}</Text>
                    ) : null}
                  </View>
                </Pressable>
              );
            })}
            <Text style={styles.muted}>{t("briefings:settings.frequencyNote")}</Text>

            <Text style={styles.groupLabel}>{t("briefings:settings.quiet")}</Text>
            {(["quiet_start", "quiet_end"] as const).map((field) => (
              <View key={field} style={styles.quietRow}>
                <Text style={styles.rowTitle}>
                  {field === "quiet_start" ? t("briefings:settings.quietFrom") : t("briefings:settings.quietUntil")}
                </Text>
                <View style={styles.stepper}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("briefings:settings.earlier")}
                    disabled={!enabled || saving}
                    style={[styles.stepButton, !enabled ? styles.disabled : undefined]}
                    onPress={() => {
                      trackBriefings("briefing_quiet_hours_changed", { field });
                      savePrefs({ [field]: shiftHour(prefs[field], -1) }).catch(() => undefined);
                    }}
                  >
                    <Ionicons name="remove" size={18} color={colors.text} />
                  </Pressable>
                  <Text style={styles.quietValue}>{prefs[field]}</Text>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={t("briefings:settings.later")}
                    disabled={!enabled || saving}
                    style={[styles.stepButton, !enabled ? styles.disabled : undefined]}
                    onPress={() => {
                      trackBriefings("briefing_quiet_hours_changed", { field });
                      savePrefs({ [field]: shiftHour(prefs[field], 1) }).catch(() => undefined);
                    }}
                  >
                    <Ionicons name="add" size={18} color={colors.text} />
                  </Pressable>
                </View>
              </View>
            ))}
            <Text style={styles.muted}>{t("briefings:settings.quietNote")}</Text>
          </Panel>

          {/* TOPICS — only the three the backend actually supports. */}
          <Text style={styles.sectionLabel}>{t("briefings:hub.topicsTitle")}</Text>
          <Panel>
            {([
              ["network_enabled", "network"],
              ["crypto_enabled", "crypto"],
              ["watchlist_enabled", "watchlist"]
            ] as const).map(([field, key]) => (
              <View key={field} style={styles.toggleRow}>
                <View style={styles.toggleCopy}>
                  <Text style={styles.rowTitle}>{t(`briefings:topics.${key}`)}</Text>
                  <Text style={styles.muted}>{t(`briefings:topics.${key}Hint`)}</Text>
                </View>
                <Switch
                  value={Boolean(prefs[field])}
                  disabled={!enabled || saving}
                  onValueChange={(value) => {
                    trackBriefings("briefing_topic_changed", { topic: key, enabled: value });
                    savePrefs({ [field]: value }).catch(() => undefined);
                  }}
                  trackColor={{ true: colors.accent, false: colors.border }}
                />
              </View>
            ))}
          </Panel>
        </>
      ) : null}

      {/* HISTORY */}
      <Text style={styles.sectionLabel}>{t("briefings:hub.historyTitle")}</Text>
      <Panel>
        {items.length ? (
          <>
            {items.map((item) => (
              <Pressable
                key={item.id}
                accessibilityRole="button"
                accessibilityHint={t("briefings:hub.openHint")}
                style={styles.eventRow}
                onPress={() => {
                  trackBriefings("briefing_opened", { briefing_id: item.id, from: "history" });
                  navigation.navigate("BriefingDetail", { briefingId: item.id, title: item.title });
                }}
              >
                <View style={styles.rowHead}>
                  <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
                  <Text style={styles.rowMeta}>{formatWhen(item.sent_at || item.generated_at)}</Text>
                </View>
                <Text style={styles.muted} numberOfLines={2}>{item.body}</Text>
              </Pressable>
            ))}
            {hasMore ? (
              <Pressable
                accessibilityRole="button"
                disabled={loadingMore}
                style={[styles.loadMore, loadingMore ? styles.disabled : undefined]}
                onPress={() => loadMore().catch(() => undefined)}
              >
                <Text style={styles.loadMoreText}>
                  {loadingMore ? t("briefings:hub.loading") : t("briefings:hub.loadMore")}
                </Text>
              </Pressable>
            ) : null}
          </>
        ) : historyFailed ? (
          <Text style={styles.error}>{t("briefings:hub.loadError")}</Text>
        ) : (
          <Text style={styles.muted}>{t("briefings:hub.empty.body")}</Text>
        )}
      </Panel>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  body: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  choiceCopy: {
    flex: 1,
    gap: 2
  },
  choiceRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    minHeight: 42
  },
  content: {
    gap: 12,
    padding: 18,
    paddingBottom: 34
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  eventRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    paddingVertical: 10
  },
  gap: {
    gap: 6
  },
  groupLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.6,
    marginTop: 10,
    textTransform: "uppercase"
  },
  header: {
    gap: 5
  },
  latest: {
    gap: 5
  },
  loadMore: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 8,
    minHeight: 42
  },
  loadMoreText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  notice: {
    color: colors.muted,
    fontSize: 13,
    fontStyle: "italic",
    lineHeight: 19
  },
  quietRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 42
  },
  quietValue: {
    color: colors.text,
    fontSize: 15,
    fontVariant: ["tabular-nums"],
    fontWeight: "800",
    minWidth: 52,
    textAlign: "center"
  },
  recommended: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800"
  },
  rowHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  rowMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  rowTitle: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 15,
    fontWeight: "800"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  sectionLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.8,
    marginTop: 6,
    textTransform: "uppercase"
  },
  statusLine: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 20
  },
  stepButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 34,
    justifyContent: "center",
    width: 34
  },
  stepper: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  toggleCopy: {
    flex: 1,
    gap: 2,
    paddingRight: 10
  },
  toggleRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 48
  }
}));
