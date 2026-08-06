/**
 * Tokens for the default PulseSoc backdrop: a deep-space digital network.
 *
 * Every colour, opacity ceiling, cycle time and coordinate lives here so the
 * component that draws it holds no numbers at all. That split is the whole
 * point of the module — the previous backdrop drifted light and blurry one
 * inline tweak at a time, and a value that only exists in a component is a
 * value nobody can review, cap, or test.
 *
 * Two rules are encoded rather than described:
 *
 *  1. Opacity ceilings. `nodeOpacity`/`lineOpacity` clamp, so no node can ever
 *     be painted brighter than 0.20 and no line brighter than 0.12 regardless
 *     of what a variant asks for. Text sits above this layer and has to stay
 *     readable on the worst screen at the worst brightness.
 *
 *  2. Determinism. Node and line positions are a fixed table, never random.
 *     A random field cannot be reviewed, cannot be regression-tested, and
 *     re-rolls itself on every remount — which reads as the backdrop twitching.
 *
 * Light themes get their own surface rather than a dimmed dark one. Painting a
 * near-black navy field under the Light Futuristic palette would be a contrast
 * inversion, not a subtler backdrop, so the light surface keeps the same
 * geometry and swaps to a pale wash with ink-toned nodes at roughly half the
 * opacity. The White theme is handled upstream: its `galacticBackground`
 * profile is disabled and the component renders nothing.
 */

/** The approved palette. Nothing outside this object may introduce a colour. */
export const PULSE_BACKGROUND_COLORS = {
  base: "#050714",
  navy: "#090B22",
  indigo: "#111032",
  darkViolet: "#1A1040",
  accentPurple: "#7C4DFF",
  softLavender: "#A67CFF",
  /** Used on two nodes out of fourteen. Any more and it stops being an accent. */
  pulseCyan: "#42E8D0"
} as const;

/**
 * Hard opacity limits for the decorative layers, applied by the helpers below.
 * These are the numbers that keep the backdrop behind the content instead of
 * competing with it, so they are ceilings and not defaults.
 */
export const PULSE_BACKGROUND_CEILINGS = {
  node: 0.2,
  line: 0.12,
  /** Halo around a node, expressed as a fraction of that node's own opacity. */
  halo: 0.3,
  /**
   * Strongest effective alpha permitted anywhere in the bottom glow, after the
   * layer's own opacity is applied. It is a lift, not a wash — past about 0.1
   * it starts reading as purple fog over the bottom of the screen.
   */
  bottomGlow: 0.1
} as const;

/**
 * Full cycle times in milliseconds — a complete there-and-back, not one leg.
 * The component halves them for the ping-pong. Long on purpose: below about
 * fifteen seconds ambient drift starts reading as activity, and activity in a
 * backdrop pulls the eye off the content.
 */
export const PULSE_BACKGROUND_CYCLES = {
  /** Node field drifting. Budget: 18–35s. */
  drift: 30000,
  /** Opacity breathing on the few nodes marked `pulse`. Budget: 5–10s. */
  pulse: 8000,
  /** Line mesh translating across the field. Budget: 25–45s. */
  travel: 36000
} as const;

/** Geometry that is shared by every variant. Sizes are device-independent px. */
export const PULSE_BACKGROUND_GEOMETRY = {
  /** How much larger a node's halo is than its core. Restrained on purpose. */
  haloScale: 3.2,
  /** Hairline thickness. One pixel keeps the mesh sharp — no bloom, no blur. */
  lineThickness: 1,
  /** Travel of the node field over a drift cycle, in px. */
  driftTranslate: 10,
  /** Travel of the line mesh over a cycle, in px. */
  lineTranslate: 16,
  /** Height of the bottom glow, as a fraction of the field. */
  bottomGlowHeight: 0.45,
  /** Layer opacity the bottom glow rests at before a variant scales it. */
  bottomGlowBase: 0.7,
  /** Resting position for the drivers when motion is suppressed. */
  restingProgress: 0.4
} as const;

