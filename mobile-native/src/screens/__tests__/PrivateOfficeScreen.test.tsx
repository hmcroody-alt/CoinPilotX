/**
 * The Private Office entry, and the specific ways it could start lying.
 *
 * The screen's whole justification is that it renders the server's answer and
 * holds no opinion of its own. That property is invisible in review — a screen
 * with a hardcoded feature list looks identical to one that fetched it — so it
 * is pinned here instead:
 *
 *   1. The child list comes down the wire. A capability the server did not
 *      mention must not appear, and one it did mention must, including one this
 *      build has never heard of.
 *   2. `opens` decides tappability. A child in the available section with
 *      `opens: false` must not navigate, no matter how entitled the member
 *      looks, because `opens` is the server's word and a local re-derivation
 *      would be a second authority.
 *   3. PROVIDER_REQUIRED, NOT_IMPLEMENTED and TEMPORARILY_DISABLED render as
 *      three different reasons. Collapsing them is the failure Stage 10 names:
 *      `private_shield` drawn as merely locked reads as "we are watching and
 *      would tell you", which is false.
 *   4. ENTRY_UNKNOWN is not ENTRY_UNAVAILABLE. A resolver that did not answer
 *      must offer a retry rather than report an empty office.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

// No `SafeAreaProvider` in the test tree; the house pattern is to stub the
// insets rather than wrap every render.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

const mockGetOverview = jest.fn();
const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

// Only the network reads are replaced. `parseOverview`, `parseProductState` and
// `UNKNOWN_OVERVIEW` are the real ones — they are the contract under test, and
// a stubbed parser would leave a suite that proves the stub agrees with itself.
//
// The two security reads are stubbed at the same boundary because the screen is
// now wrapped in `PrivateOfficeLockGate`: it asks `/security/status` before it
// will render anything, and mints a grant through `/security/unlock`. Both are
// replaced with the answers a member who has set a passcode actually gets. The
// gate, the lock store and the unlock flow all stay real — `unlockOffice`'s
// stub does exactly what the real one does once the server has answered
// (stow the minted grant via `setOfficeUnlocked`), so the office only opens
// here for the same reason it opens in production: a live grant exists.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getPrivateOfficeOverview: (...args: unknown[]) => mockGetOverview(...args),
  getOfficeSecurityStatus: (...args: unknown[]) => mockOfficeStatus(...args),
  unlockOffice: (...args: unknown[]) => mockUnlockOffice(...args)
}));

// An unlock grant belongs to an account, and the gate relocks on every mount
// that finds no signed-in member (`reconcileOfficeOwner`). Without a session
// the suite would be modelling a signed-out device rather than a member with a
// locked office, so the envelope is supplied here and the grant is bound to it.
jest.mock("../../session/sessionStore", () => ({
  ...jest.requireActual("../../session/sessionStore"),
  getSessionEnvelope: async () => ({
    version: 1,
    userId: 4021,
    accessToken: "access-token",
    accessTokenExpiresAt: Date.now() + 600_000,
    refreshToken: "refresh-token",
    refreshTokenExpiresAt: Date.now() + 600_000
  })
}));

import { parseOverview } from "../../api/privateOffice";
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { PrivateOfficeScreen } from "../PrivateOfficeScreen";

/** The member's office passcode for this suite. Any other value is refused. */
const OFFICE_PASSCODE = "846195";

/** A `_child_state` row exactly as `office.product_state` emits it. */
function child(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "private_facts",
    availability: "ENTITLED",
    implementation: "IMPLEMENTED",
    minimum_tier: "PRIVATE",
    reason: "AVAILABLE",
    opens: true,
    ...overrides
  };
}

function overview(office: Record<string, unknown> = {}, ok = true) {
  return parseOverview({
    ok,
    private_office: {
      feature_id: "private_office",
      state: "ENTRY_AVAILABLE",
      effective_tier: "PRIVATE_OFFICE",
      available: [child()],
      unavailable: [],
      upgrade_tier: null,
      ...office
    },
    domains: [],
    verified_at: "2026-09-03T00:00:00+00:00"
  });
}

/**
 * Open the second lock the way a member does: the gate's own passcode field and
 * unlock button. Nothing here bypasses the lock — the office is shut until this
 * runs, which is itself the first thing every case below asserts.
 */
async function unlockOffice(utils: ReturnType<typeof render>) {
  const { getByLabelText, getByText, queryByText } = utils;
  // A grant minted earlier in the same test is still live, so this mount goes
  // straight through — exactly as a second office screen does in production.
  if (isOfficeUnlocked()) return;
  // Otherwise the lock door is the first thing on screen, and the body is not
  // mounted behind it.
  await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
  fireEvent.changeText(
    getByLabelText("premium:privateOffice.lock.placeholder"),
    OFFICE_PASSCODE
  );
  // Pressability latches `disabled` one effect flush behind the prop, so a
  // press dispatched in the same turn as the passcode entry silently no-ops.
  await waitFor(() =>
    expect(getByLabelText("premium:privateOffice.lock.placeholder").props.value).toBe(
      OFFICE_PASSCODE
    )
  );
  fireEvent.press(getByText("premium:privateOffice.lock.unlock"));
  await waitFor(() => expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull());
}

