/**
 * The Private Office entry, and the specific ways it could start lying.
 *
 * The screen's whole justification is that it renders the server's answer and
 * holds no opinion of its own. That property is invisible in review — a screen
 * with a hardcoded feature list looks identical to one that fetched it — so it
 * is pinned here instead:
 *
 *   1. The card list comes down the wire. A capability the server did not
 *      mention must not appear, and one it did mention must, including one this
 *      build has never heard of.
 *   2. `opens` decides tappability. A card with `opens: false` must not
 *      navigate, no matter how entitled the member looks, because `opens` is the
 *      server's word and a local re-derivation would be a second authority.
 *   3. ENTRY_UNKNOWN is not ENTRY_UNAVAILABLE. A resolver that did not answer
 *      must offer a retry rather than report an empty office.
 *
 * Two of those are older than this file's current shape and are unchanged. What
 * replaced the third is the point of the Office's narrowing: there is no longer
 * a section for capabilities the member cannot open. The screen used to draw
 * `unavailable` rows under a "coming later" heading with PROVIDER_REQUIRED,
 * NOT_IMPLEMENTED and TEMPORARILY_DISABLED as three distinct reasons — a real
 * distinction, worth keeping while the section existed, and moot now that the
 * section does not. The test that pinned it is gone with it, and in its place is
 * the property that now matters more: **an `unavailable` row is not drawn at
 * all**. That is asserted below against a payload that carries some, because a
 * server is free to keep sending them and the screen must still stay quiet.
 *
 * Office Security is asserted separately from all of this, and deliberately so.
 * It is not a capability, it is not in `available`, and it must appear whatever
 * the wire says — including when the wire says nothing useful. A test that let
 * it ride along in the capability loop would not notice it disappearing.
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
// wrapped in `PrivateOfficeLockGate`: it asks `/security/status` before it will
// render anything, and mints a grant through `/security/unlock`. Both are
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
  lockOfficeLocally,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { PrivateOfficeScreen } from "../PrivateOfficeScreen";

/** The member's office passcode for this suite. Any other value is refused. */
const OFFICE_PASSCODE = "846195";

const PEOPLE = "premium:privateOffice.features.relationshipIntelligence.label";
const MEETINGS = "premium:privateOffice.features.privateMeetings.label";
const SECURITY = "premium:privateOffice.security.row.label";

/**
 * The capability names the Office no longer has.
 *
 * Listed as the label keys the old screen drew, because that is the form a
 * regression would take: the catalogs no longer carry these keys, so a screen
 * that tried to draw one would render the raw key — which is exactly what `t`
 * returns here, and exactly what these assertions look for.
 */
const RETIRED = [
  "premium:privateOffice.features.privateFacts.label",
  "premium:privateOffice.features.capitalGraph.label",
  "premium:privateOffice.features.operations.label",
  "premium:privateOffice.features.privateBriefings.label",
  "premium:privateOffice.features.privateShield.label",
  "premium:privateOffice.features.documentIntelligence.label",
  "premium:privateOffice.features.humanConcierge.label",
  "premium:privateOffice.features.breachMonitoring.label",
  "premium:privateOffice.features.privateConversations.label"
];

