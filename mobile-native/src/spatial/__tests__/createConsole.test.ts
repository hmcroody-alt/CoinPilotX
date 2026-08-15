/**
 * Contract tests for the Spatial Create Console (mission §17).
 *
 * The carousel's rendering is thin; what must never drift is the data contract:
 * exactly six modes in mission order, the reel subtitle, Go Live at the far end
 * behind confirmation, and routing that only ever targets existing composer /
 * camera / Live-setup flows — never a broadcast or publish action.
 */
import { buildModeNavigation, CREATE_CONSOLE_MODES } from "../SpatialCreateConsole";

describe("spatial create console modes", () => {
  it("presents exactly six modes in mission order", () => {
    expect(CREATE_CONSOLE_MODES.map((mode) => mode.title)).toEqual([
      "Photo",
      "Video",
      "Create a Signal",
      "Camera",
      "Create Reel",
      "Go Live"
    ]);
  });

  it("labels Create Reel with the mandated subtitle", () => {
    const reel = CREATE_CONSOLE_MODES.find((mode) => mode.id === "reel");
    expect(reel?.subtitle).toBe("Record or upload clips");
  });

  it("puts Go Live at the far end, and only Go Live requires confirmation", () => {
    const last = CREATE_CONSOLE_MODES[CREATE_CONSOLE_MODES.length - 1];
    expect(last.id).toBe("live");
    expect(last.requiresConfirmation).toBe(true);
    expect(last.subtitle).toBe("Confirmation required");
    for (const mode of CREATE_CONSOLE_MODES.slice(0, -1)) {
      expect(mode.requiresConfirmation).toBeUndefined();
    }
  });

  it("routes every mode into an existing flow", () => {
    expect(buildModeNavigation("signal")).toEqual({
      route: "Home",
      params: { openComposer: true, composerMode: "post" }
    });
    expect(buildModeNavigation("photo").route).toBe("CameraStudio");
    expect(buildModeNavigation("video").route).toBe("CameraStudio");
    expect(buildModeNavigation("camera").route).toBe("CameraStudio");
    expect(buildModeNavigation("reel")).toEqual({
      route: "CameraStudio",
      params: {
        target: "reel",
        mode: "reel",
        captureMode: "video",
        returnToComposer: true,
        composerMode: "reel",
        title: "Reel Camera"
      }
    });
  });

  it("Go Live only opens the Live setup studio — no broadcast route, no autopublish", () => {
    const live = buildModeNavigation("live");
    expect(live.route).toBe("LiveStudio");
    // The routes that actually host/join a broadcast must be unreachable here.
    for (const mode of CREATE_CONSOLE_MODES) {
      const target = buildModeNavigation(mode.id);
      expect(["NativeLiveHost", "LiveDetail", "Call"]).not.toContain(target.route);
      expect(JSON.stringify(target.params)).not.toMatch(/autoPublish|qaAutoPublish/i);
    }
  });
});
