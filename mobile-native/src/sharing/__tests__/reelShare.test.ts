/**
 * The Reel share payload, pinned from the side that matters.
 *
 * Like `postShare.test.ts`, most of what is asserted here is what must *not*
 * appear, and against the whole payload rather than one field: a restricted
 * Reel whose `description` is blank but whose `previewImageUrl` still carries
 * the creator's poster has published the frame the audience setting existed to
 * keep private, and an assertion on `description` alone would call that a pass.
 *
 * ## The allowlist tests are the reason this file exists
 *
 * `isReelPubliclyShareable` reads `visibility` as an allowlist of one literal,
 * and the tests below feed it values nobody has written yet -- `"followers"`,
 * `"close_friends"`, a visibility field that is missing entirely. Every one of
 * them must come back restricted. That is not defensive padding: the sibling
 * classifier `reelContentState` in `ReelPlayerCard` is a *denylist* over the
 * same vocabulary, so an unfamiliar availability falls through to `playable`
 * there. Copying that shape into a share would have been the natural thing to
 * do and would have leaked every audience PulseSoc adds after today. These
 * tests are what makes the two defaults stay opposite.
 */
import { PulseReel } from "../../api/reels";
import {
  buildReelShareMessage,
  buildReelShareMetadata,
  buildReelSharePreview,
  isReelPubliclyShareable
} from "../reelShare";

const URL = "https://pulsesoc.com/pulse/reels/38?pulse_app=1&pulse_src=share";
const PRIVATE_CAPTION = "the rough cut nobody outside the group should see";
const POSTER = "https://image.mux.com/PLAY123/thumbnail.jpg";

function reel(overrides: Partial<PulseReel> = {}): PulseReel {
  return {
    id: 38,
    reel_id: 38,
    title: "",
    caption: "Building PulseSoc from Port-au-Prince.",
    visibility: "public",
    poster_url: POSTER,
    author: { display_name: "Roody Cherie", username: "roody" },
    media: [],
    ...overrides
  } as PulseReel;
}

/** Every string in the payload, for "this must not appear anywhere" checks. */
function allText(metadata: ReturnType<typeof buildReelShareMetadata>) {
  return Object.values(metadata).filter((value) => typeof value === "string").join(" ");
}

describe("a public Reel", () => {
  it("leads with the Reel line, previews the caption, and ends on the link it was given", () => {
    expect(buildReelShareMessage(reel(), URL)).toBe(
      "Check out this Reel on PulseSoc 🎬\nBuilding PulseSoc from Port-au-Prince.\nWatch the Reel: " + URL
    );
  });

  /**
   * The brief's example share is headline plus URL, and a Reel with nothing to
   * say produces exactly that shape -- the caption line is earned, not assumed.
   */
  it("says only what it can when the Reel has no caption", () => {
    expect(buildReelShareMessage(reel({ caption: "", body: "" }), URL)).toBe(
      "Check out this Reel on PulseSoc 🎬\nA Reel on PulseSoc.\nWatch the Reel: " + URL
    );
  });

  it("falls back to the body when the caption is empty", () => {
    expect(buildReelSharePreview(reel({ caption: "", body: "Shot on the roof." })).preview).toBe("Shot on the roof.");
  });

  it("carries the creator, the poster and the link the caller minted", () => {
    const metadata = buildReelShareMetadata(reel(), URL);
    expect(metadata.kind).toBe("reel");
    // Not a URL rebuilt from the id: the share route's own link, tracking
    // parameters and all, is what the recipient should tap.
    expect(metadata.url).toBe(URL);
    expect(metadata.author).toBe("Roody Cherie");
    expect(metadata.previewImageUrl).toBe(POSTER);
    expect(metadata.description).toBe("Building PulseSoc from Port-au-Prince.");
  });

  it("names the object rather than leaving the title empty", () => {
    expect(buildReelShareMetadata(reel({ title: "" }), URL).title).toBe("PulseSoc Reel");
    expect(buildReelShareMetadata(reel({ title: "Night Ride" }), URL).title).toBe("Night Ride");
  });

  /**
   * A Mux playback id is public by construction -- PulseSoc mints assets with
   * `playback_policy: "public"` and the thumbnail URL embeds the id -- so the
   * poster is allowed to leave. The *asset* id is the private handle and has no
   * business in a share, which is what this holds.
   */
  it("sends the public playback thumbnail and no internal asset identifier", () => {
    const metadata = buildReelShareMetadata(reel({ poster_url: POSTER } as Partial<PulseReel>), URL);
    expect(metadata.previewImageUrl).toContain("image.mux.com");
    expect(allText(metadata)).not.toContain("asset");
  });

  it("redacts a signed storage URL that somehow reached the caption", () => {
    const signed = "https://cdn.coinpilotx.app/reels/38.mp4?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA";
    const metadata = buildReelShareMetadata(reel({ caption: `watch it ${signed}` }), URL);
    expect(allText(metadata)).not.toContain("X-Amz-Signature");
    expect(allText(metadata)).not.toContain("deadbeef");
    expect(metadata.description).toContain("[link removed]");
  });

  it("flattens a caption written across several lines", () => {
    expect(buildReelSharePreview(reel({ caption: "one\ntwo\n\nthree" })).preview).toBe("one two three");
  });
});

