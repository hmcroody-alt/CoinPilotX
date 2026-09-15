/**
 * Private Meetings — the scheduling wizard.
 *
 * Date → time → timezone → duration → details → review. It replaces three
 * preset chips ("In 15 minutes", "In 1 hour", "Tomorrow 9:00") that could not
 * express a meeting next Tuesday, let alone one in 2035.
 *
 * What this component does NOT do is the interesting part:
 *
 * **It never computes an instant.** It collects a civil date, a wall clock and
 * an IANA zone name and sends those three things. Resolving 09:30 on a date
 * years out needs that zone's DST rules *as they will be then* — including the
 * two dates a year where 09:30 might not exist or might exist twice — and the
 * server's `resolve_schedule` is the only side positioned to be right about
 * that. A client that pinned the instant would disagree with the server the
 * day a government moves a changeover.
 *
 * **It never decides whether a booking is legal.** Past dates are dimmed, not
 * disabled; the review step shows what will be sent and the server answers.
 * A phone running four minutes fast must not be able to refuse a valid
 * meeting, and one running four minutes slow must not be able to authorise an
 * invalid one.
 *
 * **It mints one idempotency key per intent, not per request.** The key is
 * generated when the user reaches Review and survives every retry from there,
 * so a double-tapped Schedule returns the first meeting instead of creating a
 * second. It is regenerated only when the wizard is reopened.
 */

import { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { MeetingCalendar } from "./MeetingCalendar";
import {
  CivilDate,
  DURATION_CHOICES,
  MAX_DURATION_MINUTES,
  MIN_DURATION_MINUTES,
  WallClock,
  deviceTimezone,
  formatWallClock,
  isPastLocally,
  localStartAt
} from "./calendar";
import { longDateLabel, timezoneLabel, timezoneOffsetLabel, wallClockLabel } from "./calendarLabels";

type Step = "DATE" | "TIME" | "ZONE" | "DURATION" | "DETAILS" | "REVIEW";

const STEP_ORDER: Step[] = ["DATE", "TIME", "ZONE", "DURATION", "DETAILS", "REVIEW"];

/** Quarter-hour grid. Anything else is typed in the custom field below it. */
const MINUTE_CHOICES = [0, 15, 30, 45];

/**
 * A short list of zones offered next to the device's own.
 *
 * Not an attempt at the full IANA database — that is 600 entries and a search
 * field, and the overwhelming majority of meetings are scheduled in the zone
 * the phone is already in. These are the anchors people actually name when
 * they mean "their morning, not mine". The device zone is always first and is
 * never duplicated if it appears here.
 */
const COMMON_ZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Lagos",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "UTC"
];

export type ScheduleDraft = {
  date: CivilDate;
  time: WallClock;
  timezone: string;
  durationMinutes: number;
  title: string;
  agenda: string;
  /** Stable across retries of one intent. See the module docstring. */
  idempotencyKey: string;
  /** The naive local datetime the server will resolve. */
  scheduledStartAt: string;
};

export type ScheduleWizardProps = {
  busy: boolean;
  onSubmit: (draft: ScheduleDraft) => void;
  onCancel: () => void;
};

