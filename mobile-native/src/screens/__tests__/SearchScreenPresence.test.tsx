/**
 * PERSON ≠ PRESENCE, and search is where the two are most easily confused: an
 * artist row and a creator row look alike in a result list. What is tested here
 * is that a presence result is routed to the presence surface and never handed
 * to the personal-profile navigator, which would resolve it against a user
 * account that does not exist.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

const mockSearch = jest.fn();
jest.mock("../../api/search", () => ({
  ...jest.requireActual("../../api/search"),
  searchPulse: (...args: unknown[]) => mockSearch(...args),
  loadCachedPulseSearch: async () => null,
  loadRecentSearches: async () => [],
  saveRecentSearch: async () => []
}));

import { normalizeSearchResponse } from "../../api/search";
import { SearchScreen } from "../SearchScreen";

const nav = () => ({ navigate: jest.fn(), setOptions: jest.fn(), goBack: jest.fn() });

beforeEach(() => {
  jest.clearAllMocks();
  mockSearch.mockResolvedValue(
    normalizeSearchResponse({
      ok: true,
      query: "night",
      results: {
        presences: [
          {
            id: 7,
            title: "Night Signal",
            description: "Musician",
            type: "presence",
            presence_type: "ARTIST",
            handle: "nightsignal",
            url: "/pulse/pages/nightsignal"
          }
        ],
        creators: [
          { id: 22, title: "Nightowl", description: "", type: "creator", username: "nightowl", url: "/pulse/u/nightowl" }
        ]
      } as never
    })
  );
});

function show() {
  const navigation = nav();
  const view = render(
    <SearchScreen
      route={{ key: "s", name: "Search", params: { query: "night" } } as never}
      navigation={navigation as never}
    />
  );
  return { view, navigation };
}

it("opens a presence result on the presence surface, not the profile navigator", async () => {
  const { view, navigation } = show();
  await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
  fireEvent.press(view.getByText("Night Signal"));
  expect(navigation.navigate).toHaveBeenCalledWith(
    "Page",
    expect.objectContaining({ pageId: 7, handle: "nightsignal" })
  );
  expect(navigation.navigate).not.toHaveBeenCalledWith("ProfileDetail", expect.anything());
});

it("still routes a person result to the profile surface", async () => {
  const { view, navigation } = show();
  await waitFor(() => expect(view.queryByText("Nightowl")).toBeTruthy());
  fireEvent.press(view.getByText("Nightowl"));
  await waitFor(() => expect(navigation.navigate).toHaveBeenCalled());
  expect(navigation.navigate).not.toHaveBeenCalledWith("Page", expect.anything());
});
