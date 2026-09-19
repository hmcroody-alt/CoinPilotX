/**
 * A body of text whose links are tappable, and whose prose is not.
 *
 * ## Why this is nested `<Text>` and not a row of `<Pressable>`s
 *
 * A link has to wrap with the sentence around it. Anything built out of views —
 * a `Pressable` per segment in a `flexWrap` row — breaks the line at segment
 * boundaries instead of at word boundaries, so a long URL mid-sentence pushes
 * the rest of the line down and a two-line message becomes four. React Native
 * gives nested `<Text>` its own `onPress` for exactly this case: the text
 * remains one laid-out paragraph, and only the characters of the link are
 * touchable. That is also what satisfies "do not make the entire bubble
 * clickable" — the tap target *is* the glyph run, with nothing to get wrong.
 *
 * ## The bubble keeps its gestures
 *
 * `onLongPress` is forwarded onto the link segments. Without it, long-pressing
 * a link would be swallowed by the inner `<Text>` and the message action sheet
 * — reply, react, report, delete — would be unreachable from the one part of a
 * link-only message there is to press. Press-in/press-out feedback is left to
 * `suppressHighlighting={false}`, the platform default, rather than a state
 * variable per segment: a `useState` here would re-render the whole paragraph
 * on touch-down.
 *
 * ## Underline, not colour alone
 *
 * The link colour is `chatGraphite.senderAccent`, which measures 4.75:1 on the
 * incoming bubble and 4.88:1 on the outgoing one, so it clears 4.5:1 on both
 * without a second token. The underline is not decoration: it is the
 * non-colour channel that keeps a link identifiable in grayscale and under a
 * colour vision deficiency.
 *
 * ## Nothing here is Messenger-specific
 *
 * It takes a string and an `onLinkPress`, so the feed, comments and listing
 * descriptions can adopt it without a second implementation. Messenger is
 * simply the first caller.
 */

import { useMemo } from "react";
import { GestureResponderEvent, StyleProp, Text, TextStyle } from "react-native";
import { chatGraphite } from "../theme/chatGraphite";
import { segmentLinks } from "./messageLinks";

export type LinkedTextProps = {
  text: string;
  style?: StyleProp<TextStyle>;
  /** Style applied on top of `style` for link segments only. */
  linkStyle?: StyleProp<TextStyle>;
  numberOfLines?: number;
  /** Receives the normalised URL, not the displayed slice. */
  onLinkPress?: (url: string) => void;
  /** Forwarded to link segments so the message action sheet stays reachable. */
  onLongPress?: () => void;
  accessibilityLabel?: string;
};

const LINK_STYLE: TextStyle = {
  color: chatGraphite.senderAccent,
  textDecorationLine: "underline"
};

export function LinkedText({
  text,
  style,
  linkStyle,
  numberOfLines,
  onLinkPress,
  onLongPress,
  accessibilityLabel
}: LinkedTextProps) {
  const segments = useMemo(() => segmentLinks(text), [text]);
  const hasLink = segments.some((segment) => Boolean(segment.url));

  // The overwhelmingly common case renders exactly what the screen rendered
  // before this component existed: one `<Text>`, one child, no wrappers. A body
  // with no link must not pay for the ones that have them.
  if (!hasLink) {
    return (
      <Text style={style} numberOfLines={numberOfLines} accessibilityLabel={accessibilityLabel}>
        {text}
      </Text>
    );
  }

  return (
    <Text style={style} numberOfLines={numberOfLines} accessibilityLabel={accessibilityLabel}>
      {segments.map((segment, index) => {
        if (!segment.url) return segment.text;
        const url = segment.url;
        return (
          <Text
            // Segments are positional and the list is rebuilt whenever `text`
            // changes, so the index is the identity.
            key={`link-${index}`}
            accessibilityRole="link"
            style={[style, LINK_STYLE, linkStyle]}
            onPress={(event: GestureResponderEvent) => {
              // Without this the bubble's own press handling runs too, and a
              // future "tap to select" on the bubble would fire alongside the
              // navigation.
              event?.stopPropagation?.();
              onLinkPress?.(url);
            }}
            onLongPress={onLongPress}
          >
            {segment.text}
          </Text>
        );
      })}
    </Text>
  );
}