/** Unique enough for a per-intent key; never used as a secret or an id. */
function mintKey(): string {
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function ScheduleWizard({ busy, onSubmit, onCancel }: ScheduleWizardProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("DATE");
  const [date, setDate] = useState<CivilDate | null>(null);
  const [time, setTime] = useState<WallClock>({ hour: 9, minute: 0 });
  const [zone, setZone] = useState<string>(() => deviceTimezone());
  const [duration, setDuration] = useState(30);
  const [customDuration, setCustomDuration] = useState("");
  const [title, setTitle] = useState("");
  const [agenda, setAgenda] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(mintKey);

  const zones = useMemo(() => {
    const device = deviceTimezone();
    const rest = COMMON_ZONES.filter((name) => name !== device);
    return device ? [device, ...rest] : rest;
  }, []);

  const resolvedDuration = useMemo(() => {
    if (!customDuration.trim()) return duration;
    const typed = Number(customDuration.trim());
    if (!Number.isFinite(typed)) return 0;
    return Math.trunc(typed);
  }, [customDuration, duration]);

  const durationValid =
    resolvedDuration >= MIN_DURATION_MINUTES && resolvedDuration <= MAX_DURATION_MINUTES;

  const index = STEP_ORDER.indexOf(step);
  const canAdvance =
    (step === "DATE" && date !== null) ||
    (step === "TIME" && true) ||
    (step === "ZONE" && true) ||
    (step === "DURATION" && durationValid) ||
    step === "DETAILS";

  const goNext = useCallback(() => {
    setStep((current) => {
      const at = STEP_ORDER.indexOf(current);
      const next = STEP_ORDER[Math.min(at + 1, STEP_ORDER.length - 1)];
      // A new intent starts at Review, so the key is minted on arrival there.
      if (next === "REVIEW" && current !== "REVIEW") setIdempotencyKey(mintKey());
      return next;
    });
  }, []);

  const goBack = useCallback(() => {
    setStep((current) => STEP_ORDER[Math.max(STEP_ORDER.indexOf(current) - 1, 0)]);
  }, []);

  const submit = useCallback(() => {
    if (!date || !durationValid) return;
    onSubmit({
      date,
      time,
      timezone: zone,
      durationMinutes: resolvedDuration,
      title: title.trim(),
      agenda: agenda.trim(),
      idempotencyKey,
      scheduledStartAt: localStartAt(date, time)
    });
  }, [agenda, date, durationValid, idempotencyKey, onSubmit, resolvedDuration, time, title, zone]);

  // A hint, not a gate. The server rules on this in the meeting's own zone.
  const looksPast = date ? isPastLocally(date, time) : false;

  return (
    <View style={styles.root}>
      <View style={styles.steps}>
        {STEP_ORDER.map((name, at) => (
          <View
            key={name}
            style={[styles.stepPip, at <= index && styles.stepPipDone]}
            accessible={false}
          />
        ))}
      </View>
      <Text style={styles.stepTitle}>
        {t(`premium:privateOffice.meetings.wizard.steps.${step.toLowerCase()}`)}
      </Text>

      {step === "DATE" ? (
        <MeetingCalendar
          selected={date}
          onSelect={setDate}
          timezone={zone}
        />
      ) : null}

      {step === "TIME" ? (
        <View style={styles.section}>
          <Text style={styles.sectionHint}>
            {t("premium:privateOffice.meetings.wizard.timeHint")}
          </Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={styles.chipRow}>
              {Array.from({ length: 24 }, (_, hour) => hour).map((hour) => (
                <Pressable
                  key={hour}
                  style={[styles.chip, time.hour === hour && styles.chipActive]}
                  onPress={() => setTime({ ...time, hour })}
                  accessibilityRole="button"
                  accessibilityState={{ selected: time.hour === hour }}
                  accessibilityLabel={wallClockLabel({ hour, minute: time.minute })}
                >
                  <Text style={[styles.chipText, time.hour === hour && styles.chipTextActive]}>
                    {wallClockLabel({ hour, minute: 0 })}
                  </Text>
                </Pressable>
              ))}
            </View>
          </ScrollView>
          <View style={styles.chipRow}>
            {MINUTE_CHOICES.map((minute) => (
              <Pressable
                key={minute}
                style={[styles.chip, time.minute === minute && styles.chipActive]}
                onPress={() => setTime({ ...time, minute })}
                accessibilityRole="button"
                accessibilityState={{ selected: time.minute === minute }}
                accessibilityLabel={wallClockLabel({ hour: time.hour, minute })}
              >
                <Text style={[styles.chipText, time.minute === minute && styles.chipTextActive]}>
                  {formatWallClock({ hour: time.hour, minute })}
                </Text>
              </Pressable>
            ))}
          </View>
          {looksPast ? (
            <Text style={styles.warnText}>
              {t("premium:privateOffice.meetings.wizard.looksPast")}
            </Text>
          ) : null}
        </View>
      ) : null}

      {step === "ZONE" ? (
        <View style={styles.section}>
          <Text style={styles.sectionHint}>
            {t("premium:privateOffice.meetings.wizard.zoneHint")}
          </Text>
          {zones.length === 0 ? (
            <Text style={styles.warnText}>
              {t("premium:privateOffice.meetings.wizard.zoneUnknown")}
            </Text>
          ) : null}
          <View style={styles.zoneList}>
            {zones.map((name) => (
              <Pressable
                key={name}
                style={[styles.zoneRow, zone === name && styles.zoneRowActive]}
                onPress={() => setZone(name)}
                accessibilityRole="button"
                accessibilityState={{ selected: zone === name }}
                accessibilityLabel={timezoneLabel(name)}
              >
                <Text style={[styles.zoneName, zone === name && styles.zoneNameActive]}>
                  {timezoneLabel(name)}
                </Text>
                <Text style={styles.zoneOffset}>{timezoneOffsetLabel(name)}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      ) : null}

      {step === "DURATION" ? (
        <View style={styles.section}>
          <View style={styles.chipRow}>
            {DURATION_CHOICES.map((minutes) => (
              <Pressable
                key={minutes}
                style={[
                  styles.chip,
                  !customDuration.trim() && duration === minutes && styles.chipActive
                ]}
                onPress={() => {
                  setDuration(minutes);
                  setCustomDuration("");
                }}
                accessibilityRole="button"
                accessibilityState={{
                  selected: !customDuration.trim() && duration === minutes
                }}
                accessibilityLabel={t("premium:privateOffice.meetings.durationMinutes", {
                  count: minutes
                })}
              >
                <Text
                  style={[
                    styles.chipText,
                    !customDuration.trim() && duration === minutes && styles.chipTextActive
                  ]}
                >
                  {t("premium:privateOffice.meetings.durationMinutes", { count: minutes })}
                </Text>
              </Pressable>
            ))}
          </View>
          <TextInput
            style={styles.input}
            value={customDuration}
            onChangeText={setCustomDuration}
            keyboardType="number-pad"
            placeholder={t("premium:privateOffice.meetings.wizard.customDuration")}
            placeholderTextColor={colors.muted}
            accessibilityLabel={t("premium:privateOffice.meetings.wizard.customDuration")}
          />
          {customDuration.trim() && !durationValid ? (
            <Text style={styles.warnText}>
              {t("premium:privateOffice.meetings.wizard.durationRange", {
                min: MIN_DURATION_MINUTES,
                max: MAX_DURATION_MINUTES
              })}
            </Text>
          ) : null}
        </View>
      ) : null}

      {step === "DETAILS" ? (
        <View style={styles.section}>
          <TextInput
            style={styles.input}
            value={title}
            onChangeText={setTitle}
            placeholder={t("premium:privateOffice.meetings.scheduleTitleField")}
            placeholderTextColor={colors.muted}
            accessibilityLabel={t("premium:privateOffice.meetings.scheduleTitleField")}
          />
          <TextInput
            style={[styles.input, styles.inputMultiline]}
            value={agenda}
            onChangeText={setAgenda}
            multiline
            placeholder={t("premium:privateOffice.meetings.wizard.agendaField")}
            placeholderTextColor={colors.muted}
            accessibilityLabel={t("premium:privateOffice.meetings.wizard.agendaField")}
          />
          <Text style={styles.sectionHint}>
            {t("premium:privateOffice.meetings.wizard.inviteLater")}
          </Text>
        </View>
      ) : null}

      {step === "REVIEW" && date ? (
        <View style={styles.section}>
          <ReviewRow
            label={t("premium:privateOffice.meetings.wizard.steps.date")}
            value={longDateLabel(date)}
          />
          <ReviewRow
            label={t("premium:privateOffice.meetings.wizard.steps.time")}
            value={wallClockLabel(time)}
          />
          <ReviewRow
            label={t("premium:privateOffice.meetings.wizard.steps.zone")}
            value={
              zone
                ? `${timezoneLabel(zone)} ${timezoneOffsetLabel(zone)}`.trim()
                : t("premium:privateOffice.meetings.wizard.zoneUnknown")
            }
          />
          <ReviewRow
            label={t("premium:privateOffice.meetings.wizard.steps.duration")}
            value={t("premium:privateOffice.meetings.durationMinutes", {
              count: resolvedDuration
            })}
          />
          <ReviewRow
            label={t("premium:privateOffice.meetings.scheduleTitleField")}
            value={title.trim() || t("premium:privateOffice.meetings.untitled")}
          />
          {looksPast ? (
            <Text style={styles.warnText}>
              {t("premium:privateOffice.meetings.wizard.looksPast")}
            </Text>
          ) : null}
        </View>
      ) : null}

      <View style={styles.actions}>
        <Pressable
          style={styles.ghostButton}
          onPress={index === 0 ? onCancel : goBack}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={
            index === 0
              ? t("premium:privateOffice.meetings.cancel")
              : t("premium:privateOffice.meetings.wizard.back")
          }
        >
          <Text style={styles.ghostButtonText}>
            {index === 0
              ? t("premium:privateOffice.meetings.cancel")
              : t("premium:privateOffice.meetings.wizard.back")}
          </Text>
        </Pressable>
        {step === "REVIEW" ? (
          <Pressable
            style={[styles.primaryButton, busy && styles.buttonDisabled]}
            onPress={submit}
            disabled={busy || !date || !durationValid}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.schedule")}
          >
            {busy ? (
              <ActivityIndicator color={colors.accentStrong} />
            ) : (
              <Text style={styles.primaryButtonText}>
                {t("premium:privateOffice.meetings.schedule")}
              </Text>
            )}
          </Pressable>
        ) : (
          <Pressable
            style={[styles.primaryButton, !canAdvance && styles.buttonDisabled]}
            onPress={goNext}
            disabled={!canAdvance}
            accessibilityRole="button"
            accessibilityState={{ disabled: !canAdvance }}
            accessibilityLabel={t("premium:privateOffice.meetings.wizard.next")}
          >
            <Text style={styles.primaryButtonText}>
              {t("premium:privateOffice.meetings.wizard.next")}
            </Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.reviewRow}>
      <Text style={styles.reviewLabel}>{label}</Text>
      <Text style={styles.reviewValue} numberOfLines={2}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: 12 },
  steps: { flexDirection: "row", gap: 6, justifyContent: "center" },
  stepPip: { width: 18, height: 3, borderRadius: 2, backgroundColor: colors.border },
  stepPipDone: { backgroundColor: colors.accent },
  stepTitle: { color: colors.text, fontSize: 14, fontWeight: "700" },
  section: { gap: 10 },
  sectionHint: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  warnText: { color: colors.warning, fontSize: 12, lineHeight: 18 },
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
  zoneList: { gap: 4 },
  zoneRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  zoneRowActive: { borderColor: colors.accent },
  zoneName: { color: colors.text, fontSize: 13, fontWeight: "600" },
  zoneNameActive: { color: colors.accentStrong },
  zoneOffset: { color: colors.muted, fontSize: 11 },
  input: {
    color: colors.text,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14
  },
  inputMultiline: { minHeight: 76, textAlignVertical: "top" },
  reviewRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  reviewLabel: { color: colors.muted, fontSize: 12, width: 92 },
  reviewValue: { color: colors.text, fontSize: 13, fontWeight: "600", flex: 1 },
  actions: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  primaryButton: {
    paddingHorizontal: 20,
    paddingVertical: 11,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    minWidth: 110
  },
  primaryButtonText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  buttonDisabled: { opacity: 0.45 },
  ghostButton: { paddingHorizontal: 12, paddingVertical: 11 },
  ghostButtonText: { color: colors.muted, fontSize: 13, fontWeight: "600" }
});
