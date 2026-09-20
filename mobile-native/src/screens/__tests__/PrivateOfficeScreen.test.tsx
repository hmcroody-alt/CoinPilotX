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

/**
 * The member's own tier, as the lock gate reads it.
 *
 * Stubbed rather than left real for two reasons. The real hook issues a network
 * read on mount, which this suite does not otherwise need and whose rejection
 * would surface as unrelated noise. More importantly the gate's upgrade door
 * now branches on this value, so leaving it to a shared module-level cache
 * would let one case's answer leak into the next and decide a sentence the test
 * never set. Default: the resolver did not answer, which is the honest starting
 * point for a suite that is mostly not about entitlement.
 */
const mockTier = jest.fn(() => ({ state: "unavailable", effectiveTier: "FREE" }));
jest.mock("../../entitlements/useCanonicalTier", () => ({
  useCanonicalTier: () => mockTier(),
  loadCanonicalTier: jest.fn(async () => mockTier()),
  resetCanonicalTier: jest.fn()
}));

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

/**
 * A `_child_state` row exactly as `office.product_state` emits it.
 *
 * The default id used to be `private_facts`, and the cases below named it
 * throughout. That feature was withdrawn: the server no longer sends the id, the
 * screen no longer has a copy key or a destination for it, and a row carrying it
 * now renders as the unknown-capability fallback. So the default moves to
 * `relationship_intelligence` — one of the two ids the Office actually has
 * children for — rather than leaving a suite that exercised the fallback path
 * while claiming to exercise the known one.
 */
