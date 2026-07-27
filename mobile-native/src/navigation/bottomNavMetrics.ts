/**
 * Geometry of the floating bottom-navigation dock.
 *
 * These numbers used to live in two places that had no way of knowing about
 * each other: the dock's own `StyleSheet` in `GlobalNavigation.tsx`, and a
 * standalone `BOTTOM_NAV_CONTENT_CLEARANCE = 92` in `BottomNavVisibility.tsx`
 * that every scrollable surface added to its bottom padding. They disagreed.
 * The dock is `paddingTop (10) + panel (minHeight 106)` tall above its own
 * safe-area padding — 116pt — so a surface reserving 92pt left the last ~24pt
 * of its content permanently underneath the dock. That is the "content hidden
 * behind the tab bar" bug, and it was invisible to review because neither
 * file's number looked wrong on its own.
 *
 * Deriving the clearance from the same constants the dock lays itself out with
 * means the two can no longer drift: change the panel height and the padding
 * every screen reserves changes with it.
 */

/** Space between the top of the dock shell and the panel it contains. */
export const BOTTOM_NAV_DOCK_PADDING_TOP = 10;

/**
 * Resting height of the dock's pill panel. The tallest child is `bottomItem`
 * at `minHeight: 72` plus 10pt panel padding top and bottom (92pt), so this
 * `minHeight` is what actually decides the panel's height, not its contents.
 * The oversized Create button does not add to it — it is pulled upward with a
 * negative margin and overhangs the panel rather than stretching it.
 */
export const BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT = 106;

/** Padding the pill panel draws on every side of its row of items. */
export const BOTTOM_NAV_DOCK_PANEL_PADDING = 10;

/**
 * The Create button is taller than its siblings and is pulled upward so that it
 * overhangs the pill rather than stretching it. These are the two numbers that
 * decide by how much.
 */
export const BOTTOM_NAV_CREATE_MARGIN_TOP = -32;
export const BOTTOM_NAV_CREATE_MIN_HEIGHT = 98;

/**
 * How far the Create button's top edge sits above the *shell's* top edge.
 *
 * The panel centres its children on the cross axis, and Yoga centres the
 * *margin* box — the button's border box then starts a further `MARGIN_TOP`
 * above that. Working outward: the panel's content box is
 * `PANEL_MIN_HEIGHT - 2 * PANEL_PADDING` tall; the button's margin box is
 * `MIN_HEIGHT + MARGIN_TOP` (the margin is negative, so this is shorter than
 * the button itself); centring puts that margin box halfway down the content
 * box; the border box starts `MARGIN_TOP` above where the margin box does.
 * Subtracting the shell's own top padding converts the result from
 * "above the panel" to "above the shell". Currently 2.
 *
 * Derived rather than written down because a literal here is a number nobody
 * recomputes when the Create button changes — which is the exact failure this
 * module exists to prevent. Without it the clearance below tracks the pill but
 * not the one control that sticks out above the pill.
 */
export const BOTTOM_NAV_CREATE_OVERHANG = Math.max(
  0,
  -(
    BOTTOM_NAV_DOCK_PANEL_PADDING +
    (BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT -
      2 * BOTTOM_NAV_DOCK_PANEL_PADDING -
      (BOTTOM_NAV_CREATE_MIN_HEIGHT + BOTTOM_NAV_CREATE_MARGIN_TOP)) /
      2 +
    BOTTOM_NAV_CREATE_MARGIN_TOP
  ) - BOTTOM_NAV_DOCK_PADDING_TOP
);

/**
 * Breathing room between the last row of content and the dock, so a scrolled
 * list ends just clear of the pill instead of flush against it.
 */
export const BOTTOM_NAV_CONTENT_GAP = 12;

/**
 * Vertical space a scrollable surface must reserve so its final content clears
 * the dock. Callers add this to their own `Math.max(insets.bottom, 12)` — the
 * dock draws its safe-area padding itself, so this figure deliberately excludes
 * it rather than baking in one device's home-indicator inset.
 */
export const BOTTOM_NAV_CONTENT_CLEARANCE =
  BOTTOM_NAV_DOCK_PADDING_TOP +
  BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT +
  BOTTOM_NAV_CREATE_OVERHANG +
  BOTTOM_NAV_CONTENT_GAP;

/**
 * Bottom padding for a scrollable surface that is *not* under the dock — a
 * pushed stack screen, or a tab whose dock is disabled. Still non-zero: content
 * should not run into the home indicator either.
 */
export const BOTTOM_NAV_UNDOCKED_PADDING = 24;
