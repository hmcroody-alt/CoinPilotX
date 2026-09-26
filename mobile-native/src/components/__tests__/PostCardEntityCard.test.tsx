/**
 * A post whose body links one PulseSoc object shows that object, not its URL.
 *
 * ## What this file is for that the card's own test is not
 *
 * `messages/__tests__/PulseEntityLinkCard.test.tsx` asks what the card says once
 * it has an entity. It is handed one directly and never sees a post. This file
 * asks the question before that: given a *body*, does a card appear, is it the
 * right one, and is it the right one to have suppressed. Those are `PostCard`'s
 * decisions and none of them are visible from inside the card.
 *
 * The card is therefore real here and `useEntityPreview` is mocked instead —
 * the opposite arrangement from `PostCardBodyLinks.test.tsx`, which stubs the
 * card because it is asking about the paragraph. Mocking the hook rather than the
 * network keeps the assertions off `act()`'s timing: the resolved state is present
 * on first render, so "the card is here" is not a race.
 *
 * ## The case that made the file
 *
 * A post can link *itself*. `sharePostFromCard` hands out `pulsePostUrl(post)`, so
 * pasting a post's own share link back into its body is an ordinary thing for a
 * person to do, and a repost wrapper quoting the original it already wraps does it
 * structurally. Without the guard in `PostCard` the card resolves, fetches, and
 * draws a picture of the post the reader is already looking at, under a "View
 * post →" that navigates to where they already are. Nothing throws and nothing
 * looks broken — it is simply the same post twice — which is why it needs an
 * assertion rather than a review.
 *
 * Chat has no equivalent case, because a message cannot link to itself, so this is
 * the first surface where the guard had to exist at all.
 */

import React from "react";
import { render, screen } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" },
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" }
}));

jest.mock("expo-av", () => ({
  ResizeMode: { COVER: "cover", CONTAIN: "contain" },
  Video: () => null
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: ({ name }: { name: string }) => name
}));

jest.mock("../NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn()
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(),
  releaseMediaPlayback: jest.fn()
}));

jest.mock("../../media/mediaAccess", () => ({
  canonicalMediaPlaybackUrl: (url: string) => url,
  refreshCanonicalMediaAccess: jest.fn().mockResolvedValue(undefined)
}));

jest.mock("../../links/openContentLink", () => ({ openContentLink: jest.fn(() => "internal") }));

// The card is real; only its data source is replaced. See the note above.
jest.mock("../../links/entityPreview", () => ({ useEntityPreview: jest.fn() }));

import { PulsePost } from "../../api/feed";
import { EntityPreviewState } from "../../links/entityPreview";
import { activateLocale } from "../../i18n/engine";
import { PostCard } from "../PostCard";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const previewModule = require("../../links/entityPreview") as { useEntityPreview: jest.Mock };

/**
 * A resolved preview for whatever the card asks about.
 *
 * Deliberately *not* keyed by entity: the hook is asked once per card and every
 * case here renders at most one, so keying it would add a lookup that could only
 * fail in ways the entity assertions already catch. What the card was asked about
 * is asserted through `useEntityPreview`'s argument instead, which is the same
 * fact stated where it cannot drift.
 */
function readyPreview(kind: "post" | "profile" | "reel", url: string): EntityPreviewState {
  return {
    status: "ready",
    preview: {
      kind,
      url,
      path: new URL(url).pathname,
      authorName: "Roody Cherie",
      authorHandle: "roody",
      authorAvatarUrl: "",
      thumbnailUrl: "",
      caption: "The linked thing.",
      video: false
    }
  };
}

function post(overrides: Partial<PulsePost> = {}): PulsePost {
  return {
    id: 42,
    body: "Hello from the feed",
    author: { display_name: "Ada", username: "ada" },
    created_at: new Date().toISOString(),
    ...overrides
  } as PulsePost;
}

/** What the card was asked to resolve, or `null` if no card was drawn. */
function askedFor() {
  const call = previewModule.useEntityPreview.mock.calls[0];
  return call ? call[0] : null;
}

beforeAll(async () => {
  // Without a real catalog the engine humanises each key's last segment, so
  // "PULSESOC POST" would come back as "Eyebrow" and an assertion on it would
  // pass against no translations at all.
  await activateLocale("en");
});

beforeEach(() => {
  previewModule.useEntityPreview.mockReset();
  previewModule.useEntityPreview.mockReturnValue({ status: "loading" });
});

