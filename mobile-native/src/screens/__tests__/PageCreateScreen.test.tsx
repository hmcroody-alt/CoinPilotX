/**
 * Creation is the one flow where a mistake is expensive: the handle is the
 * presence's address, the server refuses a duplicate with a 409, and there is
 * no second chance to pick a name that has already been taken by the time the
 * wizard asks for it.
 *
 * The load-bearing fact tested here is that an availability verdict is about
 * the handle it was asked about and nothing else. The check is debounced, so
 * between the keystroke and the answer the *previous* verdict is still sitting
 * in state — and it used to be read unconditionally, which meant the wizard
 * would carry "Available." over a handle nobody had checked, keep Next
 * enabled, and let the create fail three screens later.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockCheckHandle = jest.fn();
const mockCreatePage = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  checkPageHandle: (...args: unknown[]) => mockCheckHandle(...args),
  createPage: (...args: unknown[]) => mockCreatePage(...args)
}));

jest.mock("expo-image-picker", () => ({}), { virtual: true });

import { PageCreateScreen } from "../PageCreateScreen";
import { colors } from "../../theme/colors";
import { presenceAccent } from "../../theme/presenceAccent";

/** The server's own shape: it echoes the candidate it answered about. */
function verdict(candidate: string, available: boolean, reason?: string) {
  return {
    candidate,
    handle: candidate.toLowerCase(),
    available,
    reason: reason || (available ? "Available." : "That handle is taken by another page.")
  };
}

const nav = () => ({ navigate: jest.fn(), replace: jest.fn(), setOptions: jest.fn() });

function show(flavor?: "artist" | "business") {
  const navigation = nav();
  const view = render(
    <PageCreateScreen
      route={{ key: "c", name: "PageCreate", params: flavor ? { flavor } : {} } as never}
      navigation={navigation as never}
    />
  );
  return { view, navigation };
}

/** Walk past the 450ms debounce and let the in-flight check settle. */
async function settleHandleCheck() {
  await act(async () => {
    jest.advanceTimersByTime(500);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockCheckHandle.mockImplementation(async (candidate: string) => verdict(candidate, true));
  mockCreatePage.mockResolvedValue({ id: 7, handle: "nightsignal", name: "Night Signal" });
});

afterEach(() => {
  jest.useRealTimers();
});

/** Fill in a valid identity step and wait for the handle to come back clear. */
async function fillIdentity(view: ReturnType<typeof render>, handle = "nightsignal") {
  fireEvent.press(view.getByText("Music Artist"));
  fireEvent.changeText(view.getByPlaceholderText("e.g. Night Signal"), "Night Signal");
  fireEvent.changeText(view.getByPlaceholderText("yourname"), handle);
  await settleHandleCheck();
}

describe("the handle verdict belongs to the handle it answered about", () => {
  it("says a free handle is free", async () => {
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();

    expect(mockCheckHandle).toHaveBeenCalledWith("nightsignal");
    expect(view.getByText("Available.")).toBeTruthy();
  });

  it("repeats the server's reason for refusing rather than a generic one", async () => {
    mockCheckHandle.mockResolvedValue(verdict("admin", false, "That handle is reserved."));
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "admin");
    await settleHandleCheck();

    expect(view.getByText("That handle is reserved.")).toBeTruthy();
  });

  it("drops a verdict the moment the handle it described is edited", async () => {
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    expect(view.getByText("Available.")).toBeTruthy();

    // One keystroke later this is a different address, and nothing is yet
    // known about it. Leaving "Available." up is a claim about a handle the
    // server has not seen.
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal2");
    expect(view.queryByText("Available.")).toBeNull();
  });

  it("does not let an unchecked handle through the identity step", async () => {
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal2");

    // Next is still on screen — it is refusing, not missing.
    fireEvent.press(view.getByText("Next"));
    expect(view.queryByPlaceholderText("Tell fans who you are")).toBeNull();
  });

  it("lets it through again once the new handle has its own verdict", async () => {
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal2");
    await settleHandleCheck();

    expect(mockCheckHandle).toHaveBeenLastCalledWith("nightsignal2");
    fireEvent.press(view.getByText("Next"));
    expect(view.getByPlaceholderText("Tell fans who you are")).toBeTruthy();
  });

  it("ignores a slow answer about a handle that has since been replaced", async () => {
    // The user typed past the first check. Its answer arrives late and is
    // about an address that is no longer in the box; it must not blank the
    // verdict that was given for the one that is.
    let releaseFirst: ((value: unknown) => void) | null = null;
    mockCheckHandle.mockImplementationOnce(
      () => new Promise((resolve) => { releaseFirst = () => resolve(verdict("night", false, "That handle is taken by another page.")); })
    );
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "night");
    await act(async () => { jest.advanceTimersByTime(500); });

    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    expect(view.getByText("Available.")).toBeTruthy();

    await act(async () => { releaseFirst?.(null); });
    expect(view.getByText("Available.")).toBeTruthy();
    expect(view.queryByText("That handle is taken by another page.")).toBeNull();
  });

  it("stops the spinner when the handle is cleared instead of leaving it turning", async () => {
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "n");
    // Backspaced before the debounce ever fires, so the check that spinner
    // belongs to is never going to happen.
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "");
    await settleHandleCheck();

    expect(mockCheckHandle).not.toHaveBeenCalled();
    expect(view.UNSAFE_queryAllByType(require("react-native").ActivityIndicator)).toHaveLength(0);
  });

  it("treats a failed check as not-yet-available rather than as available", async () => {
    mockCheckHandle.mockRejectedValue(new Error("offline"));
    const { view } = show("artist");
    await fillIdentity(view);

    expect(view.getByText("Couldn't check that handle right now.")).toBeTruthy();
    fireEvent.press(view.getByText("Next"));
    expect(view.queryByPlaceholderText("Tell fans who you are")).toBeNull();
  });

  it("does not ask the server about an empty handle", async () => {
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "   ");
    await settleHandleCheck();
    expect(mockCheckHandle).not.toHaveBeenCalled();
  });

  it("strips a leading @ rather than asking about one", async () => {
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "@nightsignal");
    await settleHandleCheck();
    expect(mockCheckHandle).toHaveBeenCalledWith("nightsignal");
  });
});

