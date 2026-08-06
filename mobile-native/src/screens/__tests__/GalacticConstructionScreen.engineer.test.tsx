// @testing-library/react-native rather than react-test-renderer directly:
// react-test-renderer ships no type declarations and @types/react-test-renderer
// is not a dependency here, so importing it puts a permanent TS7016 in `tsc
// --noEmit`. The library re-exports act() and wraps the same renderer.
import { act, render as mount, RenderResult } from "@testing-library/react-native";
import { AccessibilityInfo, Text } from "react-native";
import * as Haptics from "expo-haptics";
import { GalacticConstructionScreen } from "../GalacticConstructionScreen";
import { AuthContext, stateFor } from "../../session/auth";
import { EngineerAccessModal } from "../../components/engineer/EngineerAccessModal";

type TestInstance = RenderResult["UNSAFE_root"];

jest.mock("expo-haptics", () => ({
  notificationAsync: jest.fn(() => Promise.resolve()),
  impactAsync: jest.fn(() => Promise.resolve()),
  NotificationFeedbackType: { Success: "success", Error: "error" },
  ImpactFeedbackStyle: { Medium: "medium", Light: "light", Heavy: "heavy" }
}));

jest.mock("../../components/GalacticAtmosphere", () => ({
  GalacticAtmosphere: () => null
}));

jest.mock("../../components/engineer/EngineerAccessModal", () => ({
  EngineerAccessModal: jest.fn(() => null)
}));

jest.mock("react-native-safe-area-context", () => {
  const { View } = require("react-native");
  return { SafeAreaView: View };
});

const MockedModal = EngineerAccessModal as unknown as jest.Mock;

function findByTestId(tree: RenderResult, id: string): TestInstance {
  return tree.UNSAFE_root.findAll((n) => n.props?.testID === id && typeof n.type !== "string")[0];
}

function allText(tree: RenderResult): string {
  return tree.UNSAFE_root
    .findAllByType(Text)
    .map((n) => (Array.isArray(n.props.children) ? n.props.children.join(" ") : String(n.props.children ?? "")))
    .join(" | ");
}

async function render(overrides: { onReturn?: jest.Mock; onEngineerAccessGranted?: jest.Mock } = {}) {
  const onReturn = overrides.onReturn || jest.fn();
  const onEngineerAccessGranted = overrides.onEngineerAccessGranted || jest.fn();
  // mount() outside act(): RNTL probes for host component names on first render
  // by mounting and unmounting, which throws if it happens inside an outer act.
  const tree = mount(
    <AuthContext.Provider
      value={{
        authState: stateFor("AUTHENTICATED", { user_id: 4242 } as never),
        setAuthState: jest.fn(),
        requestReauthentication: jest.fn()
      }}
    >
      <GalacticConstructionScreen onReturn={onReturn} onEngineerAccessGranted={onEngineerAccessGranted} />
    </AuthContext.Provider>
  );
  await act(async () => {});
  return { tree, onReturn, onEngineerAccessGranted };
}

beforeEach(() => {
  jest.clearAllMocks();
  MockedModal.mockImplementation(() => null);
  jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(false);
});

describe("Galactic Construction — layout", () => {
  it("keeps the Return button unchanged", async () => {
    const { tree, onReturn } = await render();
    const button = findByTestId(tree, "construction-return");
    expect(button.props.accessibilityLabel).toBe("Return");
    await act(async () => { button.props.onPress(); });
    expect(onReturn).toHaveBeenCalledTimes(1);
  });

  it("places Engineer Access directly beneath Return", async () => {
    const { tree } = await render();
    const order = tree.UNSAFE_root
      .findAll((n) => typeof n.props?.testID === "string")
      .map((n) => n.props.testID);
    expect(order.indexOf("construction-engineer-access")).toBeGreaterThan(order.indexOf("construction-return"));
  });

  it("matches the Return button's width", async () => {
    const { tree } = await render();
    const widthOf = (id: string) =>
      findByTestId(tree, id)
        .props.style({ pressed: false })
        .flat()
        .find((s: Record<string, number>) => s?.minWidth)?.minWidth;
    expect(widthOf("construction-engineer-access")).toBe(widthOf("construction-return"));
  });

  it("stays visually secondary — outlined, not filled like Return", async () => {
    const { tree } = await render();
    const engineer = findByTestId(tree, "construction-engineer-access")
      .props.style({ pressed: false })
      .flat()
      .reduce((acc: object, s: object) => ({ ...acc, ...(s || {}) }), {});
    expect(engineer.borderWidth).toBeGreaterThan(0);
    expect(engineer.backgroundColor).toMatch(/rgba/); // translucent, not a solid fill
  });

  it("shows the helper text and a lock icon", async () => {
    const { tree } = await render();
    expect(allText(tree)).toContain("Authorized engineers only");
    expect(allText(tree)).toContain("🔒");
  });

  it("meets the minimum touch target", async () => {
    const { tree } = await render();
    const height = findByTestId(tree, "construction-engineer-access")
      .props.style({ pressed: false })
      .flat()
      .find((s: Record<string, number>) => s?.minHeight)?.minHeight;
    expect(height).toBeGreaterThanOrEqual(44);
  });
});

