/**
 * One tile of a "quick links" grid, and the grid that lays them out.
 *
 * The subtitle is a live status line, not a description — "12 items · 3 low"
 * rather than "Manage your inventory". A tile that restates its own label is a
 * row of wasted pixels; a tile that reports its section's current state saves
 * the seller a tap to find out nothing changed.
 *
 * `disabled` exists because several destinations in the reference designs
 * (Shipping settings, Returns policy, Audiences, the creative library) have no
 * screen in this app. Rendering them as unavailable with an honest subtitle is
 * better than either hiding them — which would silently drop half the spec — or
 * wiring them to an unrelated screen, which would be a lie the seller only
 * discovers after tapping.
 *
 * ## Why the layout rules are in here rather than in the callers
 *
 * This component previously had `numberOfLines={1}` on its label and a wrapper
 * of `{ flex: 1 }`, and the callers decided how many tiles went in a row. Store
 * put two per row and looked right. Marketplace and Advertising put four in a
 * wrapping row, each `flex: 1` child took roughly a quarter of the width, and
 * the labels rendered as "S…", "M…", "A…" and "Cr…". That is the same defect
 * class that forced the Business Hub revert (`docs/business_os/
 * BUSINESS_HUB_REVERT.md`) — live text combined with fixed geometry and no rule
 * for what gives when they conflict.
 *
 * So the rule moved into the component and the geometry stopped being the
 * caller's decision. `StoreQuickLinkGrid` takes a flat list and chunks it into
 * pairs itself; four-across is not expressible through this module. The tile
 * wraps its label to two lines rather than clipping it to one, caps how far
 * both strings can grow under Dynamic Type, and makes the unavailable state
 * legible without relying on grey truncated text.
 *
 * These rules are asserted by `__tests__/StoreQuickLinkTile.test.tsx` at large
 * font scales. The assertion is the point: the identical lesson was already
 * written down in prose after the hub revert, and prose did not stop it
 * recurring on two more screens.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";

/**
 * How far the label and subtitle may grow under the OS text-size setting.
 *
 * Not 1 — capping growth entirely would ignore the setting, which is its own
 * accessibility failure. These ceilings are the point at which two lines of
 * label still fit a half-width tile on the narrowest supported device, which is
 * the condition the previous layout was never tested against.
 */
export const QUICK_LINK_LABEL_MAX_FONT_SCALE = 1.6;
export const QUICK_LINK_SUBTITLE_MAX_FONT_SCALE = 1.4;

/** The label wraps to this many lines before it ellipsises at a word boundary. */
export const QUICK_LINK_LABEL_LINES = 2;
export const QUICK_LINK_SUBTITLE_LINES = 2;

/** Tiles per row. Not configurable, deliberately — see the note above. */
export const QUICK_LINK_TILES_PER_ROW = 2;

export type StoreQuickLinkTileProps = {
  icon: string;
  label: string;
  /** Live status from real data, or the reason the tile is unavailable. */
  subtitle: string;
  onPress?: () => void;
  disabled?: boolean;
  /**
   * A tile that reports a state rather than gating a feature. "Not enough
   * completed sales yet" is information about the seller's own record, not a
   * locked door — so it renders at full opacity, with no lock icon, and is
   * announced as text rather than as an unavailable button. Only meaningful
   * on tiles without an `onPress`.
   */
  informational?: boolean;
  reducedMotion: boolean;
};