describe("the wizard does not create until it has been told it may", () => {
  it("requires a type, a name and a free handle before the details step", async () => {
    const { view } = show("artist");
    fireEvent.press(view.getByText("Next"));
    expect(view.queryByPlaceholderText("Tell fans who you are")).toBeNull();

    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    expect(view.getByPlaceholderText("Tell fans who you are")).toBeTruthy();
  });

  /**
   * Each of the three gates is checked on its own, with the other two
   * satisfied. Pressing Next on a blank form proves only that *something*
   * stopped it, so any one of the three could be dropped and the form would
   * still look guarded.
   */
  it("stops on a name too short to be one, with everything else in order", async () => {
    const { view } = show("artist");
    fireEvent.press(view.getByText("Music Artist"));
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    expect(view.getByText("Available.")).toBeTruthy();

    fireEvent.changeText(view.getByPlaceholderText("e.g. Night Signal"), "N");
    fireEvent.press(view.getByText("Next"));
    expect(view.queryByPlaceholderText("Tell fans who you are")).toBeNull();

    fireEvent.changeText(view.getByPlaceholderText("e.g. Night Signal"), "Ni");
    fireEvent.press(view.getByText("Next"));
    expect(view.getByPlaceholderText("Tell fans who you are")).toBeTruthy();
  });

  it("stops when no category has been chosen, with everything else in order", async () => {
    // The category is what carries the page type, and the page type decides
    // which tabs the presence is even allowed to have. A presence created
    // without one has no ceiling to fill.
    const { view } = show("artist");
    fireEvent.changeText(view.getByPlaceholderText("e.g. Night Signal"), "Night Signal");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    expect(view.getByText("Available.")).toBeTruthy();

    fireEvent.press(view.getByText("Next"));
    expect(view.queryByPlaceholderText("Tell fans who you are")).toBeNull();

    fireEvent.press(view.getByText("Music Artist"));
    fireEvent.press(view.getByText("Next"));
    expect(view.getByPlaceholderText("Tell fans who you are")).toBeTruthy();
  });

  it("will not create without the ownership confirmation the server demands", async () => {
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));

    fireEvent.press(view.getByText("Create Artist Presence"));
    expect(mockCreatePage).not.toHaveBeenCalled();
  });

  it("sends what was typed, trimmed, with the confirmation", async () => {
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    fireEvent.changeText(view.getByPlaceholderText("Tell fans who you are"), "  Synth from Lagos  ");
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Artist Presence"));
    });

    expect(mockCreatePage).toHaveBeenCalledWith(
      expect.objectContaining({
        page_type: "ARTIST",
        name: "Night Signal",
        handle: "nightsignal",
        category: "Music Artist",
        description: "Synth from Lagos",
        confirm_owner: true
      })
    );
  });

  it("lands the new presence on setup rather than on its own empty public page", async () => {
    const { view, navigation } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Artist Presence"));
    });

    // A minute-old presence has no avatar, no posts and nothing connected.
    // Its public page is the one screen with nothing on it to do next.
    expect(navigation.replace).toHaveBeenCalledWith("PagesHub", { focusPageId: 7 });
  });

  it("lands the generic flow on setup too, not on the empty page", async () => {
    // The flavourless entry point makes exactly the same presence through
    // exactly the same call. It used to be the one that opened the public
    // page, so which door an owner came through decided whether they were
    // shown their next step or a blank screen.
    const { view, navigation } = show();
    fireEvent.press(view.getByText("Artist"));
    fireEvent.changeText(view.getByPlaceholderText("e.g. CoinPlotXAI"), "Night Signal");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Presence"));
    });

    expect(navigation.replace).toHaveBeenCalledWith("PagesHub", { focusPageId: 7 });
    expect(navigation.replace).not.toHaveBeenCalledWith("Page", expect.anything());
  });

  it("repeats the server's refusal instead of a generic failure", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockCreatePage.mockRejectedValue(new PulseApiError("That handle is already in use.", 409));
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Artist Presence"));
    });

    // The handle raced away between the check and the create. Only the server
    // knows that, and it is the one sentence that tells the owner what to do.
    expect(view.getByText("That handle is already in use.")).toBeTruthy();
  });

  it("does not repeat a server fault at the owner as if it were their mistake", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockCreatePage.mockRejectedValue(new PulseApiError("IntegrityError at pulse_pages", 500));
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Artist Presence"));
    });

    expect(view.getByText("We couldn't create your Presence right now. Try again.")).toBeTruthy();
    expect(view.queryByText("IntegrityError at pulse_pages")).toBeNull();
  });
});

