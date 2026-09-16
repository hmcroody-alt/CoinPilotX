import { readFileSync } from "fs";
import { join } from "path";
import { chatGraphite } from "../chatGraphite";

/**
 * The contrast and hierarchy audit for the graphite conversation surface.
 *
 * `chatGraphite.ts` makes two claims that a reader cannot check by looking at
 * it: that the surfaces step in a particular order, and that every piece of
 * text on them clears AA. Both are arithmetic on the hex values, so both belong
 * in a test rather than in a comment — a comment that says "one step lighter"
 * keeps saying it after someone edits the hex.
 *
 * Two things this file deliberately does *not* do. It does not re-derive the
 * numbers the approved palette was handed to us with; it asserts them, so that
 * an edit to a locked value fails here and has to be argued for. And it does
 * not check anything about the wallpaper behind the bubbles — the fills are
 * opaque, and `chatWallpaperContrast.test.ts` proves that, which is what makes
 * every ratio below independent of which wallpaper is on screen.
 */

type Rgb = { r: number; g: number; b: number };
type Rgba = Rgb & { a: number };

function parse(color: string): Rgba {
  const hex = color.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  const rgba = color.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
  if (!rgba) throw new Error(`unparsed colour: ${color}`);
  return { r: Number(rgba[1]), g: Number(rgba[2]), b: Number(rgba[3]), a: rgba[4] === undefined ? 1 : Number(rgba[4]) };
}

/** Source-over, which is what the compositor does with these layers. */
function over(under: Rgb, above: Rgba): Rgb {
  return {
    r: above.a * above.r + (1 - above.a) * under.r,
    g: above.a * above.g + (1 - above.a) * under.g,
    b: above.a * above.b + (1 - above.a) * under.b
  };
}

