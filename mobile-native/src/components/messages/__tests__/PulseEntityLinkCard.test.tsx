/**
 * One component draws three kinds of object, so what is pinned here is the
 * thing a shared component makes easy to get wrong: the *words*.
 *
 * Every assertion below has a counterpart for the other kinds, deliberately.
 * Pinning only the profile wording would let `cardCopy` collapse into the
 * profile branch and stay green while every shared post in every conversation
 * started calling itself a profile; pinning only the post wording is how the
 * feature would have shipped saying "PULSESOC POST" over somebody's face. A
 * one-sided test on a many-sided branch is the gap that a passing suite hides
 * best.
 *
 * The reel branch makes that concrete rather than theoretical. `cardCopy` ends
 * in an *unguarded* post return, so a reel whose branch was never written — or
 * was written and later deleted — does not throw, does not warn, and does not
 * render differently in any way a snapshot would notice. It silently calls a
 * reel a post. The only thing that catches that is an assertion on the reel's
 * own strings, which is why there is one for every line the card can draw.
 *
 * `useEntityPreview` is mocked rather than driven through the network, because
 * the question here is what the card *says* about a state, not how the state is
 * reached -- that is `entityPreview.test.ts`'s job, and it asks it against the
 * real resolver.
 */
import React from "react";
import { render, fireEvent, screen } from "@testing-library/react-native";

jest.mock("../../../links/entityPreview", () => ({ useEntityPreview: jest.fn() }));

import { EntityPreview, EntityPreviewState } from "../../../links/entityPreview";
import { activateLocale } from "../../../i18n/engine";
import { PulseEntityRef } from "../../../links/pulseEntity";
import { PulseEntityLinkCard } from "../PulseEntityLinkCard";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const preview = require("../../../links/entityPreview") as { useEntityPreview: jest.Mock };

const POST: PulseEntityRef = {
  kind: "post",
  id: 2432,
  url: "https://pulsesoc.com/pulse/post/2432",
  path: "/pulse/post/2432"
};

const PROFILE: PulseEntityRef = {
  kind: "profile",
  id: "roody",
  url: "https://pulsesoc.com/pulse/profile/roody",
  path: "/pulse/profile/roody"
};

const REEL: PulseEntityRef = {
  kind: "reel",
  id: 38,
  url: "https://pulsesoc.com/pulse/reels/38?pulse_app=1&pulse_src=share",
  path: "/pulse/reels/38"
};

function ready(kind: EntityPreview["kind"], ref: PulseEntityRef, over: Partial<EntityPreview> = {}): EntityPreviewState {
  return {
    status: "ready",
    preview: {
      kind,
      url: ref.url,
      path: ref.path,
      authorName: "Roody Cherie",
      authorHandle: "roody",
      authorAvatarUrl: "https://cdn/a.jpg",
      thumbnailUrl: "https://cdn/c.jpg",
      caption: "Building PulseSoc from Port-au-Prince.",
      video: false,
      ...over
    }
  };
}

function show(ref: PulseEntityRef, state: EntityPreviewState, onOpen = jest.fn()) {
  preview.useEntityPreview.mockReturnValue(state);
  render(<PulseEntityLinkCard entity={ref} onOpen={onOpen} />);
  return onOpen;
}

// Without this the engine answers every key with a humanised version of its own
// last segment -- `a11yOpen` comes back as "A11y Open" -- which is close enough
// to a real string that an assertion on `toBeTruthy()` would pass against no
// translations at all. Every string asserted below is the real catalog's.
beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  preview.useEntityPreview.mockReset();
});

describe("the card names the kind of thing it is showing", () => {
  it("calls a post a post", () => {
    show(POST, ready("post", POST));
    expect(screen.getAllByText("PULSESOC POST").length).toBeGreaterThan(0);
    expect(screen.getByText("View Post →")).toBeTruthy();
  });

  it("calls a profile a profile", () => {
    show(PROFILE, ready("profile", PROFILE));
    expect(screen.getAllByText("PULSESOC PROFILE").length).toBeGreaterThan(0);
    expect(screen.getByText("View Profile →")).toBeTruthy();
  });

  /**
   * The kind is known before the preview is, and the copy has to use it.
   *
   * The loading line is drawn from the *entity*, which the body parser resolved
   * locally, not from the preview, which is still in flight. Sourcing it from
   * the preview would be the natural mistake -- the preview is where every
   * other display value comes from -- and it would make every profile link in
   * the app say "Loading post…" for the length of a round trip.
   */
  it("says what it is loading before it knows anything about it", () => {
    show(PROFILE, { status: "loading" });
    expect(screen.getByText("Loading profile…")).toBeTruthy();
  });

  it("says it is loading a post when it is loading a post", () => {
    show(POST, { status: "loading" });
    expect(screen.getByText("Loading post…")).toBeTruthy();
  });

  it("calls a reel a reel", () => {
    show(REEL, ready("reel", REEL, { video: true }));
    expect(screen.getAllByText("PULSESOC REEL").length).toBeGreaterThan(0);
    expect(screen.getByText("View Reel →")).toBeTruthy();
  });

  it("says it is loading a reel when it is loading a reel", () => {
    show(REEL, { status: "loading" });
    expect(screen.getByText("Loading Reel…")).toBeTruthy();
  });

  /**
   * The one assertion that survives `cardCopy` losing its reel branch.
   *
   * Every other test in this file asks whether a string is present. This one
   * asks whether the *wrong* string is absent, which is the only form that
   * fails when a reel silently falls through to the unguarded post return at
   * the bottom of `cardCopy`. A reel labelled "PULSESOC POST" renders, reads
   * plausibly, and passes a presence check on everything else the card draws.
   */
  it("never calls a reel a post, whatever the branch order", () => {
    show(REEL, ready("reel", REEL, { video: true }));
    expect(screen.queryByText("PULSESOC POST")).toBeNull();
    expect(screen.queryByText("View Post →")).toBeNull();
  });
});

