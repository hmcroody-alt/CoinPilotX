/**
 * Consent-model tests for Spatial Motion settings (mission §18–20).
 *
 * What must never drift: motion defaults OFF (swipe-only, not onboarded),
 * corrupt or hostile stored values sanitize back to safe defaults, and
 * updates round-trip through the same sanitizer — no path can persist an
 * enabled motion mode without an explicit, valid user choice.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  DEFAULT_MOTION_SETTINGS,
  __resetMotionSettingsForTests,
  getMotionSettings,
  hydrateMotionSettings,
  updateMotionSettings
} from "../motionSettings";

const STORAGE_KEY = "pulsesoc.spatialMotion.settings.v1";

describe("motion settings consent model", () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    __resetMotionSettingsForTests();
  });

  it("defaults to swipe-only with onboarding not completed", () => {
    expect(DEFAULT_MOTION_SETTINGS.mode).toBe("swipe-only");
    expect(DEFAULT_MOTION_SETTINGS.onboarded).toBe(false);
    expect(getMotionSettings()).toEqual(DEFAULT_MOTION_SETTINGS);
  });

  it("hydrates safe defaults when nothing is stored", async () => {
    const settings = await hydrateMotionSettings();
    expect(settings).toEqual(DEFAULT_MOTION_SETTINGS);
  });

  it("sanitizes corrupt or unknown stored values back to safe defaults", async () => {
    await AsyncStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        mode: "always-on",
        sensitivity: "extreme",
        scope: "everywhere",
        hapticsEnabled: "yes",
        neutralBaselineRad: "0.4",
        onboarded: "true"
      })
    );
    const settings = await hydrateMotionSettings();
    expect(settings.mode).toBe("swipe-only");
    expect(settings.sensitivity).toBe("medium");
    expect(settings.scope).toBe("both");
    expect(settings.neutralBaselineRad).toBeNull();
    // String "true" is not consent — onboarded must be a literal boolean.
    expect(settings.onboarded).toBe(false);
  });

  it("survives unparseable storage without throwing", async () => {
    await AsyncStorage.setItem(STORAGE_KEY, "{not json");
    const settings = await hydrateMotionSettings();
    expect(settings).toEqual(DEFAULT_MOTION_SETTINGS);
  });

  it("persists a valid explicit choice and round-trips it", async () => {
    await hydrateMotionSettings();
    const updated = await updateMotionSettings({ mode: "tilt", onboarded: true });
    expect(updated.mode).toBe("tilt");
    expect(updated.onboarded).toBe(true);

    __resetMotionSettingsForTests();
    const rehydrated = await hydrateMotionSettings();
    expect(rehydrated.mode).toBe("tilt");
    expect(rehydrated.onboarded).toBe(true);
  });

  it("rejects invalid values inside an update patch", async () => {
    const updated = await updateMotionSettings({
      mode: "warp-speed" as never,
      sensitivity: "ludicrous" as never
    });
    expect(updated.mode).toBe("swipe-only");
    expect(updated.sensitivity).toBe("medium");
  });
});
