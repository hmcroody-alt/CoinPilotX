/**
 * The layout and honesty gate for the setup checklist.
 *
 * The card exists because the Store screen used to imply readiness it could not
 * back up. Having replaced one dishonest sentence, the replacement must not
 * introduce three of its own — a button that opens nothing, a step whose text
 * is clipped to "Take a listing out of dra…", or a done/to-do distinction that
 * only exists in colour.
 *
 * As with `StoreQuickLinkTile.test.tsx`, the Dynamic Type assertions here are
 * structural. Jest cannot measure text, so a test claiming "the label is not
 * clipped" would measure nothing while sounding authoritative. What it can
 * prove is that the properties which make clipping impossible are present, and
 * it renders them at scales the original screenshots were never taken at.
 */
import React from "react";
import { PixelRatio, Pressable, Text } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

import {
  CHECKLIST_DETAIL_MAX_FONT_SCALE,
  CHECKLIST_LABEL_MAX_FONT_SCALE,
  StoreSetupChecklist
} from "../index";
import { storeReadiness, type StoreSetupStep } from "../../../api/storeDashboard";
import type { MarketplaceListing, SellerStoreSnapshot } from "../../../api/marketplace";
import { deriveRows } from "../../../api/storeDashboard";

const NOW = new Date(2026, 6, 15, 10, 30);

function listing(over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 1,
    listing_id: 1,
    title: "Bright Coffee Beans",
    price_label: "12.00",
    currency: "USD",
    quantity: 20,
    status: "active",
    approval_status: "approved",
    ...over
  } as MarketplaceListing;
}

/**
 * The real derivation, not a hand-written step list. A fixture that invents its
 * own steps would keep passing after the ladder stopped producing them.
 */
function readinessOf(listings: MarketplaceListing[]) {
  const snapshot = { listings, orders: [] } as unknown as SellerStoreSnapshot;
  return storeReadiness({ listings, rows: deriveRows(snapshot, NOW) });
}

function renderChecklist(
  listings: MarketplaceListing[],
  onStepAction: (step: StoreSetupStep) => void = jest.fn()
) {
  const state = readinessOf(listings);
  const tree = render(
    <StoreSetupChecklist
      headline={state.headline}
      steps={state.steps}
      remaining={state.remaining}
      onStepAction={onStepAction}
      reducedMotion
    />
  );
  return { tree, state };
}

function withFontScale(scale: number, body: () => void) {
  const spy = jest.spyOn(PixelRatio, "getFontScale").mockReturnValue(scale);
  try {
    body();
  } finally {
    spy.mockRestore();
  }
}

/** Every Text node in the tree, as raw strings. */
function allText(tree: ReturnType<typeof render>): string[] {
  return tree.UNSAFE_getAllByType(Text)
    .map((node) => node.props.children)
    .filter((value): value is string => typeof value === "string");
}

describe("the checklist under Dynamic Type", () => {
  /**
   * The rule that differs from the quick-link tiles, and differs on purpose. A
   * tile's label is one or two words and truncating it at three lines still
   * leaves it recognisable. A checklist step is an instruction, and half an
   * instruction is not a shorter instruction — it is an unanswerable one. So
   * the card grows and the page scrolls.
   */
  it("never truncates a step, at any font scale", () => {
    for (const scale of [1, 1.5, 2, 3]) {
      withFontScale(scale, () => {
        const { tree } = renderChecklist([]);
        for (const node of tree.UNSAFE_getAllByType(Text)) {
          expect(node.props.numberOfLines).toBeUndefined();
        }
        tree.unmount();
      });
    }
  });

  it("caps text growth without pinning it to 1", () => {
    const { tree } = renderChecklist([]);
    const label = tree.UNSAFE_getAllByType(Text).find((node) => node.props.children === "Add a listing");
    expect(label!.props.maxFontSizeMultiplier).toBe(CHECKLIST_LABEL_MAX_FONT_SCALE);

    expect(CHECKLIST_LABEL_MAX_FONT_SCALE).toBeGreaterThan(1);
    expect(CHECKLIST_DETAIL_MAX_FONT_SCALE).toBeGreaterThan(1);
    // Detail is supporting text, so it is the line that gives first.
    expect(CHECKLIST_DETAIL_MAX_FONT_SCALE).toBeLessThanOrEqual(CHECKLIST_LABEL_MAX_FONT_SCALE);
  });

  /**
   * Every sentence the card writes, not merely the two the other tests reach
   * for. A step label added later with no ceiling would grow without bound and
   * fail here rather than in somebody's screenshot.
   */
  it("puts a ceiling on every sentence it renders", () => {
    withFontScale(3, () => {
      const { tree, state } = renderChecklist([listing({ status: "draft" })]);
      const expected = [
        state.headline,
        ...state.steps.flatMap((step) => [step.label, step.detail]),
        ...state.steps.map((step) => step.action?.label).filter(Boolean)
      ];
      const nodes = tree.UNSAFE_getAllByType(Text);
      for (const value of expected) {
        const node = nodes.find((candidate) => candidate.props.children === value);
        expect(node).toBeTruthy();
        expect(typeof node!.props.maxFontSizeMultiplier).toBe("number");
      }
    });
  });

  /**
   * At three lines of wrapped label a centred tick floats in the middle of the
   * paragraph rather than sitting beside the thing it marks.
   */
  it("aligns each row from the top so the marker stays beside its first line", () => {
    const { tree } = renderChecklist([]);
    const row = tree.getByTestId("store-setup-step-add");
    const style = Object.assign({}, ...[row.props.style].flat().filter(Boolean));
    expect(style.alignItems).toBe("flex-start");
    expect(style.flexDirection).toBe("row");
  });

  /** A 44pt target survives the text growing; a text-height one does not. */
  it("keeps every step button at the minimum tap size", () => {
    const { tree } = renderChecklist([listing({ status: "draft" })]);
    const buttons = tree.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      const style = Object.assign({}, ...[button.props.style].flat().filter(Boolean));
      expect(style.minHeight).toBeGreaterThanOrEqual(44);
    }
  });
});

