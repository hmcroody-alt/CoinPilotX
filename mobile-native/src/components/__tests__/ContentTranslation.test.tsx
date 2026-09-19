/**
 * This component is the only place a user can ask for a translation, so it is
 * also the only place the Stage 1 rewiring is observable. The tests below mock
 * the router rather than the hook, which means the real `useContentTranslation`
 * runs: what is under test is the whole path from a press to a request, and
 * back from a typed failure to something a person can read.
 *
 * Two of these tests would have passed against the old component and still
 * catch real defects in the new one, which is why they are here rather than in
 * the hook's own file:
 *
 *   - the automatic path must send `userInitiated: false`. That single boolean
 *     is what stands between "Always translate" and a bill that scales with how
 *     far the user scrolls, and nothing else in the app sets it.
 *   - a download must never be a side effect of rendering. `allowDownload` is
 *     asserted false on every request except the one that comes from pressing
 *     the download control.
 *
 * Copy is asserted literally, in English, after `activateLocale("en")`. That is
 * deliberate: `t()` falls back to a humanized key when a key does not resolve,
 * and a humanized `translation.a11y.translateTo` reads "Translate To" — close
 * enough to real copy to survive a screenshot and a code review. Matching the
 * catalog exactly is the only assertion that can tell the difference.
 */

import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockTranslateText = jest.fn();
const mockCancelTranslationRequests = jest.fn();
jest.mock("../../services/translation/router", () => ({
  translateText: (...args: unknown[]) => mockTranslateText(...args),
  cancelTranslationRequests: (...args: unknown[]) => mockCancelTranslationRequests(...args)
}));

jest.mock("../../core/TimeZoneContext", () => ({
  useTimeZonePreference: () => ({ locale: "fr-FR" })
}));

jest.mock("../../api/translation", () => ({
  peekTranslationPreference: jest.fn(),
  subscribeTranslationPreference: jest.fn(() => () => undefined),
  updateTranslationPreference: jest.fn()
}));

import { ContentTranslation } from "../ContentTranslation";
import { peekTranslationPreference, updateTranslationPreference } from "../../api/translation";
import { activateLocale } from "../../i18n/engine";

const peekPreferenceMock = peekTranslationPreference as jest.MockedFunction<typeof peekTranslationPreference>;
const updatePreferenceMock = updateTranslationPreference as jest.MockedFunction<typeof updateTranslationPreference>;

function success(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    requestId: "post:42#1",
    contentId: "post:42",
    provider: "apple_on_device",
    translatedText: "Bonjour PulseSoc",
    sourceLanguage: "en",
    targetLanguage: "fr-fr",
    cached: false,
    downloadPrepared: false,
    durationMs: 8,
    ...overrides
  };
}

function failure(overrides: Record<string, unknown> = {}) {
  return {
    ok: false,
    requestId: "post:42#1",
    contentId: "post:42",
    provider: "apple_on_device",
    code: "provider_failed",
    recoverable: true,
    downloadAvailable: false,
    ...overrides
  };
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  peekPreferenceMock.mockReturnValue(undefined);
  mockTranslateText.mockResolvedValue(success());
  updatePreferenceMock.mockImplementation(async (source, target, policy) => ({
    source_language: source,
    target_language: target,
    policy,
    updated_at: new Date().toISOString()
  }));
});