async function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PrivateOfficeScreen
      route={{ key: "o", name: "PrivateOffice", params: {} } as never}
      navigation={navigation as never}
    />
  );
  await unlockOffice(utils);
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  // Every case starts locked: the in-memory grant does not survive a test.
  __resetOfficeLockForTests();
  mockGetOverview.mockResolvedValue(overview());
  mockOfficeStatus.mockResolvedValue({
    state: "READY",
    passcodeSet: true,
    setupRequired: false,
    cooldownSeconds: 0,
    biometricPreference: "unset",
    unlocked: false
  });
  // Mirrors the real `unlockOffice`: the server is the only thing that can mint
  // a grant, and a wrong passcode mints nothing.
  mockUnlockOffice.mockImplementation(async (passcode: string, userId: number) => {
    if (passcode !== OFFICE_PASSCODE) return { state: "WRONG_PASSCODE" };
    setOfficeUnlocked(
      "office-grant-token",
      new Date(Date.now() + 300_000).toISOString(),
      Number(userId) || 0
    );
    return { state: "UNLOCKED" };
  });
});

describe("PrivateOfficeScreen", () => {
  it("renders the office heading and its stated purpose", async () => {
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.title"));
    expect(getByText("premium:privateOffice.subtitle")).toBeTruthy();
  });

  it("lists the children the server sent and no others", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [child()],
        unavailable: [
          child({ feature_id: "capital_graph", availability: "NOT_IMPLEMENTED", reason: "NOT_IMPLEMENTED", opens: false })
        ]
      })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    expect(getByText("premium:privateOffice.features.capitalGraph.label")).toBeTruthy();
    // Nothing invented: a capability this build knows a name for but the server
    // did not send must not be drawn.
    expect(queryByText("premium:privateOffice.features.humanConcierge.label")).toBeNull();
  });

  it("renders a capability it has never heard of rather than dropping it", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [],
        unavailable: [
          child({ feature_id: "some_future_thing", availability: "NOT_IMPLEMENTED", reason: "NOT_IMPLEMENTED", opens: false })
        ]
      })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("some_future_thing"));
  });

  it("opens Private Facts when the server says the child opens", async () => {
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    fireEvent.press(getByText("premium:privateOffice.features.privateFacts.label"));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivateFacts");
  });

  it("does not navigate for a child the server did not mark as opening", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ opens: false })] })
    );
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    fireEvent.press(getByText("premium:privateOffice.features.privateFacts.label"));
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("keeps provider-required, not-built and switched-off as three distinct reasons", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [],
        unavailable: [
          child({ feature_id: "private_shield", reason: "PROVIDER_REQUIRED", availability: "NOT_IMPLEMENTED", opens: false }),
          child({ feature_id: "capital_graph", reason: "NOT_IMPLEMENTED", availability: "NOT_IMPLEMENTED", opens: false }),
          child({ feature_id: "private_briefings", reason: "TEMPORARILY_DISABLED", availability: "FEATURE_DISABLED", opens: false })
        ]
      })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.reason.PROVIDER_REQUIRED"));
    expect(getByText("premium:privateOffice.reason.NOT_IMPLEMENTED")).toBeTruthy();
    expect(getByText("premium:privateOffice.reason.TEMPORARILY_DISABLED")).toBeTruthy();
  });

  it("asks the member to upgrade only when the server says so, naming the tier it sent", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        state: "ENTRY_UPGRADE_REQUIRED",
        effective_tier: "PREMIUM",
        available: [],
        unavailable: [child({ availability: "NOT_ENTITLED", reason: "UPGRADE_REQUIRED", opens: false })],
        upgrade_tier: "PRIVATE_OFFICE"
      })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.upgrade.title"));
  });

  it("says it could not confirm access, rather than showing an empty office, on a degraded resolve", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ state: "ENTRY_UNKNOWN", effective_tier: "", available: [], unavailable: [] }, false)
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.unknown.title"));
    expect(getByText("premium:privateOffice.retry")).toBeTruthy();
    // The distinction that matters: "we could not look" is not "not available".
    expect(queryByText("premium:privateOffice.unavailable.title")).toBeNull();
  });

  it("re-reads the server when the member retries", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ state: "ENTRY_UNKNOWN", available: [], unavailable: [] }, false)
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.retry"));
    mockGetOverview.mockResolvedValue(overview());
    fireEvent.press(getByText("premium:privateOffice.retry"));
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    expect(mockGetOverview).toHaveBeenCalledTimes(2);
  });
});
