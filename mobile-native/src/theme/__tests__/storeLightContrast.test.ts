/**
 * The Store palette's contrast claims, measured rather than asserted in a
 * comment.
 *
 * This file exists because of a specific near-miss. Selection mode (§16–§20)
 * needed two new tokens, and the obvious values for both were wrong in ways no
 * amount of looking at them would have revealed:
 *
 * * `select.selectedBorder` was going to be `STORE_CTA_PULSESOC.to`, the brand
 *   green already used for the CTA. On the white card it measures 2.25:1 —
 *   under the 3:1 WCAG 1.4.11 asks of a non-text control. The single mark
 *   distinguishing a selected row from an unselected one would have been
 *   invisible to a low-vision seller.
 * * `select.disabled` was going to sit under `status.warning`, which is the
 *   colour the blocked reason ("2 things left") is drawn in. That pairing
 *   measures 4.16:1. `status.warning` passes on the white card at 4.54:1, with
 *   nothing to spare, so adding any wash beneath it breaks it.
 *
 * Both were caught by computing the ratio, and neither would have been caught
 * by reading the hex. So the ratios live here, where a future palette edit has
 * to answer to them, instead of in a comment that can quietly stop being true.
 *
 * The thresholds are WCAG 2.1 AA: 4.5:1 for body text, 3:1 for non-text UI
 * boundaries (1.4.11).
 */
import { storeLight } from "../storeLight";

/** Relative luminance, WCAG 2.1 §relative-luminance. */
function luminance(hex: string): number {
  const channels = (hex.replace("#", "").match(/../g) as string[])
    .map((pair) => parseInt(pair, 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

/** WCAG 2.1 contrast ratio. Order-independent. */
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("the contrast helper itself", () => {
  // A ratio function that is quietly wrong would pass every assertion below
  // while measuring nothing. These are the two fixed points of the scale.
  it("reports 21:1 for black on white and 1:1 for a colour on itself", () => {
    expect(contrast("#000000", "#FFFFFF")).toBeCloseTo(21, 1);
    expect(contrast("#567890", "#567890")).toBeCloseTo(1, 5);
  });

  it("is order-independent", () => {
    expect(contrast("#0F1111", "#FFFFFF")).toBeCloseTo(contrast("#FFFFFF", "#0F1111"), 10);
  });
});

const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;

describe("selection mode tokens (§3, §16–§20)", () => {
  const { select, bg, text } = storeLight;

  it("marks a selected row visibly enough to be a UI boundary", () => {
    // 3:1, not 4.5:1 — this is a border, not text. The point of the assertion
    // is that the brand green (2.25:1) cannot be substituted back in.
    expect(contrast(select.selectedBorder, bg.card)).toBeGreaterThanOrEqual(AA_NON_TEXT);
  });

  it("keeps the selected row's own title and detail readable on its wash", () => {
    expect(contrast(text.primary, select.selected)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(text.muted, select.selected)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("keeps a blocked row legible rather than merely faded", () => {
    // A row the seller cannot act on still has to be read: the title says which
    // listing, and the reason says how to unblock it. Dimming it to the point
    // of illegibility hides the fix.
    expect(contrast(select.disabledText, select.disabled)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("keeps the blocked reason the strongest text on a blocked row", () => {
    // The near-miss: status.warning on this wash is 4.16:1. If someone deletes
    // disabledReason and reaches for status.warning, this fails.
    expect(contrast(select.disabledReason, select.disabled)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(select.disabledReason, select.disabled)).toBeGreaterThan(
      contrast(select.disabledText, select.disabled)
    );
  });

  it("records that status.warning on the disabled wash would NOT have passed", () => {
    // Pinning the rejected pairing, not just the chosen one. Without this the
    // reason `disabledReason` exists is invisible, and the next person to
    // simplify the palette deletes it.
    expect(contrast(storeLight.status.warning, select.disabled)).toBeLessThan(AA_TEXT);
  });
});

describe("pre-existing Store text pairings", () => {
  const { text, status, bg } = storeLight;

  // These are the pairings selection mode draws on top of, so they are pinned
  // here as the baseline it must not erode. They pass today.
  it.each([
    ["text.primary on card", text.primary, bg.card],
    ["text.muted on card", text.muted, bg.card],
    ["text.link on card", text.link, bg.card],
    ["status.success on card", status.success, bg.card],
    ["status.error on card", status.error, bg.card],
    ["status.neutral on card", status.neutral, bg.card],
    ["text.onDark on the navy strip", text.onDark, bg.strip],
    ["text.onDarkMuted on the navy strip", text.onDarkMuted, bg.strip],
    ["cta.text on the CTA fill", storeLight.cta.text, storeLight.cta.from]
  ])("%s clears AA for body text", (_label, fg, bgColour) => {
    expect(contrast(fg, bgColour)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("flags status.warning on the card as passing with no margin", () => {
    // 4.54:1. It clears AA, and this asserts the *narrowness* deliberately: any
    // darkening of bg.card or lightening of this colour breaks it, and the
    // failure would show up first on the one string that tells a seller their
    // listing is running low.
    const ratio = contrast(status.warning, bg.card);
    expect(ratio).toBeGreaterThanOrEqual(AA_TEXT);
    expect(ratio).toBeLessThan(5);
  });
});
