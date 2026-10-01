/**
 * The layout gate for quick-link tiles.
 *
 * This file exists because the identical defect shipped three times. The hub
 * post-mortem (`docs/business_os/BUSINESS_HUB_REVERT.md`) wrote the lesson down
 * in prose — live text plus fixed geometry with no rule for what gives — and
 * prose did not stop Marketplace and Advertising rendering their quick links as
 * "S…", "M…", "A…" and "Cr…". So the rule moved into the component, and this is
 * the thing that fails when someone moves it back out.
 *
 * What is asserted here is deliberately structural rather than pixel-based.
 * Jest cannot measure text, so a test that claimed "the label is not clipped"
 * would be a test that measures nothing while sounding like it measures
 * everything. What it can prove is that the properties which make clipping
 * impossible are present: two tiles per row regardless of item count, a label
 * allowed to wrap, a ceiling on font growth, rows that stretch to their tallest
 * member, and a disabled state carried by a shape and by assistive-technology
 * copy rather than by opacity alone.
 *
 * The font-scale cases render at large scales because that is the condition the
 * previous layout was never tested against — the tiles were fine at 1.0 and the
 * screenshots that started this mission were not taken at 1.0.
 */
import React from "react";
import { PixelRatio, Text } from "react-native";
import { render } from "@testing-library/react-native";

import {
  StoreQuickLinkGrid,
  StoreQuickLinkTile,
  QUICK_LINK_LABEL_LINES,
  QUICK_LINK_SUBTITLE_LINES,
  QUICK_LINK_TILES_PER_ROW,
  QUICK_LINK_LABEL_MAX_FONT_SCALE,
  QUICK_LINK_SUBTITLE_MAX_FONT_SCALE
} from "../index";
import type { StoreQuickLinkTileProps } from "../index";

/**
 * The longest real labels on the three screens that use this grid, not invented
 * worst cases. "Creative library" is what rendered as "Cr…" in the screenshots.
 */
const REAL_LABELS = [
  "Inventory",
  "Collections",
  "Storefront",
  "Shipping",
  "Audiences",
  "Creative library",
  "Marketplace settings"
];

function tile(overrides: Partial<StoreQuickLinkTileProps> = {}): StoreQuickLinkTileProps {
  return {
    icon: "cube-outline",
    label: "Inventory",
    subtitle: "12 items · 3 low",
    onPress: jest.fn(),
    reducedMotion: true,
    ...overrides
  };
}

function tiles(count: number): StoreQuickLinkTileProps[] {
  return Array.from({ length: count }, (_, index) =>
    tile({ label: REAL_LABELS[index % REAL_LABELS.length] })
  );
}

/**
 * Drives the OS text-size setting for a single test. `maxFontSizeMultiplier` is
 * applied by React Native's own Text implementation, so raising the scale here
 * is what makes the ceiling assertions mean something rather than being a
 * restatement of the props object.
 */
function withFontScale(scale: number, body: () => void) {
  const spy = jest.spyOn(PixelRatio, "getFontScale").mockReturnValue(scale);
  try {
    body();
  } finally {
    spy.mockRestore();
  }
}

/** The single Text node whose content is exactly this string. */
function textNode(tree: ReturnType<typeof render>, value: string) {
  return tree.UNSAFE_getAllByType(Text).find((node) => node.props.children === value);
}

describe("StoreQuickLinkGrid row geometry", () => {
  /**
   * The regression itself. Four tiles in a wrapping row with `flex: 1` children
   * gave each about a quarter of the width; four tiles must now be two rows.
   * The grid takes a flat list precisely so that a caller cannot express four
   * across — there is no prop to pass and no children to compose.
   */
  it("puts two tiles in a row and never four, whatever the item count", () => {
    for (const count of [1, 2, 3, 4, 5, 6, 7]) {
      const tree = render(<StoreQuickLinkGrid items={tiles(count)} reducedMotion />);
      const rows = tree.getAllByTestId("quick-link-row");

      expect(rows).toHaveLength(Math.ceil(count / QUICK_LINK_TILES_PER_ROW));
      for (const row of rows) {
        const tilesInRow = row.findAllByType(StoreQuickLinkTile);
        expect(tilesInRow.length).toBeLessThanOrEqual(QUICK_LINK_TILES_PER_ROW);
      }
      tree.unmount();
    }
  });

  /**
   * An odd final tile keeps the width of every other tile. Without the filler,
   * `flex: 1` would stretch it across the whole row and the grid would lose its
   * rhythm on exactly the screens with an odd number of links.
   */
  it("pads an odd final row with a half-width filler rather than stretching the tile", () => {
    const odd = render(<StoreQuickLinkGrid items={tiles(5)} reducedMotion />);
    expect(odd.getAllByTestId("quick-link-row")).toHaveLength(3);
    expect(odd.getAllByTestId("quick-link-row-filler")).toHaveLength(1);
    odd.unmount();

    const even = render(<StoreQuickLinkGrid items={tiles(6)} reducedMotion />);
    expect(even.queryAllByTestId("quick-link-row-filler")).toHaveLength(0);
    even.unmount();
  });

  /**
   * Uneven card heights were one of the four defects recorded in the hub
   * revert. `alignItems: "stretch"` is the line that prevents it when one tile
   * in a row needs a second line of label and its neighbour does not — which is
   * the normal case once labels are allowed to wrap.
   */
  it("stretches both tiles in a row to the height of the taller one", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[tile({ label: "Inventory" }), tile({ label: "Marketplace settings" })]}
        reducedMotion
      />
    );

    const [row] = tree.getAllByTestId("quick-link-row");
    const style = Object.assign({}, ...[row.props.style].flat().filter(Boolean));
    expect(style.alignItems).toBe("stretch");
    expect(style.flexDirection).toBe("row");
  });
});

