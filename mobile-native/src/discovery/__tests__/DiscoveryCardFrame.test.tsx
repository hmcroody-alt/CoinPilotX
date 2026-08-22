/**
 * §7–§9, §14.10, §14.11, §14.12 — the living border, and when it stops living.
 *
 * ## Why these assert on presence, not on style
 *
 * "Is this card animating" cannot be answered from a style object. A looping
 * animation whose interpolated output happens to be invisible passes every
 * opacity and transform assertion you can write, and that is exactly the failure
 * mode that matters here: §8 forbids animating every card in a carousel, and the
 * cost of a violation is GPU work, not a wrong pixel. So the component removes
 * the animated layer from the tree entirely when it should not be running, and
 * these tests assert the layer is *absent*. The `-sweep` testID is the honest
 * signal, and it is honest precisely because it cannot be faked by a style.
 *
 * The same reasoning covers Reduce Motion: the still gradient ring stays, so §9's
 * hierarchy survives, but the travelling layer is gone rather than paused.
 */
import { render } from "@testing-library/react-native";
import { Text } from "react-native";
import { DiscoveryCardFrame } from "../DiscoveryCardFrame";

const mockReducedMotion = jest.fn(() => false);

jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));

function renderFrame(energy: "idle" | "focused" | "playing") {
  return render(
    <DiscoveryCardFrame energy={energy} width={300} height={430} radius={18} testID="frame">
      <Text>card</Text>
    </DiscoveryCardFrame>
  );
}

beforeEach(() => {
  mockReducedMotion.mockReturnValue(false);
});

describe("§14.11 — the active card's border animates", () => {
  it("runs the travelling sweep while the card is playing a preview", () => {
    const { getByTestId } = renderFrame("playing");

    expect(getByTestId("frame-sweep")).toBeTruthy();
  });

  it("runs it for the focused card too, so the card being looked at is alive", () => {
    const { getByTestId } = renderFrame("focused");

    expect(getByTestId("frame-sweep")).toBeTruthy();
  });

  it("makes the playing state visibly stronger than merely focused", () => {
    // §8 asks for three distinguishable states, not two. If playing and focused
    // rendered identically the border would stop meaning anything.
    const playing = renderFrame("playing").getByTestId("frame-sweep").props.style;
    const focused = renderFrame("focused").getByTestId("frame-sweep").props.style;

    expect(playing.opacity).toBeGreaterThan(focused.opacity);
  });

  it("carries a stronger glow while playing", () => {
    const playing = renderFrame("playing").getByTestId("frame").props.style;
    const focused = renderFrame("focused").getByTestId("frame").props.style;
    const flat = (style: unknown) =>
      Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : (style as Record<string, number>);

    expect(flat(playing).shadowOpacity).toBeGreaterThan(flat(focused).shadowOpacity);
  });

  it("animates one transform, not one animation per edge", () => {
    // Four animated edges is four times the work for the same effect, and it is
    // the construction that makes a carousel of these expensive.
    const sweep = renderFrame("playing").getByTestId("frame-sweep");

    expect(sweep.props.style.transform).toHaveLength(1);
  });
});

describe("§14.12 — an offscreen card's border animation stops", () => {
  it("removes the travelling layer entirely for an idle card", () => {
    // Not "paused". Removed. A paused Animated loop still exists, and a style
    // assertion could not tell the difference.
    const { queryByTestId } = renderFrame("idle");

    expect(queryByTestId("frame-sweep")).toBeNull();
  });

  it("removes the whole lit ring for an idle card", () => {
    const { queryByTestId } = renderFrame("idle");

    expect(queryByTestId("frame-ring")).toBeNull();
  });

  it("drops the glow for an idle card", () => {
    const style = renderFrame("idle").getByTestId("frame").props.style;
    const flat = Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : style;

    expect(flat.shadowOpacity).toBe(0);
  });

  it("still renders its children, because idle is a resting state and not a hidden one", () => {
    const { getByText } = renderFrame("idle");

    expect(getByText("card")).toBeTruthy();
  });
});

describe("§14.10 — Reduce Motion disables the animated border", () => {
  it("stops the travelling sweep on a playing card", () => {
    mockReducedMotion.mockReturnValue(true);

    const { queryByTestId } = renderFrame("playing");

    expect(queryByTestId("frame-sweep")).toBeNull();
  });

  it("stops it on a focused card too", () => {
    mockReducedMotion.mockReturnValue(true);

    const { queryByTestId } = renderFrame("focused");

    expect(queryByTestId("frame-sweep")).toBeNull();
  });

  it("keeps the static premium gradient ring, so §9's hierarchy survives", () => {
    // Reduce Motion removes motion, not meaning. A user who turns it on should
    // still be able to tell which card is the active one.
    mockReducedMotion.mockReturnValue(true);

    const { getByTestId } = renderFrame("playing");

    expect(getByTestId("frame-ring")).toBeTruthy();
  });

  it("still distinguishes playing from focused without any motion at all", () => {
    mockReducedMotion.mockReturnValue(true);

    const playingGlow = renderFrame("playing").getByTestId("frame").props.style;
    const focusedGlow = renderFrame("focused").getByTestId("frame").props.style;
    const flat = (style: unknown) =>
      Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : (style as Record<string, number>);

    expect(flat(playingGlow).shadowOpacity).toBeGreaterThan(flat(focusedGlow).shadowOpacity);
  });

  it("leaves an idle card with no ring under Reduce Motion either", () => {
    mockReducedMotion.mockReturnValue(true);

    const { queryByTestId } = renderFrame("idle");

    expect(queryByTestId("frame-ring")).toBeNull();
    expect(queryByTestId("frame-sweep")).toBeNull();
  });
});

describe("§7 — the border is a hairline, not a selection box", () => {
  it("keeps the ring thin enough to read as premium rather than as a highlight", () => {
    const style = renderFrame("playing").getByTestId("frame").props.style;
    const flat = Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : style;

    expect(flat.padding).toBeLessThanOrEqual(2);
  });

  it("oversizes the rotating square enough to cover the corners through a full turn", () => {
    // A rotating square must exceed the card's diagonal or a corner goes dark
    // mid-rotation — a flicker that only appears at certain angles and is
    // therefore very easy to ship.
    const sweep = renderFrame("playing").getByTestId("frame-sweep");
    const diagonal = Math.sqrt(300 * 300 + 430 * 430);

    expect(sweep.props.style.width).toBeGreaterThanOrEqual(430);
    expect(sweep.props.style.width).toBeGreaterThan(diagonal * 0.99);
  });
});
