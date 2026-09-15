/**
 * Private Meetings — the month grid.
 *
 * Six rows of seven, a month/year header that can jump decades, and a dot under
 * any day the server says has a meeting on it. It owns the visible month and
 * the fetch for that month's window; the selected date belongs to the caller,
 * because the wizard needs it after this component is gone.
 *
 * Three decisions worth stating:
 *
 * **The grid never shrinks.** Six weeks always, even for a February that fits
 * in four rows plus change. A grid that changes height as you swipe months is
 * a grid that moves the button under it while your thumb is travelling.
 *
 * **Indicator dots come from the server's `days` map, not from re-bucketing
 * the meeting list here.** The server grouped those meetings into local days
 * using the zone we asked for. Doing it again client-side would be a second
 * timezone implementation to disagree with the first.
 *
 * **A failed load is not an empty month.** `loadState` distinguishes them, so
 * a month with no dots is either "no meetings" or "we could not ask" and never
 * silently the second dressed as the first.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { fetchCalendar } from "./api";
import {
  CalendarCell,
  CivilDate,
  addMonths,
  compareDates,
  dayKey,
  deviceTimezone,
  gridWindow,
  monthGrid,
  todayIn,
  yearChoices
} from "./calendar";
import { longDateLabel, monthLabel, monthName, weekdayLabels } from "./calendarLabels";
import { MeetingCalendarEntry } from "./types";

type LoadState = "IDLE" | "LOADING" | "READY" | "FAILED";

export type MeetingCalendarProps = {
  selected: CivilDate | null;
  onSelect: (date: CivilDate) => void;
  /** The viewer's zone, so the server buckets meetings into the days shown. */
  timezone?: string;
  /** Bumped by the caller after a schedule/cancel to force a re-read. */
  reloadToken?: number;
  /** Meetings on the selected day, handed up so the caller can list them. */
  onDayMeetings?: (day: string, meetings: MeetingCalendarEntry[]) => void;
};

