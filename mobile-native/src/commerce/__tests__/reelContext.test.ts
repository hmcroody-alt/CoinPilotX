/**
 * What we tell the ranker a reel is about.
 *
 * This is the input to a scoring function that can *lower* a placement below the
 * reels floor, so the failure modes are asymmetric and worth naming:
 *
 *   - **A tag that cannot match** costs one of twelve slots and buys nothing.
 *     `ranking._tokens` drops words of two characters or fewer, and it never
 *     strips a leading `#` — so `#sneakers` sent raw is a token the listing side
 *     can never produce. Both of those are silent: the request succeeds, the
 *     score is just quietly worse.
 *
 *   - **An empty context is not a missing one.** `relevance` returns NEUTRAL
 *     when there is no context and 0.0 when there is one that matches nothing.
 *     A reel with no topic must therefore produce `null`, not `{}`, or we
 *     penalise it for our own lack of information.
 */
import { reelCommerceContext } from "../reelContext";
import type { PulseReel } from "../../api/reels";

function reel(overrides: Partial<PulseReel> = {}): PulseReel {
  return { id: 1, reel_id: 1, ...overrides };
}

describe("reelCommerceContext — what is sent", () => {
  it("carries the category, the topic and the tags", () => {
    expect(
      reelCommerceContext(
        reel({ category: "Shoes", title: "Best running shoes", tags: ["running", "sneakers"] })
      )
    ).toEqual({
      category: "Shoes",
      topic: "Best running shoes",
      tags: ["running", "sneakers"]
    });
  });

  it("prefers the author's title over the caption", () => {
    // A title is a deliberate summary; a caption is as often a greeting.
    const context = reelCommerceContext(
      reel({ title: "Kitchen knife review", caption: "hey everyone welcome back" })
    );
    expect(context?.topic).toBe("Kitchen knife review");
  });

  it("falls back to the caption, then the body", () => {
    expect(reelCommerceContext(reel({ caption: "unboxing the new mixer" }))?.topic).toBe(
      "unboxing the new mixer"
    );
    expect(reelCommerceContext(reel({ body: "garden tool haul" }))?.topic).toBe("garden tool haul");
  });
});

describe("reelCommerceContext — tags the ranker can actually use", () => {
  it("strips the leading hash so a reel tag and a listing tag are one word", () => {
    // The listing side stores `sneakers`. Sent as `#sneakers` this is a total
    // miss that looks like a perfectly well-formed request.
    expect(reelCommerceContext(reel({ tags: ["#sneakers", "##running"] }))?.tags).toEqual([
      "sneakers",
      "running"
    ]);
  });

  it("lowercases, so Sneakers and sneakers are not two tokens", () => {
    expect(reelCommerceContext(reel({ tags: ["Sneakers", "SNEAKERS"] }))?.tags).toEqual([
      "sneakers"
    ]);
  });

  it("drops tags too short for the ranker to keep", () => {
    // `_tokens` discards anything of two characters or fewer, so these can only
    // consume slots a real signal could have used.
    expect(reelCommerceContext(reel({ tags: ["ok", "a", "hi", "shoes"] }))?.tags).toEqual(["shoes"]);
  });

  it("pulls hashtags out of the caption, which is often the only signal", () => {
    // Plenty of reels have no category and no tag array, and their entire
    // subject is three hashtags at the end of the caption — past the 80
    // characters `topic` is allowed to carry.
    const context = reelCommerceContext(
      reel({
        caption: `${"x".repeat(90)} #skincare #serum`
      })
    );
    expect(context?.tags).toEqual(["skincare", "serum"]);
    expect(context?.topic).toHaveLength(80);
  });

  it("ranks author tags ahead of machine tags ahead of scraped hashtags", () => {
    const context = reelCommerceContext(
      reel({ tags: ["authored"], ai_tags: ["derived"], caption: "post #scraped" })
    );
    expect(context?.tags).toEqual(["authored", "derived", "scraped"]);
  });

  it("does not repeat a tag that arrives from two sources", () => {
    const context = reelCommerceContext(
      reel({ tags: ["sneakers"], ai_tags: ["#Sneakers"], caption: "new #sneakers" })
    );
    expect(context?.tags).toEqual(["sneakers"]);
  });

  it("stops at the twelve the server would keep anyway", () => {
    const many = Array.from({ length: 30 }, (_, index) => `tag${index}`);
    expect(reelCommerceContext(reel({ tags: many }))?.tags).toHaveLength(12);
  });

  it("clips a long tag to the server's forty characters", () => {
    expect(reelCommerceContext(reel({ tags: ["z".repeat(200)] }))?.tags?.[0]).toHaveLength(40);
  });
});

describe("reelCommerceContext — when to say nothing", () => {
  it("returns null for a reel that says nothing about itself", () => {
    // Not `{}`. An empty context scores 0.0 on relevance where an absent one
    // scores NEUTRAL, so returning `{}` here would push a chip under the reels
    // floor to punish the reel for having no metadata.
    expect(reelCommerceContext(reel())).toBeNull();
    expect(reelCommerceContext(reel({ category: "  ", caption: "", tags: [] }))).toBeNull();
    expect(reelCommerceContext(reel({ tags: ["ab"] }))).toBeNull();
  });

  it("returns null for a missing reel rather than throwing", () => {
    // The screen looks the reel up by id and the list can have moved under it.
    // A recommendation lookup must never be able to break Reels.
    expect(reelCommerceContext(null)).toBeNull();
    expect(reelCommerceContext(undefined)).toBeNull();
  });

  it("sends a partial context when only one field is known", () => {
    expect(reelCommerceContext(reel({ category: "Beauty" }))).toEqual({ category: "Beauty" });
  });
});

describe("reelCommerceContext — nothing about the viewer", () => {
  it("emits only the four allowlisted fields, never the reel's identifiers", () => {
    // The context describes public content the server already served this
    // client. It must not become a channel for author ids, view counts or
    // anything else the viewer-side ranker should be reading from the account.
    const context = reelCommerceContext(
      reel({
        id: 9,
        user_id: 4242,
        author: { id: 4242, username: "someone" } as PulseReel["author"],
        view_count: 1000,
        viewer_reaction: "like",
        category: "Shoes",
        title: "Sneaker haul",
        tags: ["sneakers"]
      })
    );
    expect(Object.keys(context || {}).sort()).toEqual(["category", "tags", "topic"]);
    expect(JSON.stringify(context)).not.toContain("4242");
  });
});
