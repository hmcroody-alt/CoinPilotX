/**
 * The one `ContentTranslation` stub, for the fourteen test files that need one.
 *
 * ## Why a stub is needed at all
 *
 * `ContentTranslation` reaches for locale storage, a translation provider and
 * the translation API. A test about which controls a comment row shows, or
 * which attachment a bubble renders, has no business booting that stack, and
 * every one of those files had independently reduced the component to the text
 * it was handed.
 *
 * ## Why they cannot keep doing it independently
 *
 * Thirteen of the fourteen copies were written as `({ text }) => <Text>{text}</Text>`,
 * which was faithful while `ContentTranslation` had one rendering path. It no
 * longer does: `renderText` is the seam through which a body's *decorations* are
 * produced — link spans in `PostCard` and `ChatScreen`, link **and** mention
 * spans in `CommentThread`. A stub that drops `renderText` renders the
 * undecorated paragraph the product stopped rendering, so a screen that had
 * quietly lost every tappable span in a body would still report green. That is
 * not hypothetical: `PostDetailScreen.comments.test.tsx` asserts on a mention
 * span by testID, and it failed the moment `CommentBody` began emitting that
 * span through `renderText` — while its own mock was still returning plain text.
 *
 * Honouring `renderText` also means these tests render the real `segmentLinks` /
 * `segmentRichBody` / `LinkedText` code, so a body arrives at the assertion in
 * as many `<Text>` nodes as it really has. A test wanting the whole body as one
 * string should match on it with a function matcher or assert on the spans; it
 * should not get a single node from a stub that the product does not produce.
 *
 * ## What is deliberately not stubbed
 *
 * `offersTranslation` is a pure predicate that callers invoke during render to
 * decide whether a Translate control is on offer. It is re-exported real, so no
 * test file becomes the authority on a question it does not test.
 *
 * No testID either. `ChatScreenMediaPreview.test.tsx` needs one — it has to tell
 * a document's name in the bubble body apart from the same name in the card
 * title, which matching on text cannot do — and it keeps its own stub for that
 * reason. Granting a testID here would mean every other test could find
 * `bubble-body` on a component that has no such id on a device, which is a
 * worse trade than one bespoke stub. It is the only file that opts out.
 */

import { Text } from "react-native";
import type { StyleProp, TextStyle } from "react-native";
import type { ReactNode } from "react";

type StubProps = {
  text?: string;
  textStyle?: StyleProp<TextStyle>;
  numberOfLines?: number;
  renderText?: (visible: string, translated: boolean) => ReactNode;
};

/**
 * The stubbed module, shaped like `../components/ContentTranslation`.
 *
 * Call it from inside a `jest.mock` factory:
 *
 *     jest.mock("../../components/ContentTranslation", () =>
 *       require("../../testing/contentTranslationStub").contentTranslationStub()
 *     );
 *
 * `require` rather than an import because a `jest.mock` factory is hoisted above
 * the imports and may not close over a variable unless its name is
 * `mock`-prefixed.
 *
 * `translated` is always `false`: this stub never translates, which is exactly
 * what the real component reports for a body showing its original text. A test
 * that needs the translated branch has to drive the real component.
 */
export function contentTranslationStub() {
  return {
    ...jest.requireActual("../components/ContentTranslation"),
    ContentTranslation: ({ text, textStyle, numberOfLines, renderText }: StubProps) =>
      renderText ? (
        renderText(String(text ?? ""), false)
      ) : (
        // `textStyle` and `numberOfLines` are forwarded because the real
        // component applies them on this branch, and a snapshot or a
        // truncation assertion would otherwise disagree with the product for a
        // reason that has nothing to do with translation.
        <Text style={textStyle} numberOfLines={numberOfLines}>
          {text}
        </Text>
      )
  };
}
