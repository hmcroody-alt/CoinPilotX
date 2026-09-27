/**
 * How large the global header's action buttons may be at a given screen width.
 *
 * ## The defect this exists to prevent
 *
 * Home's header is a fixed-width brand wordmark flanked by a leading button and
 * a trailing cluster of actions, and every one of those is a *fixed* size in
 * `GlobalNavigation`'s stylesheet. The title block between them is `flex: 1`,
 * so it absorbs whatever is left over — and `LivingPulseSocWordmark` cannot
 * absorb anything: its underline is `width: 120` and its letters are
 * `flexShrink: 0`. Below 120pt of leftover the wordmark does not compress, it
 * *overflows*, symmetrically, straight underneath the action buttons.
 *
 * With three trailing controls (search, activity, avatar) there was 139pt of
 * slack at 375pt and nobody had to think about it. Adding a fourth — Marketplace
 * — costs 52pt and takes the leftover to 87pt, which is 33pt less than the
 * wordmark occupies. The collision is not subtle and it is not only on the small
 * phones: at 393pt (iPhone 15/16) the leftover is 105pt and the wordmark still
 * overlaps. Only Pro Max widths fit four full-size actions.
 *
 * So the size is computed from the width rather than written down. Nothing about
 * the header's layout is re-expressed here: the constants below are the same
 * numbers the stylesheet lays out with, and the wordmark's floor is imported
 * from the wordmark rather than measured off a screenshot.
 *
 * ## Why a continuous size rather than two tiers
 *
 * A breakpoint has to pick a loser. Any threshold that keeps 46pt on a Pro Max
 * drops iPhone 15 to whatever the small tier is, even though 15 has room for
 * 43pt — and a 3pt-larger touch target on the phone most people hold is worth
 * more than the tidiness of having two cases. So this returns the largest size
 * that fits, clamped to the two sizes the app already ships
 * ({@link HEADER_ACTION_SIZE_STANDARD} and {@link HEADER_ACTION_SIZE_HOME}), and
 * a device wide enough for the roomy one still gets exactly the roomy one.
 *
 * The clamp's lower bound is load-bearing: 38pt is not a number invented for
 * narrow phones, it is the size every non-Home header in the app already draws,
 * so the worst case degrades to a metric that shipped rather than to a new one.
 * Shrinking a touch target below the one the rest of the app ships, to protect a
 * logo, is the wrong trade.
 *
 * ## The residual case, and why it is reported rather than hidden
 *
 * Below ~368pt the floor itself does not fit: five 38pt controls, their gaps and
 * the shell's padding leave less than 120pt however the arithmetic is arranged,
 * and no gap or radius tweak recovers it. That is not a bug to be papered over,
 * it is 320pt hardware (SE 1st gen, iPod touch 7 — both inside this app's iOS
 * 15.1 floor) genuinely being too narrow for five tap targets and a 25pt
 * wordmark.
 *
 * What matters is the *failure mode*. React Native's default `overflow` is
 * visible, so unclipped the wordmark's letters draw straight over the Marketplace
 * button — a logo sitting on top of a control, and a control that still takes the
 * tap. {@link HeaderActionMetrics.brandFits} says which case the caller is in so
 * it can clip the title block *only* there: a symmetric crop of a centred
 * wordmark instead of letters over buttons, and byte-identical layout on every
 * width that fits. Reporting it also makes it assertable, which is the only
 * reason the distinction survives a refactor.
 */
import { PULSESOC_WORDMARK_MIN_WIDTH } from "../components/home/LivingPulseSocWordmark";
import { logiNexus } from "../theme/logiNexus";

/** Horizontal padding `headerShell` draws on each side. */
export const HEADER_SHELL_PADDING_HORIZONTAL = logiNexus.spacing.md;

/** `headerRow`'s gap, between the leading button, the title block and the actions. */
export const HEADER_ROW_GAP = 8;

/** `headerActions`' gap, between one action and the next. */
export const HEADER_ACTION_GAP = 6;

/** The size every non-Home header draws its buttons at, and this module's floor. */
export const HEADER_ACTION_SIZE_STANDARD = 38;

/** The roomier size Home draws when the width can afford it, and this module's cap. */
export const HEADER_ACTION_SIZE_HOME = 46;

/**
 * Glyph size at {@link HEADER_ACTION_SIZE_HOME}, used to keep the icon in
 * proportion as the button scales. The standard header's 25pt glyph falls out of
 * this ratio at 39pt, which is why it is a ratio and not a second constant.
 */
const HOME_GLYPH_RATIO = 29 / HEADER_ACTION_SIZE_HOME;

export type HeaderActionMetrics = {
  /** Width and height of each action button, and of the leading button. */
  size: number;
  /** Gap between adjacent action buttons. */
  gap: number;
  /** Corner radius that keeps the button a circle at `size`. */
  radius: number;
  /** Ionicons glyph size, in proportion to `size`. */
  glyphSize: number;
  /**
   * False when even {@link HEADER_ACTION_SIZE_STANDARD} leaves the wordmark less
   * than its minimum. The caller clips the title block when this is false and
   * changes nothing when it is true.
   */
  brandFits: boolean;
};

/**
 * The width the header spends on everything that is not the title block.
 *
 * `actionCount` counts the trailing cluster including the avatar, because the
 * avatar is laid out inside `headerActions` and is sized with the same metric.
 * The leading button (drawer or back) is counted separately and is always
 * present — when a route has neither, `iconButtonSpacer` reserves its box, so
 * the arithmetic does not change.
 */
export function headerChromeWidth(size: number, gap: number, actionCount: number): number {
  const actions = actionCount > 0 ? actionCount * size + (actionCount - 1) * gap : 0;
  return HEADER_SHELL_PADDING_HORIZONTAL * 2 + HEADER_ROW_GAP * 2 + size + actions;
}

/**
 * The largest action size that leaves the wordmark its full width.
 *
 * Rounded down to an even number so `size / 2` stays an integer radius; a
 * fractional radius on a bordered circle renders as a visibly lumpy edge on
 * iOS.
 */
export function homeHeaderActionMetrics(
  windowWidth: number,
  actionCount: number,
  brandMinWidth: number = PULSESOC_WORDMARK_MIN_WIDTH
): HeaderActionMetrics {
  const gap = HEADER_ACTION_GAP;
  // Solve headerChromeWidth(size) + brandMinWidth <= windowWidth for size. Every
  // term except `size` is constant, and `size` appears once per button plus once
  // for the leading button.
  const fixed =
    HEADER_SHELL_PADDING_HORIZONTAL * 2 +
    HEADER_ROW_GAP * 2 +
    (actionCount > 0 ? (actionCount - 1) * gap : 0) +
    brandMinWidth;
  const perButton = actionCount + 1;
  const affordable = (windowWidth - fixed) / perButton;
  const even = Math.floor(affordable / 2) * 2;
  const size = Math.min(HEADER_ACTION_SIZE_HOME, Math.max(HEADER_ACTION_SIZE_STANDARD, even));
  return {
    size,
    gap,
    radius: size / 2,
    glyphSize: Math.round(size * HOME_GLYPH_RATIO),
    brandFits: headerChromeWidth(size, gap, actionCount) + brandMinWidth <= windowWidth
  };
}
