/**
 * Private Meetings — what the host sees after a booking succeeds.
 *
 * The wizard used to close on success. That is indistinguishable from a wizard
 * that closed on failure, and for a while it was literally the same picture:
 * the server answered `201` with a complete meeting object over a database that
 * had rolled the row away, and the only thing the host had to go on was a sheet
 * sliding shut. The persistence bug is fixed; this screen is what stops the
 * *reporting* half of it from being reintroduced by someone who only ever sees
 * the happy path.
 *
 * So the rule here is narrow and absolute: **every line is a field the server
 * sent back.** Not one value on this screen is computed from what the host
 * typed into the wizard.
 *
 *  - The time is `scheduled_start_at` rendered in `scheduled_timezone` — the
 *    instant the server resolved, not the wall clock that was submitted. If
 *    those two disagree (a DST boundary, a zone the client guessed wrong), the
 *    host finds out here, while the meeting is still trivially changeable.
 *  - The guests are `invite_result`, which reports what the server *did* with
 *    the addresses it was sent: a member's address is invited as that member,
 *    anything unusable lands in `skipped` with a reason, and both are shown.
 *    Rendering the typed list instead would confirm invitations that were never
 *    sent, which is the original defect wearing a nicer coat.
 *  - The reminders are the rows that exist, not the default ladder. They can be
 *    empty, and an empty list says so rather than reciting three mails nobody
 *    is going to send.
 *
 * Guests are named, never counted. "3 guests invited" is exactly the summary
 * that lets a typo through unread, which is the one failure the host can still
 * fix for free at this moment and can never fix afterwards.
 */

import { useCallback } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { meetingWhenLabel } from "./calendarLabels";
import { MeetingReminder, PrivateMeeting } from "./types";

/** One line in the guest list: something to show, and the reason if refused. */
type GuestLine = { key: string; label: string; detail: string; refused: boolean };

/**
 * The invite result as lines, in the order the server reported them.
 *
 * `invited_members` rather than `invited`: the latter is a list of ids, and a
 * screen holding only ids can say nothing more useful than how many there were.
 * A member with no label left — which happens on a replayed booking, where the
 * invite row stores an address only for guests who have no account — falls back
 * to naming the member id, because the alternative is a blank row that looks
 * like a rendering bug rather than like thin data.
 */
export function guestLines(
  meeting: PrivateMeeting,
  t: (key: string, vars?: Record<string, unknown>) => string
): GuestLine[] {
  const result = meeting.invite_result;
  if (!result) return [];
  const lines: GuestLine[] = [];

  for (const member of result.invited_members || []) {
    lines.push({
      key: `u:${member.user_id}`,
      label:
        member.name ||
        member.email ||
        t("premium:privateOffice.meetings.confirmed.memberFallback", {
          id: member.user_id
        }),
      detail: member.name && member.email ? member.email : "",
      refused: false
    });
  }
  for (const contact of result.invited_contacts || []) {
    lines.push({
      key: `e:${contact.email}`,
      label: contact.name || contact.email,
      detail: contact.name ? contact.email : "",
      refused: false
    });
  }
  for (const skip of result.skipped || []) {
    const who =
      skip.name ||
      skip.email ||
      (skip.user_id
        ? t("premium:privateOffice.meetings.confirmed.memberFallback", {
            id: skip.user_id
          })
        : t("premium:privateOffice.meetings.confirmed.someone"));
    lines.push({
      key: `s:${skip.user_id || ""}:${skip.email || ""}:${skip.reason}`,
      label: who,
      // An unrecognised reason renders as itself rather than as a generic
      // apology: a machine code on screen is ugly, and a wrong explanation of
      // why someone was not invited is worse than an honest untranslated one.
      detail: skipReasonLabel(skip.reason, t),
      refused: true
    });
  }
  return lines;
}

/**
 * A machine reason as a sentence.
 *
 * The code is camel-cased before the lookup because the catalog validator reads
 * a trailing `_many` / `_one` / `_few` as a plural family — `too_many` would be
 * the key `too` missing five of Arabic's six forms. A mechanical transform
 * rather than a lookup table, so a reason added on the server needs one catalog
 * entry and no code change here.
 */
export function skipReasonLabel(
  reason: string,
  t: (key: string, vars?: Record<string, unknown>) => string
): string {
  const name = reason.replace(/_([a-z])/g, (_all, char: string) => char.toUpperCase());
  const key = `premium:privateOffice.meetings.confirmed.skipped.${name}`;
  const text = t(key);
  return text && text !== key ? text : reason;
}

/**
 * One reminder as a time, not as a lead.
 *
 * "24 hours before" would need a pluralized unit in eleven catalogs including
 * Arabic's six categories, and it is the less useful of the two readings: the
 * host wants to know when the mail lands. The zone is the meeting's, so the
 * reminder and the meeting are quoted in the same clock.
 */
function reminderLine(
  reminder: MeetingReminder,
  meeting: PrivateMeeting,
  t: (key: string) => string
): string {
  const when = meetingWhenLabel(reminder.send_at, meeting.scheduled_timezone);
  if (reminder.status === "SKIPPED") {
    return `${when} — ${t("premium:privateOffice.meetings.confirmed.reminderSkipped")}`;
  }
  return when;
}

