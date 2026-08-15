/**
 * Consent-model tests for Spatial Motion settings (mission §18–20).
 *
 * What must never drift: motion defaults OFF (swipe-only, not onboarded),
 * corrupt or hostile stored values sanitize back to safe defaults, and
 * updates round-trip through the same sanitizer — no path can persist an
 * enabled motion mode without an explicit, valid user choice.
 *
 * The second block covers the Reels-only scope correction: real devices are
 * carrying `scope` values written when Home Feed motion existed, and reading
 * one must never crash, must never re-prompt, and must never turn tilt on in
 * Reels for somebody who only ever consented to it on the Feed.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  DEFAULT_MOTION_SETTINGS,
  __resetMotionSettingsForTests,
  getMotionSettings,
  hydrateMotionSettings,
  resetMotionSettings,
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

describe("legacy motion scope migration", () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    __resetMotionSettingsForTests();
  });

  async function storedAs(record: Record<string, unknown>) {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(record));
    return hydrateMotionSettings();
  }

  it("drops motion for a Feed-only tester rather than moving it to Reels", async () => {
    const settings = await storedAs({ mode: "tilt", scope: "feed", onboarded: true });
    expect(settings.mode).toBe("swipe-only");
    // Onboarding is a decision they already made — do not ask again.
    expect(settings.onboarded).toBe(true);
  });

  it("drops parallax for a Feed-only tester too", async () => {
    const settings = await storedAs({ mode: "parallax", scope: "feed", onboarded: true });
    expect(settings.mode).toBe("swipe-only");
  });

  it("keeps the chosen mode for scope 'both'", async () => {
    const settings = await storedAs({ mode: "tilt", scope: "both", onboarded: true, sensitivity: "high" });
    expect(settings.mode).toBe("tilt");
    expect(settings.sensitivity).toBe("high");
    expect(settings.onboarded).toBe(true);
  });

  it("keeps the chosen mode for scope 'reels'", async () => {
    const settings = await storedAs({ mode: "parallax", scope: "reels", onboarded: true });
    expect(settings.mode).toBe("parallax");
  });

  it("keeps the chosen mode for an unknown scope value", async () => {
    const settings = await storedAs({ mode: "tilt", scope: "everywhere", onboarded: true });
    expect(settings.mode).toBe("tilt");
  });

  it("never writes scope back, and stays idempotent across a re-read", async () => {
    await storedAs({ mode: "tilt", scope: "both", onboarded: true });
    await updateMotionSettings({ sensitivity: "low" });

    const persisted = JSON.parse((await AsyncStorage.getItem(STORAGE_KEY)) || "{}");
    expect(persisted).not.toHaveProperty("scope");

    __resetMotionSettingsForTests();
    const rehydrated = await hydrateMotionSettings();
    expect(rehydrated.mode).toBe("tilt");
    expect(rehydrated.sensitivity).toBe("low");
  });

  it("does not resurrect motion for a Feed-only record on a second read", async () => {
    await storedAs({ mode: "tilt", scope: "feed", onboarded: true });
    __resetMotionSettingsForTests();
    const again = await hydrateMotionSettings();
    expect(again.mode).toBe("swipe-only");
  });

  it("survives a non-string scope without throwing", async () => {
    const settings = await storedAs({ mode: "tilt", scope: { feed: true }, onboarded: true });
    expect(settings.mode).toBe("tilt");
  });
});

/**
 * "Reset Reels motion settings" (mission §8).
 *
 * Distinct from "Replay tutorial", which re-shows the explainer and keeps the
 * user's choices. Reset is the user-facing counterpart to the motion rollback
 * flag: it returns the device to the pre-consent state, which is the only state
 * in which the sensor provably cannot subscribe at all.
 */
describe("reset Reels motion settings", () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    __resetMotionSettingsForTests();
  });

  it("returns every preference to its factory value, including onboarding", async () => {
    await updateMotionSettings({
      mode: "tilt",
      sensitivity: "high",
      hapticsEnabled: false,
      neutralBaselineRad: 0.42,
      onboarded: true
    });

    const after = await resetMotionSettings();

    expect(after).toEqual(DEFAULT_MOTION_SETTINGS);
    // onboarded: false is the point, not a side effect — it is what makes the
    // sensor ineligible, so a reset device cannot be running motion.
    expect(after.onboarded).toBe(false);
    expect(after.mode).toBe("swipe-only");
    expect(getMotionSettings()).toEqual(DEFAULT_MOTION_SETTINGS);
  });

  it("removes the record rather than overwriting it with defaults", async () => {
    await updateMotionSettings({ mode: "parallax", onboarded: true });
    await resetMotionSettings();

    // A reset device is byte-identical to one that never ran a motion build.
    expect(await AsyncStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("does not resurrect the old settings on the next hydrate", async () => {
    await updateMotionSettings({ mode: "tilt", onboarded: true, neutralBaselineRad: 0.42 });
    await resetMotionSettings();

    __resetMotionSettingsForTests();
    const rehydrated = await hydrateMotionSettings();

    expect(rehydrated).toEqual(DEFAULT_MOTION_SETTINGS);
  });

  it("still resets in memory when storage refuses the delete", async () => {
    await updateMotionSettings({ mode: "tilt", onboarded: true });
    const removeItem = jest.spyOn(AsyncStorage, "removeItem").mockRejectedValueOnce(new Error("disk full"));

    await expect(resetMotionSettings()).resolves.toEqual(DEFAULT_MOTION_SETTINGS);
    expect(getMotionSettings().mode).toBe("swipe-only");

    removeItem.mockRestore();
  });
});