it("translates on demand and restores the original without asking again", async () => {
  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  expect(screen.getByText("Hello PulseSoc")).toBeTruthy();

  fireEvent.press(screen.getByLabelText("Translate to French"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());

  expect(mockTranslateText).toHaveBeenCalledWith(
    expect.objectContaining({
      contentType: "post",
      contentId: "post:42",
      text: "Hello PulseSoc",
      targetLanguage: "fr-fr",
      userInitiated: true,
      allowDownload: false
    })
  );
  expect(screen.getByText("Translated from English")).toBeTruthy();

  fireEvent.press(screen.getByLabelText("Show original text"));
  expect(screen.getByText("Hello PulseSoc")).toBeTruthy();

  // Going back to the translation must not be a second request. On the cloud
  // path that is a second charge for words already on screen.
  fireEvent.press(screen.getByLabelText("Translate to French"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  expect(mockTranslateText).toHaveBeenCalledTimes(1);
});

it("sends the automatic translation as not user-initiated", async () => {
  // The whole of Stage 8's cost control at this layer is this one flag. An
  // automatic request that arrived as `userInitiated: true` would be allowed to
  // reach the billable provider, and nothing on screen would look different.
  peekPreferenceMock.mockReturnValue({
    source_language: "auto",
    target_language: "fr-fr",
    policy: "always",
    updated_at: null
  });

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );

  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  expect(mockTranslateText).toHaveBeenCalledWith(
    expect.objectContaining({ userInitiated: false, allowDownload: false })
  );
});

it("persists Always translate and immediately translates", async () => {
  const screen = render(<ContentTranslation contentType="chat" contentRef="m1" text="Hello" />);

  fireEvent.press(screen.getByLabelText("Always translate to French"));

  await waitFor(() => expect(updatePreferenceMock).toHaveBeenCalledWith("auto", "fr-fr", "always"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  expect(mockTranslateText.mock.calls[0][0].userInitiated).toBe(false);
});

it("honors an existing Never translate preference without calling the router", async () => {
  peekPreferenceMock.mockReturnValue({
    source_language: "auto",
    target_language: "fr-fr",
    policy: "never",
    updated_at: null
  });

  const screen = render(
    <ContentTranslation contentType="profile" contentRef={9} text="Original bio" />
  );

  await waitFor(() =>
    expect(screen.getByLabelText("Never translate to French").props.accessibilityState).toEqual({
      selected: true
    })
  );
  expect(screen.getByText("Original bio")).toBeTruthy();
  expect(mockTranslateText).not.toHaveBeenCalled();
});

it("offers the download without starting one, and names Apple before it happens", async () => {
  mockTranslateText.mockResolvedValue(
    failure({ code: "model_not_installed", downloadAvailable: true })
  );

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  fireEvent.press(screen.getByLabelText("Translate to French"));

  await waitFor(() =>
    expect(
      screen.getByText(
        "PulseSoc uses Apple's private on-device translation. Apple may download the required language to this iPhone."
      )
    ).toBeTruthy()
  );
  // The request that produced the offer must not itself have authorised a
  // download, or the explainer would be describing something already underway.
  expect(mockTranslateText.mock.calls[0][0].allowDownload).toBe(false);

  mockTranslateText.mockResolvedValue(success());
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Download the French language to this device"));
  });

  expect(mockTranslateText.mock.calls[1][0].allowDownload).toBe(true);
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
});

it("shows the generic label for a cost-control refusal and leaks none of it", async () => {
  // `cloud_fallback_disabled` is a fact about the deployment, not about the
  // text. Stage 9 says the user gets the generic label; `detail` is for metrics
  // and may carry a host or a status line, so it must reach no pixel.
  mockTranslateText.mockResolvedValue(
    failure({
      code: "cloud_fallback_disabled",
      provider: "unavailable",
      recoverable: false,
      cloudExclusion: "fallback_disabled",
      detail: "breaker open after 503 from translate.googleapis.com"
    })
  );

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  fireEvent.press(screen.getByLabelText("Translate to French"));

  await waitFor(() => expect(screen.getByText("Translation unavailable")).toBeTruthy());
  expect(screen.queryByText(/googleapis/)).toBeNull();
  expect(screen.queryByText(/503/)).toBeNull();
  expect(screen.queryByText(/fallback_disabled/)).toBeNull();
  // Not recoverable, so there is nothing to try again.
  expect(screen.queryByLabelText("Retry translation")).toBeNull();
});

it("offers Try again for a recoverable fault", async () => {
  mockTranslateText.mockResolvedValue(failure({ code: "provider_failed", recoverable: true }));

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  fireEvent.press(screen.getByLabelText("Translate to French"));

  await waitFor(() => expect(screen.getByText("Try again")).toBeTruthy());

  mockTranslateText.mockResolvedValue(success());
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Retry translation"));
  });
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  expect(screen.queryByText("Try again")).toBeNull();
});

it("says nothing when the user cancelled the download themselves", async () => {
  // Reporting someone's own decision back to them as an error is how a status
  // row becomes something people learn to ignore.
  mockTranslateText.mockResolvedValue(
    failure({ code: "download_canceled", recoverable: true, downloadAvailable: true })
  );

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Translate to French"));
  });

  expect(screen.queryByText("Translation unavailable")).toBeNull();
  expect(screen.queryByText("Try again")).toBeNull();
  expect(
    screen.queryByText(
      "PulseSoc uses Apple's private on-device translation. Apple may download the required language to this iPhone."
    )
  ).toBeNull();
  expect(screen.getByText("Hello PulseSoc")).toBeTruthy();
});

it("reports a failed preference save without showing what failed", async () => {
  updatePreferenceMock.mockRejectedValue(
    new Error("500 from https://pulsesoc.com/api/pulse/translate/preference")
  );

  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Always translate to French"));
  });

  expect(screen.getByText("Could not save your translation preference.")).toBeTruthy();
  expect(screen.queryByText(/pulsesoc\.com/)).toBeNull();
  // The toggle went back to where it was, so the screen is not claiming a
  // preference the server never stored.
  expect(screen.getByLabelText("Always translate to French").props.accessibilityState).toEqual({
    selected: false
  });
});

