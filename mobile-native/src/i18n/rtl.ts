import { I18nManager, Platform, TextStyle, ViewStyle } from "react-native";

import { TextDirection, isRtlLanguage } from "./locales";

/**
 * Right-to-left support.
 *
 * React Native mirrors flex layout automatically once `I18nManager.isRTL` is
 * true, but that flag is read by the native layout engine at startup and
 * flipping it needs a reload to take full effect. PulseSoc therefore runs a
 * two-track strategy:
 *
 *   1. A JS-level direction that flips instantly. Every helper here derives
 *      from it, so text alignment, chevrons, gesture directions, list ordering
 *      and animation offsets all mirror the moment the user picks Arabic — no
 *      reload, no lost navigation state.
 *   2. The native `I18nManager` flag, set alongside it. It takes effect on the
 *      next launch and brings the remaining platform-owned chrome (native
 *      scroll indicators, text input caret placement, native back gestures)
 *      into alignment.
 *
 * The result is that switching language is immediately usable, and the one
 * genuinely restart-bound piece is surfaced to the user as an optional prompt
 * rather than a forced relaunch.
 */

let jsDirection: TextDirection = I18nManager.isRTL ? "rtl" : "ltr";
const directionListeners = new Set<(direction: TextDirection) => void>();

export function getLayoutDirection(): TextDirection {
  return jsDirection;
}

export function isLayoutRtl(): boolean {
  return jsDirection === "rtl";
}

/** True when the native flag disagrees with the JS direction we are rendering. */
export function isNativeDirectionStale(): boolean {
  return I18nManager.isRTL !== (jsDirection === "rtl");
}

export function subscribeToDirection(listener: (direction: TextDirection) => void): () => void {
  directionListeners.add(listener);
  return () => {
    directionListeners.delete(listener);
  };
}

/**
 * Applies a language's direction.
 *
 * Returns whether a restart is needed to finish mirroring natively — the
 * caller uses this to decide whether to offer the "restart to finish" prompt.
 */
export function applyDirectionForLocale(locale: string): { direction: TextDirection; restartRequired: boolean } {
  const direction: TextDirection = isRtlLanguage(locale) ? "rtl" : "ltr";
  const changed = direction !== jsDirection;
  jsDirection = direction;

  if (changed) {
    for (const listener of directionListeners) {
      try {
        listener(direction);
      } catch {
        // A misbehaving subscriber must not block the language change.
      }
    }
  }

  let restartRequired = false;
  try {
    I18nManager.allowRTL(true);
    if (I18nManager.isRTL !== (direction === "rtl")) {
      I18nManager.forceRTL(direction === "rtl");
      // Web re-renders from the JS direction immediately; the native platforms
      // need a reload before their own layout engine picks the flag up.
      restartRequired = Platform.OS !== "web";
    }
  } catch {
    // I18nManager is unavailable under some test renderers; the JS direction
    // still drives every style helper below, so rendering stays correct.
  }
  return { direction, restartRequired };
}

/* ------------------------------------------------------------------ *
 * Directional primitives
 * ------------------------------------------------------------------ */

/** `"left"` in LTR, `"right"` in RTL — the leading edge of the reading order. */
export function startEdge(direction: TextDirection = jsDirection): "left" | "right" {
  return direction === "rtl" ? "right" : "left";
}

/** The trailing edge of the reading order. */
export function endEdge(direction: TextDirection = jsDirection): "left" | "right" {
  return direction === "rtl" ? "left" : "right";
}

/**
 * `1` in LTR, `-1` in RTL. Multiply any horizontal offset, translation or
 * swipe delta by this so animations travel in the reading direction.
 */
export function directionSign(direction: TextDirection = jsDirection): 1 | -1 {
  return direction === "rtl" ? -1 : 1;
}

/**
 * Mirrors a horizontally-directional icon name. Chevrons, arrows and
 * back/forward glyphs must point the other way in RTL; symmetric icons
 * (search, heart, plus) pass through untouched.
 */
export function mirrorIconName(name: string, direction: TextDirection = jsDirection): string {
  if (direction !== "rtl") return name;
  const swaps: Record<string, string> = {
    "chevron-back": "chevron-forward",
    "chevron-forward": "chevron-back",
    "chevron-left": "chevron-right",
    "chevron-right": "chevron-left",
    "arrow-back": "arrow-forward",
    "arrow-forward": "arrow-back",
    "arrow-left": "arrow-right",
    "arrow-right": "arrow-left",
    "arrow-back-circle": "arrow-forward-circle",
    "arrow-forward-circle": "arrow-back-circle",
    "caret-back": "caret-forward",
    "caret-forward": "caret-back",
    "play-back": "play-forward",
    "play-forward": "play-back",
    "log-in": "log-out",
    "return-down-back": "return-down-forward",
    "return-down-forward": "return-down-back"
  };
  return swaps[name] ?? name;
}

