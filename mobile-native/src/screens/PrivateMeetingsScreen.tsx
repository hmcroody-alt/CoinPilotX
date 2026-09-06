/**
 * Private Meetings — home. Instant meeting, schedule, join by code, and the
 * three server buckets (live / upcoming / recent).
 *
 * The server is the only authority on what exists and what the viewer may do
 * (mission §4): every row here renders a projection field, and every action is
 * a meetings route whose refusal is shown as itself. The screen never guesses
 * — a meeting with no `me` row offers nothing but "Join", and host-only
 * affordances (start, cancel) appear only when the projection says this
 * viewer is a moderator.
 *
 * Media note: this screen never touches Agora or the call session store.
 * Entering a meeting hands off to `meetingSession` (enterMeeting /
 * enterMeetingWithProjection) and navigates to the room screen, which renders
 * whatever phase that store reaches.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ReactNode, useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import {
  FeatureEmptyPanel,
  FeatureLoadingPanel,
  FeatureRefusalPanel,
  FeatureRefusalState
} from "../privateOffice/FeatureStatePanels";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import {
  cancelMeeting,
  createInstantMeeting,
  listMeetings,
  scheduleMeeting,
  startMeeting
} from "../privateOffice/meetings/api";
import { enterMeeting, enterMeetingWithProjection } from "../privateOffice/meetings/meetingSession";
import { MeetingBuckets, MeetingRefusal, MODERATOR_ROLES, PrivateMeeting } from "../privateOffice/meetings/types";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateMeetings">;

export function PrivateMeetingsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateMeetingsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

/**
 * Refusal → the shared panel vocabulary. `office_locked` is not rendered as a
 * panel at all: the local grant is dropped so the enclosing gate shows the
 * actual door instead of a banner pretending to be one.
 */
function refusalPanelState(refusal: MeetingRefusal): FeatureRefusalState | "LOCKED" {
  switch (refusal.code) {
    case "office_locked":
      return "LOCKED";
    case "feature_disabled":
      return "FEATURE_DISABLED";
    case "forbidden":
      return "NOT_ENTITLED";
    case "unavailable":
      return "UNAVAILABLE";
    default:
      return "ERROR";
  }
}

/** Schedule presets — honest fixed choices instead of a broken date field. */
const PRESET_MINUTES = 15;
const PRESET_HOURS = 1;
const PRESET_TOMORROW_HOUR = 9;

/**
 * Label numbers are interpolated from the SAME constants that compute the
 * start time (premium copy ships no literal digits — premiumCopy.test.ts),
 * so a chip can never promise a time the preset does not schedule.
 */
const PRESET_LABEL_ARGS: Record<
  "in15m" | "in1h" | "tomorrowMorning",
  Record<string, unknown>
> = {
  in15m: { minutes: PRESET_MINUTES },
  in1h: { hours: PRESET_HOURS },
  tomorrowMorning: { time: `${PRESET_TOMORROW_HOUR}:00` }
};

function presetStartAt(preset: "in15m" | "in1h" | "tomorrowMorning"): string {
  const now = new Date();
  if (preset === "in15m") {
    return new Date(now.getTime() + PRESET_MINUTES * 60000).toISOString();
  }
  if (preset === "in1h") {
    return new Date(now.getTime() + PRESET_HOURS * 3600000).toISOString();
  }
  const tomorrow = new Date(now.getTime() + 24 * 3600000);
  tomorrow.setHours(PRESET_TOMORROW_HOUR, 0, 0, 0);
  return tomorrow.toISOString();
}