describe("the flavour presets the wizard without forking the backend", () => {
  it("offers artist categories and asks for a stage name", async () => {
    const { view } = show("artist");
    expect(view.getByText("What kind of artist?")).toBeTruthy();
    expect(view.getByText("Music Artist")).toBeTruthy();
    expect(view.getByText("Artist / stage name")).toBeTruthy();
    // The full 16-type grid is the generic flow's, not this one's.
    expect(view.queryByText("Restaurant")).toBeNull();
  });

  it("offers business categories and asks for a phone number", async () => {
    const { view } = show("business");
    expect(view.getByText("What kind of business?")).toBeTruthy();
    fireEvent.press(view.getByText("Restaurant"));
    fireEvent.changeText(view.getByPlaceholderText("e.g. CoinPlotXAI"), "Kofi's");
    fireEvent.changeText(view.getByPlaceholderText("yourbusiness"), "kofis");
    await settleHandleCheck();
    fireEvent.press(view.getByText("Next"));

    expect(view.getByPlaceholderText("Phone")).toBeTruthy();
    // Genres are an artist field; a restaurant is not asked for one.
    expect(view.queryByPlaceholderText("e.g. Afrobeats, House")).toBeNull();
  });

  it("asks an artist for genres and not for a phone number", async () => {
    // The mirror of the test above, and it has to be written down separately:
    // asserting only that a restaurant is not asked for genres leaves the
    // whole field set free to become the union of both flavours, which is what
    // a preset stops being the moment it presets nothing.
    const { view } = show("artist");
    await fillIdentity(view);
    fireEvent.press(view.getByText("Next"));

    expect(view.getByPlaceholderText("e.g. Afrobeats, House")).toBeTruthy();
    expect(view.queryByPlaceholderText("Phone")).toBeNull();
  });

  it("carries the chosen category's real page type, not the label", async () => {
    const { view } = show("artist");
    // "Other Artist" is a CREATOR page — the label is a category, and the type
    // behind it decides which tabs the presence gets.
    fireEvent.press(view.getByText("Other Artist"));
    fireEvent.changeText(view.getByPlaceholderText("e.g. Night Signal"), "Night Signal");
    fireEvent.changeText(view.getByPlaceholderText("yourname"), "nightsignal");
    await settleHandleCheck();
    fireEvent.press(view.getByText("Next"));
    fireEvent.press(view.getByText("Next"));
    fireEvent(view.UNSAFE_getByType(require("react-native").Switch), "valueChange", true);

    await act(async () => {
      fireEvent.press(view.getByText("Create Artist Presence"));
    });

    expect(mockCreatePage).toHaveBeenCalledWith(
      expect.objectContaining({ page_type: "CREATOR", category: "Other Artist" })
    );
  });
});

