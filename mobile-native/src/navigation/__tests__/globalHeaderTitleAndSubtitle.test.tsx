/**
 * The header shows a title and a subtitle at the same time.
 *
 * That sounds too small to test until you notice what the navigator now leans
 * on it for. Three Presence routes carry the presence's *name* in their title
 * and their *function* in their subtitle — "Night Signal" over "Who can act for
 * this presence" — and neither half says the whole thing on its own. The title
 * alone does not say what the screen does; the subtitle alone is the sentence
 * that was on screen before, with a demonstrative and no antecedent, above a
 * control that can change somebody's role.
 *
 * `navigatorLocalization.test.ts` asserts the routes pass the name. It reads
 * the navigator as text, so it cannot see what the header does with it: a
 * refactor that dropped the subtitle, or collapsed the two into one line, would
 * leave that suite green and quietly restore the defect. This is the other
 * half — the same claim, made against a render.
 */

import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

// The repo-wide stub for these: the real Icon loads its font asynchronously and
// calls setState when it lands, which arrives after the test has finished and
// prints an `act(...)` warning about a glyph nothing here asserts on.
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

/**
 * `GlobalNavigation` also exports the bottom dock, which owns the Pulse Radio
 * mini player, so importing the header reaches `core/pulseRadio` and through it
 * `expo-av` — a native module, absent under Jest, which fails the suite at
 * require time before a single assertion runs.
 *
 * Stubbed rather than worked around because the header does not read any of
 * this: the radio state is consumed a few hundred lines below, by the dock.
 * The stub is the module's shape and nothing more, so it cannot start standing
 * in for audio behaviour that belongs to the audio suite.
 */
jest.mock("../../core/pulseRadio", () => ({
  getPulseRadioState: () => ({
    status: "offline",
    track: null,
    message: "",
    userWantsPlayback: false,
    interruptedBy: null,
    queue: [],
    queueIndex: -1,
    shuffle: false,
    repeatMode: "off",
    positionMillis: 0,
    durationMillis: 0
  }),
  subscribePulseRadio: () => () => undefined,
  togglePulseRadio: () => Promise.resolve(),
  playNextTrack: () => Promise.resolve(),
  playPreviousTrack: () => Promise.resolve()
}));

import { LogiNexusGlobalHeader } from "../GlobalNavigation";

describe("global header", () => {
  it("shows the title and the subtitle at once", () => {
    const { queryByText, getByTestId } = render(
      <LogiNexusGlobalHeader title="Night Signal" subtitle="Who can act for this presence" />
    );
    expect(queryByText("Night Signal")).toBeTruthy();
    expect(getByTestId("global-header-subtitle").props.children).toBe("Who can act for this presence");
  });

  /**
   * The generic title is the fallback for a deep link that arrives with an id
   * and no name, and in that state the subtitle is the only thing on the bar
   * that says anything. If a future header hid the subtitle whenever a title
   * was present — a plausible way to "clean up" a two-line bar — this is the
   * case that would go blank rather than merely lose a name.
   */
  it("keeps the subtitle when the title is the generic fallback", () => {
    const { queryByText } = render(
      <LogiNexusGlobalHeader title="Team & access" subtitle="Who can act for this presence" />
    );
    expect(queryByText("Team & access")).toBeTruthy();
    expect(queryByText("Who can act for this presence")).toBeTruthy();
  });

  /**
   * The subtitle is optional and a handful of routes pass none, so the absent
   * case is asserted too — as the *element*, not as words.
   *
   * The first draft of this test looked for the text "undefined" and was
   * worthless: dropping the `subtitle ?` guard survived it, because React
   * renders undefined children as nothing, so the query found nothing either
   * way. What the mutant actually leaves behind is a real `<Text>` with a line
   * height and no content — a gap under the title on every route with no
   * subtitle, which no text query can see. Hence the testID.
   */
  it("renders no subtitle element at all when there is no subtitle", () => {
    const { queryByText, queryByTestId } = render(<LogiNexusGlobalHeader title="Night Signal" />);
    expect(queryByText("Night Signal")).toBeTruthy();
    expect(queryByTestId("global-header-subtitle")).toBeNull();
  });
});
