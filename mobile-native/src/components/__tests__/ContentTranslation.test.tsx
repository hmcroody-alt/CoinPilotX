import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { ContentTranslation } from "../ContentTranslation";
import {
  peekTranslationPreference,
  translatePulseContent,
  updateTranslationPreference
} from "../../api/translation";

jest.mock("../../core/TimeZoneContext", () => ({
  useTimeZonePreference: () => ({ locale: "fr-FR" })
}));

jest.mock("../../api/translation", () => ({
  peekTranslationPreference: jest.fn(),
  subscribeTranslationPreference: jest.fn(() => () => undefined),
  translatePulseContent: jest.fn(),
  updateTranslationPreference: jest.fn()
}));

const peekPreferenceMock = peekTranslationPreference as jest.MockedFunction<typeof peekTranslationPreference>;
const translateMock = translatePulseContent as jest.MockedFunction<typeof translatePulseContent>;
const updatePreferenceMock = updateTranslationPreference as jest.MockedFunction<typeof updateTranslationPreference>;

beforeEach(() => {
  jest.clearAllMocks();
  peekPreferenceMock.mockReturnValue(undefined);
  translateMock.mockResolvedValue({
    status: "translated",
    original_text: "Hello PulseSoc",
    translated: true,
    cached: false,
    translated_text: "Bonjour PulseSoc",
    source_language: "en",
    target_language: "fr-fr",
    policy: "ask"
  });
  updatePreferenceMock.mockImplementation(async (source, target, policy) => ({
    source_language: source,
    target_language: target,
    policy,
    updated_at: new Date().toISOString()
  }));
});

it("translates on demand and restores the canonical original", async () => {
  const screen = render(
    <ContentTranslation contentType="post" contentRef={42} text="Hello PulseSoc" />
  );
  expect(screen.getByText("Hello PulseSoc")).toBeTruthy();
  fireEvent.press(screen.getByLabelText("Translate to fr-fr"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
  expect(translateMock).toHaveBeenCalledWith(expect.objectContaining({
    contentType: "post",
    contentRef: 42,
    targetLanguage: "fr-fr",
    force: true
  }));
  fireEvent.press(screen.getByLabelText("Show original text"));
  expect(screen.getByText("Hello PulseSoc")).toBeTruthy();
});

it("persists Always Translate and immediately translates", async () => {
  const screen = render(
    <ContentTranslation contentType="chat" contentRef="m1" text="Hello" />
  );
  fireEvent.press(screen.getByLabelText("Always translate to fr-fr"));
  await waitFor(() => expect(updatePreferenceMock).toHaveBeenCalledWith("auto", "fr-fr", "always"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
});

it("honors an existing Never Translate preference without calling the provider", async () => {
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
    expect(screen.getByLabelText("Never translate to fr-fr").props.accessibilityState).toEqual({ selected: true })
  );
  expect(screen.getByText("Original bio")).toBeTruthy();
  expect(translateMock).not.toHaveBeenCalled();
});

it("keeps compact chat bubbles clean when no different language is detected", () => {
  const screen = render(
    <ContentTranslation contentType="chat" contentRef="m-clean" text="See you soon" controlsMode="compact" />
  );
  expect(screen.getByText("See you soon")).toBeTruthy();
  expect(screen.queryByLabelText("Translate message to fr-fr")).toBeNull();
  expect(screen.queryByLabelText("Always translate to fr-fr")).toBeNull();
  expect(screen.queryByLabelText("Never translate to fr-fr")).toBeNull();
});

it("shows one compact chat translation action and moves preferences into a sheet", async () => {
  const screen = render(
    <ContentTranslation
      contentType="chat"
      contentRef="m-foreign"
      text="Hola PulseSoc"
      sourceLanguage="es"
      controlsMode="compact"
    />
  );

  expect(screen.getByLabelText("Translate message to fr-fr")).toBeTruthy();
  expect(screen.queryByLabelText("Always translate to fr-fr")).toBeNull();
  expect(screen.queryByLabelText("Never translate to fr-fr")).toBeNull();

  fireEvent.press(screen.getByLabelText("Translate message to fr-fr"));
  expect(screen.getByText("Translate now")).toBeTruthy();
  expect(screen.getByLabelText("Always translate to fr-fr")).toBeTruthy();
  expect(screen.getByLabelText("Never translate to fr-fr")).toBeTruthy();

  fireEvent.press(screen.getByText("Translate now"));
  await waitFor(() => expect(screen.getByText("Bonjour PulseSoc")).toBeTruthy());
});
