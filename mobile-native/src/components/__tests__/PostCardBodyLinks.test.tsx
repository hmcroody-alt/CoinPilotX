/**
 * A URL in a post body is a link, on every surface that draws a post.
 *
 * ## Why this is its own file
 *
 * `PostCard.test.tsx` renders the real `ContentTranslation` and the real
 * `openContentLink`, which is right for what it asserts. This file needs
 * `openContentLink` replaced by a spy — the question here is *what PostCard hands
 * it*, and the module's own behaviour is already pinned in
 * `links/__tests__/openContentLink.test.ts`. A module-level `jest.mock` would
 * apply to that whole 480-line file, so the mock lives here instead.
 *
 * ## What is actually at stake
 *
 * Before this, a post body was a plain `<Text>`. A URL in it rendered as prose:
 * visibly a link, selectable, and completely inert. The body now renders through
 * `ContentTranslation`'s `renderText` into `LinkedText`, which is the same seam
 * `ChatScreen` already uses — so the assertion worth making is not "a link
 * appears" but that the *normalised* URL reaches the opener, and that the
 * paragraph is otherwise unchanged.
 *
 * That last part is the regression this file is really guarding. `renderText`
 * replaces `ContentTranslation`'s own `<Text>` outright, which silently drops the
 * `textStyle` and `numberOfLines` it would have applied. `PostCard` passes both
 * through by hand. If someone removes them the body keeps rendering, keeps its
 * links, and quietly stops truncating — a collapsed feed card grows to the full
 * length of the post, with nothing failing anywhere.
 */

import React from "react";
import { Text } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

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

// The one mock this file exists for. `openContentLink` resolves a URL through the
// app's router; whether it resolves correctly is that module's own test.
jest.mock("../../links/openContentLink", () => ({ openContentLink: jest.fn(() => "internal") }));

/**
 * The entity card, stubbed out — and the reason is worth recording, because this
 * mock was added in response to a failure rather than in anticipation of one.
 *
 * `PostCard` now draws a `PulseEntityLinkCard` under a body that links exactly one
 * PulseSoc object, and the card is itself an `accessibilityRole="link"`. Most
 * bodies below link a post, so the card appeared in the tree and
 * `linkSpans()` — which asks for every link role — started counting it. The
 * parity assertion in "makes every URL in the body tappable" saw 1 span where it
 * expected an even number and failed. That was the test working: a new link role
 * had appeared in the body's column and it said so.
 *
 * Stubbing rather than widening the count, because the two questions are
 * different. This file asks what the *paragraph* does with a URL. Whether the card
 * appears, and for which bodies, is `PostCardEntityCard.test.tsx`. Leaving the
 * real card in would also mean every case here made a preview request through
 * `useEntityPreview` and resolved it outside `act()`.
 */
jest.mock("../messages/PulseEntityLinkCard", () => ({ PulseEntityLinkCard: () => null }));

import { PulsePost } from "../../api/feed";
import { openContentLink } from "../../links/openContentLink";
import { PostCard } from "../PostCard";

const openSpy = openContentLink as jest.MockedFunction<typeof openContentLink>;

function post(overrides: Partial<PulsePost> = {}): PulsePost {
  return {
    id: 42,
    body: "Hello from the feed",
    author: { display_name: "Ada", username: "ada" },
    created_at: new Date().toISOString(),
    ...overrides
  } as PulsePost;
}

/**
 * The link spans in the rendered card.
 *
 * `LinkedText` marks them with `accessibilityRole="link"` rather than a testID,
 * which is the better contract to assert on: the role is what a screen reader
 * announces, so a body whose links are invisible to assistive technology fails
 * this lookup for the same reason a user would complain about it.
 */
function linkSpans(tree: ReturnType<typeof render>) {
  return tree.queryAllByRole("link");
}