describe("the badge over the picture", () => {
  /**
   * A post says "Video" because the badge is news; a reel says "Play" because
   * it is not. Both words come from `cardCopy`, so a reel that fell through to
   * the post branch would be caught here too -- and this is the assertion that
   * catches it even if someone later makes the eyebrow kind-agnostic.
   */
  it("tells a post's reader that the post carries video", () => {
    show(POST, ready("post", POST, { video: true }));
    expect(screen.getByText("Video")).toBeTruthy();
  });

  it("invites a reel's reader to start it", () => {
    show(REEL, ready("reel", REEL, { video: true }));
    expect(screen.getByText("Play")).toBeTruthy();
    expect(screen.queryByText("Video")).toBeNull();
  });

  /**
   * The black-rectangle bug, pinned from the other side.
   *
   * The badge is drawn inside the branch that *has* a thumbnail, and that is
   * load-bearing: a "Play" floating on an empty frame is a card asserting there
   * is a picture when there is not. A reel whose poster came back "" -- which
   * `pulse_reel_share_preview` returns rather than handing over the .m3u8 --
   * must draw no badge at all.
   */
  it("draws no badge over a poster the server did not have", () => {
    show(REEL, ready("reel", REEL, { video: true, thumbnailUrl: "" }));
    expect(screen.queryByText("Play")).toBeNull();
  });
});

describe("when the viewer may not see it", () => {
  it("refuses a profile in the profile's own words", () => {
    show(PROFILE, { status: "unavailable", reason: "forbidden" });
    expect(screen.getByText("This profile isn't available to you.")).toBeTruthy();
  });

  it("refuses a post in the post's own words", () => {
    show(POST, { status: "unavailable", reason: "forbidden" });
    expect(screen.getByText("This content isn't available to you.")).toBeTruthy();
  });

  /**
   * The reel's forbidden line is the post's, word for word, and that is the
   * decision rather than a copy-paste. "This Reel isn't available to you"
   * confirms a reel with that id exists and is merely closed to the reader; the
   * missing line then says something different, and the pair is a probe. Both
   * states answer with the *same* sentence for the same reason the route
   * answers 404 for deleted, blocked and invisible alike.
   */
  it("refuses a reel without confirming there is one", () => {
    show(REEL, { status: "unavailable", reason: "forbidden" });
    expect(screen.getByText("This content isn't available to you.")).toBeTruthy();
  });

  it.each([
    ["a deleted profile", PROFILE, "missing" as const, "This profile is no longer available."],
    ["a deleted post", POST, "missing" as const, "This post is no longer available."],
    ["a deleted reel", REEL, "missing" as const, "This Reel is no longer available."]
  ])("reports %s", (_label, ref, reason, line) => {
    show(ref, { status: "unavailable", reason });
    expect(screen.getByText(line)).toBeTruthy();
  });

  it("drops the call to action rather than offering a way in that leads nowhere", () => {
    show(PROFILE, { status: "unavailable", reason: "forbidden" });
    expect(screen.queryByText("View Profile →")).toBeNull();
  });

  it("does not open a destination that will refuse the viewer one screen later", () => {
    const onOpen = show(PROFILE, { status: "unavailable", reason: "forbidden" });
    fireEvent.press(screen.getByRole("link"));
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe("what a screen reader is told", () => {
  it("offers to open a person's profile, not their post", () => {
    show(PROFILE, ready("profile", PROFILE));
    expect(screen.getByRole("link").props.accessibilityLabel).toBe("Open Roody Cherie's PulseSoc profile");
  });

  it("offers to open the post by its author", () => {
    show(POST, ready("post", POST));
    expect(screen.getByRole("link").props.accessibilityLabel).toBe("Open the PulseSoc post by Roody Cherie");
  });

  it("stays specific about the kind while the name is still unknown", () => {
    show(PROFILE, { status: "loading" });
    expect(screen.getByRole("link").props.accessibilityLabel).toBe("Open this PulseSoc profile");
  });

  it("offers to open the reel by its creator", () => {
    show(REEL, ready("reel", REEL, { video: true }));
    expect(screen.getByRole("link").props.accessibilityLabel).toBe("Open the PulseSoc Reel by Roody Cherie");
  });

  it("stays specific that it is a reel while the creator is still unknown", () => {
    show(REEL, { status: "loading" });
    expect(screen.getByRole("link").props.accessibilityLabel).toBe("Open this PulseSoc Reel");
  });
});

describe("opening it", () => {
  it("hands back the URL the sender actually sent", () => {
    const onOpen = show(PROFILE, ready("profile", PROFILE));
    fireEvent.press(screen.getByRole("link"));
    // Not the path, and not a URL the card rebuilt: whatever was in the body.
    expect(onOpen).toHaveBeenCalledWith("https://pulsesoc.com/pulse/profile/roody");
  });

  /**
   * The share-tracking parameters survive the round trip.
   *
   * `pulse_reel_share_preview` answers a *canonical* url with no query on it,
   * and it would be a small, invisible mistake for the card to prefer that one:
   * the sender sent a link carrying `pulse_src=share`, and what a tap opens
   * should be the link they sent. `previewFromReel` takes `ref.url` for exactly
   * this reason and this is the assertion that holds it there.
   */
  it("opens the reel link the sender sent, query string and all", () => {
    const onOpen = show(REEL, ready("reel", REEL, { video: true }));
    fireEvent.press(screen.getByRole("link"));
    expect(onOpen).toHaveBeenCalledWith("https://pulsesoc.com/pulse/reels/38?pulse_app=1&pulse_src=share");
  });
});
