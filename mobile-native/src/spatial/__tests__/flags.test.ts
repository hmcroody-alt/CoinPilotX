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
  it("all default OFF so legacy is untouched (rollback guarantee)", () => {
    expect(spatialConsoleEnabled()).toBe(false);
    expect(spatialHomeFeedEnabled()).toBe(false);
    expect(spatialReelsEnabled()).toBe(false);
    expect(spatialCreateEnabled()).toBe(false);
    expect(messagesVisualRefreshEnabled()).toBe(false);
    expect(immersiveNavigatorEnabled()).toBe(false);
    expect(spatialMotionEnabled()).toBe(false);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(tiltParallaxEnabled()).toBe(false);
  });

  it("sub-flags stay OFF without the master switch", () => {
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
    expect(spatialReelsEnabled()).toBe(false);
    expect(spatialCreateEnabled()).toBe(false);
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