export function ScheduleConfirmation({
  meeting,
  onDone
}: {
  meeting: PrivateMeeting;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const guests = guestLines(meeting, t);
  // `reminders` absent means the server did not say; empty means it said none.
  // Only the second is a claim, and only the second gets the "none" line.
  const reminders = meeting.reminders;

  const done = useCallback(() => onDone(), [onDone]);

  return (
    <ScrollView
      contentContainerStyle={styles.body}
      accessibilityLabel={t("premium:privateOffice.meetings.confirmed.title")}
    >
      <View style={styles.badge}>
        <Ionicons name="checkmark-circle" size={22} color={colors.accentStrong} />
        <Text style={styles.badgeText}>
          {t("premium:privateOffice.meetings.confirmed.title")}
        </Text>
      </View>

      <Text style={styles.meetingTitle}>
        {meeting.title || t("premium:privateOffice.meetings.untitled")}
      </Text>

      <Row
        label={t("premium:privateOffice.meetings.confirmed.when")}
        value={meetingWhenLabel(meeting.scheduled_start_at, meeting.scheduled_timezone)}
      />
      <Row
        label={t("premium:privateOffice.meetings.confirmed.duration")}
        value={t("premium:privateOffice.meetings.durationMinutes", {
          count: meeting.duration_minutes
        })}
      />

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>
          {t("premium:privateOffice.meetings.confirmed.guests")}
        </Text>
        {guests.length === 0 ? (
          <Text style={styles.hint}>
            {t("premium:privateOffice.meetings.confirmed.guestsNone")}
          </Text>
        ) : (
          guests.map((line) => (
            <View key={line.key} style={styles.guestRow}>
              <Ionicons
                name={line.refused ? "alert-circle-outline" : "mail-outline"}
                size={15}
                color={line.refused ? colors.warning : colors.muted}
              />
              <View style={styles.guestText}>
                <Text
                  style={[styles.guestName, line.refused && styles.guestRefused]}
                  numberOfLines={1}
                >
                  {line.label}
                </Text>
                {line.detail ? (
                  <Text style={styles.hint} numberOfLines={1}>
                    {line.detail}
                  </Text>
                ) : null}
              </View>
            </View>
          ))
        )}
      </View>

      {reminders ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>
            {t("premium:privateOffice.meetings.confirmed.reminders")}
          </Text>
          {reminders.length === 0 ? (
            <Text style={styles.hint}>
              {t("premium:privateOffice.meetings.confirmed.remindersNone")}
            </Text>
          ) : (
            reminders.map((reminder) => (
              <Text
                key={`${reminder.offset_minutes}:${reminder.send_at}`}
                style={styles.hint}
              >
                {reminderLine(reminder, meeting, t)}
              </Text>
            ))
          )}
        </View>
      ) : null}

      {/*
        The identity half. `meeting_code` is host-only and is what someone
        types into "Join with a code"; `public_id` is the canonical id and is
        what a support conversation is about. Both are shown because they are
        not interchangeable and the host cannot be expected to know that.
      */}
      <Row
        label={t("premium:privateOffice.meetings.confirmed.meetingId")}
        value={meeting.public_id}
        selectable
      />
      {meeting.meeting_code ? (
        <Row
          label={t("premium:privateOffice.meetings.confirmed.meetingCode")}
          value={meeting.meeting_code}
          selectable
        />
      ) : null}

      <Pressable
        style={styles.done}
        onPress={done}
        accessibilityRole="button"
        accessibilityLabel={t("premium:privateOffice.meetings.confirmed.done")}
      >
        <Text style={styles.doneText}>
          {t("premium:privateOffice.meetings.confirmed.done")}
        </Text>
      </Pressable>
    </ScrollView>
  );
}

function Row({
  label,
  value,
  selectable
}: {
  label: string;
  value: string;
  selectable?: boolean;
}) {
  return (
    <View style={styles.row}>
      <Text style={styles.sectionLabel}>{label}</Text>
      <Text style={styles.rowValue} selectable={selectable}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, gap: 14 },
  badge: { flexDirection: "row", alignItems: "center", gap: 8 },
  badgeText: { color: colors.accentStrong, fontSize: 15, fontWeight: "800" },
  meetingTitle: { color: colors.text, fontSize: 20, fontWeight: "800" },
  row: { gap: 3 },
  rowValue: { color: colors.text, fontSize: 14, fontWeight: "600" },
  section: {
    gap: 6,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    paddingTop: 12
  },
  sectionLabel: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6
  },
  guestRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  guestText: { flex: 1, gap: 1 },
  guestName: { color: colors.text, fontSize: 14, fontWeight: "600" },
  guestRefused: { color: colors.warning },
  hint: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  done: {
    marginTop: 4,
    paddingVertical: 14,
    borderRadius: 14,
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  doneText: { color: colors.accentStrong, fontSize: 15, fontWeight: "700" }
});
