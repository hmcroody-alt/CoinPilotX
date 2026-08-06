// @testing-library/react-native rather than react-test-renderer directly:
// react-test-renderer ships no type declarations and @types/react-test-renderer
// is not a dependency here, so importing it puts a permanent TS7016 in `tsc
// --noEmit`. The library re-exports act() and wraps the same renderer.
import { act, render as mount, RenderResult } from "@testing-library/react-native";
import { getBusinessConstructionAccess } from "../../api/businessConstruction";
import { clearEngineerAccess, setEngineerAccess } from "../../security/engineerAccessSession";
import { ProtectedBusinessHubRoute } from "../ProtectedBusinessRoutes";

/**
 * Captured from the mocked construction screen so a test can simulate the
 * moment the passcode challenge succeeds. The `mock` prefix is required —
 * Jest's hoisting rule rejects any other out-of-scope name inside a factory.
 */
let mockGrantHook: (() => void) | undefined;

jest.mock("../../api/businessConstruction", () => {
  const actual = jest.requireActual("../../api/businessConstruction");
  return { ...actual, getBusinessConstructionAccess: jest.fn() };
});

/**
 * The construction screen is replaced with a marker so these tests can tell
 * "locked" from "unlocked" without pulling in the whole galactic UI. The
 * grant callback is captured so a test can simulate a successful challenge.
 */
jest.mock("../GalacticConstructionScreen", () => {
  const { Text } = require("react-native");
  return {
    GalacticConstructionScreen: jest.fn(({ onEngineerAccessGranted }: { onEngineerAccessGranted?: () => void }) => {
      mockGrantHook = onEngineerAccessGranted;
      return <Text testID="construction-gate">LOCKED</Text>;
    })
  };
});

jest.mock("../BusinessHubRoute", () => {
  const { Text } = require("react-native");
  return { BusinessHubRoute: () => <Text testID="business-hub">BUSINESS HUB</Text> };
});

jest.mock("../../session/auth", () => {
  const actual = jest.requireActual("../../session/auth");
  return {
    ...actual,
    useAuth: () => ({ authState: { phase: "AUTHENTICATED", status: "signedIn", user: { user_id: 4242 } } })
  };
});

const mockedAccess = getBusinessConstructionAccess as jest.Mock;

const LOCKED = {
  ok: true,
  mode: "construction" as const,
  can_access_private_business_os: false,
  construction_mode: true,
  developer_mode: false,
  developer_badge: false
};

const ENGINEER_OPEN = {
  ok: true,
  mode: "construction" as const,
  can_access_private_business_os: true,
  construction_mode: true,
  developer_mode: true,
  developer_badge: true,
  engineer_access: true
};

function rendered(tree: RenderResult): string {
  return JSON.stringify(tree.toJSON());
}

async function renderRoute(navigation = { goBack: jest.fn() }) {
  // mount() outside act(): RNTL probes for host component names on first render
  // by mounting and unmounting, which throws if it happens inside an outer act.
  const tree = mount(<ProtectedBusinessHubRoute navigation={navigation} />);
  await act(async () => {});
  return tree;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGrantHook = undefined;
  clearEngineerAccess();
});

describe("protected business routes — gating", () => {
  it("shows the construction gate when the server says no", async () => {
    mockedAccess.mockResolvedValue(LOCKED);
    const tree = await renderRoute();
    expect(rendered(tree)).toContain("LOCKED");
    expect(rendered(tree)).not.toContain("BUSINESS HUB");
  });

  it("does not mount the protected route while locked", async () => {
    // §2: a locked sector must never mount or preload. The real module is only
    // require()d after the server answers yes.
    mockedAccess.mockResolvedValue(LOCKED);
    const tree = await renderRoute();
    expect(rendered(tree)).not.toContain("business-hub");
  });

  it("fails closed when the access check errors", async () => {
    // A network failure must never be mistaken for permission.
    mockedAccess.mockRejectedValue(new Error("offline"));
    const tree = await renderRoute();
    expect(rendered(tree)).toContain("LOCKED");
  });

  it("mounts the real route when the server grants access", async () => {
    mockedAccess.mockResolvedValue(ENGINEER_OPEN);
    const tree = await renderRoute();
    expect(rendered(tree)).toContain("BUSINESS HUB");
  });

  it("marks an engineer session distinctly from a developer one", async () => {
    mockedAccess.mockResolvedValue(ENGINEER_OPEN);
    const tree = await renderRoute();
    expect(rendered(tree)).toContain("ENGINEER");
  });
});

describe("protected business routes — destination preservation", () => {
  it("continues to the originally requested screen after a grant", async () => {
    // §10: not a generic dashboard. The gate renders *in place of* the
    // requested route, so re-resolving mounts exactly what was asked for.
    mockedAccess.mockResolvedValueOnce(LOCKED).mockResolvedValue(ENGINEER_OPEN);

    const tree = await renderRoute();
    expect(rendered(tree)).toContain("LOCKED");

    await act(async () => { mockGrantHook?.(); });

    expect(rendered(tree)).toContain("BUSINESS HUB");
  });

  it("never navigates, so the back stack is untouched", async () => {
    mockedAccess.mockResolvedValueOnce(LOCKED).mockResolvedValue(ENGINEER_OPEN);
    const navigation = { goBack: jest.fn() };

    const tree = await renderRoute(navigation);
    await act(async () => { mockGrantHook?.(); });

    expect(navigation.goBack).not.toHaveBeenCalled();
    expect(rendered(tree)).toContain("BUSINESS HUB");
  });

  it("re-asks the server rather than trusting a locally held grant", async () => {
    // A patched client that plants a grant object still gets nothing: the
    // decision comes from the server on every resolve.
    mockedAccess.mockResolvedValue(LOCKED);
    setEngineerAccess(4242, {
      token: "forged.token",
      expiresAt: Math.floor(Date.now() / 1000) + 600,
      scope: ["business_os"]
    });

    const tree = await renderRoute();

    expect(mockedAccess).toHaveBeenCalled();
    expect(rendered(tree)).toContain("LOCKED");
  });
});

describe("protected business routes — revocation", () => {
  it("re-locks when the grant is cleared while the screen is mounted", async () => {
    mockedAccess.mockResolvedValueOnce(ENGINEER_OPEN).mockResolvedValue(LOCKED);
    setEngineerAccess(4242, { token: "t", expiresAt: Math.floor(Date.now() / 1000) + 600, scope: [] });

    const tree = await renderRoute();
    expect(rendered(tree)).toContain("BUSINESS HUB");

    // Sign-out, account switch, and server revocation all funnel through here.
    await act(async () => { clearEngineerAccess(); });

    expect(rendered(tree)).toContain("LOCKED");
  });
});
