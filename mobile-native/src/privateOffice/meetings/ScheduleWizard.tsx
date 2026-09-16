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
 *
 * **It collects guests before the meeting is created, not after.** Booking and
 * inviting in one request is what stops a meeting existing with nobody on it
 * because the app was closed in between — with the host already told otherwise.
 * The address shape is checked here only so a typo is caught while the user is
 * still looking at it; whether an address is usable, already a member, or a
 * duplicate is the server's ruling, exactly as with the date.
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

type Step = "DATE" | "TIME" | "ZONE" | "DURATION" | "DETAILS" | "GUESTS" | "REVIEW";

export const NEW_MEETING_STEPS: Step[] = [
  "DATE",
  "TIME",
  "ZONE",
  "DURATION",
  "DETAILS",
  "GUESTS",
  "REVIEW"
];

/**
 * Rescheduling skips Guests.
 *
 * The guest list is sent with the create request and nowhere else — a
 * reschedule carries only the fields it can actually change. Offering the step
 * during an edit would show an existing meeting's invitees as an empty list and
 * then discard whatever was typed into it, which is the same lie as a wizard
 * that closes without booking anything.
 */
export const EDIT_STEPS: Step[] = NEW_MEETING_STEPS.filter((name) => name !== "GUESTS");

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

/**
 * One person to invite, as the host typed them.
 *
 * `id` is local to this component — a list key and an edit handle, never sent
 * and never an identity. The server keys an invitee on the normalized address,
 * which is the only thing here that means anything outside this screen.
 */
export type GuestDraft = {
  id: string;
  name: string;
  email: string;
};

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
  /** Sent with the create request; empty when rescheduling. */
  invitees: { name: string; email: string }[];
};

/**
 * The shape check, mirroring the server's `_EMAIL_SHAPE`.
 *
 * Deliberately crude, and deliberately the same crudeness as the server: one
 * `@`, a dot after it, no whitespace. Anything stricter starts refusing valid
 * addresses — and an address this rejects is a typo the user can see, not a
 * judgement about whether the inbox exists.
 */
const EMAIL_SHAPE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;

/**
 * Trim and lowercase, matching `auth_service.normalize_email`.
 *
 * No provider-specific folding: stripping dots or `+tags` is right at Gmail and
 * wrong nearly everywhere else, and a rule that silently merges two genuinely
 * different addresses sends someone else's meeting to the wrong person.
 */
export function normalizeGuestEmail(value: string): string {
  return value.trim().toLowerCase();
}

export function guestEmailLooksValid(value: string): boolean {
  return EMAIL_SHAPE.test(normalizeGuestEmail(value));
}

/**
 * What an existing meeting looks like on the way back into the wizard.
 *
 * Editing reuses the same six steps rather than a second, smaller form: a
 * reschedule is the same decision as a schedule, and a cut-down edit screen is
 * how a meeting ends up with a new time in the old zone.
 */
export type ScheduleSeed = {
  date: CivilDate;
  time: WallClock;
  timezone: string;
  durationMinutes: number;
  title: string;
  agenda: string;
};

export type ScheduleWizardProps = {
  busy: boolean;
  onSubmit: (draft: ScheduleDraft) => void;
  onCancel: () => void;
  /** Absent when scheduling something new. */
  initial?: ScheduleSeed | null;
  /** Overrides the final button's label; defaults to "Schedule". */
  submitLabel?: string;
};