describe("a Reel the author did not publish publicly", () => {
  it.each([
    ["followers only", "followers"],
    ["private", "private"],
    ["close friends", "close_friends"],
    ["an audience invented after this file was written", "supporters_tier_2"],
    ["a blank visibility", ""]
  ])("refuses to preview %s", (_label, visibility) => {
    expect(isReelPubliclyShareable(reel({ visibility }))).toBe(false);
  });

  it("refuses a Reel whose payload carries no visibility at all", () => {
    const { visibility: _dropped, ...withoutVisibility } = reel();
    expect(isReelPubliclyShareable(withoutVisibility as PulseReel)).toBe(false);
  });

  /**
   * The assertion the whole file is for. Not "description is empty" -- the
   * caption must be absent from every field, because the share sheet passes
   * all of them to whatever target the person picks.
   */
  it("lets no part of a private Reel out of the app", () => {
    const metadata = buildReelShareMetadata(reel({ visibility: "private", caption: PRIVATE_CAPTION, title: "Rough cut" }), URL);
    const text = allText(metadata);
    expect(text).not.toContain(PRIVATE_CAPTION);
    expect(text).not.toContain("Rough cut");
    expect(text).not.toContain("Roody Cherie");
    expect(text).not.toContain("roody");
    expect(metadata.previewImageUrl).toBe("");
  });

  /**
   * The preview builder is checked on its own, not only through the metadata.
   *
   * `buildReelShareMetadata` blanks `description` with its own `restricted ?
   * "" :` ternary, so a `buildReelSharePreview` that started returning the
   * caption alongside `restricted: true` would leak nothing *through that one
   * caller* and every assertion in this file would stay green. It is an
   * exported function; the next surface to want a Reel preview line will call
   * it directly and will not know to re-check the flag it was handed. A
   * mutation pass found exactly this gap, which is why the assertion is on the
   * whole returned object rather than on the flag.
   */
  it("returns no preview text at all, not merely a flag saying to ignore it", () => {
    expect(buildReelSharePreview(reel({ visibility: "private", caption: PRIVATE_CAPTION })))
      .toEqual({ preview: "", restricted: true });
    expect(buildReelSharePreview(reel({ availability: "blocked", caption: PRIVATE_CAPTION })))
      .toEqual({ preview: "", restricted: true });
  });

  it("still hands over the link, because the link enforces its own access", () => {
    const metadata = buildReelShareMetadata(reel({ visibility: "private" }), URL);
    expect(metadata.url).toBe(URL);
    expect(metadata.message).toBe("Someone shared a Reel with you on PulseSoc.\nWatch the Reel: " + URL);
  });
});

describe("a Reel that is public but cannot be watched", () => {
  /**
   * Publication and availability fail separately, so they are checked
   * separately: a deleted public Reel and a healthy private one are each
   * unshareable for a reason the other test would miss entirely.
   */
  it.each([
    ["removed", { is_removed: true }],
    ["deleted", { deleted_at: "2026-09-01T00:00:00Z" }],
    ["restricted by availability", { availability: "restricted" }],
    ["blocked", { availability: "blocked" }],
    ["followers-gated at view time", { visibility_state: "followers_only" }],
    ["held in an availability state nobody has named yet", { availability: "quarantined_pending_appeal" }],
    ["rejected by moderation", { moderation_status: "rejected" }]
  ])("refuses a Reel that is %s", (_label, over) => {
    expect(isReelPubliclyShareable(reel(over as Partial<PulseReel>))).toBe(false);
    expect(allText(buildReelShareMetadata(reel({ ...over, caption: PRIVATE_CAPTION } as Partial<PulseReel>), URL)))
      .not.toContain(PRIVATE_CAPTION);
  });

  /**
   * `availability` is optional and most payloads omit it. Treating absence as
   * restriction would have made the allowlist correct and useless -- every
   * ordinary share would have gone out blank, which is the failure mode that
   * gets an allowlist reverted a week later.
   */
  it("shares normally when availability is simply absent", () => {
    expect(isReelPubliclyShareable(reel())).toBe(true);
    expect(isReelPubliclyShareable(reel({ availability: "available", visibility_state: "available" }))).toBe(true);
  });

  /**
   * Moderation is the one denylist here, and this pins why: a Reel waiting in a
   * review queue is still its author's public Reel. Ruling on it is what
   * silences the share, not being looked at.
   */
  it("does not silence a Reel merely for being in the queue", () => {
    expect(isReelPubliclyShareable(reel({ moderation_status: "pending" }))).toBe(true);
    expect(isReelPubliclyShareable(reel({ moderation_status: "rejected" }))).toBe(false);
  });
});
