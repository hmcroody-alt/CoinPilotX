/**
 * Every surface that renders a host card must wire the commerce handler.
 *
 * ## Why a source scan and not a render test
 *
 * The PulseDrop overlay is gated on `onOpenCommerceProduct` in both host cards,
 * deliberately: a screen that cannot open a product must not draw a button
 * promising one. The cost of that gate is that *forgetting the prop is silent*.
 * There is no crash, no warning and no failing test — the commerce block simply
 * does not exist, and a PulseDrop Reel degrades into an anonymous product video.
 *
 * That is not hypothetical. It is exactly how the Reel overlay first shipped:
 * the component was complete and unit-tested, `ReelsScreen` did not pass the
 * handler, and the feature rendered nothing in production while the suite was
 * green. A per-surface render test would not have caught it either, because the
 * surface that was broken is the one nobody had written a test for.
 *
 * So this asserts over the source text of the call sites themselves. It is the
 * only form of test that fails when someone adds a *new* screen — which is the
 * case that actually matters, since every existing screen is already wired.
 *
 * ## The one exemption
 *
 * `ContentPreviewRenderer` renders both cards with every callback inert by
 * design; it is the composer's draft preview, and its contract is that nothing
 * in it is actionable. A draft never carries a `commerce` payload in the first
 * place — only PulseDrop publishes those, and PulseDrop does not use the
 * composer — so the gate resolves to "no overlay" for the right reason there.
 * It is named explicitly rather than pattern-matched away, so adding a second
 * inert renderer is a decision someone has to write down.
 */

import fs from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "..", "..");

/** Surfaces allowed to render a host card without commerce navigation. */
const INERT_SURFACES = new Set(["components/preview/ContentPreviewRenderer.tsx"]);

const HOST_CARDS = ["<PostCard", "<ReelPlayerCard"];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      walk(full, out);
    } else if (entry.name.endsWith(".tsx")) {
      out.push(full);
    }
  }
  return out;
}

function callSites(): { file: string; wired: boolean }[] {
  const found: { file: string; wired: boolean }[] = [];
  for (const file of walk(SRC)) {
    const relative = path.relative(SRC, file).split(path.sep).join("/");
    const text = fs.readFileSync(file, "utf8");
    if (!HOST_CARDS.some((tag) => text.includes(tag))) continue;
    found.push({ file: relative, wired: text.includes("onOpenCommerceProduct") });
  }
  return found.sort((a, b) => a.file.localeCompare(b.file));
}

describe("commerce overlay wiring", () => {
  it("finds the host cards at all", () => {
    // A rename that this scan cannot see would turn every assertion below
    // vacuously true, which is worse than no test.
    expect(callSites().length).toBeGreaterThanOrEqual(4);
  });

  it("passes a product handler at every live call site", () => {
    const unwired = callSites()
      .filter((site) => !INERT_SURFACES.has(site.file))
      .filter((site) => !site.wired)
      .map((site) => site.file);
    expect(unwired).toEqual([]);
  });

  it("keeps the draft preview deliberately inert", () => {
    for (const exempt of INERT_SURFACES) {
      const site = callSites().find((candidate) => candidate.file === exempt);
      // If the exemption stops matching a real file it is stale, and a stale
      // exemption is a hole someone else will walk through.
      expect(site).toBeDefined();
      expect(site?.wired).toBe(false);
    }
  });
});
