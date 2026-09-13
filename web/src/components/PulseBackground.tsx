import { Fragment } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ACTIVE_THEME } from "../theme/themes";
import {
  PULSE_BACKGROUND_GEOMETRY,
  PULSE_BACKGROUND_LINES,
  PULSE_BACKGROUND_NODES,
  PULSE_BACKGROUND_SURFACES,
  PULSE_BACKGROUND_VARIANTS,
  PULSE_BACKGROUND_VARIANT_CYCLES,
  type PulseBackgroundIntensity,
  type PulseBackgroundVariant,
  bottomGlowOpacity,
  galacticProfileFor,
  gradientCss,
  haloOpacity,
  includesTier,
  lineOpacity,
  nodeOpacity,
  pulseBackgroundScale,
  pulseOpacityRange,
} from "../theme/pulseBackground";
import "../styles/pulse-background.css";

type Props = {
  variant?: PulseBackgroundVariant;
  intensity?: PulseBackgroundIntensity;
  /** Pin to the viewport instead of the nearest positioned ancestor. */
  fixed?: boolean;
  className?: string;
  /**
   * With children this is a wrapper; without them it is an absolutely
   * positioned layer a surface drops in behind its own content.
   */
  children?: ReactNode;
};

/**
 * The PulseSoc backdrop: a deep-space digital network.
 *
 * A port of `mobile-native/src/components/PulseBackground.tsx`, structure for
 * structure. Sharp, dark and almost still: no blur, no haze, no bloom. The
 * backdrop this replaced on native went light-purple and soft, and every one
 * of those effects costs text contrast on top of costing a frame. What is left
 * is a multi-stop gradient, a hairline mesh and fourteen small nodes, all of
 * them below the opacity ceilings in `theme/pulseBackground`.
 *
 * Why DOM elements and not an SVG
 * -------------------------------
 * The phase plan called for the mesh as inline SVG. Tracing the native
 * component ruled that out: a node's *position* is a percentage of the field
 * (`left: "12%"`) while its *size* is device px (`size: 3`). SVG has one user
 * space, so reproducing that needs a `viewBox` with
 * `preserveAspectRatio="none"` -- which stretches every circle into an ellipse
 * by whatever the field's aspect ratio happens to be, on a layer whose entire
 * job is to look identical to the app. Absolutely-positioned elements carry
 * percent-position-with-px-size natively, and map onto native's
 * `left: "12%"` + `width: 3` with no translation at all. Fidelity to the
 * traced source wins over fidelity to the plan's guess at the mechanism.
 *
 * Three things about the structure are load-bearing, all of them inherited:
 *
 *  - Every decorative layer is inert: `pointer-events: none` and
 *    `aria-hidden`. A backdrop that swallows one gesture is worse than no
 *    backdrop. When there are children the root must not be `aria-hidden`,
 *    because hiding a parent hides its content too.
 *
 *  - Three drivers, not thirty. One drift, one breath, one line translation,
 *    all CSS animations off the same tokens, so nothing runs per frame in JS.
 *
 *  - The active theme's galactic profile decides globally. White renders
 *    nothing at all, Black dims the whole field, and light themes get the
 *    light surface rather than a dimmed dark one -- a near-black field under a
 *    light palette is a contrast inversion, not a subtler backdrop.
 *
 * Motion suppression differs from native only in what the platform provides.
 * Reduce Motion is honoured in CSS; a hidden tab is not composited, which
 * covers native's foreground check. There is no web equivalent of Low Power
 * Mode -- the Battery Status API does not expose it and is unavailable in
 * Safari entirely -- so that one native rule has no counterpart here rather
 * than a worse imitation of one.
 */
