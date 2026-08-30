/**
 * Zero-delay live end: static architecture guard.
 *
 * Ensures finishBroadcast in LiveHostSessionScreen never re-acquires the
 * blocking `await endLive(...)` ordering before navigation release.
 * Reads source text only — no rendering, no LiveKit, no audio path.
 *
 * Principle: docs/never_block_the_user.md
 */
import * as fs from "fs";
import * as path from "path";

const screenPath = path.resolve(
  __dirname,
  "../screens/LiveHostSessionScreen.tsx",
);
const source = fs.readFileSync(screenPath, "utf8");

function extractFinishBroadcast(src: string): string {
  const start = src.indexOf("const finishBroadcast");
  expect(start).toBeGreaterThan(-1);
  // End at the useCallback closing deps array.
  const end = src.indexOf("]);", start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

describe("LiveEndNonBlockingArchitecture", () => {
  const fn = extractFinishBroadcast(source);

  it("does not await endLive before releasing navigation", () => {
    expect(fn).not.toMatch(/await\s+endLive\s*\(/);
  });

  it("fires endLive non-blocking with failure handling", () => {
    expect(fn).toMatch(/endLive\s*\(\s*liveId\s*\)\s*\.catch/);
  });

  it("still awaits the unchanged broadcast teardown before goBack", () => {
    const stopIdx = fn.indexOf('await room.stopBroadcast("host_ended")');
    const backIdx = fn.indexOf("navigation.goBack()");
    expect(stopIdx).toBeGreaterThan(-1);
    expect(backIdx).toBeGreaterThan(stopIdx);
  });

  it("emits live-end telemetry", () => {
    expect(fn).toContain("[live-end] navigation released");
    expect(fn).toContain("[live-end] server ack");
  });

  it("does not set state after navigation release", () => {
    const backIdx = fn.indexOf("navigation.goBack()");
    const afterBack = fn.slice(backIdx);
    expect(afterBack).not.toContain("setEnding(");
  });
});
