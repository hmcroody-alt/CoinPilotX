/**
 * The Messenger conversation background.
 *
 * Every wallpaper here is a stack of flat layers: one opaque base colour, a
 * vertical gradient, a few soft rounded glows, a sparse star field, and a
 * legibility scrim. There is no image asset and no animation, on purpose.
 *
 * - **No asset.** A bundled PNG still decodes after the first commit, so the
 *   conversation would open on whatever is underneath — black — and the
 *   background would appear a frame later. A background colour plus
 *   `LinearGradient` paints in the first commit, which is the only way to get
 *   "no flash before the wallpaper appears" with certainty. It also means the
 *   wallpaper needs no network, no cache, and no disk.
 * - **No animation.** The layers are static, so the background composites once
 *   and the message list scrolls over it. Nothing here re-renders per message,
 *   per frame, or per scroll event.
 *
 * The vocabulary mirrors the web build's `--control-wallpaper` stacks in
 * `static/css/pulse_messages_v2.css`, so a wallpaper someone picks on one
 * client is recognisably the same wallpaper on the other. The ids are the ones
 * the control centre already writes through
 * `PATCH /conversations/<id>/control-center` (`appearance.wallpaper`), and the
 * server's allowed set in `pulse_communications_v2/service.py`.
 */

export type ChatWallpaperId =
  | "pulsesoc_cosmic"
  | "deep_space"
  | "neon_planet"
  | "galaxy_grid"
  | "pulse_horizon"
  | "alien_city"
  | "cosmic_ocean"
  | "aurora_signal"
  | "dark_nebula"
  | "star_tunnel"
  | "minimal_black";

/**
 * A single soft shape. Glows and the wide "curve" bands are the same
 * primitive: a positioned, rounded, low-alpha rectangle. Position is the
 * shape's centre as a percentage of the background box so it lands in the
 * same place on every screen size.
 */
export type ChatWallpaperShape = {
  color: string;
  width: number;
  height: number;
  /** Centre X as a percentage of the background width. */
  x: number;
  /** Centre Y as a percentage of the background height. */
  y: number;
  radius: number;
  rotate?: number;
};

export type ChatWallpaperSpec = {
  id: ChatWallpaperId;
  /**
   * Painted as the hosting view's `backgroundColor`, which is why it must be
   * opaque: it is the colour on screen for the first commit, before the
   * gradient child has laid out. Keep it close to the gradient's first stop.
   */
  base: string;
  gradient: readonly [string, string, ...string[]];
  locations: readonly [number, number, ...number[]];
  shapes: readonly ChatWallpaperShape[];
  /**
   * Vertical top-to-bottom scrim over the shapes. Holds the top edge under the
   * header and the bottom edge under the composer without flattening the
   * middle, where the wallpaper is supposed to be visible.
   */
  scrim: readonly [string, string, ...string[]];
  /** How many of `CHAT_WALLPAPER_STARS` to draw. 0 for a starless wallpaper. */
  stars: number;
};

/**
 * Sparse and fixed: `[x%, y%, size, opacity]`. Fixed positions keep the field
 * deterministic, which is what lets it be a plain static subtree instead of
 * something regenerated per mount.
 */
export const CHAT_WALLPAPER_STARS = [
  [9, 7, 1, 0.5], [22, 15, 1, 0.34], [35, 5, 2, 0.4], [47, 22, 1, 0.46], [61, 10, 1, 0.3],
  [74, 27, 1, 0.38], [86, 7, 2, 0.33], [95, 19, 1, 0.44], [13, 37, 1, 0.36], [27, 49, 1, 0.26],
  [41, 41, 1, 0.42], [55, 58, 2, 0.28], [68, 45, 1, 0.46], [81, 65, 1, 0.26], [92, 48, 1, 0.38],
  [6, 72, 1, 0.3], [20, 85, 2, 0.26], [36, 76, 1, 0.42], [51, 90, 1, 0.32], [66, 79, 1, 0.36],
  [78, 92, 1, 0.28], [90, 81, 2, 0.26]
] as const;

/**
 * PulseSoc Cosmic — the default.
 *
 * Deep navy into indigo, one soft blue glow with a cyan highlight beside it,
 * a very light violet accent opposite, and two wide low-alpha curves for the
 * futuristic sweep.
 *
 * The layer intensities are not eyeballed — they are the output of the
 * objective in `__tests__/chatWallpaperContrast.test.ts`. A translucent bubble
 * over a field has two failure modes that pull in opposite directions: brighten
 * the field and the 0.82-alpha outgoing bubble dissolves into it; darken the
 * field and the 0.88-alpha incoming bubble does. Maximising the *worse* of the
 * two separations puts both at 1.45:1, which is a wider margin than any of the
 * ten inherited wallpapers manages, and leaves the field dark enough
 * (luminance 0.039) to still read as a dark-mode background.
 *
 * The first draft of this spec was roughly twice as bright and failed exactly
 * that way: it was the lightest field of the eleven and the outgoing bubble sat
 * at 1.13:1 against it — technically still AA-legible text, but the bubble had
 * stopped looking like a bubble.
 */