describe("a post body that links one PulseSoc object", () => {
  it("draws that object's card", () => {
    previewModule.useEntityPreview.mockReturnValue(readyPreview("post", "https://pulsesoc.com/pulse/post/12"));
    render(<PostCard post={post({ body: "look at this https://pulsesoc.com/pulse/post/12" })} />);
    expect(screen.getByText("PULSESOC POST")).toBeTruthy();
  });

  it("asks about the object the body actually named", () => {
    render(<PostCard post={post({ body: "look at this https://pulsesoc.com/pulse/post/12" })} />);
    expect(askedFor()).toMatchObject({ kind: "post", id: 12 });
  });

  it("tells a profile link apart from a post link", () => {
    // Both resolve to a card, and the wrong branch would still render one. The
    // id is a string here and a number above, which is the shape difference that
    // makes `kind` the thing worth asserting.
    render(<PostCard post={post({ body: "her work: https://pulsesoc.com/pulse/profile/roody" })} />);
    expect(askedFor()).toMatchObject({ kind: "profile", id: "roody" });
  });

  it("keeps the sentence the author wrote around the link", () => {
    previewModule.useEntityPreview.mockReturnValue(readyPreview("post", "https://pulsesoc.com/pulse/post/12"));
    const tree = render(<PostCard post={post({ body: "look at this https://pulsesoc.com/pulse/post/12" })} />);
    // The card is additive. Replacing prose with a card would discard the only
    // part of the post the author actually typed.
    expect(tree.getAllByText(/look at this/).length).toBeGreaterThan(0);
  });
});

describe("a post body that is nothing but the link", () => {
  it("still draws the card", () => {
    previewModule.useEntityPreview.mockReturnValue(readyPreview("post", "https://pulsesoc.com/pulse/post/12"));
    render(<PostCard post={post({ body: "https://pulsesoc.com/pulse/post/12" })} />);
    expect(screen.getByText("PULSESOC POST")).toBeTruthy();
  });

  it("stops drawing the bare URL underneath it", () => {
    // There is no sentence to preserve, so the card is the whole content and the
    // URL is the one part of it carrying nothing a reader can use.
    previewModule.useEntityPreview.mockReturnValue(readyPreview("post", "https://pulsesoc.com/pulse/post/12"));
    const tree = render(<PostCard post={post({ body: "https://pulsesoc.com/pulse/post/12" })} />);
    expect(tree.queryAllByText("https://pulsesoc.com/pulse/post/12")).toHaveLength(0);
  });
});

describe("a post body that links itself", () => {
  it("draws no card", () => {
    // `pulsePostUrl(post)` for post 42, pasted into post 42's own body.
    render(<PostCard post={post({ id: 42, body: "https://pulsesoc.com/pulse/post/42" })} />);
    expect(askedFor()).toBeNull();
    expect(screen.queryByText("PULSESOC POST")).toBeNull();
  });

  it("keeps its body text, having suppressed nothing", () => {
    // The suppression and the card are one decision. If the guard skipped the
    // card but left `cardReplacesBody` true, a self-linking post would render
    // with no card and no body -- an empty post.
    const tree = render(<PostCard post={post({ id: 42, body: "https://pulsesoc.com/pulse/post/42" })} />);
    expect(tree.getAllByText("https://pulsesoc.com/pulse/post/42").length).toBeGreaterThan(0);
  });

  it("still cards a different post with a neighbouring id", () => {
    // The guard compares ids; an `>=`, a truthiness test or a stringified
    // comparison would all pass the case above and take this one with it.
    render(<PostCard post={post({ id: 42, body: "https://pulsesoc.com/pulse/post/43" })} />);
    expect(askedFor()).toMatchObject({ kind: "post", id: 43 });
  });

  it("still cards a reel that happens to share the post's id", () => {
    // `pulse_reels.id` and `pulse_posts.id` are unrelated counters from different
    // tables, so reel 42 under post 42 is a collision and not a self-reference.
    // A guard that compared only ids would silently drop this card.
    render(<PostCard post={post({ id: 42, body: "https://pulsesoc.com/pulse/reels/42" })} />);
    expect(askedFor()).toMatchObject({ kind: "reel", id: 42 });
  });
});

describe("a post body with nothing to card", () => {
  it("asks for no preview when there is no link at all", () => {
    render(<PostCard post={post({ body: "just a sentence" })} />);
    expect(askedFor()).toBeNull();
  });

  it("asks for no preview for an external link", () => {
    // An external URL is tappable -- `PostCardBodyLinks.test.tsx` holds that --
    // but there is no PulseSoc object behind it to draw.
    render(<PostCard post={post({ body: "via https://apple.com/newsroom" })} />);
    expect(askedFor()).toBeNull();
  });

  it("draws no card when the body is about two different objects", () => {
    // The card claims the post *is* that object. With two candidates the claim is
    // a guess, and whichever came first would be promoted for no reason the
    // author chose. Both links stay tappable; neither is elevated.
    render(
      <PostCard
        post={post({ body: "https://pulsesoc.com/pulse/post/12 and https://pulsesoc.com/pulse/post/13" })}
      />
    );
    expect(askedFor()).toBeNull();
  });

  it("draws one card when the body repeats the same link twice", () => {
    // Repetition is not ambiguity: the post still unambiguously means post 12.
    render(
      <PostCard
        post={post({ body: "https://pulsesoc.com/pulse/post/12 again https://pulsesoc.com/pulse/post/12" })}
      />
    );
    expect(askedFor()).toMatchObject({ kind: "post", id: 12 });
    expect(previewModule.useEntityPreview.mock.calls).toHaveLength(1);
  });
});