export function PulseBackground({
  variant = "default",
  intensity = "standard",
  fixed = false,
  className,
  children,
}: Props) {
  // Native resolves the scheme from the pinned theme; `dark` is the pin, and
  // the profile table is what turns that into a treatment.
  const scheme = ACTIVE_THEME === "light_futuristic" || ACTIVE_THEME === "white" ? "light" : "dark";
  const profile = galacticProfileFor(ACTIVE_THEME, scheme);

  // The early return is the White rule, not an optimisation.
  if (!profile.enabled) return children ? <>{children}</> : null;

  const surfaceKey = profile.variant === "light" ? "light" : "dark";
  const surface = PULSE_BACKGROUND_SURFACES[surfaceKey];
  const scale = pulseBackgroundScale(variant, intensity, surfaceKey);
  const cycles = PULSE_BACKGROUND_VARIANT_CYCLES[variant];
  const animated = PULSE_BACKGROUND_VARIANTS[variant].animated;

  const { driftTranslate, lineTranslate, restingProgress, haloScale, lineThickness, bottomGlowHeight } =
    PULSE_BACKGROUND_GEOMETRY;

  // Where the drivers settle when motion is suppressed: mid-cycle, matching
  // native's `restingProgress`, so the still composition is the one the eye was
  // already looking at rather than an endpoint it never rests on.
  const restingShift = (travel: number) => -travel / 2 + travel * restingProgress;
  const restingDrift = restingShift(driftTranslate);
  const restingLine = restingShift(lineTranslate);

  const fieldStyle: CSSProperties = {
    opacity: profile.intensity,
  };

  const rootStyle = {
    "--pulse-drift": `${driftTranslate / 2}px`,
    "--pulse-travel": `${lineTranslate / 2}px`,
    "--pulse-cycle-drift": `${cycles.drift / 2}ms`,
    "--pulse-cycle-pulse": `${cycles.pulse / 2}ms`,
    "--pulse-cycle-travel": `${cycles.travel / 2}ms`,
  } as CSSProperties;

  const nodes = PULSE_BACKGROUND_NODES.filter((node) => includesTier(variant, node.tier)).map((node, index) => {
    const value = nodeOpacity(node.opacity, scale);
    const [dim, bright] = pulseOpacityRange(value);
    const color = surface.node[node.tone];
    const halo = node.size * haloScale;
    const key = `${node.x}-${node.y}`;

    // A Fragment, not a wrapper element, for the reason native gives: these
    // percentages resolve against the nearest positioned ancestor, and an
    // intervening box is one `position: relative` away from silently becoming
    // that ancestor and collapsing the whole field to zero.
    return (
      <Fragment key={key}>
        <span
          className="pulse-bg__halo"
          data-testid={`pulse-background-halo-${index}`}
          style={{
            backgroundColor: color,
            height: halo,
            left: `${node.x}%`,
            marginLeft: -halo / 2,
            marginTop: -halo / 2,
            opacity: haloOpacity(value),
            top: `${node.y}%`,
            width: halo,
          }}
        />
        <span
          className={`pulse-bg__node${node.pulse ? " pulse-bg__node--pulse" : ""}`}
          data-testid={`pulse-background-node-${index}`}
          style={
            {
              backgroundColor: color,
              height: node.size,
              left: `${node.x}%`,
              marginLeft: -node.size / 2,
              marginTop: -node.size / 2,
              // Breathing nodes settle at the resting point of their own
              // range, not at full brightness, for the same reason the layers
              // settle mid-drift.
              opacity: node.pulse && animated ? dim + (bright - dim) * restingProgress : value,
              top: `${node.y}%`,
              width: node.size,
              "--pulse-dim": dim,
              "--pulse-bright": bright,
            } as CSSProperties
          }
        />
      </Fragment>
    );
  });

  const lines = PULSE_BACKGROUND_LINES.filter((line) => includesTier(variant, line.tier)).map((line, index) => (
    <span
      key={`${line.x}-${line.y}`}
      className="pulse-bg__line"
      data-testid={`pulse-background-line-${index}`}
      style={{
        backgroundColor: surface.line,
        height: lineThickness,
        left: `${line.x}%`,
        opacity: lineOpacity(line.opacity, scale),
        top: `${line.y}%`,
        transform: `rotate(${line.angle}deg)`,
        width: `${line.length}%`,
      }}
    />
  ));

  const field = (
    <div
      className={[
        "pulse-bg",
        fixed ? "pulse-bg--fixed" : "",
        animated ? "pulse-bg--animated" : "",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={rootStyle}
      data-testid="pulse-background"
      aria-hidden="true"
    >
      <div className="pulse-bg__field" style={fieldStyle} data-testid="pulse-background-field">
        <div
          className="pulse-bg__gradient"
          data-testid="pulse-background-gradient"
          style={{ backgroundImage: gradientCss(surface.gradient) }}
        />
        <div
          className="pulse-bg__layer pulse-bg__layer--lines"
          data-testid="pulse-background-lines"
          style={{ transform: animated ? `translate3d(${restingLine}px, 0, 0)` : undefined }}
        >
          {lines}
        </div>
        <div
          className="pulse-bg__layer pulse-bg__layer--nodes"
          data-testid="pulse-background-nodes"
          style={{
            transform: animated ? `translate3d(${restingDrift}px, ${-restingDrift}px, 0)` : undefined,
          }}
        >
          {nodes}
        </div>
        <div
          className="pulse-bg__glow"
          data-testid="pulse-background-glow"
          style={{
            backgroundImage: gradientCss(surface.bottomGlow),
            height: `${bottomGlowHeight * 100}%`,
            opacity: bottomGlowOpacity(variant),
          }}
        />
      </div>
    </div>
  );

  if (!children) return field;

  return (
    <div className="pulse-bg-wrapper">
      {field}
      <div className="pulse-bg-wrapper__content">{children}</div>
    </div>
  );
}
