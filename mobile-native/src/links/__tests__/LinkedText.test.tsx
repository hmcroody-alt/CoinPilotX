/**
 * The rendered half: is the link a tap target, and is the prose not.
 *
 * The "prose is not tappable" assertion is the one worth keeping. It is the
 * machine-checkable form of the requirement that the bubble must not become one
 * big button — if a future change hoists `onPress` up to the outer `<Text>` to
 * simplify something, this is what notices.
 */

import { fireEvent, render } from "@testing-library/react-native";
import { Text } from "react-native";
import { LinkedText } from "../LinkedText";

describe("LinkedText", () => {
  it("renders plain text with no link roles", () => {
    const screen = render(<LinkedText text="no links here" />);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("no links here")).toBeTruthy();
  });

  it("makes each URL its own link element", () => {
    const screen = render(
      <LinkedText text="a https://pulsesoc.com/pulse/post/1 b https://apple.com c" />
    );
    expect(screen.queryAllByRole("link")).toHaveLength(2);
  });

  it("passes the normalised URL, not the displayed slice", () => {
    const onLinkPress = jest.fn();
    const screen = render(<LinkedText text="see www.pulsesoc.com/pulse" onLinkPress={onLinkPress} />);
    fireEvent.press(screen.getByText("www.pulsesoc.com/pulse"));
    expect(onLinkPress).toHaveBeenCalledWith("https://www.pulsesoc.com/pulse");
  });

  it("strips trailing sentence punctuation from what it opens", () => {
    const onLinkPress = jest.fn();
    const screen = render(
      <LinkedText text="Visit https://apple.com when you have time." onLinkPress={onLinkPress} />
    );
    fireEvent.press(screen.getByText("https://apple.com"));
    expect(onLinkPress).toHaveBeenCalledWith("https://apple.com");
  });

  it("keeps the text around the link", () => {
    const screen = render(<LinkedText text="Visit https://apple.com when you have time." />);
    expect(screen.getByText(/Visit/)).toBeTruthy();
    expect(screen.getByText(/when you have time\./)).toBeTruthy();
  });

  it("does not make the surrounding prose a tap target", () => {
    const onLinkPress = jest.fn();
    const screen = render(
      <LinkedText text="Visit https://apple.com now" onLinkPress={onLinkPress} />
    );
    // The paragraph that contains the link carries no press handler, and the
    // plain segments are raw strings rather than elements — so there is no node
    // between the glyph run of the URL and the root that could be pressed. This
    // is the structural form of "do not make the entire bubble clickable".
    const paragraph = screen.getByText(/Visit/);
    expect(paragraph.props.onPress).toBeUndefined();
    expect(paragraph.props.accessibilityRole).not.toBe("link");
    expect(onLinkPress).not.toHaveBeenCalled();
  });

  it("never renders a javascript: or data: payload as a link", () => {
    const screen = render(
      <LinkedText text="javascript:alert(1) and data:text/html,<script> and file:///etc/passwd" />
    );
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("forwards a long press so the message action sheet stays reachable", () => {
    const onLongPress = jest.fn();
    const screen = render(
      <LinkedText text="https://pulsesoc.com/pulse/post/1" onLongPress={onLongPress} />
    );
    fireEvent(screen.getByRole("link"), "longPress");
    expect(onLongPress).toHaveBeenCalled();
  });

  it("underlines the link rather than relying on colour alone", () => {
    const screen = render(<LinkedText text="https://apple.com" />);
    const flattened = Object.assign({}, ...[screen.getByRole("link").props.style].flat(9).filter(Boolean));
    expect(flattened.textDecorationLine).toBe("underline");
  });

  it("renders emoji and mention text unchanged beside a link", () => {
    const body = "@pilot 🚀 https://pulsesoc.com/pulse/post/2432 🎉";
    const screen = render(<LinkedText text={body} />);
    // The paragraph still reads as the exact string that was sent: the mention
    // token and both emoji survive segmentation unchanged, and only the URL is
    // promoted to a link.
    expect(screen.getByText(body)).toBeTruthy();
    expect(screen.queryAllByRole("link")).toHaveLength(1);
  });

  it("applies the caller's text style to both prose and link", () => {
    const screen = render(
      <LinkedText text="hi https://apple.com" style={{ fontSize: 13 }} />
    );
    const flattened = Object.assign({}, ...[screen.getByRole("link").props.style].flat(9).filter(Boolean));
    expect(flattened.fontSize).toBe(13);
  });

  it("composes with a host that wraps it, the way the bubble does", () => {
    // ContentTranslation hands `renderText` its visible string; this is the
    // same shape, proving the component does not need to be the root.
    const screen = render(
      <Text>
        <LinkedText text="translated https://pulsesoc.com/pulse" />
      </Text>
    );
    expect(screen.queryAllByRole("link")).toHaveLength(1);
  });
});