export type PulseBackgroundTone = "accent" | "lavender" | "pulse";

/**
 * `core` nodes appear in every variant; `full` nodes are dropped by the quiet
 * variant. Tiering rather than slicing the array means the quiet composition
 * still covers the whole field instead of losing its bottom half.
 */
export type PulseBackgroundTier = "core" | "full";

export type PulseBackgroundNode = {
  /** Percent of the field's width / height. */
  x: number;
  y: number;
  size: number;
  opacity: number;
  tone: PulseBackgroundTone;
  /** Whether this node breathes on the shared pulse driver. */
  pulse: boolean;
  tier: PulseBackgroundTier;
};

/**
 * Fourteen nodes, four of which breathe. Deliberately few: the composition is
 * doing the work here, and density is what turns a network into static.
 */
export const PULSE_BACKGROUND_NODES: readonly PulseBackgroundNode[] = [
  { x: 12, y: 14, size: 3, opacity: 0.18, tone: "accent", pulse: false, tier: "core" },
  { x: 27, y: 9, size: 2, opacity: 0.13, tone: "lavender", pulse: true, tier: "full" },
  { x: 41, y: 20, size: 4, opacity: 0.2, tone: "accent", pulse: false, tier: "core" },
  { x: 58, y: 12, size: 2, opacity: 0.12, tone: "lavender", pulse: false, tier: "full" },
  { x: 74, y: 22, size: 3, opacity: 0.17, tone: "accent", pulse: true, tier: "core" },
  { x: 88, y: 15, size: 2, opacity: 0.12, tone: "lavender", pulse: false, tier: "full" },
  { x: 18, y: 38, size: 2, opacity: 0.13, tone: "lavender", pulse: false, tier: "full" },
  { x: 34, y: 47, size: 3, opacity: 0.16, tone: "accent", pulse: false, tier: "core" },
  { x: 52, y: 41, size: 2, opacity: 0.15, tone: "pulse", pulse: true, tier: "core" },
  { x: 69, y: 55, size: 4, opacity: 0.19, tone: "accent", pulse: false, tier: "core" },
  { x: 85, y: 44, size: 2, opacity: 0.12, tone: "lavender", pulse: false, tier: "full" },
  { x: 22, y: 68, size: 3, opacity: 0.16, tone: "accent", pulse: true, tier: "core" },
  { x: 46, y: 76, size: 2, opacity: 0.14, tone: "pulse", pulse: false, tier: "core" },
  { x: 78, y: 82, size: 3, opacity: 0.17, tone: "accent", pulse: false, tier: "core" }
];

export type PulseBackgroundLine = {
  /** Percent of the field's width / height for the line's start. */
  x: number;
  y: number;
  /** Length as a percent of the field's width, before rotation. */
  length: number;
  /** Degrees, clockwise. Shallow angles read as a mesh, steep ones as scratches. */
  angle: number;
  opacity: number;
  tier: PulseBackgroundTier;
};

/** Seven hairlines threading between the node clusters. Four survive `quiet`. */
export const PULSE_BACKGROUND_LINES: readonly PulseBackgroundLine[] = [
  { x: 8, y: 16, length: 24, angle: -14, opacity: 0.1, tier: "core" },
  { x: 26, y: 12, length: 20, angle: 12, opacity: 0.08, tier: "full" },
  { x: 40, y: 22, length: 28, angle: -8, opacity: 0.12, tier: "core" },
  { x: 14, y: 40, length: 24, angle: 18, opacity: 0.08, tier: "full" },
  { x: 34, y: 48, length: 30, angle: -6, opacity: 0.11, tier: "core" },
  { x: 20, y: 70, length: 28, angle: -10, opacity: 0.1, tier: "core" },
  { x: 50, y: 78, length: 26, angle: 6, opacity: 0.09, tier: "full" }
];