/** Unique enough for a per-intent key; never used as a secret or an id. */
function mintKey(): string {
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function ScheduleWizard({
  busy,
  onSubmit,
  onCancel,
  initial,
  submitLabel
}: ScheduleWizardProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("DATE");
  const [date, setDate] = useState<CivilDate | null>(initial ? initial.date : null);
  const [time, setTime] = useState<WallClock>(initial ? initial.time : { hour: 9, minute: 0 });
  const [zone, setZone] = useState<string>(() => initial?.timezone || deviceTimezone());
  // A seeded duration that is not one of the six chips is carried in the
  // custom field, which is where the user will look for it. A seed of zero is
  // a row from before the column was required, not a choice, so it gets the
  // same default a new meeting gets rather than an unusable "0".
  const [duration, setDuration] = useState(() =>
    initial && DURATION_CHOICES.includes(initial.durationMinutes) ? initial.durationMinutes : 30
  );
  const [customDuration, setCustomDuration] = useState(() =>
    initial &&
    initial.durationMinutes > 0 &&
    !DURATION_CHOICES.includes(initial.durationMinutes)
      ? String(initial.durationMinutes)
      : ""
  );
  const [title, setTitle] = useState(initial ? initial.title : "");
  const [agenda, setAgenda] = useState(initial ? initial.agenda : "");
  const [idempotencyKey, setIdempotencyKey] = useState(mintKey);
  const [guests, setGuests] = useState<GuestDraft[]>([]);
  const [guestName, setGuestName] = useState("");
  const [guestEmail, setGuestEmail] = useState("");
  const [editingGuestId, setEditingGuestId] = useState<string | null>(null);
  const [guestError, setGuestError] = useState<"invalid" | "duplicate" | "">("");

  const STEP_ORDER = initial ? EDIT_STEPS : NEW_MEETING_STEPS;

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
    step === "DETAILS" ||
    // Guests are optional, so this step never blocks on an empty list. It
    // blocks only on text the user has typed and not yet added, which `goNext`
    // resolves rather than discards.
    step === "GUESTS";

  /**
   * Commit the guest currently in the fields.
   *
   * Returns the new list, or `null` if the typed text cannot be added. The
   * caller uses that to decide whether advancing is safe — the wizard must
   * never carry a half-typed guest past this step and silently drop them.
   */
  const commitGuest = useCallback((): GuestDraft[] | null => {
    const email = normalizeGuestEmail(guestEmail);
    const name = guestName.trim();
    if (!email) {
      // A name with no address is not a guest we can reach. The server refuses
      // it too; saying so here means the host finds out while they can fix it.
      setGuestError(name ? "invalid" : "");
      return name ? null : guests;
    }
    if (!guestEmailLooksValid(email)) {
      setGuestError("invalid");
      return null;
    }
    const clash = guests.some(
      (guest) => guest.id !== editingGuestId && normalizeGuestEmail(guest.email) === email
    );
    if (clash) {
      setGuestError("duplicate");
      return null;
    }
    const next = editingGuestId
      ? guests.map((guest) =>
          guest.id === editingGuestId ? { ...guest, name, email } : guest
        )
      : [...guests, { id: mintKey(), name, email }];
    setGuests(next);
    setGuestName("");
    setGuestEmail("");
    setEditingGuestId(null);
    setGuestError("");
    return next;
  }, [editingGuestId, guestEmail, guestName, guests]);

  const editGuest = useCallback((guest: GuestDraft) => {
    setGuestName(guest.name);
    setGuestEmail(guest.email);
    setEditingGuestId(guest.id);
    setGuestError("");
  }, []);

  const removeGuest = useCallback(
    (id: string) => {
      setGuests((current) => current.filter((guest) => guest.id !== id));
      if (editingGuestId === id) {
        setGuestName("");
        setGuestEmail("");
        setEditingGuestId(null);
      }
      setGuestError("");
    },
    [editingGuestId]
  );

  const goNext = useCallback(() => {
    // Pressing Next with a valid address still in the field means "add this
    // one and carry on" — the alternative is a guest the host typed, watched
    // the review step omit, and has no reason to suspect was never invited.
    if (step === "GUESTS" && commitGuest() === null) return;
    setStep((current) => {
      const at = STEP_ORDER.indexOf(current);
      const next = STEP_ORDER[Math.min(at + 1, STEP_ORDER.length - 1)];
      // A new intent starts at Review, so the key is minted on arrival there.
      if (next === "REVIEW" && current !== "REVIEW") setIdempotencyKey(mintKey());
      return next;
    });
  }, [STEP_ORDER, commitGuest, step]);

  const goBack = useCallback(() => {
    setStep((current) => STEP_ORDER[Math.max(STEP_ORDER.indexOf(current) - 1, 0)]);
  }, [STEP_ORDER]);

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
      scheduledStartAt: localStartAt(date, time),
      invitees: guests.map((guest) => ({ name: guest.name, email: guest.email }))
    });
  }, [
    agenda,
    date,
    durationValid,
    guests,
    idempotencyKey,
    onSubmit,
    resolvedDuration,
    time,
    title,
    zone
  ]);

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

      {step === "GUESTS" ? (
        <View style={styles.section}>
          <Text style={styles.sectionHint}>
            {t("premium:privateOffice.meetings.wizard.guestsHint")}
          </Text>

          {guests.length === 0 ? (
            <Text style={styles.sectionHint}>
              {t("premium:privateOffice.meetings.wizard.guestsNone")}
            </Text>
          ) : (
            <View style={styles.guestList}>
              {guests.map((guest) => (
                <View key={guest.id} style={styles.guestRow}>
                  <Pressable
                    style={styles.guestIdentity}
                    onPress={() => editGuest(guest)}
                    accessibilityRole="button"
                    accessibilityLabel={t(
                      "premium:privateOffice.meetings.wizard.editGuest",
                      { name: guest.name || guest.email }
                    )}
                  >
                    {guest.name ? (
                      <Text style={styles.guestName} numberOfLines={1}>
                        {guest.name}
                      </Text>
                    ) : null}
                    <Text style={styles.guestEmail} numberOfLines={1}>
                      {guest.email}
                    </Text>
                  </Pressable>
                  <Pressable
                    style={styles.guestRemove}
                    onPress={() => removeGuest(guest.id)}
                    accessibilityRole="button"
                    accessibilityLabel={t(
                      "premium:privateOffice.meetings.wizard.removeGuest",
                      { name: guest.name || guest.email }
                    )}
                  >
                    <Text style={styles.guestRemoveText}>
                      {t("premium:privateOffice.meetings.wizard.remove")}
                    </Text>
                  </Pressable>
                </View>
              ))}
            </View>
          )}

          <TextInput
            style={styles.input}
            value={guestName}
            onChangeText={setGuestName}
            placeholder={t("premium:privateOffice.meetings.wizard.guestName")}
            placeholderTextColor={colors.muted}
            autoCapitalize="words"
            accessibilityLabel={t("premium:privateOffice.meetings.wizard.guestName")}
          />
          <TextInput
            style={styles.input}
            value={guestEmail}
            onChangeText={(value) => {
              setGuestEmail(value);
              setGuestError("");
            }}
            placeholder={t("premium:privateOffice.meetings.wizard.guestEmail")}
            placeholderTextColor={colors.muted}
            keyboardType="email-address"
            autoCapitalize="none"
            autoCorrect={false}
            onSubmitEditing={() => commitGuest()}
            accessibilityLabel={t("premium:privateOffice.meetings.wizard.guestEmail")}
          />
          {guestError ? (
            <Text style={styles.warnText}>
              {t(
                guestError === "duplicate"
                  ? "premium:privateOffice.meetings.wizard.guestDuplicate"
                  : "premium:privateOffice.meetings.wizard.guestEmailInvalid"
              )}
            </Text>
          ) : null}
          <Pressable
            style={styles.addGuestButton}
            onPress={() => commitGuest()}
            accessibilityRole="button"
            accessibilityLabel={t(
              editingGuestId
                ? "premium:privateOffice.meetings.wizard.updateGuest"
                : "premium:privateOffice.meetings.wizard.addGuest"
            )}
          >
            <Text style={styles.addGuestText}>
              {t(
                editingGuestId
                  ? "premium:privateOffice.meetings.wizard.updateGuest"
                  : "premium:privateOffice.meetings.wizard.addGuest"
              )}
            </Text>
          </Pressable>
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
          {initial ? null : (
            <ReviewRow
              label={t("premium:privateOffice.meetings.wizard.steps.guests")}
              // Naming every guest, not counting them. "3 guests" is exactly
              // the summary that lets a wrong address through unread.
              value={
                guests.length === 0
                  ? t("premium:privateOffice.meetings.wizard.guestsNoneShort")
                  : guests
                      .map((guest) => (guest.name ? `${guest.name} (${guest.email})` : guest.email))
                      .join("\n")
              }
            />
          )}
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
            accessibilityLabel={submitLabel || t("premium:privateOffice.meetings.schedule")}
          >
            {busy ? (
              <ActivityIndicator color={colors.accentStrong} />
            ) : (
              <Text style={styles.primaryButtonText}>
                {submitLabel || t("premium:privateOffice.meetings.schedule")}
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
  // The guest list is one row holding several lines, and a guest clipped out of
  // the review is a guest the host never gets to notice is wrong. The cap
  // scales with the content instead of truncating it.
  const lines = Math.max(2, value.split("\n").length);
  return (
    <View style={styles.reviewRow}>
      <Text style={styles.reviewLabel}>{label}</Text>
      <Text style={styles.reviewValue} numberOfLines={lines}>
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
  guestList: { gap: 6 },
  guestRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 10,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  guestIdentity: { flex: 1, gap: 2 },
  guestName: { color: colors.text, fontSize: 13, fontWeight: "600" },
  guestEmail: { color: colors.muted, fontSize: 12 },
  guestRemove: { paddingHorizontal: 6, paddingVertical: 4 },
  guestRemoveText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  addGuestButton: {
    paddingVertical: 10,
    borderRadius: 10,
    borderColor: colors.border,
    borderWidth: 1,
    alignItems: "center",
    backgroundColor: colors.surfaceRaised
  },
  addGuestText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
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