/** A `_child_state` row exactly as `office.product_state` emits it. */
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

  it("draws the three cards the office is now for, and nothing else", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child(), child({ feature_id: "private_meetings" })] })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));
    expect(getByText(MEETINGS)).toBeTruthy();
    expect(getByText(SECURITY)).toBeTruthy();
    for (const key of RETIRED) expect(queryByText(key)).toBeNull();
  });

  it("does not draw a capability the server did not send", async () => {
    // The server offered one capability; the build knows copy for two. The
    // second must not appear, because the screen keeps no list of its own.
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));
    expect(queryByText(MEETINGS)).toBeNull();
  });

  it("renders a capability it has never heard of rather than dropping it", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ feature_id: "some_future_thing" })] })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("some_future_thing"));
  });

  it("does not navigate for a capability it has no screen for", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ feature_id: "some_future_thing" })] })
    );
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("some_future_thing"));
    fireEvent.press(getByText("some_future_thing"));
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("opens the people directory when the server says the card opens", async () => {
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));
    fireEvent.press(getByText(PEOPLE));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivatePeople");
  });

  it("opens the meeting list from its own card", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ feature_id: "private_meetings" })] })
    );
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText(MEETINGS));
    fireEvent.press(getByText(MEETINGS));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivateMeetings");
  });

  it("does not navigate for a card the server did not mark as opening", async () => {
    mockGetOverview.mockResolvedValue(overview({ available: [child({ opens: false })] }));
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));
    fireEvent.press(getByText(PEOPLE));
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  // The replacement for the old three-reasons test. A server that still reports
  // what the member cannot have is answering an older question; the screen's job
  // is now to say nothing about it rather than to say it precisely.
  it("says nothing at all about a capability the member cannot open", async () => {
    mockGetOverview.mockResolvedValue(
      overview({
        available: [child()],
        unavailable: [
          child({ feature_id: "private_shield", reason: "PROVIDER_REQUIRED", availability: "NOT_IMPLEMENTED", opens: false }),
          child({ feature_id: "capital_graph", reason: "NOT_IMPLEMENTED", availability: "NOT_IMPLEMENTED", opens: false }),
          child({ feature_id: "private_briefings", reason: "TEMPORARILY_DISABLED", availability: "FEATURE_DISABLED", opens: false })
        ]
      })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));
    for (const key of RETIRED) expect(queryByText(key)).toBeNull();
    // Nor the raw ids, which is what an unlabelled row would render as.
    for (const id of ["private_shield", "capital_graph", "private_briefings"]) {
      expect(queryByText(id)).toBeNull();
    }
    // And no heading invites the member to wait for them.
    expect(queryByText("premium:privateOffice.sections.notYet")).toBeNull();
  });

  // Office Security is a property of the room, not a thing the member subscribes
  // to. It is asserted on its own because it is drawn outside the capability
  // loop, and a suite that only walked `available` could not tell it apart from
  // a capability that happened to be entitled.
  it("offers Office Security even when the office holds no capability at all", async () => {
    mockGetOverview.mockResolvedValue(overview({ available: [], unavailable: [] }));
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText(SECURITY));
    fireEvent.press(getByText(SECURITY));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivateOfficeSecurity");
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
    // Not even Office Security is drawn here. A card under a "we could not
    // check" banner would read as a partial answer, and the office has not
    // answered at all.
    expect(queryByText(SECURITY)).toBeNull();
  });

  it("re-reads the server when the member retries", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ state: "ENTRY_UNKNOWN", available: [], unavailable: [] }, false)
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.retry"));
    mockGetOverview.mockResolvedValue(overview());
    fireEvent.press(getByText("premium:privateOffice.retry"));
    await waitFor(() => getByText(PEOPLE));
    expect(mockGetOverview).toHaveBeenCalledTimes(2);
  });

  // A lapsed membership reaches the server perfectly well; the server simply
  // says no. This mounts without `renderScreen`, which exists to get past the
  // passcode door — a member whose membership has expired never sees that door.
  it("offers a way to renew, not a retry, when the membership has lapsed", async () => {
    mockOfficeStatus.mockResolvedValue({
      state: "UPGRADE_REQUIRED",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false
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
    expect(queryByText(PEOPLE)).toBeNull();
    expect(queryByText(SECURITY)).toBeNull();
  });
});

/**
 * The second lock, asserted against the narrowed office.
 *
 * `officeLock.test.ts` already pins the store: expiry, owner binding, the
 * background timings, the headers. None of that is repeated here. What is
 * asserted here is the one thing a store test cannot see — that this screen,
 * after being rebuilt around three cards, is still *behind* that store and did
 * not acquire a way around it while the cards were being rearranged.
 *
 * The failure being guarded against is not subtle in effect and is very subtle
 * in diff: a lock gate that mounts its children eagerly and merely covers them,
 * or a card list that renders before the gate resolves, leaks the member's
 * capabilities through a closed door. Every case below therefore asserts on the
 * *absence of the cards*, not on the presence of the door — a door drawn over a
 * mounted body would satisfy the weaker assertion.
 */
