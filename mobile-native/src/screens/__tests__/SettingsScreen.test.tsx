/**
 * The settings index.
 *
 * This file used to be `SettingsScreen.biometric.test.tsx` and covered a Face ID
 * toggle rendered inline on the index. That control now lives on
 * `SecuritySettingsScreen`, which has its own suite — so the assertions here are
 * the mirror image: the index must *not* own security controls, and must
 * instead route to the screen that does.
 *
 * The rest of the file guards the properties that make a registry-driven index
 * safe: search narrows without inventing rows, hidden entries stay hidden, and
 * every visible row navigates somewhere.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: mockNavigate, goBack: jest.fn() })
}));

const mockSetAuthState = jest.fn();
let mockAuthState: { status: string; user?: { user_id: number; username: string } } = {
  status: "signedIn",
  user: { user_id: 5, username: "roody" }
};
jest.mock("../../session/auth", () => ({
  signOut: jest.fn(),
  signOutEverywhere: jest.fn(),
  useAuth: () => ({ setAuthState: mockSetAuthState, authState: mockAuthState })
}));

let mockDeveloperEnabled = false;
jest.mock("../../settings/store", () => ({
  usePreferenceGroup: () => ({
    value: { enabled: mockDeveloperEnabled, showPerfOverlay: false, verboseApiLogging: false },
    setGroup: jest.fn(),
    pending: false,
    status: "idle",
    error: null
  }),
  usePreferences: () => ({
    preferences: {},
    hydrated: true,
    refreshing: false,
    status: "idle",
    error: null,
    pendingGroups: [],
    update: jest.fn(),
    refresh: jest.fn(),
    resetAll: jest.fn(),
    clearError: jest.fn()
  })
}));

import { SettingsScreen } from "../SettingsScreen";
import { SETTINGS_ENTRIES } from "../../settings/registry";
import { activateLocale } from "../../i18n/engine";

/**
 * Every label on this screen — including the search placeholder these tests
 * query by — comes from the catalogs, and the engine only serves a namespace
 * once it is loaded. The provider does this before the first frame in the app;
 * without it here the rows render humanized keys and search matches nothing.
 */
beforeAll(async () => {
  await activateLocale("en");
});

describe("SettingsScreen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuthState = { status: "signedIn", user: { user_id: 5, username: "roody" } };
    mockDeveloperEnabled = false;
  });

  describe("security controls moved off the index", () => {
    it("renders no biometric or two-factor control of its own", () => {
      const { queryByTestId } = render(<SettingsScreen />);
      expect(queryByTestId("settings-biometric-toggle")).toBeNull();
      expect(queryByTestId("security-biometric-toggle")).toBeNull();
      expect(queryByTestId("security-two-factor-toggle")).toBeNull();
    });

    it("routes to the Security screen instead", () => {
      const { getByTestId } = render(<SettingsScreen />);
      fireEvent.press(getByTestId("settings-entry-security"));
      expect(mockNavigate).toHaveBeenCalledWith("SecuritySettings", undefined);
    });
  });

  describe("rows", () => {
    it("renders a row for every visible registry entry", () => {
      const { getByTestId } = render(<SettingsScreen />);
      SETTINGS_ENTRIES.filter((entry) => !entry.developerOnly).forEach((entry) => {
        expect(getByTestId(`settings-entry-${entry.id}`)).toBeTruthy();
      });
    });

    it("passes the registry params through when navigating", () => {
      // Discovered rather than named: which entry carries params is the
      // registry's business, and hardcoding one turns this into a test that
      // quietly stops asserting anything the day that entry is reshuffled.
      const entry = SETTINGS_ENTRIES.find((candidate) => candidate.params);
      expect(entry).toBeTruthy();
      const { getByTestId } = render(<SettingsScreen />);
      fireEvent.press(getByTestId(`settings-entry-${entry!.id}`));
      expect(mockNavigate).toHaveBeenCalledWith(entry!.route, entry!.params);
    });

    it("hides developer options until they are enabled", () => {
      const { queryByTestId } = render(<SettingsScreen />);
      expect(queryByTestId("settings-entry-developer")).toBeNull();

      mockDeveloperEnabled = true;
      const { getByTestId } = render(<SettingsScreen />);
      expect(getByTestId("settings-entry-developer")).toBeTruthy();
    });

    it("hides authenticated-only rows when signed out", () => {
      mockAuthState = { status: "signedOut" };
      const { queryByTestId, getByTestId } = render(<SettingsScreen />);
      expect(queryByTestId("settings-entry-blocked")).toBeNull();
      expect(queryByTestId("settings-entry-security")).toBeNull();
      // Non-auth rows are still reachable — a signed-out user can read Legal.
      expect(getByTestId("settings-entry-legal")).toBeTruthy();
    });
  });

  describe("search", () => {
    it("narrows to matching rows and hides the rest", async () => {
      const { getByPlaceholderText, getByTestId, queryByTestId } = render(<SettingsScreen />);
      fireEvent.changeText(getByPlaceholderText("Search settings"), "dark");
      await waitFor(() => expect(getByTestId("settings-entry-appearance")).toBeTruthy());
      expect(queryByTestId("settings-entry-storage")).toBeNull();
    });

    it("offers a way out when nothing matches", async () => {
      const { getByPlaceholderText, findByTestId, getByTestId } = render(<SettingsScreen />);
      fireEvent.changeText(getByPlaceholderText("Search settings"), "zzzznotasetting");
      const clear = await findByTestId("settings-clear-search");

      fireEvent.press(clear);
      await waitFor(() => expect(getByTestId("settings-entry-appearance")).toBeTruthy());
    });

    it("hides the session actions while searching", async () => {
      const { getByPlaceholderText, getByTestId, queryAllByTestId } = render(<SettingsScreen />);
      expect(getByTestId("settings-sign-out")).toBeTruthy();
      fireEvent.changeText(getByPlaceholderText("Search settings"), "dark");
      await waitFor(() => expect(queryAllByTestId("settings-sign-out").length).toBe(0));
    });
  });

  describe("session", () => {
    it("shows both sign-out actions when signed in", () => {
      const { getByTestId } = render(<SettingsScreen />);
      expect(getByTestId("settings-sign-out")).toBeTruthy();
      expect(getByTestId("settings-sign-out-everywhere")).toBeTruthy();
    });

    it("shows neither when signed out", () => {
      mockAuthState = { status: "signedOut" };
      const { queryByTestId } = render(<SettingsScreen />);
      expect(queryByTestId("settings-sign-out")).toBeNull();
      expect(queryByTestId("settings-sign-out-everywhere")).toBeNull();
    });
  });
});
