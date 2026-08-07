/**
 * §31 on the Advertising surfaces: no component may render the universal dash.
 *
 * These tests are written against the *reader*, not against the implementation.
 * Each one asks the question a person would ask looking at the screen — "is this
 * balance zero or is it still loading?", "did this fail or has nothing happened
 * yet?" — and asserts that the answer is legible without knowing which code path
 * produced it.
 *
 * The dash is asserted by importing `LEGACY_ABSENT_TEXT` rather than by pasting
 * the character. An em dash and a hyphen are visually near-identical in a diff,
 * and a test that silently checks for the wrong one of them passes forever.
 *
 * The three cases that used to be indistinguishable, and now are not:
 *
 *   * loading      — a request is in flight, resolves on its own, do nothing
 *   * unavailable  — a request failed, retry
 *   * zero         — the answer arrived and is zero, which for money means
 *                    delivery stops
 *
 * The last one is why this matters most on the wallet. A dash standing in for
 * "loading" on a funded account and a dash standing in for "$0.00" on an empty
 * one are the same glyph in the same slot, and only one of them means the
 * advertiser's campaigns are about to stop.
 */

import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

// The real icon set loads its font asynchronously and setStates after the test
// has finished, which fills the run with act() warnings that have nothing to do
// with what is being asserted. Same stub the screen suites use.
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

import { LEGACY_ABSENT_TEXT, absentValueText } from "../../../api/stateLanguage";
import { WalletChip } from "../WalletChip";
import { AdsWalletUnavailable } from "../AdsStates";
import { PromotedPostCard } from "../PromotedPostCard";

/** Every string this tree renders, flattened. */
function renderedText(tree: ReturnType<typeof render>): string[] {
  return tree.root
    .findAllByType(require("react-native").Text)
    .flatMap((node: { props: { children?: unknown } }) => {
      const kids = node.props.children;
      const flat = Array.isArray(kids) ? kids : [kids];
      return flat.filter((kid): kid is string => typeof kid === "string");
    });
}

/** Every accessibilityLabel in the tree. */
function spokenText(tree: ReturnType<typeof render>): string[] {
  return tree.root
    .findAll((node) => typeof node.props?.accessibilityLabel === "string")
    .map((node) => String(node.props.accessibilityLabel));
}

describe("the ad wallet chip", () => {
  function chip(props: Partial<React.ComponentProps<typeof WalletChip>> = {}) {
    return render(
      <WalletChip
        balanceLabel="$142.00"
        fundingLive={false}
        onPress={jest.fn()}
        reducedMotion
        {...props}
      />
    );
  }

  it("shows the balance the server sent, untouched", () => {
    expect(renderedText(chip())).toContain("$142.00");
  });

  it("never renders the dash, in any state", () => {
    for (const props of [
      {},
      { loading: true, balanceLabel: null },
      { balanceLabel: null },
      { balanceLabel: "$0.00" }
    ]) {
      expect(renderedText(chip(props))).not.toContain(LEGACY_ABSENT_TEXT);
    }
  });

  it("says it is checking rather than showing a placeholder", () => {
    const text = renderedText(chip({ loading: true, balanceLabel: null }));
    expect(text).toContain(absentValueText("loading"));
  });

  it("says a failed balance failed, not that it is still coming", () => {
    const text = renderedText(chip({ loading: false, balanceLabel: null }));
    expect(text).toContain(absentValueText("unavailable"));
    expect(text).not.toContain(absentValueText("loading"));
  });

  /*
   * The defect this whole phase is named after. The chip's accessibility label
   * already said "loading balance" while the visible text said "—", so the two
   * readers were being told different things and only one of them was true.
   */
  it("tells the sighted reader and the screen-reader reader the same thing", () => {
    const loading = chip({ loading: true, balanceLabel: null });
    expect(renderedText(loading).join(" ")).toMatch(/checking/i);
    expect(spokenText(loading).join(" ")).toMatch(/checking/i);

    const failed = chip({ loading: false, balanceLabel: null });
    expect(renderedText(failed).join(" ")).toMatch(/couldn.t load/i);
    expect(spokenText(failed).join(" ")).toMatch(/couldn.t load/i);
  });

  /*
   * A zero balance is a real answer and a consequential one — it is the state in
   * which nothing delivers. It must not be softened into "None yet" or shared
   * with the loading wording.
   */
  it("keeps a real zero balance looking like money", () => {
    const text = renderedText(chip({ balanceLabel: "$0.00" }));
    expect(text).toContain("$0.00");
    expect(text).not.toContain(absentValueText("loading"));
    expect(text).not.toContain(absentValueText("unavailable"));
  });

  it("still offers a way into the wallet when the balance is unknown", () => {
    // §37: an empty state with no destination is a dead end. Not knowing the
    // balance is not a reason to take away the wallet.
    expect(renderedText(chip({ loading: true, balanceLabel: null }))).toContain("Wallet");
  });
});

