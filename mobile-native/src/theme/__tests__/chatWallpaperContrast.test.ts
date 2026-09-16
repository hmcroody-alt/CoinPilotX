import { readFileSync } from "fs";
import { join } from "path";
import { chatGraphite } from "../chatGraphite";
import {
  CHAT_WALLPAPER_IDS,
  ChatWallpaperSpec,
  DEFAULT_CHAT_WALLPAPER,
  resolveChatWallpaper
} from "../chatWallpaper";

/**
 * The contrast audit for the conversation wallpaper.
 *
 * This file used to ask a different question, and the question changed because
 * the design did. The bubbles were translucent, so the wallpaper leaked into
 * every timestamp and the property worth testing was erosion: *putting the
 * wallpaper behind a bubble must not make the text in that bubble harder to
 * read than the flat theme background did.*
 *
 * Graphite bubbles are opaque. So that entire mechanism is gone, and the
 * honest thing to test is the new premise rather than a comparison that is now
 * arithmetically guaranteed to come out at 1.000. Two properties replace it:
 *
 *   1. the fills really are opaque, so no wallpaper can reach the text at all
 *      (this is what lets `chatGraphiteContrast.test.ts` audit text against
 *      flat surfaces and be correct about the whole screen);
 *   2. a bubble still has to read as a shape against every one of the eleven
 *      fields it can be drawn on, since that is the one thing the wallpaper can
 *      still take away.
 *
 * The bubble colours are imported from `chatGraphite` rather than copied. The
 * previous version of this file pinned four literals by hand and had to add a
 * separate test to catch them drifting out of sync with the screen; importing
 * the source of truth removes the failure mode instead of guarding it.
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

function contrast(a: Rgb, b: Rgb): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * The two extremes of what the wallpaper can put behind a bubble.
 *
 * `lightest` stacks every soft shape over the brightest gradient stop, which no
 * single point on screen actually gets — several of the shapes are on opposite
 * sides of the screen. Over-counting is the point: if the extreme still holds,
 * every real point holds.
 */
function wallpaperExtremes(spec: ChatWallpaperSpec): { darkest: Rgb; lightest: Rgb } {
  const stops = spec.gradient.map(parse);
  const byLuminance = [...stops].sort((a, b) => relativeLuminance(a) - relativeLuminance(b));
  const scrims = spec.scrim.map(parse);

  let lightest: Rgb = byLuminance[byLuminance.length - 1];
  for (const shape of spec.shapes) lightest = over(lightest, parse(shape.color));
  // The scrim sits above the shapes, so the lightest case is the weakest scrim.
  const weakestScrim = [...scrims].sort((a, b) => a.a - b.a)[0];
  lightest = over(lightest, weakestScrim);

  let darkest: Rgb = byLuminance[0];
  const strongestScrim = [...scrims].sort((a, b) => b.a - a.a)[0];
  darkest = over(darkest, strongestScrim);

  return { darkest, lightest };
}

const BUBBLES = [
  { name: "incoming", fill: chatGraphite.incomingSurface, border: chatGraphite.incomingBorder },
  { name: "outgoing", fill: chatGraphite.outgoingSurface, border: chatGraphite.outgoingBorder }
] as const;

function bothExtremes(id: (typeof CHAT_WALLPAPER_IDS)[number]): Rgb[] {
  const { darkest, lightest } = wallpaperExtremes(resolveChatWallpaper(id));
  return [darkest, lightest];
}

