import { depthForPosition, settledIndexForOffset, shouldHideDockAfterSettle, shouldRenderPage } from "../pagerMath";

describe("settledIndexForOffset", () => {
  it("rounds to the nearest page", () => {
    expect(settledIndexForOffset(0, 390, 10)).toBe(0);
    expect(settledIndexForOffset(390, 390, 10)).toBe(1);
    expect(settledIndexForOffset(410, 390, 10)).toBe(1);
    expect(settledIndexForOffset(585, 390, 10)).toBe(2);
  });

  it("clamps to valid range and tolerates degenerate input", () => {
    expect(settledIndexForOffset(-50, 390, 10)).toBe(0);
    expect(settledIndexForOffset(999999, 390, 10)).toBe(9);
    expect(settledIndexForOffset(100, 0, 10)).toBe(0);
    expect(settledIndexForOffset(100, 390, 0)).toBe(0);
  });
});

describe("depthForPosition", () => {
  it("is identity at rest — no visible neighbor treatment when settled", () => {
    expect(depthForPosition(0, false)).toEqual({ scale: 1, opacity: 1 });
    const offscreen = depthForPosition(1, false);
    expect(offscreen.scale).toBeCloseTo(1, 5);
    expect(offscreen.opacity).toBeCloseTo(1, 5);
  });

  it("applies restrained depth mid-drag", () => {
    const mid = depthForPosition(0.5, false);
    expect(mid.scale).toBeLessThan(1);
    expect(mid.scale).toBeGreaterThanOrEqual(0.96);
    expect(mid.opacity).toBeLessThan(1);
    expect(mid.opacity).toBeGreaterThanOrEqual(0.82);
  });

  it("is disabled entirely under Reduce Motion", () => {
    expect(depthForPosition(0.5, true)).toEqual({ scale: 1, opacity: 1 });
  });
});

describe("shouldRenderPage (current ±1 virtualization)", () => {
  it("mounts only the settled page and its neighbors", () => {
    expect(shouldRenderPage(4, 5)).toBe(true);
    expect(shouldRenderPage(5, 5)).toBe(true);
    expect(shouldRenderPage(6, 5)).toBe(true);
    expect(shouldRenderPage(3, 5)).toBe(false);
    expect(shouldRenderPage(7, 5)).toBe(false);
  });
});

describe("shouldHideDockAfterSettle", () => {
  it("never hides before the first completed swipe", () => {
    expect(shouldHideDockAfterSettle(0, 5000, 1200)).toBe(false);
  });

  it("hides only after the settle delay elapses", () => {
    expect(shouldHideDockAfterSettle(1, 800, 1200)).toBe(false);
    expect(shouldHideDockAfterSettle(1, 1200, 1200)).toBe(true);
    expect(shouldHideDockAfterSettle(3, 1500, 1200)).toBe(true);
  });
});
