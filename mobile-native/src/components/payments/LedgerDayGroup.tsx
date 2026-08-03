/**
 * A day's worth of ledger rows under one date header.
 *
 * Grouping is presentation, not accounting. This component never sums a day,
 * never shows a running balance, and never reorders within a day — it renders
 * the rows the server sent, in the order the server sent them, under a heading
 * derived from their timestamps. A per-day subtotal would be exactly the
 * client-side balance arithmetic the mission forbids, and it would be wrong the
 * moment a row's status changed without its amount changing.
 *
 * `groupLedgerByDay` below is the only date logic on the screen. It groups on
 * the *local* calendar day, because "Today" has to mean the seller's today, and
 * it preserves server order rather than re-sorting: the server already sorted
 * by id descending, which is stable across rows that share a timestamp. Sorting
 * again on `created_at` here would reintroduce the exact ambiguity the keyset
 * cursor was chosen to avoid, since a webhook batch writes many rows within the
 * same second.
 *
 * Rows with no usable timestamp are not dropped and not backdated into today.
 * They collect under "Date unavailable", which is true and keeps the money
 * visible.
 */

import { Animated, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";
import type { LedgerEntry } from "../../api/paymentsHub";
import { LedgerRow } from "./LedgerRow";

export type LedgerDay = {
  /** Stable key: the ISO date, or "unknown". */
  key: string;
  heading: string;
  entries: LedgerEntry[];
};

export type LedgerDayGroupProps = {
  day: LedgerDay;
  /**
   * Supplies the per-row entrance driver. The screen owns the judgement of
   * which ids are new — only it knows what the list looked like a moment ago —
   * so this component neither tracks nor guesses that.
   */
  entranceFor?: (entry: LedgerEntry) => Animated.Value | null;
  onPressEntry?: (entry: LedgerEntry) => void;
};

export function LedgerDayGroup({ day, entranceFor, onPressEntry }: LedgerDayGroupProps) {
  return (
    <View>
      <View style={styles.header} accessibilityRole="header">
        <Text style={styles.heading} allowFontScaling>
          {day.heading}
        </Text>
      </View>
      {day.entries.map((entry) => (
        <LedgerRow
          key={entry.id}
          entry={entry}
          entrance={entranceFor ? entranceFor(entry) : null}
          onPress={onPressEntry}
        />
      ))}
    </View>
  );
}

/**
 * Group entries into local calendar days, preserving server order.
 *
 * Exported separately from the component so the screen can group once per page
 * rather than per render, and so it can be reasoned about (and tested) without
 * mounting anything.
 */
export function groupLedgerByDay(entries: readonly LedgerEntry[], now = new Date()): LedgerDay[] {
  const days: LedgerDay[] = [];
  const index = new Map<string, LedgerDay>();

  for (const entry of entries) {
    const key = dayKey(entry.created_at);
    let day = index.get(key);
    if (!day) {
      day = { key, heading: dayHeading(key, now), entries: [] };
      index.set(key, day);
      days.push(day);
    }
    day.entries.push(entry);
  }
  return days;
}

function dayKey(createdAt: string | null): string {
  if (!createdAt) return "unknown";
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) return "unknown";
  // Local components, not toISOString — the latter is UTC and would file an
  // evening sale under tomorrow for anyone east of Greenwich.
  const y = parsed.getFullYear();
  const m = String(parsed.getMonth() + 1).padStart(2, "0");
  const d = String(parsed.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function dayHeading(key: string, now: Date): string {
  if (key === "unknown") return "Date unavailable";
  const [y, m, d] = key.split("-").map(Number);
  const date = new Date(y, (m || 1) - 1, d || 1);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.round((today.getTime() - date.getTime()) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "long",
      day: "numeric",
      // The year is shown only when it is not the current one — a ledger of
      // recent activity does not need "2026" on every heading, but a row from
      // two years ago absolutely does.
      year: date.getFullYear() === now.getFullYear() ? undefined : "numeric"
    }).format(date);
  } catch {
    return key;
  }
}

const styles = StyleSheet.create({
  header: {
    backgroundColor: paymentsLight.ledger.dayHeaderBg,
    paddingHorizontal: paymentsLight.space.gutter,
    paddingTop: 16,
    paddingBottom: 6
  },
  heading: {
    color: paymentsLight.ledger.dayHeader,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.5,
    textTransform: "uppercase"
  }
});
