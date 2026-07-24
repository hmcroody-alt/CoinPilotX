import { resolvePreviewStop, resolvePreviewToggle } from "../musicPreviewLifecycle";

describe("resolvePreviewToggle", () => {
  it("starts a preview when nothing is playing", () => {
    expect(resolvePreviewToggle("", "track-a")).toEqual({ stopCurrent: true, nextTrackId: "track-a" });
  });

  it("stops when the currently-previewing track is tapped again", () => {
    expect(resolvePreviewToggle("track-a", "track-a")).toEqual({ stopCurrent: true, nextTrackId: "" });
  });

  it("stops the old track and starts the new one when switching tracks (only one at a time)", () => {
    const transition = resolvePreviewToggle("track-a", "track-b");
    expect(transition.stopCurrent).toBe(true);
    expect(transition.nextTrackId).toBe("track-b");
  });
});

describe("resolvePreviewStop", () => {
  it("requests a stop when a track is currently previewing (picker close / select / leave / finish)", () => {
    expect(resolvePreviewStop("track-a")).toEqual({ stopCurrent: true, nextTrackId: "" });
  });

  it("is a no-op when nothing is previewing so closing an idle picker does no work", () => {
    expect(resolvePreviewStop("")).toEqual({ stopCurrent: false, nextTrackId: "" });
  });
});

describe("music-picker preview lifecycle (mission invariants)", () => {
  // Simulate the composer's imperative side effects against the pure resolver to
  // prove the mandated invariant: at most one preview plays, and it always stops
  // when the picker closes.
  function makeEngine() {
    let loadedTrackId = "";
    return {
      get playing() {
        return loadedTrackId;
      },
      toggle(tappedTrackId: string) {
        const t = resolvePreviewToggle(loadedTrackId, tappedTrackId);
        if (t.stopCurrent) loadedTrackId = "";
        if (t.nextTrackId) loadedTrackId = t.nextTrackId;
      },
      closePicker() {
        if (resolvePreviewStop(loadedTrackId).stopCurrent) loadedTrackId = "";
      }
    };
  }

  it("never has two tracks playing at once", () => {
    const engine = makeEngine();
    engine.toggle("a");
    expect(engine.playing).toBe("a");
    engine.toggle("b");
    expect(engine.playing).toBe("b");
    engine.toggle("c");
    expect(engine.playing).toBe("c");
  });

  it("stops preview when the picker closes after selecting a track", () => {
    const engine = makeEngine();
    engine.toggle("a");
    expect(engine.playing).toBe("a");
    // Selecting a track closes the picker -> preview must stop.
    engine.closePicker();
    expect(engine.playing).toBe("");
  });

  it("closing an already-silent picker is safe", () => {
    const engine = makeEngine();
    engine.closePicker();
    expect(engine.playing).toBe("");
  });
});
