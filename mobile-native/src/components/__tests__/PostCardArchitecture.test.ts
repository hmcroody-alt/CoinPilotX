import { readFileSync } from "fs";
import { join } from "path";

const postCardSource = readFileSync(join(__dirname, "..", "PostCard.tsx"), "utf8");

describe("PostCard architecture guards", () => {
  it("defaults media layout to fullBleed", () => {
    expect(postCardSource).toMatch(/mediaLayout\s*=\s*"fullBleed"/);
  });

  it("derives full-bleed media width from measured viewport, not fixed widths", () => {
    // The bleed contract must key off window/card measurements, never a hardcoded
    // per-screen edgeInset margin.
    expect(postCardSource).toMatch(/computeMediaBleedStyle/);
    expect(postCardSource).not.toMatch(/-edgeInset/);
    expect(postCardSource).not.toMatch(/edgeInset\?\s*:/);
  });

  it("keeps Save grouped inside the shared action row (no standalone/floating save region)", () => {
    const saveIndex = postCardSource.indexOf("home-feed-save-");
    const actionRowIndex = postCardSource.indexOf("styles.actionRow");
    expect(saveIndex).toBeGreaterThan(-1);
    expect(actionRowIndex).toBeGreaterThan(-1);

    // The Save button must live after the action row opens and before the row's
    // sibling (the reaction selector) — i.e. inside the same row container.
    const reactionSelectorIndex = postCardSource.indexOf("home-feed-reaction-selector-");
    expect(saveIndex).toBeGreaterThan(actionRowIndex);
    expect(saveIndex).toBeLessThan(reactionSelectorIndex);

    // Guard against reintroducing a separate/oversized save surface.
    expect(postCardSource).not.toMatch(/styles\.saveRow/);
    expect(postCardSource).not.toMatch(/styles\.floatingSave/);
    expect(postCardSource).not.toMatch(/styles\.saveRegion/);
  });

  it("orders the compact actions Like, Comment, Repost, Share, Save", () => {
    const order = ["home-feed-like-", "home-feed-comment-", "home-feed-repost-", "home-feed-share-", "home-feed-save-"];
    const positions = order.map((id) => postCardSource.indexOf(id));
    positions.forEach((pos) => expect(pos).toBeGreaterThan(-1));
    const sorted = [...positions].sort((a, b) => a - b);
    expect(positions).toEqual(sorted);
  });
});