describe("chat wallpaper contrast", () => {
  it("defaults to PulseSoc Graphite", () => {
    expect(resolveChatWallpaper(undefined).id).toBe(DEFAULT_CHAT_WALLPAPER);
    // The id is a wire value shared with the server's allowed set and with
    // every stored `appearance.wallpaper` row, so it stays `pulsesoc_cosmic`
    // even though the spec it names is now graphite. See `chatWallpaper.ts`.
    expect(DEFAULT_CHAT_WALLPAPER).toBe("pulsesoc_cosmic");
  });

  /**
   * The acceptance criterion that the mockup's artwork is not in the product.
   *
   * The approved direction supplied colour, not imagery. So the default is the
   * only spec in the file with nothing in it but a gradient, and that emptiness
   * is the design — a planet, an arc or a star field reappearing here would be
   * the single most likely way for this change to be quietly undone.
   */
  it("draws the default as a flat graphite field and nothing else", () => {
    const spec = resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER);
    expect(spec.shapes).toEqual([]);
    expect(spec.stars).toBe(0);
    for (const stop of spec.scrim) expect(parse(stop).a).toBe(0);
    expect(spec.gradient).toEqual([chatGraphite.canvasTop, chatGraphite.canvasBottom]);
    expect(spec.base).toBe(chatGraphite.canvasTop);
  });

  /**
   * The property that makes the rest of the palette auditable against flat
   * colours: the wallpaper cannot reach the text.
   *
   * Asserted as an identity rather than as a ratio, because a ratio would let a
   * 0.99-alpha fill through while reading as "basically opaque". If a bubble
   * ever goes translucent again this fails, and the erosion reasoning this file
   * used to carry has to come back with it.
   */
  it.each(CHAT_WALLPAPER_IDS)("cannot reach the text inside a bubble over %s", (id) => {
    for (const bubble of BUBBLES) {
      const fill = parse(bubble.fill);
      expect(fill.a).toBe(1);
      for (const field of bothExtremes(id)) {
        expect(over(field, fill)).toEqual({ r: fill.r, g: fill.g, b: fill.b });
      }
    }
  });

  /**
   * What the wallpaper *can* still take away: the bubble's outline as a shape.
   *
   * Both fills are now lighter than every field they can land on, which is the
   * opposite of the old navy design — there, incoming read darker than the
   * field and outgoing lighter, and they were held apart in both directions.
   * Graphite puts both above the canvas, so the direction is asserted too: if
   * one of them ever crossed under a field, it would be competing with the
   * wallpaper instead of sitting on it.
   *
   * 1.30 is the floor. Measured worst case across all eleven wallpapers is
   * 1.353 (outgoing, over `alien_city` at its synthetic brightest); the default
   * sits at 1.402 outgoing and 1.432 incoming.
   */
  it.each(CHAT_WALLPAPER_IDS)("keeps both bubbles reading as shapes over %s", (id) => {
    for (const field of bothExtremes(id)) {
      for (const bubble of BUBBLES) {
        const fill = parse(bubble.fill);
        expect(contrast(field, fill)).toBeGreaterThanOrEqual(1.3);
        expect(relativeLuminance(fill)).toBeGreaterThan(relativeLuminance(field));
      }
    }
  });

  /**
   * And the borders, which finish the edge the fill already draws.
   *
   * Note the asymmetry, because it is a real and reported one. The outgoing
   * border is a solid `#4D8FE9` and clears 3:1 everywhere, so the blue bubble's
   * boundary satisfies WCAG 1.4.11 on its own. The incoming border is the
   * approved `rgba(214,222,232,0.20)` and reaches only 2.08–4.29 depending on
   * the field — under 3:1 on the graphite default. That is shipped as specified:
   * a message bubble is content rather than a control, and it is additionally
   * separated by fill, by which edge of the screen it hangs off, by its squared
   * corner and by the sender label above it. The floor here is therefore 2.0,
   * which is what the design actually delivers, not a bar it clears with room.
   */
  it.each(CHAT_WALLPAPER_IDS)("keeps both bubble borders drawing an edge over %s", (id) => {
    for (const field of bothExtremes(id)) {
      for (const bubble of BUBBLES) {
        const edge = over(parse(bubble.fill), parse(bubble.border));
        expect(contrast(field, edge)).toBeGreaterThanOrEqual(2);
      }
    }
  });

  it("keeps the default no brighter than the loudest wallpaper anyone already chose", () => {
    // Graphite is a middle grey, so it is legitimately brighter than eight of
    // the ten inherited navy fields — the old ceiling of 0.045 encoded "the
    // default must be nearly the darkest", which was a property of the navy
    // design and not a requirement. What still holds, and is worth holding, is
    // that the field nobody picked is not the brightest thing in the set.
    const inherited = CHAT_WALLPAPER_IDS.filter((id) => id !== DEFAULT_CHAT_WALLPAPER);
    const loudest = Math.max(
      ...inherited.map((id) => relativeLuminance(wallpaperExtremes(resolveChatWallpaper(id)).lightest))
    );
    const mine = relativeLuminance(wallpaperExtremes(resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER)).lightest);
    expect(mine).toBeLessThanOrEqual(loudest);
    // An absolute ceiling as well, so the whole set cannot drift up together.
    expect(mine).toBeLessThan(0.06);
  });

  it("keeps the default subtle if a layer is ever added back to it", () => {
    // Vacuous today, and kept for the same reason a seatbelt is kept in a
    // parked car. The default has no shapes at all now, so the cap guards the
    // edit that reintroduces one: "subtle, not loud" as a number is a 14%
    // wash, above which a layer reads as a graphic rather than as depth.
    //
    // Scoped to the default on purpose. The inherited wallpapers go up to 0.22
    // because they are *meant* to be graphics — someone picked them — and
    // holding them to the default's restraint would be re-designing ten
    // wallpapers nobody asked us to touch.
    for (const shape of resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER).shapes) {
      expect(parse(shape.color).a).toBeLessThanOrEqual(0.14);
    }
  });

  it("paints an opaque base for every wallpaper", () => {
    // The base colour is the first thing on screen. A translucent one would
    // show whatever is underneath — black — for that first commit, which is
    // the flash this whole approach exists to avoid.
    for (const id of CHAT_WALLPAPER_IDS) {
      expect(parse(resolveChatWallpaper(id).base).a).toBe(1);
    }
  });

  it("draws the canvas from the wallpaper, not from the screen", () => {
    // The wallpaper's opaque `base` is the first paint layer in `ChatScreen`,
    // ahead of the header and the list, which is why the canvas tokens live in
    // the wallpaper spec rather than in a `backgroundColor` on the root view.
    // A root fill would paint over the wallpaper and make the whole gradient
    // dead code, so the root must stay transparent.
    const source = readFileSync(join(__dirname, "..", "..", "screens", "ChatScreen.tsx"), "utf8").replace(/\s+/g, "");
    expect(source).toContain('root:{backgroundColor:"transparent"');
    expect(source).toContain("<ChatWallpaperwallpaper={wallpaper}");
  });
});
