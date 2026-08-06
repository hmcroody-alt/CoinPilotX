import { readFileSync } from "fs";
import { join } from "path";
// @testing-library/react-native rather than react-test-renderer directly:
// react-test-renderer ships no type declarations and @types/react-test-renderer
// is not a dependency here, so importing it puts a permanent TS7016 in `tsc
// --noEmit`. The library re-exports act() and wraps the same renderer.
import { act, render as mount, RenderResult } from "@testing-library/react-native";
import { AccessibilityInfo, Pressable, Text, TextInput } from "react-native";
import { EngineerAccessModal } from "../EngineerAccessModal";
import { getEngineerAccessStatus, verifyEngineerAccess } from "../../../api/engineerAccess";

jest.mock("../../../api/engineerAccess", () => ({
  verifyEngineerAccess: jest.fn(),
  getEngineerAccessStatus: jest.fn()
}));

/**
 * The development fallback is compiled in whenever __DEV__ is true, which it is
 * under Jest. Left alone it would silently change the lockout expectations
 * below, so it is mocked off by default: these tests describe the public
 * production build, and the one test that cares flips `mockDevFallbackCompiledIn`.
 */
let mockDevFallbackCompiledIn = false;
jest.mock("../../../security/engineerAccessDevFallback", () => {
  const actual = jest.requireActual("../../../security/engineerAccessDevFallback");
  return { ...actual, engineerDevFallbackEnabled: () => mockDevFallbackCompiledIn };
});

jest.mock("expo-haptics", () => ({
  notificationAsync: jest.fn(() => Promise.resolve()),
  impactAsync: jest.fn(() => Promise.resolve()),
  NotificationFeedbackType: { Success: "success", Error: "error" },
  ImpactFeedbackStyle: { Medium: "medium" }
}));

const mockedVerify = verifyEngineerAccess as jest.Mock;
const mockedStatus = getEngineerAccessStatus as jest.Mock;

const CLEAN_STATUS = {
  active: false,
  expiresAt: null,
  scope: [],
  lockedSecondsRemaining: 0,
  requiresReauthentication: false
};

const PASSCODE = "13572468";

type TestInstance = RenderResult["UNSAFE_root"];

function findByLabel(tree: RenderResult, label: string): TestInstance {
  return tree.UNSAFE_root.findAll(
    (node) => node.props?.accessibilityLabel === label && typeof node.type !== "string"
  )[0];
}

function allText(tree: RenderResult): string {
  return tree.UNSAFE_root
    .findAllByType(Text)
    .map((node) => (Array.isArray(node.props.children) ? node.props.children.join(" ") : String(node.props.children ?? "")))
    .join(" | ");
}

async function render(props: Partial<Parameters<typeof EngineerAccessModal>[0]> = {}) {
  // mount() outside act(): RNTL probes for host component names on first render
  // by mounting and unmounting, which throws if it happens inside an outer act.
  const tree = mount(
    <EngineerAccessModal
      visible
      userId={4242}
      onCancel={props.onCancel || jest.fn()}
      onGranted={props.onGranted || jest.fn()}
    />
  );
  await act(async () => {});
  return tree;
}

async function type(tree: RenderResult, digits: string) {
  const input = tree.UNSAFE_root.findByType(TextInput);
  await act(async () => {
    input.props.onChangeText(digits);
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockDevFallbackCompiledIn = false;
  mockedStatus.mockResolvedValue(CLEAN_STATUS);
  jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(false);
  jest.spyOn(AccessibilityInfo, "announceForAccessibility").mockImplementation(() => undefined);
});

describe("EngineerAccessModal — entry", () => {
  it("shows the required title and subtitle", async () => {
    const tree = await render();
    expect(allText(tree)).toContain("Engineer Access");
    expect(allText(tree)).toContain("Enter the engineer passcode to continue.");
  });

  it("masks input and blocks copy, paste, and autofill", async () => {
    const tree = await render();
    const input = tree.UNSAFE_root.findByType(TextInput).props;
    expect(input.secureTextEntry).toBe(true);
    expect(input.contextMenuHidden).toBe(true);       // no copy/paste callout
    expect(input.autoComplete).toBe("off");
    expect(input.importantForAutofill).toBe("no");
    expect(input.textContentType).toBe("none");
    expect(input.keyboardType).toBe("number-pad");
    expect(input.maxLength).toBe(8);
    expect(input.autoFocus).toBe(true);
  });

  it("never exposes the entered digits to VoiceOver", async () => {
    const tree = await render();
    await type(tree, PASSCODE);
    // The dot row is hidden from the accessibility tree, and the input's own
    // label describes the field rather than its contents.
    expect(tree.UNSAFE_root.findByType(TextInput).props.accessibilityLabel).toBe("Engineer passcode");
    expect(allText(tree)).not.toContain(PASSCODE);
  });

  it("rejects non-digits and caps length at eight", async () => {
    const tree = await render();
    await type(tree, "12ab34cd5678999");
    expect(tree.UNSAFE_root.findByType(TextInput).props.value).toBe("12345678");
  });
});

describe("EngineerAccessModal — Verify Access gating", () => {
  it.each([["", 0], ["1", 1], ["1357246", 7]])("is disabled at %s (%i digits)", async (digits) => {
    const tree = await render();
    await type(tree, digits as string);
    expect(findByLabel(tree, "Verify Access").props.disabled).toBe(true);
  });

  it("enables only at exactly eight digits", async () => {
    const tree = await render();
    await type(tree, PASSCODE);
    expect(findByLabel(tree, "Verify Access").props.disabled).toBe(false);
  });

  it("does not call the server when tapped below eight digits", async () => {
    const tree = await render();
    await type(tree, "135");
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });
    expect(mockedVerify).not.toHaveBeenCalled();
  });
});

