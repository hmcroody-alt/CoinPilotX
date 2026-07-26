import { useMemo } from "react";

import {
  formatAccessibleDateTime,
  formatCount,
  formatCurrencyAmount,
  formatDate,
  formatDateTime,
  formatDay,
  formatDistance,
  formatDuration,
  formatFileSize,
  formatList,
  formatMeasurement,
  formatNumber,
  formatOrdinal,
  formatPercent,
  formatRange,
  formatRelative,
  formatRelativeLong,
  formatScheduled,
  formatTemperature,
  formatTime,
  formatWeight,
  monthNames,
  timeZoneLabel,
  usesImperialUnits,
  weekdayNames
} from "./format";
import { useI18n } from "./I18nContext";
import {
  borderRadiusLogical,
  directionSign,
  endEdge,
  isolateBidi,
  marginHorizontal,
  mirrorIconName,
  mirrorTransform,
  paddingHorizontal,
  positionEnd,
  positionStart,
  row,
  startEdge,
  textAlign,
  writingDirectionStyle
} from "./rtl";

/**
 * React bindings for the formatting and RTL layers.
 *
 * Both modules underneath are plain functions that read module-level state, so
 * they are usable anywhere — including outside React. These hooks exist for one
 * reason: they depend on the provider's `revision`, so a component that
 * formats a date or mirrors a chevron re-renders the moment the language
 * changes, without every screen having to subscribe to anything by hand.
 */

/* ------------------------------------------------------------------ *
 * Formatting
 * ------------------------------------------------------------------ */

export interface Formatters {
  number: typeof formatNumber;
  count: typeof formatCount;
  percent: typeof formatPercent;
  currency: typeof formatCurrencyAmount;
  ordinal: typeof formatOrdinal;
  measurement: typeof formatMeasurement;
  distance: typeof formatDistance;
  weight: typeof formatWeight;
  temperature: typeof formatTemperature;
  fileSize: typeof formatFileSize;
  list: typeof formatList;
  date: typeof formatDate;
  time: typeof formatTime;
  dateTime: typeof formatDateTime;
  day: typeof formatDay;
  range: typeof formatRange;
  accessibleDateTime: typeof formatAccessibleDateTime;
  relative: typeof formatRelative;
  relativeLong: typeof formatRelativeLong;
  scheduled: typeof formatScheduled;
  duration: typeof formatDuration;
  timeZoneLabel: typeof timeZoneLabel;
  weekdayNames: typeof weekdayNames;
  monthNames: typeof monthNames;
  /** True when the user's region expects miles, pounds and Fahrenheit. */
  imperial: boolean;
  /** The BCP-47 tag every formatter above defaults to. */
  locale: string;
}

/**
 * Every locale-aware formatter, rebound whenever the language changes.
 *
 *   const fmt = useFormatters();
 *   <Text>{fmt.relative(post.created_at)}</Text>
 *   <Text>{fmt.count(post.like_count)}</Text>
 */
export function useFormatters(): Formatters {
  const { intlLocale, revision } = useI18n();
  return useMemo<Formatters>(
    () => ({
      number: formatNumber,
      count: formatCount,
      percent: formatPercent,
      currency: formatCurrencyAmount,
      ordinal: formatOrdinal,
      measurement: formatMeasurement,
      distance: formatDistance,
      weight: formatWeight,
      temperature: formatTemperature,
      fileSize: formatFileSize,
      list: formatList,
      date: formatDate,
      time: formatTime,
      dateTime: formatDateTime,
      day: formatDay,
      range: formatRange,
      accessibleDateTime: formatAccessibleDateTime,
      relative: formatRelative,
      relativeLong: formatRelativeLong,
      scheduled: formatScheduled,
      duration: formatDuration,
      timeZoneLabel,
      weekdayNames,
      monthNames,
      imperial: usesImperialUnits(intlLocale),
      locale: intlLocale
    }),
    // `revision` forces a fresh object after a language change so memoized
    // children that captured the old formatters recompute their output.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [intlLocale, revision]
  );
}

/* ------------------------------------------------------------------ *
 * Direction
 * ------------------------------------------------------------------ */

export interface DirectionHelpers {
  direction: "ltr" | "rtl";
  isRTL: boolean;
  /** `"left"` in LTR, `"right"` in RTL. */
  start: "left" | "right";
  end: "left" | "right";
  /** Multiply horizontal offsets and swipe deltas by this. */
  sign: 1 | -1;
  icon: (name: string) => string;
  mirror: () => ReturnType<typeof mirrorTransform>;
  align: (align?: "start" | "end" | "center") => ReturnType<typeof textAlign>;
  row: () => ReturnType<typeof row>;
  paddingX: (start: number, end: number) => ReturnType<typeof paddingHorizontal>;
  marginX: (start: number, end: number) => ReturnType<typeof marginHorizontal>;
  positionStart: (offset: number) => ReturnType<typeof positionStart>;
  positionEnd: (offset: number) => ReturnType<typeof positionEnd>;
  radius: (radii: Parameters<typeof borderRadiusLogical>[0]) => ReturnType<typeof borderRadiusLogical>;
  writingDirection: () => ReturnType<typeof writingDirectionStyle>;
  /** Wraps a handle, URL or number so it reads correctly inside RTL copy. */
  isolate: (value: string) => string;
}

/**
 * Direction-aware style helpers bound to the active language.
 *
 *   const dir = useDirection();
 *   <View style={dir.row()}>
 *     <Icon name={dir.icon("chevron-forward")} />
 *     <Text style={dir.align()}>{title}</Text>
 *   </View>
 *
 * Every helper takes the direction from the provider rather than reading the
 * module default, so a component renders correctly on the very first frame
 * after a switch to Arabic — no reload, no stale mirroring.
 */
export function useDirection(): DirectionHelpers {
  const { direction, isRTL } = useI18n();
  return useMemo<DirectionHelpers>(
    () => ({
      direction,
      isRTL,
      start: startEdge(direction),
      end: endEdge(direction),
      sign: directionSign(direction),
      icon: (name: string) => mirrorIconName(name, direction),
      mirror: () => mirrorTransform(direction),
      align: (align: "start" | "end" | "center" = "start") => textAlign(align, direction),
      row: () => row(direction),
      paddingX: (start: number, end: number) => paddingHorizontal(start, end, direction),
      marginX: (start: number, end: number) => marginHorizontal(start, end, direction),
      positionStart: (offset: number) => positionStart(offset, direction),
      positionEnd: (offset: number) => positionEnd(offset, direction),
      radius: (radii: Parameters<typeof borderRadiusLogical>[0]) => borderRadiusLogical(radii, direction),
      writingDirection: () => writingDirectionStyle(direction),
      isolate: (value: string) => isolateBidi(value, direction)
    }),
    [direction, isRTL]
  );
}
