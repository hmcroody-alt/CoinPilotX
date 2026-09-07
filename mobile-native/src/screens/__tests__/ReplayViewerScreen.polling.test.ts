import fs from "fs";
import path from "path";

const source = fs.readFileSync(path.resolve(__dirname, "../ReplayViewerScreen.tsx"), "utf8");

describe("ReplayViewer replay finalization recovery", () => {
  it("keeps polling while the archive is processing", () => {
    expect(source).toContain("REPLAY_POLL_INTERVAL_MS");
    expect(source).toContain('archive?.status === "failed"');
    expect(source).toContain('archive?.status === "unavailable"');
    expect(source).toContain("setTimeout(resolveReplay, REPLAY_POLL_INTERVAL_MS)");
  });

  it("cancels its pending poll when the viewer closes", () => {
    expect(source).toContain("if (timer) clearTimeout(timer)");
  });

  it("re-resolves a stale URL after the native player rejects it", () => {
    expect(source).toContain("(paramUrl && !failed)");
    expect(source).toContain('setState("resolving")');
    expect(source).toContain("setFailed(false)");
    expect(source).toContain("resolved !== rejectedUrlRef.current");
  });
});
