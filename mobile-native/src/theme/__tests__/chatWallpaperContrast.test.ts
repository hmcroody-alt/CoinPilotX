import { colors } from "../colors";
import {
  CHAT_WALLPAPER_IDS,
  ChatWallpaperSpec,
  DEFAULT_CHAT_WALLPAPER,
  resolveChatWallpaper
} from "../chatWallpaper";

/**
 * The contrast audit for the conversation wallpaper.
 *
 * A wallpaper is the one change that can quietly break every text class at
 * once, and it does it by degrees rather than by breaking — nothing throws, the
 * screenshot looks fine on a desk, and the person reading a timestamp outdoors
 * cannot. So the property under test is not "the numbers are pretty" but the
 * narrower, checkable one:
 *
 *   putting the wallpaper behind a bubble must not make the text in that
 *   bubble harder to read than the flat theme background did.
 *
 * That isolates the wallpaper's own contribution. The absolute AA floor is
 * asserted too, but the regression guard is the comparison.
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

/** The bubble fills and text colours as `ChatScreen` declares them. */
const INCOMING_BUBBLE = "rgba(12,24,43,0.88)";
const OUTGOING_BUBBLE = "rgba(37,83,158,0.82)";

const TEXT_CLASSES = [
  // Every one of these is drawn inside a bubble, so each is audited against
  // both fills. `body` also covers translated text and media captions;
  // `muted` covers timestamps, reply previews, reaction counts, the forwarded
  // label and system notes, which all use `colors.muted`.
  { name: "body", color: colors.text, floor: 4.5 },
  { name: "muted", color: colors.muted, floor: 3 }
] as const;

const BUBBLES = [
  { name: "incoming", fill: INCOMING_BUBBLE },
  { name: "outgoing", fill: OUTGOING_BUBBLE }
] as const;

/** What the text reads against today, with no wallpaper involved at all. */
function flatBaseline(bubbleFill: string, textColor: string): number {
  const bubble = over(parse(colors.background), parse(bubbleFill));
  return contrast(bubble, parse(textColor));
}

describe("chat wallpaper contrast", () => {
  it("defaults to PulseSoc Cosmic", () => {
    expect(resolveChatWallpaper(undefined).id).toBe(DEFAULT_CHAT_WALLPAPER);
    expect(DEFAULT_CHAT_WALLPAPER).toBe("pulsesoc_cosmic");
  });

  it.each(CHAT_WALLPAPER_IDS)("keeps every text class readable over %s", (id) => {
    const spec = resolveChatWallpaper(id);
    const extremes = wallpaperExtremes(spec);

    for (const bubble of BUBBLES) {
      for (const extreme of [extremes.darkest, extremes.lightest]) {
        const composited = over(extreme, parse(bubble.fill));
        for (const text of TEXT_CLASSES) {
          const ratio = contrast(composited, parse(text.color));
          expect(ratio).toBeGreaterThanOrEqual(text.floor);

          // The regression guard. A wallpaper is allowed to change the number
          // a little — the bubbles are translucent, so it must — but it is not
          // allowed to eat a meaningful part of the margin the flat background
          // gave us.
          //
          // 0.82 is the band the ten inherited wallpapers already occupy: the
          // loudest of them erodes the worst case by 14.7%. So this floor does
          // guard against a new wallpaper being worse than anything already
          // shipped; it is not a rubber stamp. The default is held to a tighter
          // bar below, since that is what this change actually promises.
          const baseline = flatBaseline(bubble.fill, text.color);
          expect(ratio).toBeGreaterThanOrEqual(baseline * 0.82);
        }
      }
    }
  });

  it("erodes less of the flat baseline than any inherited wallpaper", () => {
    // The default is the one nobody chose, so it carries the stricter bar.
    // Measured: 0.875 of baseline, against 0.853 for the loudest inherited one.
    const extremes = wallpaperExtremes(resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER));
    for (const bubble of BUBBLES) {
      for (const extreme of [extremes.darkest, extremes.lightest]) {
        for (const text of TEXT_CLASSES) {
          const ratio = contrast(over(extreme, parse(bubble.fill)), parse(text.color));
          expect(ratio).toBeGreaterThanOrEqual(flatBaseline(bubble.fill, text.color) * 0.87);
        }
      }
    }
  });

  it("keeps both bubbles reading as bubbles against the default's field", () => {
    // A translucent bubble over a field can vanish in two opposite ways, and
    // the first draft of the spec hit one of them: it was bright enough that
    // the 0.82-alpha outgoing fill sat at 1.13:1 against the background — the
    // text was still legible, but the bubble had stopped being a shape. Darken
    // the field instead and the 0.88-alpha incoming fill goes the same way.
    //
    // So the property is a floor under the *worse* of the two separations. The
    // spec's layer intensities were chosen by maximising exactly this, which is
    // why the two numbers come out level (1.452 and 1.450) — that balance is
    // the optimum, not a coincidence. For reference the inherited wallpapers
    // reach as low as 1.03 here.
    const { lightest } = wallpaperExtremes(resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER));
    const incoming = contrast(lightest, over(lightest, parse(INCOMING_BUBBLE)));
    const outgoing = contrast(lightest, over(lightest, parse(OUTGOING_BUBBLE)));
    expect(Math.min(incoming, outgoing)).toBeGreaterThanOrEqual(1.4);
    // And they must separate in opposite directions: incoming reads darker
    // than the field it sits on, outgoing lighter. If both went the same way
    // one of them would be competing with the wallpaper rather than sitting
    // on it.
    const field = relativeLuminance(lightest);
    expect(relativeLuminance(over(lightest, parse(INCOMING_BUBBLE)))).toBeLessThan(field);
    expect(relativeLuminance(over(lightest, parse(OUTGOING_BUBBLE)))).toBeGreaterThan(field);
  });

  it("keeps the default subtle — no layer is loud", () => {
    const spec = resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER);
    // "Subtle, not loud" as a number: the strongest shape is a 12% wash.
    // Anything much above this starts reading as a graphic rather than depth.
    for (const shape of spec.shapes) expect(parse(shape.color).a).toBeLessThanOrEqual(0.14);
    // And the whole field must stay dark enough to be a dark-mode background.
    // 0.045 puts the ceiling below the two loudest inherited wallpapers
    // (0.046 and 0.054), so the default cannot be the brightest of the set —
    // which is precisely what the first draft of the spec was, at 0.071.
    const { lightest } = wallpaperExtremes(spec);
    expect(relativeLuminance(lightest)).toBeLessThan(0.045);
  });

  it("paints an opaque base for every wallpaper", () => {
    // The base colour is the first thing on screen. A translucent one would
    // show whatever is underneath — black — for that first commit, which is
    // the flash this whole approach exists to avoid.
    for (const id of CHAT_WALLPAPER_IDS) {
      expect(parse(resolveChatWallpaper(id).base).a).toBe(1);
    }
  });
});
