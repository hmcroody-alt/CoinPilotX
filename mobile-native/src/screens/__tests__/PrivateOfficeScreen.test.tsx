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

// Only the network read is replaced. `parseOverview`, `parseProductState` and
// `UNKNOWN_OVERVIEW` are the real ones — they are the contract under test, and
// a stubbed parser would leave a suite that proves the stub agrees with itself.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getPrivateOfficeOverview: (...args: unknown[]) => mockGetOverview(...args)
}));

import { parseOverview } from "../../api/privateOffice";
import { PrivateOfficeScreen } from "../PrivateOfficeScreen";

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

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PrivateOfficeScreen
      route={{ key: "o", name: "PrivateOffice", params: {} } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetOverview.mockResolvedValue(overview());
});

describe("PrivateOfficeScreen", () => {
  it("renders the office heading and its stated purpose", async () => {
    const { getByText } = renderScreen();
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
    const { getByText, queryByText } = renderScreen();
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
    const { getByText } = renderScreen();
    await waitFor(() => getByText("some_future_thing"));
  });

  it("opens Private Facts when the server says the child opens", async () => {
    const { getByText, navigation } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    fireEvent.press(getByText("premium:privateOffice.features.privateFacts.label"));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivateFacts");
  });

  it("does not navigate for a child the server did not mark as opening", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ available: [child({ opens: false })] })
    );
    const { getByText, navigation } = renderScreen();
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
    const { getByText } = renderScreen();
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
    const { getByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.upgrade.title"));
  });

  it("says it could not confirm access, rather than showing an empty office, on a degraded resolve", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ state: "ENTRY_UNKNOWN", effective_tier: "", available: [], unavailable: [] }, false)
    );
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.unknown.title"));
    expect(getByText("premium:privateOffice.retry")).toBeTruthy();
    // The distinction that matters: "we could not look" is not "not available".
    expect(queryByText("premium:privateOffice.unavailable.title")).toBeNull();
  });

  it("re-reads the server when the member retries", async () => {
    mockGetOverview.mockResolvedValue(
      overview({ state: "ENTRY_UNKNOWN", available: [], unavailable: [] }, false)
    );
    const { getByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.retry"));
    mockGetOverview.mockResolvedValue(overview());
    fireEvent.press(getByText("premium:privateOffice.retry"));
    await waitFor(() => getByText("premium:privateOffice.features.privateFacts.label"));
    expect(mockGetOverview).toHaveBeenCalledTimes(2);
  });
});
