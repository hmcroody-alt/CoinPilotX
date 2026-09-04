/**
 * Private Facts, and the four ways a screen about personal records can lie.
 *
 * The screen's promise is narrow and total: every row on it came from the
 * member's own store, and every refusal names the actual reason. Neither
 * property is visible in review — a screen with a seeded example row looks
 * identical to one that fetched it, and six refusals collapsed into one banner
 * still compiles. So they are pinned here:
 *
 *   1. Six refusals stay six. NOT_ENTITLED, FEATURE_DISABLED, NOT_IMPLEMENTED,
 *      UNAVAILABLE and ERROR each render their own copy, and EMPTY is a sixth
 *      that is not a refusal at all.
 *   2. UNAVAILABLE is not EMPTY. This is the one that matters: "we could not
 *      look" drawn as "there is nothing here" is how a member concludes that a
 *      document they filed was lost. The two must never share a shape.
 *   3. Nothing is invented. An empty store renders the empty state and zero
 *      rows — no placeholder, no example.
 *   4. The provenance sheet explains without leaking. It names the source type,
 *      the observed date, the verification state and the confidence, and it does
 *      not surface `source_id`, which is a pointer into private storage rather
 *      than an explanation a member asked for.
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
    t: (key: string, options?: { defaultValue?: string; date?: string }) =>
      options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

const mockGetFacts = jest.fn();

// Only the network read is replaced. `parseFact` is the real one — it is the
// contract under test, and a stubbed parser would leave a suite that proves the
// stub agrees with itself.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getPrivateFacts: (...args: unknown[]) => mockGetFacts(...args)
}));

import { parseFact } from "../../api/privateOffice";
import { PrivateFactsScreen } from "../PrivateFactsScreen";

/** A row exactly as `office.project_facts` emits it. */
function rawFact(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    fact_type: "PRIMARY_RESIDENCE",
    value: "Miami, FL",
    value_type: "TEXT",
    domain: "GENERAL",
    sensitivity: "NORMAL",
    observed_at: "2026-08-01",
    lifecycle_state: "ACTIVE",
    provenance: {
      source_type: "DOCUMENT",
      source_id: "vault-object-8831",
      has_source_document: true,
      provenance_type: "DOCUMENT_EXTRACTED",
      verification: "SOURCED",
      observed_at: "2026-08-01",
      confidence: 0.92
    },
    freshness: { stale: false, age_days: 33, horizon_days: 365 },
    ...overrides
  };
}

function ready(rows: Record<string, unknown>[], domain = "") {
  return { state: "READY", facts: rows.map(parseFact), domain };
}

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PrivateFactsScreen
      route={{ key: "f", name: "PrivateFacts", params: {} } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetFacts.mockResolvedValue(ready([rawFact()]));
});