describe("StoreQuickLinkTile text under Dynamic Type", () => {
  /**
   * One line was what produced "Busine…" on the hub and "S…" on Marketplace.
   * Wrapping to two lines and ellipsising at a word boundary is the rule; this
   * asserts the props that carry it, at a scale where they matter.
   */
  it("wraps the label instead of clipping it, at every font scale", () => {
    for (const scale of [1, 1.5, 2, 3]) {
      withFontScale(scale, () => {
        const tree = render(
          <StoreQuickLinkGrid items={[tile({ label: "Creative library" })]} reducedMotion />
        );

        const label = textNode(tree, "Creative library");
        expect(label).toBeTruthy();
        expect(label!.props.numberOfLines).toBe(QUICK_LINK_LABEL_LINES);
        expect(label!.props.ellipsizeMode).toBe("tail");
        tree.unmount();
      });
    }
  });

  /**
   * The ceilings are not 1. Refusing to grow the text at all would ignore the
   * OS setting, which is its own accessibility failure; these values are the
   * point at which two lines of label still fit a half-width tile on the
   * narrowest supported device.
   */
  it("caps how far label and subtitle grow, without pinning them to 1", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[tile({ label: "Creative library", subtitle: "8 assets · 2 in review" })]}
        reducedMotion
      />
    );

    expect(textNode(tree, "Creative library")!.props.maxFontSizeMultiplier).toBe(
      QUICK_LINK_LABEL_MAX_FONT_SCALE
    );
    expect(textNode(tree, "8 assets · 2 in review")!.props.maxFontSizeMultiplier).toBe(
      QUICK_LINK_SUBTITLE_MAX_FONT_SCALE
    );

    expect(QUICK_LINK_LABEL_MAX_FONT_SCALE).toBeGreaterThan(1);
    expect(QUICK_LINK_SUBTITLE_MAX_FONT_SCALE).toBeGreaterThan(1);
    // The subtitle is the line that gives first — it is status, the label is identity.
    expect(QUICK_LINK_SUBTITLE_MAX_FONT_SCALE).toBeLessThanOrEqual(
      QUICK_LINK_LABEL_MAX_FONT_SCALE
    );
  });

  it("gives the subtitle the same wrapping treatment as the label", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[tile({ subtitle: "Not available in the app yet" })]}
        reducedMotion
      />
    );

    const subtitle = textNode(tree, "Not available in the app yet");
    expect(subtitle!.props.numberOfLines).toBe(QUICK_LINK_SUBTITLE_LINES);
    expect(subtitle!.props.ellipsizeMode).toBe("tail");
  });
});

describe("StoreQuickLinkTile unavailable state", () => {
  /**
   * Reduced opacity on truncated grey text is indistinguishable from a
   * rendering fault, and it is invisible to anyone who cannot perceive the
   * contrast difference. The unavailable state has to be carried by a shape and
   * by words, not only by alpha.
   */
  it("marks an unavailable tile with a lock, not only with opacity", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[
          tile({
            label: "Shipping",
            subtitle: "Not available in the app yet",
            onPress: undefined,
            disabled: true
          })
        ]}
        reducedMotion
      />
    );

    expect(tree.UNSAFE_queryAllByProps({ name: "lock-closed-outline" }).length).toBeGreaterThan(0);
    expect(tree.UNSAFE_queryAllByProps({ name: "chevron-forward" })).toHaveLength(0);
  });

  it("says 'Unavailable' to assistive technology and reports the disabled state", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[
          tile({
            label: "Shipping",
            subtitle: "Not available in the app yet",
            onPress: undefined,
            disabled: true
          })
        ]}
        reducedMotion
      />
    );

    const button = tree.getByRole("button");
    expect(button.props.accessibilityLabel).toContain("Shipping");
    expect(button.props.accessibilityLabel).toContain("Unavailable");
    expect(button.props.accessibilityLabel).toContain("Not available in the app yet");
    expect(button.props.accessibilityState.disabled).toBe(true);
  });

  /**
   * An available tile carries its live status into the accessibility label —
   * the subtitle is "12 items · 3 low", not "Manage your inventory", and a
   * screen-reader user should hear the same saving of a tap that a sighted user
   * gets.
   */
  it("reads an available tile as label plus live status, with no 'Unavailable'", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[tile({ label: "Inventory", subtitle: "12 items · 3 low" })]}
        reducedMotion
      />
    );

    const button = tree.getByRole("button");
    expect(button.props.accessibilityLabel).toBe("Inventory. 12 items · 3 low");
    expect(button.props.accessibilityState.disabled).toBe(false);
    expect(tree.UNSAFE_queryAllByProps({ name: "lock-closed-outline" })).toHaveLength(0);
  });

  /**
   * A tile with no destination is unavailable even if the caller forgot to say
   * so. `Pressable` folds its own `disabled` into `accessibilityState`, so
   * before this the tile was announced as disabled while its label read like a
   * working link and its chevron promised a screen that does not exist. The
   * three signals — visual, spoken, touch — now come from one derivation, so
   * they cannot disagree.
   */
  it("treats a tile with no destination as unavailable in all three signals", () => {
    const tree = render(
      <StoreQuickLinkGrid
        items={[tile({ label: "Audiences", subtitle: "Not available yet", onPress: undefined })]}
        reducedMotion
      />
    );

    const button = tree.getByRole("button");
    expect(button.props.accessibilityState.disabled).toBe(true);
    expect(button.props.accessibilityLabel).toBe("Audiences. Unavailable. Not available yet");
    expect(tree.UNSAFE_queryAllByProps({ name: "lock-closed-outline" }).length).toBeGreaterThan(0);
  });
});
