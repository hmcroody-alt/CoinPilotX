/**
 * The row indicator has one job and two ways to get it wrong.
 *
 * The first is drawing furniture. Market Pulse is fifty rows scanned at a
 * glance, and a "no signal" chip in every row that lacks analysis is fifty
 * pieces of nothing — worse, it makes an analysis outage look like a market
 * condition. So no analysis means no badge at all.
 *
 * The second is inventing colour. Tone is chosen on the server beside the state,
 * and a client that mapped state to colour itself would render a new negative
 * state green the day it was added, by falling through to a default. These tests
 * pin the direction of that dependency.
 */

import renderer, { act } from "react-test-renderer";

import { AssetIntelligence } from "../../api/marketIntelligence";
import { colors } from "../../theme/colors";
import { IntelligenceBadge, riskColor, toneColor } from "../crypto/IntelligenceBadge";

function verdict(overrides: Partial<AssetIntelligence> = {}): AssetIntelligence {
  return {
    symbol: "TEST",
    action: {
      state: "WAIT_FOR_PULLBACK",
      label: "Wait for pullback",
      tone: "caution",
      perspective: "non_holder",
      reasons: [],
      confidence: "KNOWN"
    },
    opportunity: { score: 93, band: "HIGH", confidence: "KNOWN" },
    entry: { score: 39, band: "LOW", confidence: "KNOWN" },
    risk: { level: "HIGH", confidence: "KNOWN" },
    dataQuality: { level: "FULL", note: "", points: 168 },
    disclaimer: "",
    why: [],
    ...overrides
  } as AssetIntelligence;
}

/**
 * Render inside `act` so the committed tree is the one we assert on.
 * Without it `toJSON()` reads before the commit lands and every badge looks
 * empty — which would make these tests pass for a broken component and fail
 * for a working one.
 */
function render(intelligence: AssetIntelligence | null): renderer.ReactTestRenderer {
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(<IntelligenceBadge intelligence={intelligence} />);
  });
  return tree;
}

/** Every string the badge actually put on screen, in order. */
function textOf(tree: renderer.ReactTestRenderer): string {
  const out: string[] = [];
  const walk = (node: unknown): void => {
    if (typeof node === "string") {
      out.push(node);
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node && typeof node === "object" && "children" in node) {
      walk((node as { children: unknown }).children);
    }
  };
  walk(tree.toJSON());
  return out.join(" ");
}

describe("a row the server could not analyse", () => {
  it("renders nothing at all rather than a placeholder opinion", () => {
    expect(render(null).toJSON()).toBeNull();
  });

  it("renders nothing when a verdict arrived without a state", () => {
    const empty = verdict({ action: { ...verdict().action, state: null as never } });
    expect(render(empty).toJSON()).toBeNull();
  });
});

describe("a row with a verdict", () => {
  it("shows the label the server wrote, not one the client composed", () => {
    const tree = render(verdict());
    expect(textOf(tree)).toContain("Wait for pullback");
  });

  it("keeps opportunity and entry as two numbers, because they disagree on purpose", () => {
    const tree = render(verdict());
    const line = textOf(tree);
    expect(line).toContain("93");
    expect(line).toContain("39");
  });

  it("draws an unscored reading as -- rather than dropping the row's second line", () => {
    const partial = verdict({ entry: { score: null, band: null, confidence: "UNAVAILABLE" } });
    const line = textOf(render(partial));
    expect(line).toContain("93");
    expect(line).toContain("--");
  });

  it("omits the score line entirely when neither score exists", () => {
    const none = verdict({
      opportunity: { score: null, band: null, confidence: "UNAVAILABLE" },
      entry: { score: null, band: null, confidence: "UNAVAILABLE" }
    });
    const line = textOf(render(none));
    expect(line).not.toContain("--");
  });
});

describe("colour", () => {
  it("follows the tone the server chose", () => {
    expect(toneColor("positive")).toBe(colors.accent);
    expect(toneColor("negative")).toBe(colors.danger);
    expect(toneColor("caution")).toBe(colors.warning);
  });

  it("treats an unknown tone as neutral text rather than as approval", () => {
    expect(toneColor("brand_new_tone" as never)).toBe(colors.text);
  });

  it("reads muted as absence, in the same grey the rest of the app uses for unknown", () => {
    expect(toneColor("muted")).toBe(colors.muted);
  });

  it("does not colour an unmeasured risk level as safe", () => {
    expect(riskColor(null)).toBe(colors.muted);
    expect(riskColor(null)).not.toBe(colors.accent);
  });

  it("gives EXTREME the same weight as HIGH rather than falling through", () => {
    expect(riskColor("EXTREME")).toBe(colors.danger);
    expect(riskColor("HIGH")).toBe(colors.danger);
  });
});