/**
 * A surface is everything that depends on whether the active theme is dark or
 * light. Both share the node/line tables above, so the two treatments are the
 * same composition in different ink.
 */
type GradientStops = {
  colors: readonly [string, string, ...string[]];
  locations: readonly [number, number, ...number[]];
};

export type PulseBackgroundSurface = {
  gradient: GradientStops;
  bottomGlow: GradientStops;
  node: Record<PulseBackgroundTone, string>;
  line: string;
  /** Multiplies every node and line opacity before the ceiling is applied. */
  opacityScale: number;
};

export const PULSE_BACKGROUND_SURFACES: Record<"dark" | "light", PulseBackgroundSurface> = {
  /**
   * Near-black navy at the top, deep indigo with a restrained violet through
   * the middle, back to near-black at the bottom. Six stops rather than three
   * because a three-stop ramp over this range bands visibly on OLED.
   */
  dark: {
    gradient: {
      colors: [
        PULSE_BACKGROUND_COLORS.base,
        PULSE_BACKGROUND_COLORS.navy,
        PULSE_BACKGROUND_COLORS.indigo,
        PULSE_BACKGROUND_COLORS.darkViolet,
        PULSE_BACKGROUND_COLORS.navy,
        PULSE_BACKGROUND_COLORS.base
      ],
      locations: [0, 0.22, 0.46, 0.62, 0.84, 1]
    },
    bottomGlow: {
      colors: ["rgba(124,77,255,0)", "rgba(124,77,255,0.07)", "rgba(5,7,20,0.55)"],
      locations: [0, 0.62, 1]
    },
    node: {
      accent: PULSE_BACKGROUND_COLORS.accentPurple,
      lavender: PULSE_BACKGROUND_COLORS.softLavender,
      pulse: PULSE_BACKGROUND_COLORS.pulseCyan
    },
    line: PULSE_BACKGROUND_COLORS.softLavender,
    opacityScale: 1
  },
  /**
   * The light treatment. A dark field under a light palette is a contrast
   * inversion, so this keeps the geometry and swaps to a pale wash with ink
   * nodes at roughly half strength — present enough to be the same product,
   * quiet enough that it never fights body text.
   */
  light: {
    gradient: {
      colors: ["#F6F7FC", "#F1F2FA", "#ECEBF8", "#EFEDFB", "#F4F5FC", "#F8F8FD"],
      locations: [0, 0.22, 0.46, 0.62, 0.84, 1]
    },
    bottomGlow: {
      colors: ["rgba(124,77,255,0)", "rgba(124,77,255,0.05)", "rgba(255,255,255,0)"],
      locations: [0, 0.62, 1]
    },
    node: { accent: "#5B3FC4", lavender: "#7C4DFF", pulse: "#1F9E8C" },
    line: "#5B3FC4",
    opacityScale: 0.55
  }
};

export type PulseBackgroundVariant = "default" | "quiet" | "elevated" | "static";

/** `subtle` is for surfaces that already carry a lot of chrome. */
export type PulseBackgroundIntensity = "subtle" | "standard";

export type PulseBackgroundVariantSpec = {
  /** False means the composition is drawn once and no loop is ever started. */
  animated: boolean;
  /** Which node/line tiers this variant draws. */
  tiers: readonly PulseBackgroundTier[];
  /** Multiplies node and line opacity, before the ceiling. */
  opacityScale: number;
  /** Multiplies every cycle time. Kept inside the approved budget ranges. */
  cycleScale: number;
  /** Multiplies the bottom glow's opacity. */
  glowScale: number;
};

