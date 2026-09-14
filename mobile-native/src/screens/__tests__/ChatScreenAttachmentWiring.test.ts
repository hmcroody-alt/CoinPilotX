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

const bubble = (() => {
  const start = source.indexOf("const mediaIdentity = {");
  const end = source.indexOf("function DocumentAttachmentCard");
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end);
})();

describe("a media bubble takes one access grant", () => {
  it("calls the grant hook exactly once, carrying both URLs", () => {
    expect(bubble.match(/useMessengerMediaAccessUrl\(/g)).toHaveLength(1);
  });
});
