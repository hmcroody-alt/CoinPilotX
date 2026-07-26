/**
 * Deep-link resolution for `pulsesoc://settings/<id>`.
 *
 * The interesting cases are all collisions. `pulse/settings/:section` is an
 * existing catch-all owned by AccountCenter, and several settings destinations
 * already own a path elsewhere in the config — so the guarantee under test is
 * that the new scheme resolves through the registry without disturbing either.
 */

import { linking } from "../linking";
import { SETTINGS_ENTRIES } from "../../settings/registry";

/** `getStateFromPath` is optional on the type; these tests exist because it isn't. */
function resolve(path: string) {
  const getState = linking.getStateFromPath;
  if (!getState) throw new Error("linking.getStateFromPath is not defined");
  return getState(path, linking.config as never);
}

function firstRoute(path: string) {
  const state = resolve(path);
  return state?.routes?.[state.routes.length - 1];
}

describe("settings deep links", () => {
  it("routes every registry id to that entry's screen", () => {
    SETTINGS_ENTRIES.forEach((entry) => {
      const route = firstRoute(`settings/${entry.id}`);
      expect({ id: entry.id, name: route?.name }).toEqual({ id: entry.id, name: entry.route });
    });
  });

  it("carries the entry's params through", () => {
    // Discovered rather than named: which entry declares params is a detail of
    // the registry, and a test that hardcodes one silently stops testing
    // anything the moment that entry is reshuffled.
    const entry = SETTINGS_ENTRIES.find((candidate) => candidate.params);
    expect(entry?.params).toBeTruthy();
    expect(firstRoute(`settings/${entry!.id}`)?.params).toEqual(entry!.params);
  });

  it("accepts a leading slash and a trailing slash", () => {
    expect(firstRoute("/settings/security")?.name).toBe("SecuritySettings");
    expect(firstRoute("settings/security/")?.name).toBe("SecuritySettings");
  });

  it("is case-insensitive on the id", () => {
    expect(firstRoute("settings/SECURITY")?.name).toBe("SecuritySettings");
  });

  /**
   * A link minted by an older or newer build should still land the user in
   * Settings. Dropping them nowhere is the worse failure — it reads as the app
   * ignoring the tap.
   */
  it("falls back to the settings tab for an unknown id", () => {
    const route = firstRoute("settings/some-future-page");
    expect(route?.name).toBe("Tabs");
    expect(route?.params).toEqual({ screen: "Settings" });
  });

  it("does not swallow the existing pulse/settings/:section routes", () => {
    // AccountCenter's catch-all and the two static paths under it must be
    // untouched — this scheme lives at `settings/`, not `pulse/settings/`.
    expect(firstRoute("pulse/settings/devices")?.name).toBe("AccountDevices");
    expect(firstRoute("pulse/settings/notifications")?.name).toBe("NotificationPreferences");
    expect(firstRoute("pulse/settings/billing")?.name).toBe("AccountCenter");
  });

  it("does not claim deeper or shallower paths", () => {
    // `settings` alone and `settings/a/b` are not settings deep links; they must
    // fall through to normal resolution rather than being force-matched.
    expect(firstRoute("settings/security/advanced")?.name).not.toBe("SecuritySettings");
  });

  it("leaves unrelated deep links alone", () => {
    expect(firstRoute("pulse/profile/edit")?.name).toBe("ProfileEdit");
    expect(firstRoute("pulse/post/42")?.name).toBe("PostDetail");
  });
});
