import { readFileSync } from "fs";
import { join } from "path";

/**
 * ChatScreen is 2000+ lines with ~60 imports and no render test, so these read
 * the source instead of mounting it. That is a weaker guarantee than a render
 * and it is stated as such: what it pins is the *wiring* of the two defects
 * fixed here, both of which were a single expression in the bubble.
 *
 * Both defects were invisible in review for the same reason -- the code looked
 * like it was doing the right thing. The document card had an `onPress` that
 * evaluated to `undefined`, and the thumbnail slot was handed the original
 * asset's URL, which renders correctly and merely costs the whole file.
 */
const source = readFileSync(join(__dirname, "..", "ChatScreen.tsx"), "utf8");

const bubble = (() => {
  const start = source.indexOf("const mediaIdentity = {");
  const end = source.indexOf("function DocumentAttachmentCard");
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end);
})();

describe("a document attachment is openable", () => {
  it("taps through to the shared open action rather than a Messenger-local one", () => {
    expect(source).toMatch(/import \{ openDocument \} from "\.\.\/media\/mediaActions"/);
    const card = source.slice(source.indexOf("function DocumentAttachmentCard"));
    expect(card).toMatch(/await openDocument\(\{/);
    // The handler has to reach the Pressable. A card that computes `open` and
    // renders without it is exactly the shape of the bug being fixed.
    expect(card).toMatch(/onPress=\{open\}/);
  });

  it("reports a failed open instead of leaving the tap looking ignored", () => {
    const card = source.slice(source.indexOf("function DocumentAttachmentCard"));
    expect(card).toMatch(/if \(result\.status !== "opened"\) setFailure\(result\.message\)/);
  });

  it("is the fallback for every attachment type with no richer renderer", () => {
    // Not gated on a mime allowlist: an unrecognised type must still land on a
    // card that can open, which is what makes the branch unreachable-proof.
    // Two-space indent followed by the function's own closing brace: the card is
    // the component's last statement, so it is reached unconditionally.
    expect(bubble).toMatch(/\n  return <DocumentAttachmentCard message=\{message\} url=\{mediaUrl\} \/>;\n\}/);
  });
});

describe("a thread does not download the videos in it", () => {
  it("takes one access grant per bubble, carrying both URLs", () => {
    expect(bubble.match(/useMessengerMediaAccessUrl\(/g)).toHaveLength(1);
  });

  it("posters a video from the derived thumbnail only, never from the movie", () => {
    const videoBranch = bubble.slice(bubble.indexOf('if (type === "video")'));
    expect(videoBranch).toMatch(/thumbnailUrl \? \(/);
    // The falsy-poster fallback is the defect. There is no bound worth falling
    // back through for video: a 90-minute file behind `<Image>` is a download of
    // the whole asset to paint a card.
    expect(videoBranch).not.toMatch(/thumbnailUrl \|\| mediaUrl/);
  });

  it("still allows a photo to fall back, because its size is bounded", () => {
    const imageBranch = bubble.slice(
      bubble.indexOf('if (type === "image" || type === "gif") {'),
      bubble.indexOf('if (type === "video")')
    );
    expect(imageBranch).toMatch(/uri: thumbnailUrl \|\| mediaUrl/);
  });
});