export function StoreQuickLinkTile({
  icon,
  label,
  subtitle,
  onPress,
  disabled = false,
  informational = false,
  reducedMotion
}: StoreQuickLinkTileProps) {
  const press = useStorePress(reducedMotion, 0.97);

  /**
   * A tile with no destination is unavailable whether or not the caller
   * remembered to say so.
   *
   * `Pressable` folds its own `disabled` into `accessibilityState`, so a tile
   * passed no `onPress` was already being announced as dimmed and disabled
   * while its label read like a working link and its icon was a chevron
   * promising a screen that does not exist. One source of truth for the state
   * removes the possibility of the visual, the assistive-technology copy and
   * the touch behaviour disagreeing — which is the same class of defect as the
   * layout one this component exists to close.
   */
  const info = informational && !onPress && !disabled;
  const unavailable = !info && (disabled || !onPress);

  return (
    <Animated.View style={[styles.wrap, press.style]}>
      <Pressable
        style={[styles.tile, unavailable && styles.tileDisabled]}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        disabled={unavailable || info}
        accessibilityRole={info ? "text" : "button"}
        accessibilityState={info ? undefined : { disabled: unavailable }}
        accessibilityLabel={
          unavailable ? `${label}. Unavailable. ${subtitle}` : `${label}. ${subtitle}`
        }
      >
        <Ionicons
          name={icon as never}
          size={20}
          color={unavailable ? storeLight.text.muted : storeLight.text.primary}
        />
        <View style={styles.body}>
          {/* Two lines, ellipsised at a word boundary if even two will not do.
              One line was what produced "Busine…" on the hub and "S…" here. */}
          <Text
            style={[styles.label, unavailable && styles.muted]}
            numberOfLines={QUICK_LINK_LABEL_LINES}
            ellipsizeMode="tail"
            maxFontSizeMultiplier={QUICK_LINK_LABEL_MAX_FONT_SCALE}
          >
            {label}
          </Text>
          <Text
            style={styles.subtitle}
            numberOfLines={QUICK_LINK_SUBTITLE_LINES}
            ellipsizeMode="tail"
            maxFontSizeMultiplier={QUICK_LINK_SUBTITLE_MAX_FONT_SCALE}
          >
            {subtitle}
          </Text>
        </View>
        {/* An unavailable tile says so with a shape, not only with grey. Reduced
            opacity on truncated text is indistinguishable from a rendering
            fault, and it is invisible to anyone who cannot perceive the
            contrast difference. An informational tile gets neither symbol —
            there is no destination to promise and nothing locked to signal. */}
        {info ? null : (
          <Ionicons
            name={unavailable ? "lock-closed-outline" : "chevron-forward"}
            size={16}
            color={storeLight.text.muted}
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
        )}
      </Pressable>
    </Animated.View>
  );
}

export type StoreQuickLinkGridProps = {
  items: StoreQuickLinkTileProps[];
  /**
   * Applied to every tile, so callers pass it once rather than repeating it on
   * each item and risking one tile animating while its neighbour does not.
   */
  reducedMotion: boolean;
};

/**
 * Lays quick-link tiles out two per row, with rows of equal height.
 *
 * Takes a flat list rather than children so that the row count is derived from
 * the item count instead of being a composition the caller could get wrong. A
 * caller with four tiles gets two rows; a caller with five gets three, the last
 * holding one tile at half width rather than one stretched across the row —
 * matching the rest of the grid instead of drawing attention to the odd one.
 */
export function StoreQuickLinkGrid({ items, reducedMotion }: StoreQuickLinkGridProps) {
  const rows: StoreQuickLinkTileProps[][] = [];
  for (let index = 0; index < items.length; index += QUICK_LINK_TILES_PER_ROW) {
    rows.push(items.slice(index, index + QUICK_LINK_TILES_PER_ROW));
  }

  return (
    <View style={styles.grid}>
      {rows.map((row, rowIndex) => (
        <View key={`quick-link-row-${rowIndex}`} style={styles.row} testID="quick-link-row">
          {row.map((item, itemIndex) => (
            <StoreQuickLinkTile
              key={`${item.label}-${itemIndex}`}
              {...item}
              reducedMotion={reducedMotion}
            />
          ))}
          {/* A half-width spacer keeps the lone tile on an odd final row the
              same width as every other tile. Without it `flex: 1` would stretch
              it across the row and the grid would lose its rhythm on exactly
              the screens with an odd number of links. */}
          {row.length < QUICK_LINK_TILES_PER_ROW ? (
            <View style={styles.rowFiller} testID="quick-link-row-filler" />
          ) : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: { gap: 10 },
  /**
   * `alignItems: "stretch"` is what makes the two tiles in a row the same
   * height when one of them needs a second line of label and the other does
   * not. The hub revert recorded uneven card heights as one of its four
   * defects; this is the line that prevents it here.
   */
  row: { flexDirection: "row", alignItems: "stretch", gap: 10 },
  rowFiller: { flex: 1 },
  wrap: { flex: 1 },
  tile: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    minHeight: 64
  },
  tileDisabled: { opacity: 0.6 },
  body: { flex: 1, gap: 2 },
  label: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  muted: { color: storeLight.text.muted },
  subtitle: { fontSize: 11, color: storeLight.text.muted }
});