describe("the wallet's failure state", () => {
  it("names the failure instead of drawing a dash", () => {
    const tree = render(<AdsWalletUnavailable onRetry={jest.fn()} reducedMotion />);
    const text = renderedText(tree);
    expect(text).not.toContain(LEGACY_ABSENT_TEXT);
    expect(text).toContain(absentValueText("unavailable"));
    expect(text).toContain("Retry");
  });

  it("says the same thing aloud that it says on screen", () => {
    const tree = render(<AdsWalletUnavailable onRetry={jest.fn()} reducedMotion />);
    expect(spokenText(tree).join(" ")).toMatch(/couldn.t load/i);
  });
});

describe("the promoted-post metric strip", () => {
  function card(props: Partial<React.ComponentProps<typeof PromotedPostCard>> = {}) {
    return render(
      <PromotedPostCard
        contentType="post"
        title="Summer drop"
        phase="promoting"
        phaseLabel="Promoting"
        phaseTone="success"
        onPress={jest.fn()}
        reducedMotion
        {...props}
      />
    );
  }

  it("renders no dash for a promotion that has not delivered", () => {
    const tree = card({
      metrics: [
        { key: "reach", label: "Reach", value: absentValueText("no_activity") },
        { key: "cpm", label: "Cost per 1k views", value: absentValueText("no_activity") }
      ]
    });
    const text = renderedText(tree);
    expect(text).not.toContain(LEGACY_ABSENT_TEXT);
    expect(text).toContain("None yet");
  });

  /*
   * A cell is a claim that the thing above the label was measured. Likes and
   * follows have no source anywhere in the product, so they are described in a
   * sentence rather than given a cell with a placeholder under the word — the
   * same finding as conversions in Phase 4, and the same answer.
   */
  it("states an unmeasured metric in prose rather than giving it a cell", () => {
    const note = "Likes and follows from a promotion aren’t tracked.";
    const tree = card({
      metrics: [{ key: "reach", label: "Reach", value: "12.4k" }],
      metricsNote: note
    });
    const text = renderedText(tree);
    expect(text).toContain(note);
    expect(text).not.toContain("Likes");
    expect(text).not.toContain("Follows");
  });

  it("does not show a note about a strip that is not there", () => {
    // On its own the note is a disclaimer about numbers the reader cannot see,
    // which reads as a fault rather than a scope statement.
    const note = "Likes and follows from a promotion aren’t tracked.";
    expect(renderedText(card({ metrics: [], metricsNote: note }))).not.toContain(note);
  });

  it("gives a screen reader the same value the eye gets", () => {
    const tree = card({
      metrics: [{ key: "reach", label: "Reach", value: absentValueText("no_activity") }]
    });
    // Not "not yet available" while the eye sees something else — one sentence,
    // both readers.
    expect(spokenText(tree)).toContain("Reach, None yet");
  });
});
