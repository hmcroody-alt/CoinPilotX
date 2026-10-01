/**
 * One event in the Upcoming / Past / Drafts lists. The row is the same object in
 * two dresses:
 *
 *   • upcoming / draft → DateTile + title + meta + a status LED. The LED reads a
 *     derived EventStatus: green Published (+ interest), violet Promoted (+ reach
 *     from Advertising), grey Draft (+ the real blocking reason). Nothing here is
 *     invented — a promoted row with no campaign figure shows "Promoted" with no
 *     number.
 *   • past → muted DateTile + title + the derived results metrics (reached ·
 *     follows · attributed-sales for livestreams, attended · checked-in ·
 *     attributed-sales for in-person). Attributed sales render "—" when withheld.
 *
 * The row never computes status or results; it only lays out what the derivation
 * handed it. Tapping the row is the parent's job via onPress.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import type { EventResults, EventStatus, HostedEvent } from "../../api/eventsManager";
import { DateTile } from "./DateTile";

export function EventRow({
  event,
  month,
  day,
  meta,
  status,
  results,
  onPress
}: {
  event: HostedEvent;
  month: string;
  day: string;
  meta?: string;
  /** Present for upcoming/draft rows. */
  status?: EventStatus;
  /** Present for past rows. */
  results?: EventResults;
  onPress?: (event: HostedEvent) => void;
}) {
  const past = Boolean(results);
  return (
    <Pressable
      style={styles.row}
      accessibilityRole="button"
      accessibilityLabel={rowA11y(event, status, results)}
      onPress={() => onPress?.(event)}
    >
      <DateTile month={month} day={day} past={past} />

      <View style={styles.body}>
        <Text style={styles.title} numberOfLines={1}>
          {event.title}
        </Text>
        {meta ? (
          <Text style={styles.meta} numberOfLines={1}>
            {meta}
          </Text>
        ) : null}

        {status ? <StatusLED status={status} /> : null}
        {results ? <ResultsLine results={results} /> : null}
      </View>
    </Pressable>
  );
}

function StatusLED({ status }: { status: EventStatus }) {
  const color =
    status.kind === "published"
      ? eventsLight.status.published
      : status.kind === "promoted"
        ? eventsLight.status.promoted
        : eventsLight.status.draft;
  return (
    <View style={styles.ledRow}>
      <View style={[styles.led, { backgroundColor: color }]} />
      <Text style={[styles.ledText, { color }]} numberOfLines={1}>
        {status.line}
      </Text>
    </View>
  );
}

function ResultsLine({ results }: { results: EventResults }) {
  return (
    <View style={styles.metricsRow} accessibilityLabel={results.a11yLabel}>
      {results.metrics.map((m, i) => (
        <View key={m.label} style={styles.metric}>
          <Text style={styles.metricValue}>{m.value}</Text>
          <Text style={styles.metricLabel} numberOfLines={1}>
            {m.label}
          </Text>
          {results.salesWithheld && m.label === "Attributed sales" ? (
            <Text style={styles.withheld}>no attribution model</Text>
          ) : null}
          {i < results.metrics.length - 1 ? <View style={styles.metricDivider} /> : null}
        </View>
      ))}
    </View>
  );
}

function rowA11y(event: HostedEvent, status?: EventStatus, results?: EventResults): string {
  if (results) return `${event.title}. ${results.a11yLabel}`;
  if (status) return `${event.title}. ${status.line}`;
  return event.title;
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    backgroundColor: eventsLight.bg.card,
    borderRadius: eventsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.hairline,
    padding: eventsLight.space.card
  },
  body: { flex: 1, gap: 4 },
  title: { fontSize: 15, fontWeight: "800", color: eventsLight.text.primary },
  meta: { fontSize: 12, color: eventsLight.text.muted },
  ledRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 2 },
  led: { width: 8, height: 8, borderRadius: 4 },
  ledText: { fontSize: 12, fontWeight: "700" },
  metricsRow: { flexDirection: "row", alignItems: "flex-start", marginTop: 4, flexWrap: "wrap" },
  metric: { flexDirection: "row", alignItems: "center", marginRight: 8 },
  metricValue: { fontSize: 14, fontWeight: "800", color: eventsLight.text.primary, marginRight: 4 },
  metricLabel: { fontSize: 11, color: eventsLight.text.muted },
  withheld: { fontSize: 10, fontStyle: "italic", color: eventsLight.text.muted, marginLeft: 4 },
  metricDivider: {
    width: StyleSheet.hairlineWidth,
    height: 14,
    backgroundColor: eventsLight.border.hairline,
    marginLeft: 8
  }
});