describe("the wizard shows the colour the choice will be drawn in", () => {
  // The flavourless entry point is the only place the sixteen page types are
  // offered as themselves. A flavour ("artist", "business") offers categories
  // instead — a label with a type hidden behind it — and those are deliberately
  // left neutral, because the chip's word is not the thing being coloured.
  function flatten(style: unknown): Record<string, unknown> {
    return Object.assign({}, ...[style].flat(Infinity).filter(Boolean)) as Record<string, unknown>;
  }

  it("fills the chosen type in that type's own colour, caption included", () => {
    const { view } = show();
    fireEvent.press(view.getByText("Artist"));

    const tone = presenceAccent("ARTIST");
    // The fill and the caption are asserted separately on purpose: a chip that
    // is the right colour with an unreadable word on it is the failure this
    // system's `ink` token exists to prevent, and only the caption assertion
    // catches it.
    //
    // Worth being honest about what the second assertion can and cannot do.
    // All four hues are bright, so `ink` is the dark background for every one
    // of them, and today that is the same string the sheet's generic active
    // caption uses — swapping one for the other is invisible to any test that
    // could be written. What this pins is the *contract*: the caption comes
    // from the accent, so the day a hue arrives dark enough to need pale ink,
    // this fails instead of shipping a chip nobody can read. The redundant
    // layering that made the swap easy to perform by accident is gone from the
    // screen; this is what would notice if it came back with real consequences.
    expect(flatten(view.getByTestId("page-type-ARTIST").props.style).backgroundColor).toBe(tone.base);
    expect(flatten(view.getByText("Artist").props.style).color).toBe(tone.ink);
  });

  it("does not paint every chosen type the same", () => {
    // Without this, the whole chooser could fill in one colour — the app accent
    // by another name — and the test above would still pass.
    const { view } = show();
    fireEvent.press(view.getByText("Artist"));
    const artist = flatten(view.getByTestId("page-type-ARTIST").props.style).backgroundColor;
    fireEvent.press(view.getByText("Restaurant"));
    const restaurant = flatten(view.getByTestId("page-type-RESTAURANT").props.style).backgroundColor;

    expect(artist).toBe(presenceAccent("ARTIST").base);
    expect(restaurant).toBe(presenceAccent("RESTAURANT").base);
    expect(artist).not.toBe(restaurant);
  });

  it("leaves the types that were not chosen alone", () => {
    // Sixteen filled chips would be a palette to pick from rather than a
    // question to answer. The colour marks *which* one, so it has to be on
    // exactly one of them.
    const { view } = show();
    fireEvent.press(view.getByText("Artist"));

    const unchosen = flatten(view.getByTestId("page-type-RESTAURANT").props.style);
    expect(unchosen.backgroundColor).toBeUndefined();
    expect(flatten(view.getByText("Restaurant").props.style).color).toBe(colors.text);
  });
});