it("keeps compact chat bubbles clean when no different language is detected", () => {
  const screen = render(
    <ContentTranslation contentType="chat" contentRef="m-clean" text="See you soon" controlsMode="compact" />
  );
  expect(screen.getByText("See you soon")).toBeTruthy();
  expect(screen.queryByLabelText("Translate to French")).toBeNull();
  expect(screen.queryByLabelText("Always translate to French")).toBeNull();
  expect(screen.queryByLabelText("Never translate to French")).toBeNull();
});

it("shows one compact chat action and moves preferences into a sheet", async () => {
  const screen = render(
    <ContentTranslation
      contentType="chat"
      contentRef="m-foreign"
      text="Hola PulseSoc"
      sourceLanguage="es"
      controlsMode="compact"
    />
  );

  expect(screen.getByLabelText("Translate to French")).toBeTruthy();
  expect(screen.queryByLabelText("Always translate to French")).toBeNull();
  expect(screen.queryByLabelText("Never translate to French")).toBeNull();

  fireEvent.press(screen.getByLabelText("Translate to French"));
  expect(screen.getByText("Message language options")).toBeTruthy();
  expect(screen.getByText("Translate now")).toBeTruthy();
  expect(screen.getByLabelText("Always translate to French")).toBeTruthy();
  expect(screen.getByLabelText("Never translate to French")).toBeTruthy();

  fireEvent.press(screen.getByText("Translate now"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
});

/**
 * The messenger's long-press menu asks for a translation by bumping
 * `translateRequestId`. These four cases are the ones that decide whether that
 * is genuinely the same control or a second one wearing its name.
 *
 * Mutation contract:
 *   - deleting the `if (!translateRequestId) return;` guard must turn "does not
 *     translate a bubble nobody asked about" red;
 *   - adding `toggleTranslation` to the effect's dependency array does not turn
 *     a test red -- it exhausts the V8 heap, because the effect changes the
 *     state that re-creates the callback that re-runs the effect. A crashed
 *     suite is a kill, and the loudest one available;
 *   - calling `translate()` from the effect instead of the toggle must turn
 *     "puts the original back when the menu is used a second time" red.
 */
describe("a translation asked for from outside the component", () => {
  it("does not translate a bubble nobody asked about", async () => {
    const screen = render(
      <ContentTranslation contentType="chat" contentRef="m-1" text="Hola PulseSoc" sourceLanguage="es" />
    );
    await act(async () => undefined);
    expect(screen.getByText("Hola PulseSoc")).toBeTruthy();
    expect(mockTranslateText).not.toHaveBeenCalled();
  });

  it("translates when the id first arrives", async () => {
    const screen = render(
      <ContentTranslation
        contentType="chat"
        contentRef="m-1"
        text="Hola PulseSoc"
        sourceLanguage="es"
        translateRequestId={1}
      />
    );
    await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  });

  it("stays translated once it settles", async () => {
    // `toggleTranslation` is re-created *as a result of* translating. An effect
    // that listed it as a dependency would therefore run a second time and
    // toggle straight back -- and because restoring the original is a local
    // switch rather than a round trip, the request count would stay at one the
    // whole way through. So the assertion that catches it is what is on screen
    // after the dust settles, not how many times the router was called.
    const screen = render(
      <ContentTranslation
        contentType="chat"
        contentRef="m-1"
        text="Hola PulseSoc"
        sourceLanguage="es"
        translateRequestId={1}
      />
    );
    await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
    await act(async () => undefined);
    await act(async () => undefined);
    expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy();
    expect(screen.queryByText("Hola PulseSoc")).toBeNull();
    expect(mockTranslateText).toHaveBeenCalledTimes(1);
  });

  it("puts the original back when the menu is used a second time", async () => {
    // Not a boolean for exactly this reason: the second ask has to be
    // expressible, and it means the same thing pressing the inline control
    // twice means.
    const screen = render(
      <ContentTranslation
        contentType="chat"
        contentRef="m-1"
        text="Hola PulseSoc"
        sourceLanguage="es"
        translateRequestId={1}
      />
    );
    await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());

    screen.rerender(
      <ContentTranslation
        contentType="chat"
        contentRef="m-1"
        text="Hola PulseSoc"
        sourceLanguage="es"
        translateRequestId={2}
      />
    );
    await waitFor(() => expect(screen.getByText("Hola PulseSoc")).toBeTruthy());
    // Restoring the original is a local switch, not a second round trip.
    expect(mockTranslateText).toHaveBeenCalledTimes(1);
  });
});
