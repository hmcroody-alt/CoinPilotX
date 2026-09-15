import { readFileSync } from "fs";
import { join } from "path";

/**
 * The one attachment claim a render cannot make.
 *
 * This file used to hold the whole guard for the two Messenger attachment
 * defects, and it read `ChatScreen.tsx` as text because the screen had no render
 * test. `ChatScreenAttachmentRender.test.tsx` now mounts the real screen and
 * presses the real card, so every behavioural assertion that used to live here
 * lives there instead — which is the stronger guarantee, because a handler can be
 * spelled `onPress={open}` and still open nothing.
 *
 * What stays is the one property that has no rendered consequence to observe:
 * how many times the bubble asks for a grant. The original and its preview are
 * two objects behind one authorization decision, and the module-level cache in
 * `messengerMediaAccess` deduplicates concurrent requests for the same id — so a
 * bubble with two call sites issues exactly as many network requests as a bubble
 * with one, and a render test cannot tell them apart. It was two call sites that
 * resolved both URL slots to the same `/download` path in the first place, which
 * is how a video bubble came to hand an entire movie to `<Image>`. The render
 * test pins that consequence; this pins the cause.
 */
const source = readFileSync(join(__dirname, "..", "ChatScreen.tsx"), "utf8");

/**
 * The text of one top-level function, up to the next top-level declaration.
 *
 * This used to be a single slice spanning everything between the bubble's
 * identity literal and `DocumentAttachmentCard`, and counting one match across
 * it. That worked while exactly one component in the range took a grant. §21's
 * multi-photo grid puts a second component in the same range — legitimately, as
 * each tile is a separate surface with a separate identity — and a span-wide
 * count cannot tell "one component asking twice for the same photo" (the defect)
 * apart from "two components each asking once for their own" (the design).
 *
 * So the property is now asserted where it actually holds: per surface. The
 * nested `function openInGallery()` inside the bubble is indented, so anchoring
 * on column zero keeps it out of the boundary search.
 */
function surface(name: string): string {
  const start = source.indexOf(`function ${name}(`);
  expect(start).toBeGreaterThan(-1);
  const next = source.slice(start + 1).search(/\n(?:function|const) [A-Za-z_]/);
  return next < 0 ? source.slice(start) : source.slice(start, start + 1 + next);
}

describe("a media surface takes one access grant", () => {
  it("the single-media bubble calls the grant hook exactly once, carrying both URLs", () => {
    expect(surface("MessageMedia").match(/useMessengerMediaAccessUrl\(/g)).toHaveLength(1);
  });

  /**
   * A tile is the same discipline at a smaller scale: one grant, covering that
   * tile's original and its preview. A grid of three photos should be three
   * authorization decisions, not six.
   */
  it("a grid tile calls the grant hook exactly once for its own identity", () => {
    expect(surface("MediaGridTile").match(/useMessengerMediaAccessUrl\(/g)).toHaveLength(1);
  });

  /**
   * The layout component must not take one at all. If the grid resolved a URL
   * itself it would have to pick *an* identity, and the only one available to it
   * is the message's — which is attachment one, handed to every tile.
   */
  it("the grid itself takes no grant, because it has no identity of its own", () => {
    expect(surface("MessageMediaGrid")).not.toMatch(/useMessengerMediaAccessUrl\(/);
  });
});