function child(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "relationship_intelligence",
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
  // Re-established, not merely cleared. `clearAllMocks` strips the
  // implementation, and a tier hook returning `undefined` would crash the gate
  // in every test after the first one that sets its own answer.
  mockTier.mockImplementation(() => ({ state: "unavailable", effectiveTier: "FREE" }));
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
      overview({ available: [child()], unavailable: [] })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    // Nothing invented: a capability this build knows a name for but the server
    // did not send must not be drawn. `private_meetings` is the check with teeth
    // now — the Office has exactly two children, so the only way to state "and
    // no others" against a name the client can actually render is to withhold
    // one of the two. It used to be stated against `human_concierge`, which
    // stopped meaning anything the moment that id left `COPY_KEYS`: a key the
    // screen can no longer produce is absent from every render, including a
    // broken one.
    expect(queryByText("premium:privateOffice.features.privateMeetings.label")).toBeNull();
  });

  // This pair replaces a case that asserted the opposite — that an unknown id
  // renders as its raw string rather than vanishing. That was the better rule
  // while the client and the server agreed on what existed. Once the Office was
  // narrowed they stop agreeing on exactly the window that matters: a mobile
  // build ships on its own train, so a narrowed client stands in front of an
  // un-narrowed server during rollout, and in front of a rolled-back one after.
  // Run against production, that rendered ten rows, seven of them retired, each
  // titled with a machine id over an `Open` that went nowhere. A ghost feature
  // is not a gentler failure than a dropped row.
  it("drops an available capability this build has no screen for", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child(), child({ feature_id: "some_future_thing" })] })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    expect(queryByText("some_future_thing")).toBeNull();
  });

  it("drops an unavailable row this build cannot name, section and all", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [],
        unavailable: [
          child({ feature_id: "some_future_thing", availability: "NOT_IMPLEMENTED", reason: "NOT_IMPLEMENTED", opens: false })
        ]
      })
    );
    const { queryByText } = await renderScreen();
    await waitFor(() => expect(queryByText("some_future_thing")).toBeNull());
    // The heading goes with its only row. A "not yet" section with nothing
    // under it is a promise with no subject.
    expect(queryByText("premium:privateOffice.sections.notYet")).toBeNull();
  });

  it("opens Relationship Intelligence when the server says the child opens", async () => {
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    fireEvent.press(getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivatePeople");
  });

  it("does not navigate for a child the server did not mark as opening", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ opens: false })] })
    );
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    fireEvent.press(getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  // The three reasons are the subject, not the ids. The ids that used to carry
  // them here were all withdrawn, and the two that survive are the only ones
  // this build can name — so the third reason gets its own render rather than a
  // third id. The vocabulary is still the server's, and the screen must still
  // keep the three apart.
  it.each([
    ["PROVIDER_REQUIRED", "NOT_IMPLEMENTED"],
    ["NOT_IMPLEMENTED", "NOT_IMPLEMENTED"],
    ["TEMPORARILY_DISABLED", "FEATURE_DISABLED"]
  ])("renders %s as its own reason", async (reason, availability) => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [],
        unavailable: [
          child({ feature_id: "relationship_intelligence", reason, availability, opens: false })
        ]
      })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText(`premium:privateOffice.reason.${reason}`));
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
    await waitFor(() => getByText("premium:privateOffice.features.relationshipIntelligence.label"));
    expect(mockGetOverview).toHaveBeenCalledTimes(2);
  });

  // A lapsed membership reaches the server perfectly well; the server simply
  // says no. This mounts without `renderScreen`, which exists to get past the
  // passcode door — a member whose membership has expired never sees that door.
  it("offers a way to renew, not a retry, when the membership has lapsed", async () => {
    // The lapse is now ESTABLISHED rather than assumed. This case used to mock
    // only the 403 and then assert the renew sentence — but a 403 alone does
    // not mean "lapsed", it means "your tier does not reach this", and an
    // active member gets the identical status code. The suite was therefore
    // asserting the renew copy for a member it had never shown to be lapsed,
    // which is precisely the conflation the gate itself used to make.
    mockTier.mockImplementation(() => ({ state: "resolved", effectiveTier: "FREE" }));
    mockOfficeStatus.mockResolvedValue({
      state: "UPGRADE_REQUIRED",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false,
      upgradeTier: "PRIVATE"
    });
    const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
    const { getByText, queryByText } = render(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );

    await waitFor(() => getByText("premium:privateOffice.lock.upgrade.title"));
    // The whole point of the fix: the member is told the real reason, not that
    // the app could not reach a server it reached and got an answer from.
    expect(queryByText("premium:privateOffice.lock.unavailable.title")).toBeNull();

    fireEvent.press(getByText("premium:privateOffice.lock.upgrade.action"));
    expect(navigation.navigate).toHaveBeenCalledWith("Premium");

    // And the office itself stays shut: a renew prompt is still a closed door.
    expect(queryByText("premium:privateOffice.features.relationshipIntelligence.label")).toBeNull();
  });

  /**
   * The case the door was getting wrong, and the one behind the report.
   *
   * An ACTIVE Premium member opening Private Meetings gets the same 403 a
   * lapsed member gets, because `private_meetings` is `TIER_PRIVATE` — a rung
   * above PREMIUM — and a 403 only ever meant "your tier does not reach this".
   * The gate read that as expiry and said so: "Private Office is part of
   * premium membership, and yours isn't active right now. Renew to open it
   * again." Every clause of that is false to this member, and the button under
   * it sold them the tier they were already standing on.
   *
   * The assertions are deliberately about the ABSENCE of the renew copy as much
   * as the presence of the upgrade copy. Rendering both would technically show
   * the true sentence while leaving the false one on screen next to it.
   */
  it("tells an active member the rung is higher, not that their membership lapsed", async () => {
    mockTier.mockImplementation(() => ({ state: "resolved", effectiveTier: "PREMIUM" }));
    mockOfficeStatus.mockResolvedValue({
      state: "UPGRADE_REQUIRED",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false,
      upgradeTier: "PRIVATE"
    });
    const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
    const { getByText, queryByText } = render(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );

    await waitFor(() => getByText("premium:privateOffice.upgrade.title"));
    // The tier the server named is the tier the member is shown.
    expect(getByText("premium:privateOffice.upgrade.body")).toBeTruthy();

    // None of the expiry vocabulary may survive anywhere on this screen.
    expect(queryByText("premium:privateOffice.lock.upgrade.title")).toBeNull();
    expect(queryByText("premium:privateOffice.lock.upgrade.body")).toBeNull();
    // And no button that charges for a tier they already hold.
    expect(queryByText("premium:privateOffice.lock.upgrade.action")).toBeNull();
    expect(navigation.navigate).not.toHaveBeenCalledWith("Premium");
  });

  /**
   * The third person at this door: one whose membership we could not resolve.
   *
   * `UNKNOWN` is not `LAPSED`. The member most likely to hit a degraded resolve
   * is the one who paid, so guessing "lapsed" here would aim the renew prompt
   * at precisely the wrong person. The plan-neutral sentence is true whichever
   * way the unresolved read would have gone, and nothing asks for money on the
   * strength of a question the app could not answer.
   */
  it("does not sell a renewal to a member whose tier it could not resolve", async () => {
    mockTier.mockImplementation(() => ({ state: "unavailable", effectiveTier: "FREE" }));
    mockOfficeStatus.mockResolvedValue({
      state: "UPGRADE_REQUIRED",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false,
      upgradeTier: "PRIVATE"
    });
    const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
    const { getByText, queryByText } = render(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );

    await waitFor(() => getByText("premium:privateOffice.upgrade.title"));
    // Generic, not tier-named: naming a rung implies we know where they stand.
    expect(getByText("premium:privateOffice.upgrade.bodyGeneric")).toBeTruthy();
    expect(queryByText("premium:privateOffice.lock.upgrade.body")).toBeNull();
    expect(queryByText("premium:privateOffice.lock.upgrade.action")).toBeNull();
  });
});