describe("a URL in a post body", () => {
  beforeEach(() => openSpy.mockClear());

  it("hands the tapped URL to the opener", () => {
    const tree = render(<PostCard post={post({ body: "read https://pulsesoc.com/pulse/post/12 now" })} />);
    const [link] = linkSpans(tree);
    expect(link).toBeDefined();
    fireEvent.press(link);
    expect(openSpy).toHaveBeenCalledWith("https://pulsesoc.com/pulse/post/12");
  });

  it("hands over the normalised URL, not the characters on screen", () => {
    // A trailing bracket or full stop is punctuation of the sentence, not of the
    // URL. Sending the displayed slice would produce a 404 for a link that looks
    // perfectly well formed in the body.
    const tree = render(<PostCard post={post({ body: "(see https://pulsesoc.com/pulse/post/12)." })} />);
    fireEvent.press(linkSpans(tree)[0]);
    expect(openSpy).toHaveBeenCalledWith("https://pulsesoc.com/pulse/post/12");
  });

  it("links an external URL too, rather than only PulseSoc ones", () => {
    // Deciding internal from external is `openContentLink`'s job. `PostCard`
    // must not pre-filter, or an external link goes back to being prose.
    const tree = render(<PostCard post={post({ body: "via https://apple.com" })} />);
    fireEvent.press(linkSpans(tree)[0]);
    expect(openSpy).toHaveBeenCalledWith("https://apple.com");
  });

  it("makes every URL in the body tappable, not just the first", () => {
    const tree = render(
      <PostCard post={post({ body: "https://pulsesoc.com/pulse/post/1 and https://apple.com" })} />
    );
    // Two link spans per rendered copy of the body: `PostCard` draws an
    // invisible measuring copy to decide whether Read more is needed, so the
    // count is asserted as a multiple rather than exactly two.
    expect(linkSpans(tree).length % 2).toBe(0);
    expect(linkSpans(tree).length).toBeGreaterThanOrEqual(2);
  });

  it("does not open anything until a link is actually tapped", () => {
    render(<PostCard post={post({ body: "read https://pulsesoc.com/pulse/post/12 now" })} />);
    expect(openSpy).not.toHaveBeenCalled();
  });
});

describe("a post body with no URL in it", () => {
  beforeEach(() => openSpy.mockClear());

  it("renders as one undecorated paragraph", () => {
    const tree = render(<PostCard post={post({ body: "Hello from the feed" })} />);
    expect(linkSpans(tree)).toHaveLength(0);
    expect(tree.getAllByText("Hello from the feed").length).toBeGreaterThan(0);
  });

  it("is still one findable string, so linkifying cost prose nothing", () => {
    // `LinkedText` has a fast path returning a single `<Text>` when there is no
    // link. Losing it would split every body in the app into per-word nodes and
    // break `getByText` across the suite -- cheap to assert, expensive to find.
    const tree = render(<PostCard post={post({ body: "a plain sentence with no urls" })} />);
    expect(tree.getAllByText("a plain sentence with no urls").length).toBeGreaterThan(0);
  });
});

describe("the collapsed card still collapses", () => {
  // `renderText` suppresses the `numberOfLines` that `ContentTranslation` would
  // have applied, so `PostCard` re-passes it. These two cases are what notice if
  // that stops happening.

  const LONG_BODY_WITH_LINK = `${"word ".repeat(200)}https://apple.com`;

  /**
   * `PostCard.tsx:31`'s `COLLAPSED_BODY_LINES`, which is not exported.
   *
   * Matching this exact value rather than "any limit > 0" is deliberate: the card
   * also renders single-line chrome — the author name and the title both carry
   * `numberOfLines={1}` — so a broader filter finds three limits on a detail card
   * that truncates no body at all.
   */
  const COLLAPSED_BODY_LINES = 4;

  /** How many paragraphs are limited to the collapsed body's height. */
  function collapsedParagraphs(tree: ReturnType<typeof render>) {
    return tree
      .UNSAFE_getAllByType(Text)
      .filter((node) => node.props.numberOfLines === COLLAPSED_BODY_LINES).length;
  }

  it("truncates a long body on the feed", () => {
    // Counted rather than pinned to a chosen node: which `<Text>` carries the
    // limit is `LinkedText`'s business, since it returns a different tree
    // depending on whether the body has a link in it.
    expect(collapsedParagraphs(render(<PostCard post={post({ body: LONG_BODY_WITH_LINK })} />))).toBeGreaterThan(0);
  });

  it("does not truncate the same body on the detail screen", () => {
    // `detail` passes `undefined`, which is how a post page shows the whole post.
    // Dropping the hand-passed `numberOfLines` entirely would still satisfy this
    // case on its own; it is the pair that cannot both pass.
    expect(collapsedParagraphs(render(<PostCard post={post({ body: LONG_BODY_WITH_LINK })} detail />))).toBe(0);
  });
});