describe("the second lock still stands in front of the narrowed office", () => {
  it("does not draw a single card until the passcode has been accepted", async () => {
    const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
    const { getByText, queryByText } = render(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(queryByText(PEOPLE)).toBeNull();
    expect(queryByText(MEETINGS)).toBeNull();
    // Office Security is drawn by the office body like the capabilities are, so
    // it is behind the same door. The settings screen it leads to is reachable
    // on its own and has its own gate; what must not happen is this screen
    // offering the way in before the member has proved who they are.
    expect(queryByText(SECURITY)).toBeNull();
  });

  it("a wrong passcode opens nothing", async () => {
    const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
    const { getByLabelText, getByText, queryByText } = render(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    fireEvent.changeText(getByLabelText("premium:privateOffice.lock.placeholder"), "000000");
    await waitFor(() =>
      expect(getByLabelText("premium:privateOffice.lock.placeholder").props.value).toBe("000000")
    );
    fireEvent.press(getByText("premium:privateOffice.lock.unlock"));
    await waitFor(() => getByText("premium:privateOffice.lock.wrong"));
    expect(isOfficeUnlocked()).toBe(false);
    expect(queryByText(PEOPLE)).toBeNull();
    expect(queryByText(SECURITY)).toBeNull();
  });

  it("shuts again the moment the grant is dropped, and asks for the passcode on re-entry", async () => {
    const { getByText, queryByText, rerender, navigation } = await renderScreen();
    await waitFor(() => getByText(PEOPLE));

    // What the app does when it leaves the foreground, when the member taps
    // "lock now", or when the server answers 423: one call, no arguments, no
    // way for a caller to ask for an exception.
    lockOfficeLocally();

    // The gate subscribes to the store, so the open office closes where it
    // stands — a screen that only re-checked on mount would still be showing
    // the member's capabilities to whoever picked the phone up.
    await waitFor(() => expect(queryByText(PEOPLE)).toBeNull());
    expect(queryByText(MEETINGS)).toBeNull();
    expect(queryByText(SECURITY)).toBeNull();
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));

    // Re-entering the office is re-entering the passcode. Nothing about having
    // been in a moment ago shortens the door.
    rerender(
      <PrivateOfficeScreen
        route={{ key: "o", name: "PrivateOffice", params: {} } as never}
        navigation={navigation as never}
      />
    );
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(queryByText(PEOPLE)).toBeNull();
  });

  /**
   * A grant the server has stopped honouring shuts the office from inside it.
   *
   * This is the one lock decision the *screen* makes rather than the gate or the
   * store: `load()` reads `overview.locked` — set from a 423 or a
   * `PRIVATE_OFFICE_LOCKED` body — and calls `lockOfficeLocally()` before it
   * renders anything. Without that line the member keeps looking at a rendered
   * office whose every subsequent read is being refused, which is worse than a
   * closed door: it is a closed door that looks open.
   *
   * Owner binding is not asserted here even though it is the more famous rule.
   * It is the store's property, it is pinned in `officeLock.test.ts` against the
   * store directly, and at this level the server's own `unlocked: false`
   * dominates every outcome — a screen test for it would pass whether or not the
   * binding existed.
   */
  it("relocks when the server refuses the grant mid-session", async () => {
    // The grant is live and the passcode is accepted, so the door opens — and
    // then the very first read behind it comes back refused.
    mockGetOverview.mockResolvedValue({ ...overview(), locked: true });
    const { getByText, queryByText } = await renderScreen();

    // The office does not settle into a rendered state it has no grant for.
    await waitFor(() => expect(isOfficeUnlocked()).toBe(false));
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(queryByText(PEOPLE)).toBeNull();
    expect(queryByText(SECURITY)).toBeNull();
  });
});