/**
 * Flips a glyph that has no mirrored counterpart in the icon set. Applied as a
 * transform so a single asset serves both directions.
 */
export function mirrorTransform(direction: TextDirection = jsDirection): ViewStyle {
  return direction === "rtl" ? { transform: [{ scaleX: -1 }] } : {};
}

/* ------------------------------------------------------------------ *
 * Style helpers
 * ------------------------------------------------------------------ */

/**
 * Text alignment that follows the reading order.
 *
 * React Native's `textAlign: "left"` is absolute, not logical, so Arabic text
 * left-aligns unless we resolve it ourselves. Pass `"start"` (the default) for
 * body copy and `"center"` where a design genuinely centers.
 */
export function textAlign(
  align: "start" | "end" | "center" = "start",
  direction: TextDirection = jsDirection
): TextStyle {
  if (align === "center") return { textAlign: "center" };
  const start = startEdge(direction);
  const end = endEdge(direction);
  return { textAlign: align === "start" ? start : end };
}

/** Row direction that mirrors, for cases where automatic mirroring is off. */
export function row(direction: TextDirection = jsDirection): ViewStyle {
  return { flexDirection: direction === "rtl" ? "row-reverse" : "row" };
}

/** Asymmetric horizontal padding expressed in logical (start/end) terms. */
export function paddingHorizontal(
  start: number,
  end: number,
  direction: TextDirection = jsDirection
): ViewStyle {
  return direction === "rtl" ? { paddingLeft: end, paddingRight: start } : { paddingLeft: start, paddingRight: end };
}

/** Asymmetric horizontal margin expressed in logical (start/end) terms. */
export function marginHorizontal(
  start: number,
  end: number,
  direction: TextDirection = jsDirection
): ViewStyle {
  return direction === "rtl" ? { marginLeft: end, marginRight: start } : { marginLeft: start, marginRight: end };
}

/** Absolute positioning against the leading edge. */
export function positionStart(offset: number, direction: TextDirection = jsDirection): ViewStyle {
  return direction === "rtl" ? { right: offset } : { left: offset };
}

/** Absolute positioning against the trailing edge. */
export function positionEnd(offset: number, direction: TextDirection = jsDirection): ViewStyle {
  return direction === "rtl" ? { left: offset } : { right: offset };
}

/** Asymmetric corner radii that follow the reading order (e.g. chat bubbles). */
export function borderRadiusLogical(
  radii: { topStart?: number; topEnd?: number; bottomStart?: number; bottomEnd?: number },
  direction: TextDirection = jsDirection
): ViewStyle {
  const rtl = direction === "rtl";
  return {
    borderTopLeftRadius: rtl ? radii.topEnd : radii.topStart,
    borderTopRightRadius: rtl ? radii.topStart : radii.topEnd,
    borderBottomLeftRadius: rtl ? radii.bottomEnd : radii.bottomStart,
    borderBottomRightRadius: rtl ? radii.bottomStart : radii.bottomEnd
  };
}

/**
 * The `writingDirection` style RN passes to the platform text engine. Setting
 * it explicitly keeps mixed-script strings (an Arabic sentence containing a
 * Latin @handle or a URL) from reordering incorrectly under the bidi algorithm.
 */
export function writingDirectionStyle(direction: TextDirection = jsDirection): TextStyle {
  return { writingDirection: direction };
}

/**
 * Wraps a bidirectional-sensitive value so it renders correctly inside a
 * sentence of the opposite direction. Used for usernames, hashtags, URLs and
 * numbers embedded in Arabic copy.
 */
export function isolateBidi(value: string, direction: TextDirection = jsDirection): string {
  if (!value) return value;
  // U+200F RIGHT-TO-LEFT MARK / U+200E LEFT-TO-RIGHT MARK.
  const mark = direction === "rtl" ? "\u200F" : "\u200E";
  return `${mark}${value}${mark}`;
}

/** Test seam: forces a direction without touching the native flag. */
export function __setDirectionForTests(direction: TextDirection): void {
  jsDirection = direction;
}
