/**
 * The screen owns fetch/loading/error/refresh and nothing else — rendering and
 * the honest empty state belong to UndxCapabilityPanel. So these tests assert the
 * wiring: a successful fetch shows the real server identity, and a failed fetch
 * with no prior payload shows an explicit error state with a retry, never a
 * fabricated capability list.
 */

import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockFetch = jest.fn();
jest.mock("../../api/undxSelfKnowledge", () => ({
  ...jest.requireActual("../../api/undxSelfKnowledge"),
  fetchUndxSelfKnowledge: () => mockFetch()
}));

import { UndxCapabilitiesScreen } from "../UndxCapabilitiesScreen";
import { UndxSelfKnowledge } from "../../api/undxSelfKnowledge";

function knowledge(): UndxSelfKnowledge {
  return {
    assistant: { name: "UNDX", description: "PulseSoc intelligence companion." },
    company: {
      version: 1,
      legal_name: "CoinPlotXAI Inc.",
      primary_product: "PulseSoc",
      founder: { name: "Roody Cherie", title: "Founder & CEO" },
      product_category: ["social platform"]
    },
    canonical: {
      company_explanation: "Roody Cherie is the Founder and CEO of CoinPlotXAI Inc.",
      pulsesoc_definition: "PulseSoc is an intelligent digital ecosystem."
    },
    capabilities: {
      counts: { total: 1, read_only: 1, write: 0, requires_confirmation: 0, by_domain: { crypto: 1 } },
      available: [
        {
          capability_id: "crypto.alerts.list",
          description: "List price alerts.",
          domain: "crypto",
          status: "AVAILABLE",
          executionMode: "READ",
          requiresConfirmation: false,
          requiresVerification: false,
          receiptRequired: false
        }
      ]
    },
    honesty: {
      never_fabricates: ["revenue"],
      capability_rule: "Anything not listed here is not executable yet."
    },
    version: { company_identity: 1 }
  };
}

// The screen ignores its navigation props entirely, so these only need to satisfy
// the type. `as never` would typecheck on its own but makes the spread below
// illegal (TS2698: spread types may only be created from object types).
const navProps = { navigation: {}, route: { params: undefined } } as unknown as React.ComponentProps<
  typeof UndxCapabilitiesScreen
>;

beforeEach(() => {
  mockFetch.mockReset();
});

describe("UndxCapabilitiesScreen", () => {
  it("renders server identity once the fetch resolves", async () => {
    mockFetch.mockResolvedValue(knowledge());
    const { getByText, getByTestId } = render(<UndxCapabilitiesScreen {...navProps} />);
    await waitFor(() => expect(getByTestId("undx-capability-panel")).toBeTruthy());
    expect(getByText("CoinPlotXAI Inc.")).toBeTruthy();
    expect(getByTestId("undx-capability-crypto.alerts.list")).toBeTruthy();
  });

  it("shows an error state with retry when the fetch fails and nothing is cached", async () => {
    mockFetch.mockRejectedValue(new Error("network down"));
    const { getByText, queryByTestId } = render(<UndxCapabilitiesScreen {...navProps} />);
    await waitFor(() => expect(getByText("UNDX capabilities are unavailable")).toBeTruthy());
    expect(getByText("network down")).toBeTruthy();
    expect(queryByTestId("undx-capability-panel")).toBeNull();

    // Retry re-invokes the fetch; this time it succeeds.
    mockFetch.mockResolvedValue(knowledge());
    await act(async () => {
      fireEvent.press(getByText("Try again"));
    });
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  });
});