describe("EngineerAccessModal — success", () => {
  it("grants and clears the field", async () => {
    const onGranted = jest.fn();
    mockedVerify.mockResolvedValue({ authorized: true, expiresAt: 999, scope: ["business_os"] });

    const tree = await render({ onGranted });
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    expect(onGranted).toHaveBeenCalledTimes(1);
    expect(tree.UNSAFE_root.findByType(TextInput).props.value).toBe("");
  });

  it("clears the digits before the network call resolves", async () => {
    // The passcode must not sit in component state across the round trip.
    let release: (value: unknown) => void = () => undefined;
    mockedVerify.mockReturnValue(new Promise((resolve) => { release = resolve; }));

    const tree = await render();
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    expect(tree.UNSAFE_root.findByType(TextInput).props.value).toBe("");

    await act(async () => { release({ authorized: false, retryAfterSeconds: 0, requiresReauthentication: false }); });
  });
});

describe("EngineerAccessModal — failure", () => {
  beforeEach(() => {
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 0, requiresReauthentication: false });
  });

  async function fail() {
    const onGranted = jest.fn();
    const tree = await render({ onGranted });
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });
    return { tree, onGranted };
  }

  it("never grants access", async () => {
    const { onGranted } = await fail();
    expect(onGranted).not.toHaveBeenCalled();
  });

  it("shows the strict warning with the required copy", async () => {
    const { tree } = await fail();
    const text = allText(tree);
    expect(text).toContain("Access Denied");
    expect(text).toContain("Unauthorized engineer access attempt detected");
    expect(text).toContain("This protected system is monitored");
    expect(text).toContain("Continued failed attempts will temporarily disable access");
    expect(findByLabel(tree, "Understood")).toBeTruthy();
  });

  it("never says how close the attempt was", async () => {
    const { tree } = await fail();
    const text = allText(tree).toLowerCase();
    // §7: no digit-level feedback, no partial-match count, no length hint.
    for (const leak of ["digit", "character", "incorrect passcode", "wrong passcode", "matched", "close"]) {
      expect(text).not.toContain(leak);
    }
    expect(text).not.toContain(PASSCODE);
  });

  it("does not rely on colour alone to signal the warning", async () => {
    const { tree } = await fail();
    // Both a warning glyph and the literal word carry the meaning.
    expect(allText(tree)).toContain("⚠");
    expect(allText(tree)).toContain("Warning");
  });

  it("announces the denial to VoiceOver", async () => {
    await fail();
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(
      expect.stringContaining("Access denied")
    );
  });

  it("returns to an empty field after Understood", async () => {
    const { tree } = await fail();
    await act(async () => { findByLabel(tree, "Understood").props.onPress(); });
    expect(tree.UNSAFE_root.findByType(TextInput).props.value).toBe("");
    expect(findByLabel(tree, "Verify Access").props.disabled).toBe(true);
  });
});