function whenLabel(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

function PrivateMeetingsBody({ navigation }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [buckets, setBuckets] = useState<MeetingBuckets | null>(null);
  const [refusal, setRefusal] = useState<FeatureRefusalState | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleTitle, setScheduleTitle] = useState("");
  const [schedulePreset, setSchedulePreset] = useState<"in15m" | "in1h" | "tomorrowMorning">("in1h");
  const [scheduleDuration, setScheduleDuration] = useState(30);

  const load = useCallback(async () => {
    try {
      const next = await listMeetings();
      setBuckets(next);
      setRefusal(null);
    } catch (error) {
      if (error instanceof MeetingRefusal) {
        const state = refusalPanelState(error);
        if (state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        setRefusal(state);
        return;
      }
      setRefusal("UNAVAILABLE");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const openRoom = useCallback(
    (ref: string, title: string) => {
      navigation.navigate("PrivateMeetingRoom", { ref, title });
    },
    [navigation]
  );

  const startInstant = useCallback(async () => {
    setBusy("instant");
    try {
      const meeting = await createInstantMeeting({});
      await enterMeetingWithProjection(meeting);
      openRoom(meeting.public_id, meeting.title);
    } catch (error) {
      Alert.alert(
        t("premium:privateOffice.meetings.instantFailed"),
        refusalMessage(error, t)
      );
    } finally {
      setBusy("");
    }
  }, [openRoom, t]);

  const joinByCode = useCallback(async () => {
    const code = joinCode.trim();
    if (!code) return;
    setBusy("join");
    try {
      const session = await enterMeeting(code);
      setJoinCode("");
      openRoom(session.meetingRef || code, session.meeting?.title || "");
    } catch (error) {
      Alert.alert(
        t("premium:privateOffice.meetings.joinFailed"),
        refusalMessage(error, t)
      );
    } finally {
      setBusy("");
    }
  }, [joinCode, openRoom, t]);

  const submitSchedule = useCallback(async () => {
    setBusy("schedule");
    try {
      await scheduleMeeting({
        title: scheduleTitle.trim(),
        scheduledStartAt: presetStartAt(schedulePreset),
        durationMinutes: scheduleDuration
      });
      setScheduleOpen(false);
      setScheduleTitle("");
      await load();
    } catch (error) {
      Alert.alert(
        t("premium:privateOffice.meetings.scheduleFailed"),
        refusalMessage(error, t)
      );
    } finally {
      setBusy("");
    }
  }, [load, schedulePreset, scheduleDuration, scheduleTitle, t]);

  const startScheduled = useCallback(
    async (meeting: PrivateMeeting) => {
      setBusy(`start:${meeting.public_id}`);
      try {
        const started = await startMeeting(meeting.public_id);
        await enterMeetingWithProjection(started);
        openRoom(started.public_id, started.title);
      } catch (error) {
        Alert.alert(
          t("premium:privateOffice.meetings.startFailed"),
          refusalMessage(error, t)
        );
      } finally {
        setBusy("");
      }
    },
    [openRoom, t]
  );

  const cancelScheduled = useCallback(
    async (meeting: PrivateMeeting) => {
      setBusy(`cancel:${meeting.public_id}`);
      try {
        await cancelMeeting(meeting.public_id);
        await load();
      } catch (error) {
        Alert.alert(
          t("premium:privateOffice.meetings.cancelFailed"),
          refusalMessage(error, t)
        );
      } finally {
        setBusy("");
      }
    },
    [load, t]
  );

  const joinLive = useCallback(
    async (meeting: PrivateMeeting) => {
      setBusy(`join:${meeting.public_id}`);
      try {
        const session = await enterMeeting(meeting.public_id);
        openRoom(session.meetingRef || meeting.public_id, meeting.title);
      } catch (error) {
        Alert.alert(
          t("premium:privateOffice.meetings.joinFailed"),
          refusalMessage(error, t)
        );
      } finally {
        setBusy("");
      }
    },
    [openRoom, t]
  );

  const isEmpty =
    buckets !== null &&
    buckets.live.length === 0 &&
    buckets.upcoming.length === 0 &&
    buckets.recent.length === 0;

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>
          {t("premium:privateOffice.features.privateMeetings.label")}
        </Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.meetings.subtitle")}</Text>
      </View>

      {refusal ? <FeatureRefusalPanel state={refusal} onRetry={load} /> : null}
      {!refusal && buckets === null ? <FeatureLoadingPanel /> : null}

      {!refusal && buckets !== null ? (
        <>
          <Pressable
            style={styles.primary}
            onPress={startInstant}
            disabled={Boolean(busy)}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.instant")}
          >
            {busy === "instant" ? (
              <ActivityIndicator color={colors.accentStrong} />
            ) : (
              <Ionicons name="videocam-outline" size={18} color={colors.accentStrong} />
            )}
            <Text style={styles.primaryText}>
              {t(
                busy === "instant"
                  ? "premium:privateOffice.meetings.starting"
                  : "premium:privateOffice.meetings.instant"
              )}
            </Text>
          </Pressable>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>{t("premium:privateOffice.meetings.joinTitle")}</Text>
            <View style={styles.joinRow}>
              <TextInput
                style={styles.input}
                value={joinCode}
                onChangeText={setJoinCode}
                placeholder={t("premium:privateOffice.meetings.joinPlaceholder")}
                placeholderTextColor={colors.muted}
                autoCapitalize="none"
                autoCorrect={false}
                accessibilityLabel={t("premium:privateOffice.meetings.joinTitle")}
              />
              <Pressable
                style={[styles.smallButton, !joinCode.trim() && styles.smallButtonDisabled]}
                onPress={joinByCode}
                disabled={!joinCode.trim() || Boolean(busy)}
                accessibilityRole="button"
                accessibilityLabel={t("premium:privateOffice.meetings.join")}
              >
                {busy === "join" ? (
                  <ActivityIndicator color={colors.accentStrong} />
                ) : (
                  <Text style={styles.smallButtonText}>
                    {t("premium:privateOffice.meetings.join")}
                  </Text>
                )}
              </Pressable>
            </View>
          </View>

          <View style={styles.card}>
            <Pressable
              style={styles.cardHead}
              onPress={() => setScheduleOpen(!scheduleOpen)}
              accessibilityRole="button"
              accessibilityLabel={t("premium:privateOffice.meetings.scheduleTitle")}
            >
              <Text style={styles.cardTitle}>
                {t("premium:privateOffice.meetings.scheduleTitle")}
              </Text>
              <Ionicons
                name={scheduleOpen ? "chevron-up" : "chevron-down"}
                size={16}
                color={colors.muted}
              />
            </Pressable>
            {scheduleOpen ? (
              <View style={styles.scheduleForm}>
                <TextInput
                  style={styles.input}
                  value={scheduleTitle}
                  onChangeText={setScheduleTitle}
                  placeholder={t("premium:privateOffice.meetings.scheduleTitleField")}
                  placeholderTextColor={colors.muted}
                  accessibilityLabel={t("premium:privateOffice.meetings.scheduleTitleField")}
                />
                <View style={styles.chipRow}>
                  {(["in15m", "in1h", "tomorrowMorning"] as const).map((preset) => (
                    <Pressable
                      key={preset}
                      style={[styles.chip, schedulePreset === preset && styles.chipActive]}
                      onPress={() => setSchedulePreset(preset)}
                      accessibilityRole="button"
                      accessibilityState={{ selected: schedulePreset === preset }}
                      accessibilityLabel={t(
                        `premium:privateOffice.meetings.presets.${preset}`,
                        PRESET_LABEL_ARGS[preset]
                      )}
                    >
                      <Text
                        style={[styles.chipText, schedulePreset === preset && styles.chipTextActive]}
                      >
                        {t(
                          `premium:privateOffice.meetings.presets.${preset}`,
                          PRESET_LABEL_ARGS[preset]
                        )}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                <View style={styles.chipRow}>
                  {[30, 60].map((minutes) => (
                    <Pressable
                      key={minutes}
                      style={[styles.chip, scheduleDuration === minutes && styles.chipActive]}
                      onPress={() => setScheduleDuration(minutes)}
                      accessibilityRole="button"
                      accessibilityState={{ selected: scheduleDuration === minutes }}
                      accessibilityLabel={t("premium:privateOffice.meetings.durationMinutes", {
                        count: minutes
                      })}
                    >
                      <Text
                        style={[
                          styles.chipText,
                          scheduleDuration === minutes && styles.chipTextActive
                        ]}
                      >
                        {t("premium:privateOffice.meetings.durationMinutes", { count: minutes })}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                <Pressable
                  style={styles.smallButton}
                  onPress={submitSchedule}
                  disabled={Boolean(busy)}
                  accessibilityRole="button"
                  accessibilityLabel={t("premium:privateOffice.meetings.schedule")}
                >
                  {busy === "schedule" ? (
                    <ActivityIndicator color={colors.accentStrong} />
                  ) : (
                    <Text style={styles.smallButtonText}>
                      {t("premium:privateOffice.meetings.schedule")}
                    </Text>
                  )}
                </Pressable>
              </View>
            ) : null}
          </View>

          {isEmpty ? (
            <FeatureEmptyPanel
              title={t("premium:privateOffice.meetings.empty.title")}
              body={t("premium:privateOffice.meetings.empty.body")}
            />
          ) : null}

          {buckets.live.length ? (
            <MeetingBucket
              title={t("premium:privateOffice.meetings.buckets.live")}
              meetings={buckets.live}
              busy={busy}
              renderActions={(meeting) => (
                <Pressable
                  style={styles.smallButton}
                  onPress={() => joinLive(meeting)}
                  disabled={Boolean(busy)}
                  accessibilityRole="button"
                  accessibilityLabel={t("premium:privateOffice.meetings.join")}
                >
                  {busy === `join:${meeting.public_id}` ? (
                    <ActivityIndicator color={colors.accentStrong} />
                  ) : (
                    <Text style={styles.smallButtonText}>
                      {t("premium:privateOffice.meetings.join")}
                    </Text>
                  )}
                </Pressable>
              )}
            />
          ) : null}

          {buckets.upcoming.length ? (
            <MeetingBucket
              title={t("premium:privateOffice.meetings.buckets.upcoming")}
              meetings={buckets.upcoming}
              busy={busy}
              renderActions={(meeting) =>
                meeting.me && MODERATOR_ROLES.has(meeting.me.role) ? (
                  <View style={styles.rowActions}>
                    <Pressable
                      style={styles.smallButton}
                      onPress={() => startScheduled(meeting)}
                      disabled={Boolean(busy)}
                      accessibilityRole="button"
                      accessibilityLabel={t("premium:privateOffice.meetings.start")}
                    >
                      {busy === `start:${meeting.public_id}` ? (
                        <ActivityIndicator color={colors.accentStrong} />
                      ) : (
                        <Text style={styles.smallButtonText}>
                          {t("premium:privateOffice.meetings.start")}
                        </Text>
                      )}
                    </Pressable>
                    <Pressable
                      style={styles.ghostButton}
                      onPress={() => cancelScheduled(meeting)}
                      disabled={Boolean(busy)}
                      accessibilityRole="button"
                      accessibilityLabel={t("premium:privateOffice.meetings.cancel")}
                    >
                      <Text style={styles.ghostButtonText}>
                        {t("premium:privateOffice.meetings.cancel")}
                      </Text>
                    </Pressable>
                  </View>
                ) : null
              }
            />
          ) : null}

          {buckets.recent.length ? (
            <MeetingBucket
              title={t("premium:privateOffice.meetings.buckets.recent")}
              meetings={buckets.recent}
              busy={busy}
              renderActions={() => null}
            />
          ) : null}
        </>
      ) : null}
    </ScrollView>
  );
}

function refusalMessage(error: unknown, t: (key: string) => string): string {
  if (error instanceof MeetingRefusal) {
    const key = `premium:privateOffice.meetings.refusals.${error.code}`;
    const text = t(key);
    // i18n returns the key itself when it is missing; fall back to generic.
    if (text && text !== key) return text;
  }
  return t("premium:privateOffice.feature.error.body");
}

function MeetingBucket({
  title,
  meetings,
  renderActions
}: {
  title: string;
  meetings: PrivateMeeting[];
  busy: string;
  renderActions: (meeting: PrivateMeeting) => ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {meetings.map((meeting) => (
        <View key={meeting.public_id} style={styles.meetingRow}>
          <View style={styles.meetingInfo}>
            <Text style={styles.meetingTitle} numberOfLines={1}>
              {meeting.title || t("premium:privateOffice.meetings.untitled")}
            </Text>
            <Text style={styles.meetingHint} numberOfLines={1}>
              {meeting.status === "LIVE"
                ? t("premium:privateOffice.meetings.liveNow")
                : whenLabel(meeting.scheduled_start_at || meeting.ended_at || meeting.started_at)}
            </Text>
          </View>
          {renderActions(meeting)}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 14 },
  header: { gap: 4 },
  title: { color: colors.text, fontSize: 22, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  primary: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    paddingVertical: 14
  },
  primaryText: { color: colors.accentStrong, fontSize: 15, fontWeight: "700" },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    gap: 10
  },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  joinRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: {
    flex: 1,
    color: colors.text,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14
  },
  smallButton: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center"
  },
  smallButtonDisabled: { opacity: 0.5 },
  smallButtonText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  ghostButton: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center"
  },
  ghostButtonText: { color: colors.muted, fontSize: 13, fontWeight: "600" },
  scheduleForm: { gap: 10 },
  chipRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    borderColor: colors.border,
    borderWidth: 1,
    backgroundColor: colors.surfaceRaised
  },
  chipActive: { borderColor: colors.accent },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  chipTextActive: { color: colors.accentStrong },
  meetingRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  meetingInfo: { flex: 1, gap: 2 },
  meetingTitle: { color: colors.text, fontSize: 14, fontWeight: "600" },
  meetingHint: { color: colors.muted, fontSize: 12 },
  rowActions: { flexDirection: "row", alignItems: "center", gap: 6 }
});
