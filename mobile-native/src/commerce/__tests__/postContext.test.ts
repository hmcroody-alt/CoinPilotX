/**
 * What we tell the ranker a post is about.
 *
 * The failure modes are the same asymmetric pair `reelContext.test.ts` names, and
 * they bite harder here: `min_score("post_detail")` sits a tenth above the feed's
 * floor, so a context that scores badly on this surface does not merely rank a
 * card lower — it removes it.
 *
 *   - **A tag that cannot match** costs one of twelve slots and buys nothing.
 *     `ranking._tokens` drops words of two characters or fewer and never strips a
 *     leading `#`, so `#sneakers` sent raw is a token the listing side can never
 *     produce. Both are silent: the request succeeds, the score is quietly worse.
 *
 *   - **An empty context is not a missing one.** `relevance` answers NEUTRAL with
 *     no context and 0.0 for a context that matches nothing, so a post with no
 *     readable subject must produce `null` rather than `{}`.
 *
 * And one that is specific to posts: a post's whole topical surface is a title and
 * a body. There is no `category` at any layer and no `tags` on the response, so
 * scraped hashtags are the only tags that exist. The case below that pins the
 * emitted key set is therefore not decoration — it is what stops `category` being
 * back-filled with the same string as `topic`, which would double one signal's
 * weight against a listing's real category field.
 */
import { postCommerceContext } from "../postContext";
import type { PulsePost } from "../../api/feed";

function post(overrides: Partial<PulsePost> = {}): PulsePost {
  return { id: 1, post_id: 1, body: "", ...overrides };
}

describe("postCommerceContext — what is sent", () => {
  it("carries the title as the topic and the scraped hashtags as tags", () => {
    expect(
      postCommerceContext(post({ title: "Best running shoes", body: "tried the #sneakers again" }))
    ).toEqual({ topic: "Best running shoes", tags: ["sneakers"] });
  });

  it("prefers the author's title over the body", () => {
    // A title is a deliberate summary; a body is as often a greeting.
    expect(
      postCommerceContext(post({ title: "Kitchen knife review", body: "hey everyone" }))?.topic
    ).toBe("Kitchen knife review");
  });

  it("falls back to the body when there is no title", () => {
    expect(postCommerceContext(post({ body: "garden tool haul" }))?.topic).toBe("garden tool haul");
  });

  it("clips the topic to the eighty characters the server would keep", () => {
    expect(postCommerceContext(post({ body: "y".repeat(200) }))?.topic).toHaveLength(80);
  });

  it("never sends a category, because a post does not have one", () => {
    // Passing the title as `category` too would weigh one string twice against a
    // listing's real category field -- a stronger claim than the text supports.
    // Asserted on the key set rather than on `category` being undefined, so
    // adding *any* new field to the payload has to come through this case.
    const context = postCommerceContext(post({ title: "Sneaker haul", body: "#sneakers" }));
    expect(Object.keys(context || {}).sort()).toEqual(["tags", "topic"]);
  });
});