export const PULSE_BACKGROUND_VARIANTS: Record<PulseBackgroundVariant, PulseBackgroundVariantSpec> = {
  default: { animated: true, tiers: ["core", "full"], opacityScale: 1, cycleScale: 1, glowScale: 1 },
  /** For dense surfaces — fewer elements, dimmer, slower. */
  quiet: { animated: true, tiers: ["core"], opacityScale: 0.7, cycleScale: 1.15, glowScale: 0.7 },
  /** For sparse hero surfaces — same node budget, a touch quicker, more glow. */
  elevated: { animated: true, tiers: ["core", "full"], opacityScale: 1, cycleScale: 0.9, glowScale: 1.35 },
  /** The default composition with the motion removed. */
  static: { animated: false, tiers: ["core", "full"], opacityScale: 0.85, cycleScale: 1, glowScale: 1 }
};

/** `subtle` dims the whole decorative set without touching the gradient. */
export const PULSE_BACKGROUND_INTENSITY_SCALE: Record<PulseBackgroundIntensity, number> = {
  subtle: 0.7,
  standard: 1
};

/** Full cycle times for a variant. Precomputed so no render ever does the maths. */
export const PULSE_BACKGROUND_VARIANT_CYCLES: Record<
  PulseBackgroundVariant,
  { drift: number; pulse: number; travel: number }
> = Object.fromEntries(
  (Object.keys(PULSE_BACKGROUND_VARIANTS) as PulseBackgroundVariant[]).map((variant) => {
    const scale = PULSE_BACKGROUND_VARIANTS[variant].cycleScale;
    return [
      variant,
      {
        drift: Math.round(PULSE_BACKGROUND_CYCLES.drift * scale),
        pulse: Math.round(PULSE_BACKGROUND_CYCLES.pulse * scale),
        travel: Math.round(PULSE_BACKGROUND_CYCLES.travel * scale)
      }
    ];
  })
) as Record<PulseBackgroundVariant, { drift: number; pulse: number; travel: number }>;

/** The combined dimming a variant and an intensity apply together. */
export function pulseBackgroundScale(
  variant: PulseBackgroundVariant,
  intensity: PulseBackgroundIntensity,
  surface: "dark" | "light"
): number {
  return (
    PULSE_BACKGROUND_VARIANTS[variant].opacityScale *
    PULSE_BACKGROUND_INTENSITY_SCALE[intensity] *
    PULSE_BACKGROUND_SURFACES[surface].opacityScale
  );
}

/** Node opacity after scaling, clamped to the ceiling. Never returns more. */
export function nodeOpacity(base: number, scale: number): number {
  return Math.min(PULSE_BACKGROUND_CEILINGS.node, Math.max(0, base * scale));
}

/** Line opacity after scaling, clamped to the ceiling. Never returns more. */
export function lineOpacity(base: number, scale: number): number {
  return Math.min(PULSE_BACKGROUND_CEILINGS.line, Math.max(0, base * scale));
}

/** Halo opacity, derived from the node it surrounds so it can never outshine it. */
export function haloOpacity(nodeValue: number): number {
  return nodeValue * PULSE_BACKGROUND_CEILINGS.halo;
}

/**
 * The dim/bright pair a breathing node interpolates between. The bright end is
 * the node's own (already clamped) opacity, so breathing can only ever take
 * brightness away — it cannot push a node past the ceiling.
 */
export function pulseOpacityRange(nodeValue: number): readonly [number, number] {
  return [nodeValue * 0.55, nodeValue];
}

/**
 * Layer opacity for the bottom glow under a variant. Kept at or below 1 so the
 * gradient's own alphas stay the binding constraint on how strong it can get.
 */
export function bottomGlowOpacity(variant: PulseBackgroundVariant): number {
  return Math.min(1, PULSE_BACKGROUND_GEOMETRY.bottomGlowBase * PULSE_BACKGROUND_VARIANTS[variant].glowScale);
}

/** Whether a node or line is drawn under this variant. */
export function includesTier(variant: PulseBackgroundVariant, tier: PulseBackgroundTier): boolean {
  return PULSE_BACKGROUND_VARIANTS[variant].tiers.includes(tier);
}