const PULSESOC_COSMIC: ChatWallpaperSpec = {
  id: "pulsesoc_cosmic",
  base: "#050C1E",
  gradient: ["#050D20", "#08132C", "#091631", "#060F21"],
  locations: [0, 0.34, 0.66, 1],
  shapes: [
    // The blue glow and its cyan highlight, upper right.
    { color: "rgba(52,104,204,0.12)", width: 520, height: 470, x: 78, y: 14, radius: 260 },
    { color: "rgba(72,196,226,0.06)", width: 300, height: 280, x: 88, y: 44, radius: 150 },
    // Indigo depth low-left, violet accent high-left.
    { color: "rgba(66,74,178,0.09)", width: 560, height: 520, x: 14, y: 80, radius: 280 },
    { color: "rgba(124,96,212,0.06)", width: 340, height: 320, x: 6, y: 12, radius: 170 },
    // Two curves. Wide, shallow, rotated, and faint enough to be structure.
    { color: "rgba(96,150,232,0.033)", width: 720, height: 150, x: 44, y: 58, radius: 300, rotate: -15 },
    { color: "rgba(112,206,230,0.021)", width: 640, height: 110, x: 58, y: 33, radius: 280, rotate: 9 }
  ],
  scrim: ["rgba(4,9,20,0.20)", "rgba(4,9,20,0.00)", "rgba(3,7,16,0.30)"],
  stars: 22
};