describe("no dead controls", () => {
  /**
   * The defect this whole tier exists to remove. `storeReadiness` returns
   * `action: null` for a completed step and for the review step; a row that
   * rendered a button anyway would be a control that does nothing.
   */
  it("renders exactly one button per step that has an action, and none otherwise", () => {
    const cases: MarketplaceListing[][] = [
      [],
      [listing({ status: "draft" })],
      [listing({ status: "active", quantity: 0 })],
      [listing({ status: "active", approval_status: "pending" })],
      [listing({ status: "active", quantity: 40 })]
    ];
    for (const listings of cases) {
      const { tree, state } = renderChecklist(listings);
      const expected = state.steps.filter((step) => step.action).length;
      expect(tree.queryAllByRole("button")).toHaveLength(expected);
      tree.unmount();
    }
  });

  it("shows no button at all once the store is live", () => {
    const { tree, state } = renderChecklist([listing({ status: "active", quantity: 40 })]);
    expect(state.remaining).toBe(0);
    expect(tree.queryAllByRole("button")).toHaveLength(0);
  });

  /**
   * Waiting on a review is the one outstanding step with nothing to press, and
   * the copy has to say that rather than leaving a gap where a button was.
   */
  it("leaves the review step without a button and says why in its detail", () => {
    const { tree } = renderChecklist([listing({ status: "active", approval_status: "pending" })]);
    const review = tree.getByTestId("store-setup-step-review");
    expect(review.findAllByType(Pressable)).toHaveLength(0);
    expect(allText(tree).join(" ")).toMatch(/nothing is needed from you/i);
  });

  it("hands the pressed step back to the caller so the screen decides where it goes", () => {
    const onStepAction = jest.fn();
    const { tree } = renderChecklist([], onStepAction);
    fireEvent.press(tree.getByRole("button"));
    expect(onStepAction).toHaveBeenCalledTimes(1);
    expect(onStepAction.mock.calls[0][0].key).toBe("add");
    expect(onStepAction.mock.calls[0][0].action.key).toBe("add_listing");
  });
});

describe("state is not carried by colour alone", () => {
  /**
   * A tick and a ring are different shapes. The same shape in two tones would
   * be invisible to a reader who cannot tell the tones apart, and the checklist
   * would then be a list of identical rows.
   */
  it("uses a different glyph for done and to do, not the same glyph in two colours", () => {
    const { tree } = renderChecklist([listing({ status: "draft" })]);
    expect(tree.UNSAFE_queryAllByProps({ name: "checkmark-circle" }).length).toBeGreaterThan(0);
    expect(tree.UNSAFE_queryAllByProps({ name: "ellipse-outline" }).length).toBeGreaterThan(0);
  });

  it("speaks the state of every step out loud", () => {
    const { tree, state } = renderChecklist([listing({ status: "draft" })]);
    for (const step of state.steps) {
      const row = tree.getByTestId(`store-setup-step-${step.key}`);
      const body: { props: Record<string, unknown> }[] = row.findAll(
        (node: { props: Record<string, unknown> }) =>
          typeof node.props.accessibilityLabel === "string" && node.props.accessible === true
      );
      const spoken = body.map((node) => String(node.props.accessibilityLabel)).join(" | ");
      expect(spoken).toContain(step.complete ? "Done" : "To do");
      expect(spoken).toContain(step.label);
    }
  });

  /**
   * A drafts-only store shows two buttons at once, which is exactly why this
   * matters: "Open drafts" and "Open out of stock" read identically out of
   * context, so each has to carry the step it belongs to.
   */
  it("names the step a button belongs to in its spoken label", () => {
    const { tree, state } = renderChecklist([listing({ status: "draft" })]);
    const buttons = tree.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(1);
    for (const step of state.steps) {
      if (!step.action) continue;
      const spoken = buttons
        .map((button) => String(button.props.accessibilityLabel))
        .find((label) => label.startsWith(step.action!.label));
      expect(spoken).toContain(step.label);
    }
  });
});

describe("what the card says", () => {
  it("counts the remaining steps in words rather than leaving the reader to add up ticks", () => {
    expect(allText(renderChecklist([]).tree).join(" ")).toMatch(/steps left/);
    const oneLeft = renderChecklist([listing({ status: "active", quantity: 0 })]);
    expect(allText(oneLeft.tree).join(" ")).toContain("1 step left.");
  });

  it("renders the headline it was handed and does not assemble one of its own", () => {
    const { tree, state } = renderChecklist([]);
    expect(tree.getByTestId("store-setup-headline").props.children).toBe(state.headline);
  });

  /** The dash meant four things; none of them belongs in an instruction. */
  it("renders no em dash anywhere in the card", () => {
    for (const listings of [[], [listing({ status: "draft" })], [listing({ status: "active", quantity: 0 })]]) {
      const { tree } = renderChecklist(listings);
      expect(allText(tree).join(" ")).not.toContain("—");
      tree.unmount();
    }
  });
});
