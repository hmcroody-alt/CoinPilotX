import {
  __clearSpatialFlagOverrides,
  __setSpatialFlagOverride,
  immersiveNavigatorEnabled,
  messagesVisualRefreshEnabled,
  spatialConsoleEnabled,
  spatialCreateEnabled,
  spatialHomeFeedEnabled,
  spatialMotionEnabled,
  spatialReelsEnabled,
  tiltNavigationEnabled,
  tiltParallaxEnabled
} from "../flags";

afterEach(() => {
  __clearSpatialFlagOverrides();
});

describe("spatial flags", () => {
  it("everything still rolling out defaults OFF so legacy is untouched", () => {
    // The three shipped Reels flags default ON and are pinned separately in
    // flagDefaults.test.ts. Everything else keeps the rollback-by-doing-nothing
    // posture: unset means off.
    expect(spatialHomeFeedEnabled()).toBe(false);
    expect(spatialCreateEnabled()).toBe(false);
    expect(messagesVisualRefreshEnabled()).toBe(false);
    expect(spatialMotionEnabled()).toBe(false);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(tiltParallaxEnabled()).toBe(false);
  });

  it("sub-flags stay OFF without the master switch", () => {
    // The master is set explicitly rather than left to its default: this test is
    // about the AND with the master, so it must not change meaning the next time
    // a flag's default posture moves.
    __setSpatialFlagOverride("spatialConsoleEnabled", false);
    __setSpatialFlagOverride("spatialHomeFeedEnabled", true);
    __setSpatialFlagOverride("spatialReelsEnabled", true);
    __setSpatialFlagOverride("spatialCreateEnabled", true);
    __setSpatialFlagOverride("messagesVisualRefreshEnabled", true);
    __setSpatialFlagOverride("immersiveNavigatorEnabled", true);
    expect(spatialHomeFeedEnabled()).toBe(false);
    expect(spatialReelsEnabled()).toBe(false);
    expect(spatialCreateEnabled()).toBe(false);
    expect(messagesVisualRefreshEnabled()).toBe(false);
    expect(immersiveNavigatorEnabled()).toBe(false);
  });

  it("master + sub enables exactly that surface", () => {
    __setSpatialFlagOverride("spatialConsoleEnabled", true);
    __setSpatialFlagOverride("spatialHomeFeedEnabled", true);
    expect(spatialHomeFeedEnabled()).toBe(true);
    // Surfaces that are still rolling out stay off even with the master on.
    expect(spatialCreateEnabled()).toBe(false);
    expect(messagesVisualRefreshEnabled()).toBe(false);
    expect(spatialMotionEnabled()).toBe(false);
  });

  it("turning the master off rolls everything back at once", () => {
    __setSpatialFlagOverride("spatialConsoleEnabled", true);
    __setSpatialFlagOverride("spatialReelsEnabled", true);
    expect(spatialReelsEnabled()).toBe(true);
    __setSpatialFlagOverride("spatialConsoleEnabled", false);
    expect(spatialReelsEnabled()).toBe(false);
  });

  it("tilt flags require BOTH the console master and the motion master", () => {
    __setSpatialFlagOverride("tiltNavigationEnabled", true);
    __setSpatialFlagOverride("tiltParallaxEnabled", true);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(tiltParallaxEnabled()).toBe(false);

    __setSpatialFlagOverride("spatialConsoleEnabled", true);
    // Console on but motion master still off — tilt stays dead.
    expect(spatialMotionEnabled()).toBe(false);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(tiltParallaxEnabled()).toBe(false);

    __setSpatialFlagOverride("spatialMotionEnabled", true);
    expect(spatialMotionEnabled()).toBe(true);
    expect(tiltNavigationEnabled()).toBe(true);
    expect(tiltParallaxEnabled()).toBe(true);
  });

  it("turning the motion master off rolls back tilt but leaves swipe surfaces", () => {
    __setSpatialFlagOverride("spatialConsoleEnabled", true);
    __setSpatialFlagOverride("spatialHomeFeedEnabled", true);
    __setSpatialFlagOverride("spatialMotionEnabled", true);
    __setSpatialFlagOverride("tiltNavigationEnabled", true);
    expect(tiltNavigationEnabled()).toBe(true);

    __setSpatialFlagOverride("spatialMotionEnabled", false);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(spatialHomeFeedEnabled()).toBe(true);
  });
});