const SPECS: Record<ChatWallpaperId, ChatWallpaperSpec> = {
  pulsesoc_cosmic: PULSESOC_COSMIC,
  // The former default. Faint teal/cyan over near-black, with no large shapes.
  deep_space: {
    id: "deep_space",
    base: "#03070F",
    gradient: ["#02050A", "#040A14", "#06101C"],
    locations: [0, 0.48, 1],
    shapes: [
      { color: "rgba(53,244,182,0.08)", width: 420, height: 400, x: 15, y: 18, radius: 210 },
      { color: "rgba(102,231,255,0.06)", width: 400, height: 380, x: 82, y: 22, radius: 200 }
    ],
    scrim: ["rgba(0,6,14,0.34)", "rgba(0,4,12,0.44)"],
    stars: 18
  },
  neon_planet: {
    id: "neon_planet",
    base: "#04091A",
    gradient: ["#010915", "#030C20", "#050B1B"],
    locations: [0, 0.5, 1],
    shapes: [
      { color: "rgba(102,231,255,0.22)", width: 200, height: 200, x: 75, y: 26, radius: 100 },
      { color: "rgba(102,231,255,0.08)", width: 300, height: 300, x: 75, y: 26, radius: 150 },
      { color: "rgba(155,103,255,0.18)", width: 560, height: 540, x: 75, y: 26, radius: 280 }
    ],
    scrim: ["rgba(1,7,16,0.26)", "rgba(1,5,13,0.40)"],
    stars: 12
  },
  galaxy_grid: {
    id: "galaxy_grid",
    base: "#020812",
    gradient: ["#020914", "#03101F", "#020A16"],
    locations: [0, 0.52, 1],
    shapes: [
      { color: "rgba(155,103,255,0.10)", width: 460, height: 440, x: 30, y: 18, radius: 230 },
      { color: "rgba(102,231,255,0.05)", width: 700, height: 130, x: 50, y: 52, radius: 280 }
    ],
    scrim: ["rgba(1,6,14,0.28)", "rgba(1,5,12,0.42)"],
    stars: 22
  },
  pulse_horizon: {
    id: "pulse_horizon",
    base: "#020A15",
    gradient: ["#010813", "#031020", "#030E14"],
    locations: [0, 0.55, 1],
    shapes: [
      { color: "rgba(102,231,255,0.14)", width: 640, height: 420, x: 50, y: 57, radius: 300 },
      { color: "rgba(53,244,182,0.13)", width: 760, height: 44, x: 50, y: 53, radius: 22 }
    ],
    scrim: ["rgba(1,7,16,0.24)", "rgba(2,10,16,0.40)"],
    stars: 14
  },
  alien_city: {
    id: "alien_city",
    base: "#020812",
    gradient: ["#02091A", "#030C1B", "#020712"],
    locations: [0, 0.55, 1],
    shapes: [
      { color: "rgba(53,244,182,0.13)", width: 420, height: 400, x: 75, y: 20, radius: 210 },
      { color: "rgba(102,231,255,0.10)", width: 3, height: 420, x: 16, y: 34, radius: 2 },
      { color: "rgba(53,244,182,0.08)", width: 3, height: 380, x: 43, y: 30, radius: 2 },
      { color: "rgba(155,103,255,0.08)", width: 3, height: 440, x: 72, y: 36, radius: 2 }
    ],
    scrim: ["rgba(2,9,18,0.10)", "rgba(2,9,18,0.62)"],
    stars: 10
  },
  cosmic_ocean: {
    id: "cosmic_ocean",
    base: "#020F19",
    gradient: ["#010A14", "#01121B", "#01161F"],
    locations: [0, 0.5, 1],
    shapes: [
      { color: "rgba(45,224,192,0.18)", width: 620, height: 460, x: 42, y: 100, radius: 300 },
      { color: "rgba(58,141,255,0.14)", width: 460, height: 440, x: 88, y: 18, radius: 230 }
    ],
    scrim: ["rgba(1,10,20,0.22)", "rgba(1,14,22,0.38)"],
    stars: 16
  },
  aurora_signal: {
    id: "aurora_signal",
    base: "#030812",
    gradient: ["#030A16", "#040D1B", "#030812"],
    locations: [0, 0.5, 1],
    shapes: [
      { color: "rgba(102,231,255,0.10)", width: 560, height: 420, x: 50, y: 0, radius: 280 },
      { color: "rgba(53,244,182,0.11)", width: 700, height: 170, x: 32, y: 34, radius: 300, rotate: -25 },
      { color: "rgba(155,103,255,0.10)", width: 700, height: 150, x: 62, y: 62, radius: 300, rotate: -25 }
    ],
    scrim: ["rgba(2,7,15,0.24)", "rgba(2,6,13,0.40)"],
    stars: 20
  },
  dark_nebula: {
    id: "dark_nebula",
    base: "#04050F",
    gradient: ["#04050F", "#060717", "#03040C"],
    locations: [0, 0.5, 1],
    shapes: [
      { color: "rgba(155,103,255,0.12)", width: 460, height: 440, x: 26, y: 24, radius: 230 },
      { color: "rgba(255,102,189,0.08)", width: 420, height: 400, x: 72, y: 30, radius: 210 },
      { color: "rgba(102,231,255,0.07)", width: 500, height: 470, x: 55, y: 80, radius: 250 }
    ],
    scrim: ["rgba(3,4,12,0.26)", "rgba(2,3,10,0.42)"],
    stars: 22
  },
  star_tunnel: {
    id: "star_tunnel",
    base: "#020710",
    gradient: ["#020710", "#030B16", "#02060E"],
    locations: [0, 0.5, 1],
    shapes: [
      { color: "rgba(102,231,255,0.06)", width: 560, height: 560, x: 50, y: 50, radius: 280 },
      { color: "rgba(102,231,255,0.08)", width: 240, height: 240, x: 50, y: 50, radius: 120 }
    ],
    scrim: ["rgba(1,6,13,0.24)", "rgba(1,4,11,0.40)"],
    stars: 22
  },
  // Deliberately plain: the point of this one is that there is nothing in it.
  minimal_black: {
    id: "minimal_black",
    base: "#02060D",
    gradient: ["#02060D", "#000206"],
    locations: [0, 1],
    shapes: [],
    scrim: ["rgba(0,0,0,0.30)", "rgba(0,0,0,0.44)"],
    stars: 0
  }
};

/** The wallpaper a conversation gets when nobody has chosen one. */
export const DEFAULT_CHAT_WALLPAPER: ChatWallpaperId = "pulsesoc_cosmic";

export const CHAT_WALLPAPER_IDS = Object.keys(SPECS) as ChatWallpaperId[];

export function isChatWallpaperId(value: unknown): value is ChatWallpaperId {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(SPECS, value);
}

/**
 * Resolve a stored `appearance.wallpaper` value to something drawable.
 *
 * The precedence the product asks for is *user's explicit choice, then the
 * PulseSoc Cosmic default*, so this only ever substitutes the default for a
 * value that is absent or unrecognised — it never replaces a value it knows.
 * `"default"` is accepted because the server has always allowed it as a
 * synonym for "whatever PulseSoc ships"; that is now Cosmic.
 */
export function resolveChatWallpaper(value: unknown): ChatWallpaperSpec {
  if (value === "default" || value === "" || value === null || value === undefined) return SPECS[DEFAULT_CHAT_WALLPAPER];
  if (isChatWallpaperId(value)) return SPECS[value];
  return SPECS[DEFAULT_CHAT_WALLPAPER];
}