describe("PrivateFactsScreen", () => {
  it("says it is loading before the server has answered", () => {
    // A promise that never settles: the screen must have something to show in
    // the window where it knows nothing.
    mockGetFacts.mockReturnValue(new Promise(() => undefined));
    const { getByText } = renderScreen();
    expect(getByText("premium:privateOffice.facts.loading")).toBeTruthy();
  });

  it("renders the record the server sent, under the domain the record declares", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => getByText("Miami, FL"));
    expect(getByText("premium:privateOffice.domains.GENERAL")).toBeTruthy();
    expect(getByText("PRIMARY_RESIDENCE")).toBeTruthy();
  });

  it("groups by the domains present, in the order the server returned them", async () => {
    mockGetFacts.mockResolvedValue(
      ready([
        rawFact({ id: 1, domain: "FINANCIAL", value: "Two accounts" }),
        rawFact({ id: 2, domain: "LEGAL", value: "One filing" }),
        rawFact({ id: 3, domain: "FINANCIAL", value: "One mortgage" })
      ])
    );
    const { getByText, getAllByText, queryByText } = renderScreen();
    await waitFor(() => getByText("Two accounts"));
    // FINANCIAL appears once even though it holds two rows, and a domain with no
    // rows is not drawn at all — the headings come from the data, not from a
    // local copy of the seven-domain vocabulary.
    expect(getAllByText("premium:privateOffice.domains.FINANCIAL")).toHaveLength(1);
    expect(getByText("premium:privateOffice.domains.LEGAL")).toBeTruthy();
    expect(queryByText("premium:privateOffice.domains.HEALTH")).toBeNull();
  });

  it("renders an empty store as empty, and invents no rows to fill it", async () => {
    mockGetFacts.mockResolvedValue(ready([]));
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.facts.empty.title"));
    expect(getByText("premium:privateOffice.facts.empty.body")).toBeTruthy();
    expect(queryByText("premium:privateOffice.facts.why")).toBeNull();
    // An empty store is a real answer, not a failure to get one.
    expect(queryByText("premium:privateOffice.retry")).toBeNull();
  });

  it("does not draw a store it could not read as a store that is empty", async () => {
    mockGetFacts.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.facts.unavailable.title"));
    // The distinction the whole result union exists to protect.
    expect(queryByText("premium:privateOffice.facts.empty.title")).toBeNull();
    expect(getByText("premium:privateOffice.retry")).toBeTruthy();
  });

  it("names the tier when the plan does not include the capability", async () => {
    mockGetFacts.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "PRIVATE" });
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.facts.notEntitled.title"));
    expect(getByText("premium:privateOffice.facts.notEntitled.body")).toBeTruthy();
    // Nothing to retry: a plan is not a transient failure.
    expect(queryByText("premium:privateOffice.retry")).toBeNull();
  });

  it("falls back to generic upgrade copy when the server named no tier", async () => {
    mockGetFacts.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "" });
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.facts.notEntitled.bodyGeneric"));
    expect(queryByText("premium:privateOffice.facts.notEntitled.body")).toBeNull();
  });

  it("keeps switched-off, never-built, unreadable and broken as four separate answers", async () => {
    // The kill switch: distinct copy, and offered a retry, because it can come
    // back within the session.
    mockGetFacts.mockResolvedValue({ state: "FEATURE_DISABLED" });
    const disabled = renderScreen();
    await waitFor(() => disabled.getByText("premium:privateOffice.facts.disabled.title"));
    expect(disabled.queryByText("premium:privateOffice.facts.notImplemented.title")).toBeNull();
    expect(disabled.getByText("premium:privateOffice.retry")).toBeTruthy();

    mockGetFacts.mockResolvedValue({ state: "NOT_IMPLEMENTED" });
    const unbuilt = renderScreen();
    await waitFor(() => unbuilt.getByText("premium:privateOffice.facts.notImplemented.title"));
    // Nothing to sell and nothing to wait for.
    expect(unbuilt.queryByText("premium:privateOffice.retry")).toBeNull();

    mockGetFacts.mockResolvedValue({ state: "ERROR", message: "boom" });
    const broken = renderScreen();
    await waitFor(() => broken.getByText("premium:privateOffice.facts.error.title"));
    expect(broken.queryByText("premium:privateOffice.facts.unavailable.title")).toBeNull();
    // The raw server message is not shown to the member.
    expect(broken.queryByText("boom")).toBeNull();
  });

  it("re-reads the server when the member retries a failed read", async () => {
    mockGetFacts.mockResolvedValue({ state: "ERROR", message: "" });
    const { getByText } = renderScreen();
    await waitFor(() => getByText("premium:privateOffice.retry"));
    mockGetFacts.mockResolvedValue(ready([rawFact()]));
    fireEvent.press(getByText("premium:privateOffice.retry"));
    await waitFor(() => getByText("Miami, FL"));
    expect(mockGetFacts).toHaveBeenCalledTimes(2);
  });

  it("explains a record's provenance without exposing the internal locator", async () => {
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("Miami, FL"));
    fireEvent.press(getByText("premium:privateOffice.facts.why"));

    await waitFor(() => getByText("premium:privateOffice.facts.source"));
    expect(getByText("DOCUMENT")).toBeTruthy();
    expect(getByText("premium:privateOffice.facts.verified")).toBeTruthy();
    expect(getByText("premium:privateOffice.facts.observedLabel")).toBeTruthy();
    expect(getByText("92%")).toBeTruthy();
    expect(getByText("premium:privateOffice.facts.hasDocument")).toBeTruthy();
    // `source_id` is a pointer into private storage, not an explanation.
    expect(queryByText("vault-object-8831")).toBeNull();
  });

  it("says the source was not recorded rather than showing a blank line", async () => {
    mockGetFacts.mockResolvedValue(
      ready([
        rawFact({
          provenance: {
            source_type: "",
            source_id: "",
            has_source_document: false,
            provenance_type: "USER_ASSERTED",
            verification: "SELF_REPORTED",
            observed_at: "",
            confidence: 0.4
          },
          observed_at: ""
        })
      ])
    );
    const { getAllByText, getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("Miami, FL"));
    fireEvent.press(getByText("premium:privateOffice.facts.why"));
    await waitFor(() => getByText("premium:privateOffice.facts.source"));
    // Both the source and the observed line fall back to the same honest phrase.
    expect(getAllByText("premium:privateOffice.facts.sourceUnknown")).toHaveLength(2);
    expect(queryByText("premium:privateOffice.facts.hasDocument")).toBeNull();
  });

  it("closes the provenance sheet without disturbing the list", async () => {
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => getByText("Miami, FL"));
    fireEvent.press(getByText("premium:privateOffice.facts.why"));
    await waitFor(() => getByText("premium:privateOffice.facts.close"));
    fireEvent.press(getByText("premium:privateOffice.facts.close"));
    await waitFor(() => expect(queryByText("premium:privateOffice.facts.close")).toBeNull());
    expect(getByText("Miami, FL")).toBeTruthy();
    // Closing a sheet is not a reason to re-read the store.
    expect(mockGetFacts).toHaveBeenCalledTimes(1);
  });

  it("marks a record the server called stale, and leaves the others unmarked", async () => {
    mockGetFacts.mockResolvedValue(
      ready([
        rawFact({ id: 1, value: "Miami, FL", freshness: { stale: true, age_days: 900, horizon_days: 365 } }),
        rawFact({ id: 2, value: "Lisbon, PT" })
      ])
    );
    const { getAllByText, getByText } = renderScreen();
    await waitFor(() => getByText("Lisbon, PT"));
    // Staleness is the server's word, carried across — not recomputed from the
    // observed date, which would be a second authority on freshness.
    expect(getAllByText("premium:privateOffice.facts.stale")).toHaveLength(1);
  });

  it("renders a verification word this build has never heard of rather than blanking it", async () => {
    mockGetFacts.mockResolvedValue(
      ready([rawFact({ provenance: { ...rawFact().provenance, verification: "COUNTERSIGNED" } })])
    );
    const { getByText } = renderScreen();
    await waitFor(() => getByText("COUNTERSIGNED"));
  });
});