function relativeLuminance({ r, g, b }: Rgb): number {
  const channel = (value: number) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: string | Rgb, b: string | Rgb): number {
  const la = relativeLuminance(typeof a === "string" ? parse(a) : a);
  const lb = relativeLuminance(typeof b === "string" ? parse(b) : b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function luminance(color: string): number {
  return relativeLuminance(parse(color));
}

/** A bubble inset — reply previews, attachment cards, media backings. */
const insetOver = (bubble: string) => over(parse(bubble), parse(chatGraphite.insetSurface));

const CHAT_SCREEN = readFileSync(join(__dirname, "..", "..", "screens", "ChatScreen.tsx"), "utf8");

describe("graphite chat palette — the locked values", () => {
  /**
   * The approved visual system, asserted rather than re-derived.
   *
   * These are the values the design was signed off on. They are pinned here so
   * that "one of the greys looked a bit flat on my monitor" cannot land as a
   * silent edit: changing any of them fails this test, which is the point where
   * it has to become a conversation instead.
   */
  it("ships the approved baseline unchanged", () => {
    expect(chatGraphite.headerSurface).toBe("#292E36");
    expect(chatGraphite.canvasTop).toBe("#3A4049");
    expect(chatGraphite.canvasBottom).toBe("#343A42");
    expect(chatGraphite.incomingSurface).toBe("#505761");
    expect(chatGraphite.incomingBorder).toBe("rgba(214, 222, 232, 0.20)");
    expect(chatGraphite.outgoingSurface).toBe("#24549B");
    expect(chatGraphite.outgoingBorder).toBe("#4D8FE9");
    expect(chatGraphite.composerSurface).toBe("#20262E");
    expect(chatGraphite.primaryText).toBe("#F7F8FA");
    expect(chatGraphite.quietDivider).toBe("rgba(230, 236, 245, 0.16)");
    expect(chatGraphite.shadow).toBe("#080B0F");
    expect(chatGraphite.shadowOpacity).toBe(0.18);
  });

  /**
   * The two measured deviations, and *why* they are deviations.
   *
   * This is the shape that keeps a deviation honest. Asserting only that we
   * ship `#C8D0DB` would let someone "restore the spec" tomorrow and be
   * congratulated for it by a green suite. So the test states the actual
   * finding: the specified value fails on the incoming bubble, ours passes.
   * Restoring the spec value turns this red and explains itself.
   */
  it("lifts exactly two values, because the specified ones fail on the incoming bubble", () => {
    const bubble = chatGraphite.incomingSurface;

    // Timestamps, delivery labels, the forwarded tag and the sender name are
    // all 9–12px. That is small text, so 4.5:1 is the bar, not 3:1.
    expect(contrast(bubble, "#BEC6D1")).toBeLessThan(4.5); // the specified metadata grey: 4.24
    expect(contrast(bubble, chatGraphite.secondaryText)).toBeGreaterThanOrEqual(4.5); // ours: 4.69

    expect(contrast(bubble, "#61D8FF")).toBeLessThan(4.5); // colors.accentStrong: 4.44
    expect(contrast(bubble, chatGraphite.senderAccent)).toBeGreaterThanOrEqual(4.5); // ours: 4.81

    // Same hue in both cases — these are lifts, not new colours. A lift stays
    // inside the approved palette's language; a hue change would not.
    expect(chatGraphite.secondaryText).toBe("#C8D0DB");
    expect(chatGraphite.senderAccent).toBe("#7BDFFF");
  });
});

describe("graphite chat palette — hierarchy", () => {
  /**
   * The whole design is one sentence — chrome is darkest, canvas is middle,
   * bubbles sit above the canvas — and luminance is the only form of that
   * sentence a machine can check.
   */
  it("steps from chrome to canvas to bubble, in that order", () => {
    const order = [
      ["composerSurface", chatGraphite.composerSurface],
      ["headerSurface", chatGraphite.headerSurface],
      ["canvasBottom", chatGraphite.canvasBottom],
      ["canvasTop", chatGraphite.canvasTop],
      ["outgoingSurface", chatGraphite.outgoingSurface],
      ["incomingSurface", chatGraphite.incomingSurface]
    ] as const;

    for (let i = 1; i < order.length; i += 1) {
      expect(luminance(order[i][1])).toBeGreaterThan(luminance(order[i - 1][1]));
    }

    // And the composer field is under the footer it sits in, not over it.
    expect(luminance(chatGraphite.composerSurface)).toBeLessThan(luminance(chatGraphite.headerSurface));
  });

  it("keeps the whole surface inside dark-mode range", () => {
    // The lightest anchored surface is an incoming bubble. Above roughly 0.12
    // the screen stops being a dark theme and starts being a grey one.
    expect(luminance(chatGraphite.incomingSurface)).toBeLessThan(0.12);
  });

  /**
   * The accessibility requirement that colour is never the only signal.
   *
   * Outgoing and incoming are within 3.3% of each other in luminance, which
   * means a grayscale screenshot cannot tell them apart and neither can a
   * red/green deficiency. That is on purpose — but it is only safe because the
   * *geometry* differs, so this test asserts both halves: the luminances are
   * level, and the screen really does draw them differently.
   */
  it("does not distinguish sender from recipient by weight, and says so in geometry", () => {
    const ratio = contrast(chatGraphite.incomingSurface, chatGraphite.outgoingSurface);
    expect(ratio).toBeLessThan(1.05);

    const source = CHAT_SCREEN.replace(/\s+/g, "");
    // Opposite edges of the screen.
    expect(source).toContain('mineWrap:{justifyContent:"flex-end"}');
    expect(source).toContain('theirWrap:{justifyContent:"flex-start"}');
    // Opposite squared corner.
    expect(source).toContain("borderBottomRightRadius:6");
    expect(source).toContain("borderBottomLeftRadius:6");
  });
});

describe("graphite chat palette — text contrast", () => {
  /**
   * Every surface this palette draws text on, with the text classes that land
   * on it. AA for normal text is 4.5:1; everything here is normal text or
   * smaller, so 4.5 is the floor throughout and there is no 3:1 exception to
   * argue about.
   */
  const SURFACES: Array<{ name: string; field: string | Rgb; classes: string[] }> = [
    {
      name: "header/footer",
      field: chatGraphite.headerSurface,
      // Thread title, and the composer's own state line.
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText]
    },
    {
      name: "composer field",
      field: chatGraphite.composerSurface,
      // Draft text, the placeholder, and the reply strip above the input.
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText]
    },
    {
      name: "icon control",
      field: chatGraphite.controlSurface,
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText]
    },
    {
      name: "canvas (top)",
      field: chatGraphite.canvasTop,
      // The pagination label and the empty state sit directly on the field.
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    },
    {
      name: "canvas (bottom)",
      field: chatGraphite.canvasBottom,
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    },
    {
      name: "incoming bubble",
      field: chatGraphite.incomingSurface,
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    },
    {
      name: "outgoing bubble",
      field: chatGraphite.outgoingSurface,
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    },
    {
      name: "inset in an incoming bubble",
      field: insetOver(chatGraphite.incomingSurface),
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    },
    {
      name: "inset in an outgoing bubble",
      field: insetOver(chatGraphite.outgoingSurface),
      classes: [chatGraphite.primaryText, chatGraphite.secondaryText, chatGraphite.senderAccent]
    }
  ];

  it.each(SURFACES)("clears AA for every text class on the $name", ({ field, classes }) => {
    for (const text of classes) {
      expect(contrast(field, text)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps the inset readable inside either bubble with one token", () => {
    // The reason `insetSurface` is translucent rather than a solid grey: one
    // value has to work over the grey bubble and the blue one. If it were
    // solid, the outgoing case would need its own token, and a second token is
    // a second thing to forget to update.
    expect(parse(chatGraphite.insetSurface).a).toBeLessThan(1);
    for (const bubble of [chatGraphite.incomingSurface, chatGraphite.outgoingSurface]) {
      const inset = insetOver(bubble);
      // It has to darken the bubble it is in, or it is not an inset.
      expect(relativeLuminance(inset)).toBeLessThan(luminance(bubble));
      expect(contrast(inset, chatGraphite.secondaryText)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("graphite chat palette — control boundaries", () => {
  /**
   * The composer field is the one place where the fill cannot draw the control.
   *
   * `#20262E` inside `#292E36` is 1.12:1 — a step the eye will not find. So the
   * border is load-bearing here in the way the incoming bubble's border used to
   * be, and WCAG 1.4.11 wants 3:1 for the visual boundary of a control.
   *
   * Nothing in the graphite palette reaches that: `quietDivider` composites to
   * 1.01:1 over the field. The existing cyan input edge does, at 3.06:1, and it
   * is already the app's language for an active field, so it stays. Asserted
   * here because that is a load-bearing decision that reads like leftover paint.
   */
  it("draws the composer field with its border, because its fill cannot", () => {
    expect(contrast(chatGraphite.composerSurface, chatGraphite.headerSurface)).toBeLessThan(1.5);

    const quiet = over(parse(chatGraphite.composerSurface), parse(chatGraphite.quietDivider));
    expect(contrast(quiet, chatGraphite.headerSurface)).toBeLessThan(3);

    const INPUT_BORDER = "rgba(97,216,255,0.5)";
    expect(CHAT_SCREEN.replace(/\s+/g, "")).toContain(`borderColor:"${INPUT_BORDER}"`);
    const edge = over(parse(chatGraphite.composerSurface), parse(INPUT_BORDER));
    expect(contrast(edge, chatGraphite.headerSurface)).toBeGreaterThanOrEqual(3);
  });

  /**
   * The icon buttons are the same story with a different answer.
   *
   * `controlSurface` against the header is 1.16:1, so the fill is a hint and
   * not a boundary. These buttons keep their per-tone inline border — the call
   * button's teal, the video button's violet — which is both the 3:1 boundary
   * and the thing that tells the two apart. So the assertion is that the
   * inline border is still there, since deleting it as "redundant styling"
   * would take the boundary with it.
   */
  it("keeps the per-tone border on the header controls", () => {
    expect(contrast(chatGraphite.controlSurface, chatGraphite.headerSurface)).toBeLessThan(1.5);
    expect(CHAT_SCREEN).toContain("borderColor: `${color}88`");
  });
});

describe("graphite chat palette — the screen consumes the tokens", () => {
  /**
   * The anti-duplication requirement, as a test.
   *
   * A palette in a module is only a single source of truth for as long as
   * nobody pastes one of its values into a component. So: the screen must
   * reference the tokens, and the raw values must not appear in it.
   */
  it("references tokens rather than copies of their values", () => {
    for (const token of [
      "chatGraphite.headerSurface",
      "chatGraphite.composerSurface",
      "chatGraphite.controlSurface",
      "chatGraphite.incomingSurface",
      "chatGraphite.incomingBorder",
      "chatGraphite.outgoingSurface",
      "chatGraphite.outgoingBorder",
      "chatGraphite.insetSurface",
      "chatGraphite.primaryText",
      "chatGraphite.secondaryText",
      "chatGraphite.senderAccent",
      "chatGraphite.quietDivider",
      "chatGraphite.shadow",
      "chatGraphite.shadowOpacity"
    ]) {
      expect(CHAT_SCREEN).toContain(token);
    }
  });

  it("does not paste any palette value into the screen", () => {
    const source = CHAT_SCREEN.replace(/\s+/g, "");
    for (const value of [
      chatGraphite.headerSurface,
      chatGraphite.canvasTop,
      chatGraphite.canvasBottom,
      chatGraphite.incomingSurface,
      chatGraphite.incomingBorder,
      chatGraphite.outgoingSurface,
      chatGraphite.outgoingBorder,
      chatGraphite.composerSurface,
      chatGraphite.primaryText,
      chatGraphite.secondaryText,
      chatGraphite.senderAccent,
      chatGraphite.quietDivider,
      chatGraphite.insetSurface,
      chatGraphite.controlSurface
    ]) {
      expect(source).not.toContain(value.replace(/\s+/g, ""));
    }
  });
});