describe("EngineerAccessModal — lockout", () => {
  it("shows the countdown when the server locks the account", async () => {
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 60, requiresReauthentication: false });

    const tree = await render();
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    const text = allText(tree);
    expect(text).toContain("Engineer Access Temporarily Locked");
    expect(text).toContain("60 seconds");
  });

  it("removes the passcode field entirely while locked", async () => {
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 60, requiresReauthentication: false });

    const tree = await render();
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    expect(tree.UNSAFE_root.findAllByType(TextInput)).toHaveLength(0);
    expect(tree.UNSAFE_root.findAll((n) => n.props?.accessibilityLabel === "Verify Access")).toHaveLength(0);
  });

  it("keeps Return available while locked", async () => {
    const onCancel = jest.fn();
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 60, requiresReauthentication: false });

    const tree = await render({ onCancel });
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });
    await act(async () => { findByLabel(tree, "Return").props.onPress(); });

    expect(onCancel).toHaveBeenCalled();
  });

  it("re-reads a server lockout on open, so an app restart does not clear it", async () => {
    // Simulates a cold start during an active lockout: the component knows
    // nothing, asks the server, and lands straight in the locked state.
    mockedStatus.mockResolvedValue({ ...CLEAN_STATUS, lockedSecondsRemaining: 240 });

    const tree = await render();

    expect(allText(tree)).toContain("Engineer Access Temporarily Locked");
    expect(allText(tree)).toContain("4 minutes");
    expect(tree.UNSAFE_root.findAllByType(TextInput)).toHaveLength(0);
  });

  it("keeps the field enterable while the development fallback is compiled in", async () => {
    // A lockout is a verdict about server attempts. The passcode the local
    // fallback accepts is not one of them, so a leftover countdown must not hide
    // the input and make a valid passcode unenterable.
    mockDevFallbackCompiledIn = true;
    mockedStatus.mockResolvedValue({ ...CLEAN_STATUS, lockedSecondsRemaining: 900 });

    const tree = await render();

    expect(tree.UNSAFE_root.findAllByType(TextInput)).toHaveLength(1);
    expect(findByLabel(tree, "Verify Access")).toBeTruthy();
  });

  it("tells a repeatedly-failing account to re-authenticate", async () => {
    mockedStatus.mockResolvedValue({ ...CLEAN_STATUS, lockedSecondsRemaining: 3600, requiresReauthentication: true });
    const tree = await render();
    expect(allText(tree)).toContain("Sign out and sign in again");
  });

  it("exposes the countdown as a polite live region", async () => {
    mockedStatus.mockResolvedValue({ ...CLEAN_STATUS, lockedSecondsRemaining: 90 });
    const tree = await render();
    const countdown = tree.UNSAFE_root.findAll(
      (n) => n.props?.accessibilityLiveRegion === "polite" && typeof n.type !== "string"
    );
    expect(countdown.length).toBeGreaterThan(0);
  });
});

describe("EngineerAccessModal — cancel", () => {
  it("closes without verifying and wipes the field", async () => {
    const onCancel = jest.fn();
    const tree = await render({ onCancel });
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Cancel").props.onPress(); });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(mockedVerify).not.toHaveBeenCalled();
    expect(tree.UNSAFE_root.findByType(TextInput).props.value).toBe("");
  });
});

describe("EngineerAccessModal — accessibility", () => {
  it("labels every interactive control", async () => {
    const tree = await render();
    for (const label of ["Cancel", "Verify Access", "Engineer passcode"]) {
      expect(findByLabel(tree, label)).toBeTruthy();
    }
  });

  it("meets the minimum touch target on both actions", async () => {
    const tree = await render();
    for (const label of ["Cancel", "Verify Access"]) {
      const styles = findByLabel(tree, label).props.style({ pressed: false }).flat();
      const minHeight = styles.find((s: Record<string, number>) => s?.minHeight)?.minHeight;
      expect(minHeight).toBeGreaterThanOrEqual(44);
    }
  });

  it("skips motion when Reduce Motion is enabled", async () => {
    jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(true);
    const tree = await render();
    // A fade transition would still animate the modal itself; 'none' is the
    // instant swap Reduce Motion asks for.
    const modal = tree.UNSAFE_root.findAll((n) => n.props?.animationType !== undefined)[0];
    expect(modal.props.animationType).toBe("none");
  });
});

describe("EngineerAccessModal — passcode containment", () => {
  it("does not render the passcode anywhere in the tree snapshot", async () => {
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 0, requiresReauthentication: false });
    const tree = await render();
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    // §13: the raw passcode must not appear in test snapshots or crash reports.
    expect(JSON.stringify(tree.toJSON())).not.toContain(PASSCODE);
  });

  it("hands the passcode to exactly one place — the verify call", async () => {
    mockedVerify.mockResolvedValue({ authorized: false, retryAfterSeconds: 0, requiresReauthentication: false });
    const tree = await render();
    await type(tree, PASSCODE);
    await act(async () => { findByLabel(tree, "Verify Access").props.onPress(); });

    expect(mockedVerify).toHaveBeenCalledTimes(1);
    expect(mockedVerify).toHaveBeenCalledWith(4242, PASSCODE);
  });

  it("contains no hardcoded passcode in its own source", async () => {
    const source = readFileSync(join(__dirname, "..", "EngineerAccessModal.tsx"), "utf8");
    // §3/§4: an embedded passcode is extractable from the bundle. There is none.
    expect(source).not.toMatch(/(?<![\w])\d{8}(?![\w])/);
  });
});