export function MeetingCalendar({
  selected,
  onSelect,
  timezone,
  reloadToken = 0,
  onDayMeetings
}: MeetingCalendarProps) {
  const { t } = useTranslation();
  const today = useMemo(() => todayIn(), []);
  const [visible, setVisible] = useState<{ year: number; month: number }>(() =>
    selected
      ? { year: selected.year, month: selected.month }
      : { year: today.year, month: today.month }
  );
  const [days, setDays] = useState<Record<string, number>>({});
  const [entries, setEntries] = useState<MeetingCalendarEntry[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("IDLE");
  const [yearPickerOpen, setYearPickerOpen] = useState(false);
  const [monthPickerOpen, setMonthPickerOpen] = useState(false);

  const zone = timezone || deviceTimezone();
  const cells = useMemo(
    () => monthGrid(visible.year, visible.month),
    [visible.year, visible.month]
  );
  const weekdays = useMemo(() => weekdayLabels(), []);

  useEffect(() => {
    let cancelled = false;
    const { start, end } = gridWindow(cells);
    setLoadState("LOADING");
    fetchCalendar({ start, end, timezone: zone })
      .then((window) => {
        if (cancelled) return;
        setDays(window.days || {});
        setEntries(window.meetings || []);
        setLoadState("READY");
      })
      .catch(() => {
        if (cancelled) return;
        // Deliberately not clearing `days`: last month's dots are stale, but
        // blanking the grid would say "no meetings" in a language the user
        // cannot tell apart from the truth. FAILED renders its own banner.
        setLoadState("FAILED");
      });
    return () => {
      cancelled = true;
    };
  }, [cells, zone, reloadToken]);

  const selectedKey = selected ? dayKey(selected) : "";

  useEffect(() => {
    if (!onDayMeetings || !selectedKey) return;
    onDayMeetings(
      selectedKey,
      entries.filter((entry) => entry.local_day === selectedKey)
    );
  }, [entries, onDayMeetings, selectedKey]);

  const step = useCallback((delta: number) => {
    setVisible((current) => {
      const moved = addMonths({ year: current.year, month: current.month, day: 1 }, delta);
      return { year: moved.year, month: moved.month };
    });
  }, []);

  const jumpToYear = useCallback((year: number) => {
    setVisible((current) => ({ year, month: current.month }));
    setYearPickerOpen(false);
  }, []);

  const jumpToMonth = useCallback((month: number) => {
    setVisible((current) => ({ year: current.year, month }));
    setMonthPickerOpen(false);
  }, []);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Pressable
          style={styles.arrow}
          onPress={() => step(-1)}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.calendar.previousMonth")}
        >
          <Ionicons name="chevron-back" size={18} color={colors.accentStrong} />
        </Pressable>
        <View style={styles.headerLabels}>
          <Pressable
            onPress={() => setMonthPickerOpen(true)}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.calendar.pickMonth")}
          >
            <Text style={styles.monthText}>{monthLabel(visible.year, visible.month)}</Text>
          </Pressable>
          <Pressable
            onPress={() => setYearPickerOpen(true)}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.calendar.pickYear")}
          >
            <Text style={styles.jumpText}>
              {t("premium:privateOffice.meetings.calendar.jumpToYear")}
            </Text>
          </Pressable>
        </View>
        <Pressable
          style={styles.arrow}
          onPress={() => step(1)}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.calendar.nextMonth")}
        >
          <Ionicons name="chevron-forward" size={18} color={colors.accentStrong} />
        </Pressable>
      </View>

      <View style={styles.weekRow}>
        {weekdays.map((label, index) => (
          <Text key={index} style={styles.weekday} numberOfLines={1}>
            {label}
          </Text>
        ))}
      </View>

      <View style={styles.grid}>
        {cells.map((cell) => (
          <DayCell
            key={cell.key}
            cell={cell}
            today={today}
            selected={selectedKey === cell.key}
            count={days[cell.key] || 0}
            onPress={() => onSelect(cell.date)}
          />
        ))}
      </View>

      <View style={styles.footer}>
        {loadState === "LOADING" ? (
          <ActivityIndicator color={colors.accent} />
        ) : loadState === "FAILED" ? (
          <Text style={styles.failedText}>
            {t("premium:privateOffice.meetings.calendar.loadFailed")}
          </Text>
        ) : (
          <Pressable
            onPress={() => {
              setVisible({ year: today.year, month: today.month });
              onSelect(today);
            }}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.calendar.today")}
          >
            <Text style={styles.todayText}>
              {t("premium:privateOffice.meetings.calendar.today")}
            </Text>
          </Pressable>
        )}
      </View>

      <PickerSheet
        open={monthPickerOpen}
        title={t("premium:privateOffice.meetings.calendar.pickMonth")}
        onClose={() => setMonthPickerOpen(false)}
        options={[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((month) => ({
          value: month,
          label: monthName(month),
          active: month === visible.month
        }))}
        onPick={jumpToMonth}
      />
      <PickerSheet
        open={yearPickerOpen}
        title={t("premium:privateOffice.meetings.calendar.pickYear")}
        onClose={() => setYearPickerOpen(false)}
        options={yearChoices().map((year) => ({
          value: year,
          label: String(year),
          active: year === visible.year
        }))}
        onPick={jumpToYear}
      />
    </View>
  );
}

/**
 * One day.
 *
 * Borrowed neighbours are dimmed but fully pressable. Greying out the 1st of
 * next month and then refusing the tap is the behaviour that makes people
 * swipe twice; every calendar worth copying lets you select it in place.
 *
 * Past days are dimmed too, and for the same reason are *not* disabled here:
 * the server decides what is bookable, in the meeting's own zone and with a
 * tolerance for a phone whose clock is four minutes fast. A day that is past
 * locally may still be selectable for a meeting in a zone ahead of this one.
 */
function DayCell({
  cell,
  today,
  selected,
  count,
  onPress
}: {
  cell: CalendarCell;
  today: CivilDate;
  selected: boolean;
  count: number;
  onPress: () => void;
}) {
  const { t } = useTranslation();
  const isToday = compareDates(cell.date, today) === 0;
  const isPast = compareDates(cell.date, today) < 0;
  return (
    <Pressable
      style={[styles.cell, selected && styles.cellSelected]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={
        count > 0
          ? t("premium:privateOffice.meetings.calendar.dayWithMeetings", {
              day: longDateLabel(cell.date),
              count
            })
          : longDateLabel(cell.date)
      }
    >
      <Text
        style={[
          styles.cellText,
          !cell.inMonth && styles.cellTextMuted,
          isPast && styles.cellTextMuted,
          isToday && styles.cellTextToday,
          selected && styles.cellTextSelected
        ]}
      >
        {cell.date.day}
      </Text>
      <View style={[styles.dot, count > 0 && styles.dotOn]} />
    </Pressable>
  );
}

/** A plain scrolling list of choices. The year list is long by design (§the
 *  point of the picker is 2045 without five hundred swipes), so it scrolls. */
function PickerSheet<T extends number>({
  open,
  title,
  options,
  onPick,
  onClose
}: {
  open: boolean;
  title: string;
  options: { value: T; label: string; active: boolean }[];
  onPick: (value: T) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityRole="button"
        accessibilityLabel={t("premium:privateOffice.meetings.calendar.close")}>
        <Pressable style={styles.sheet} onPress={() => undefined}>
          <Text style={styles.sheetTitle}>{title}</Text>
          <ScrollView style={styles.sheetScroll}>
            {options.map((option) => (
              <Pressable
                key={option.value}
                style={[styles.option, option.active && styles.optionActive]}
                onPress={() => onPick(option.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: option.active }}
                accessibilityLabel={option.label}
              >
                <Text style={[styles.optionText, option.active && styles.optionTextActive]}>
                  {option.label}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { gap: 8 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  headerLabels: { alignItems: "center", gap: 2 },
  arrow: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  monthText: { color: colors.text, fontSize: 15, fontWeight: "700" },
  jumpText: { color: colors.accentStrong, fontSize: 11, fontWeight: "600" },
  weekRow: { flexDirection: "row" },
  weekday: {
    flex: 1,
    textAlign: "center",
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600"
  },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  cell: {
    // 100/7 to the sixth place: a rounded 14.28% leaves a visible ragged right
    // edge on wide screens, and 14.29% overflows to six columns on narrow ones.
    width: "14.2857%",
    aspectRatio: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
    borderRadius: 10
  },
  cellSelected: { backgroundColor: colors.surfaceRaised, borderColor: colors.accent, borderWidth: 1 },
  cellText: { color: colors.text, fontSize: 13, fontWeight: "600" },
  cellTextMuted: { color: colors.muted, opacity: 0.55 },
  cellTextToday: { color: colors.accentStrong },
  cellTextSelected: { color: colors.accentStrong, fontWeight: "800" },
  dot: { width: 4, height: 4, borderRadius: 2, backgroundColor: "transparent" },
  dotOn: { backgroundColor: colors.accent },
  footer: { minHeight: 22, alignItems: "center", justifyContent: "center" },
  todayText: { color: colors.accentStrong, fontSize: 12, fontWeight: "600" },
  failedText: { color: colors.warning, fontSize: 12, textAlign: "center" },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  sheet: {
    width: "100%",
    maxHeight: "70%",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    gap: 8
  },
  sheetTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  sheetScroll: { flexGrow: 0 },
  option: { paddingVertical: 12, paddingHorizontal: 10, borderRadius: 10 },
  optionActive: { backgroundColor: colors.surfaceRaised },
  optionText: { color: colors.text, fontSize: 14 },
  optionTextActive: { color: colors.accentStrong, fontWeight: "700" }
});