describe("Galactic Construction — engineer flow", () => {
  it("fires a warning haptic and opens the challenge without navigating", async () => {
    const { tree, onReturn, onEngineerAccessGranted } = await render();

    await act(async () => { findByTestId(tree, "construction-engineer-access").props.onPress(); });

    expect(Haptics.impactAsync).toHaveBeenCalledWith("medium");
    expect(MockedModal.mock.calls.at(-1)?.[0].visible).toBe(true);
    // §2: tapping must not navigate away or mount a protected route.
    expect(onReturn).not.toHaveBeenCalled();
    expect(onEngineerAccessGranted).not.toHaveBeenCalled();
  });

  it("keeps the modal closed until the button is pressed", async () => {
    const { tree } = await render();
    expect(MockedModal.mock.calls.at(-1)?.[0].visible).toBe(false);
    expect(tree).toBeTruthy();
  });

  it("passes the authenticated account's id to the challenge", async () => {
    await render();
    expect(MockedModal.mock.calls.at(-1)?.[0].userId).toBe(4242);
  });

  it("signals the host immediately on grant, without waiting on animation", async () => {
    const { tree, onEngineerAccessGranted } = await render();
    await act(async () => { findByTestId(tree, "construction-engineer-access").props.onPress(); });

    await act(async () => { MockedModal.mock.calls.at(-1)![0].onGranted(); });

    // §11: access is never delayed for the sake of the unlock animation.
    expect(onEngineerAccessGranted).toHaveBeenCalledTimes(1);
    expect(MockedModal.mock.calls.at(-1)?.[0].visible).toBe(false);
    expect(allText(tree)).toContain("🔓");
  });

  it("leaves the screen intact when the challenge is cancelled", async () => {
    const { tree, onReturn, onEngineerAccessGranted } = await render();
    await act(async () => { findByTestId(tree, "construction-engineer-access").props.onPress(); });
    await act(async () => { MockedModal.mock.calls.at(-1)![0].onCancel(); });

    expect(MockedModal.mock.calls.at(-1)?.[0].visible).toBe(false);
    expect(onReturn).not.toHaveBeenCalled();
    expect(onEngineerAccessGranted).not.toHaveBeenCalled();
    expect(findByTestId(tree, "construction-engineer-access")).toBeTruthy();
  });
});

describe("Galactic Construction — accessibility", () => {
  it("labels the Engineer Access control and explains what it does", async () => {
    const { tree } = await render();
    const button = findByTestId(tree, "construction-engineer-access");
    expect(button.props.accessibilityRole).toBe("button");
    expect(button.props.accessibilityLabel).toBe("Engineer Access");
    expect(button.props.accessibilityHint).toContain("passcode");
  });

  it("hides the decorative lock glyph from VoiceOver", async () => {
    const { tree } = await render();
    const icon = tree.UNSAFE_root.findAllByType(Text).find((n) => String(n.props.children).includes("🔒"));
    expect(icon?.props.importantForAccessibility).toBe("no");
  });

  it("stops the glow pulse under Reduce Motion", async () => {
    jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(true);
    const { tree } = await render();
    const glow = tree.UNSAFE_root.findAll(
      (n) => n.props?.pointerEvents === "none" && Array.isArray(n.props?.style)
    )[0];
    const opacity = glow.props.style.flat().find((s: Record<string, unknown>) => s?.opacity !== undefined)?.opacity;
    // A fixed number rather than an Animated interpolation.
    expect(typeof opacity).toBe("number");
  });
});