describe("postCommerceContext — tags the ranker can actually use", () => {
  it("strips the leading hash so a post tag and a listing tag are one word", () => {
    // The listing side stores `sneakers`. Left as `#sneakers` this is a total miss
    // that looks like a perfectly well-formed request.
    expect(postCommerceContext(post({ body: "#sneakers and ##running" }))?.tags).toEqual([
      "sneakers",
      "running"
    ]);
  });

  it("lowercases, so Sneakers and sneakers are not two tokens", () => {
    expect(postCommerceContext(post({ body: "#Sneakers #SNEAKERS" }))?.tags).toEqual(["sneakers"]);
  });

  it("drops hashtags too short for the ranker to keep", () => {
    // `_tokens` discards anything of two characters or fewer, so these can only
    // consume slots a real signal could have used.
    expect(postCommerceContext(post({ body: "#ok #hi #shoes" }))?.tags).toEqual(["shoes"]);
  });

  it("finds hashtags past the eighty characters the topic can carry", () => {
    // The shape this function exists for: a photo, a long caption, and the only
    // topical signal of any kind sitting at the very end of it. Sending the body
    // as `topic` alone would truncate that signal away.
    const context = postCommerceContext(post({ body: `${"x".repeat(90)} #skincare #serum` }));
    expect(context?.tags).toEqual(["skincare", "serum"]);
    expect(context?.topic).toHaveLength(80);
  });

  it("reads the title's hashtags before the body's", () => {
    expect(postCommerceContext(post({ title: "#titled", body: "#bodied" }))?.tags).toEqual([
      "titled",
      "bodied"
    ]);
  });

  it("does not repeat a tag that appears in both the title and the body", () => {
    expect(postCommerceContext(post({ title: "#sneakers", body: "more #Sneakers" }))?.tags).toEqual([
      "sneakers"
    ]);
  });

  it("stops at the twelve the server would keep anyway", () => {
    const many = Array.from({ length: 30 }, (_, index) => `#tag${index}`).join(" ");
    expect(postCommerceContext(post({ body: many }))?.tags).toHaveLength(12);
  });

  it("clips a long hashtag to the server's forty characters", () => {
    // The pattern itself only matches forty word characters, so this pins the two
    // caps agreeing rather than the slice doing the work.
    expect(postCommerceContext(post({ body: `#${"z".repeat(200)}` }))?.tags?.[0]).toHaveLength(40);
  });
});

describe("postCommerceContext — when to say nothing", () => {
  it("returns null for a post that says nothing about itself", () => {
    // Not `{}`. On the surface with the highest relevance floor in the app, an
    // empty context scoring 0.0 where an absent one scores NEUTRAL is the
    // difference between a card and no card.
    expect(postCommerceContext(post())).toBeNull();
    expect(postCommerceContext(post({ title: "   ", body: "  " }))).toBeNull();
  });

  it("still sends a topic the ranker cannot tokenise, because it is harmless", () => {
    // A body of `#ok` yields no tags (two characters) but the body itself is a
    // non-empty string, so `topic` is sent. Measured rather than assumed: the
    // server's `_tokens` drops words of two characters or fewer, so this
    // tokenises to nothing, and `relevance` returns NEUTRAL for a context with no
    // tokens -- the same score as sending no context at all. It is wire noise,
    // not a penalty, so it is not worth a special case here. If `_tokens` ever
    // stopped treating an untokenisable context as absent, this would need to
    // become a `null`, and that is why the behaviour is pinned rather than left
    // to be rediscovered.
    expect(postCommerceContext(post({ body: "#ok" }))).toEqual({ topic: "#ok" });
  });

  it("returns null for a missing post rather than throwing", () => {
    // Null is also the answer for a post that failed to load or was deleted.
    // A recommendation lookup must never be able to break reading a post.
    expect(postCommerceContext(null)).toBeNull();
    expect(postCommerceContext(undefined)).toBeNull();
  });

  it("sends a hashtag-only body as both the topic and a tag", () => {
    // The hash survives in `topic` and is stripped in `tags`, which looks like an
    // inconsistency and is not one: the server tokenises punctuation as a
    // separator, so `#skincare` and `skincare` are the same token and the two
    // fields agree once tokenised. `tags` is stripped anyway because a context
    // carries twelve tag slots and `#skincare` beside `skincare` would spend two
    // of them on one word -- see `ranking._WORD` for why the server stopped
    // relying on clients to get this right.
    expect(postCommerceContext(post({ body: "#skincare" }))).toEqual({
      topic: "#skincare",
      tags: ["skincare"]
    });
  });
});

describe("postCommerceContext — nothing about the viewer", () => {
  it("emits only the post's own public text, never its identifiers", () => {
    // The context describes content the same server served this client moments
    // ago. It must not become a channel for author ids or engagement counts --
    // the viewer-side signals ranking uses are read server-side from the account.
    const context = postCommerceContext(
      post({
        id: 9,
        post_id: 9,
        user_id: 4242,
        author: { id: 4242, username: "someone" } as PulsePost["author"],
        title: "Sneaker haul",
        body: "#sneakers"
      })
    );
    expect(Object.keys(context || {}).sort()).toEqual(["tags", "topic"]);
    expect(JSON.stringify(context)).not.toContain("4242");
  });
});
