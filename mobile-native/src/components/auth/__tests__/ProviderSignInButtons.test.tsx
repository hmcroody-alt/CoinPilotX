/**
 * What these tests are for.
 *
 * The mission's rule is "do not show dead provider buttons", and this component
 * is the only thing enforcing it on iOS. The failure it prevents is not a
 * crash: a Google button in a build with no client id renders fine, opens a
 * sheet, and fails after the member has already chosen an identity -- which
 * reads as PulseSoc rejecting their Google account. Every gate below is
 * asserted in both directions, because a gate that is stuck open and a gate
 * that is stuck closed look identical in a test that only checks one.
 *
 * The second thing pinned here is that neither provider mark is drawn by
 * PulseSoc. The buttons are the vendors' own native controls, and the test
 * asserts that by identity -- if somebody later replaces them with a
 * hand-built Pressable and a bundled PNG to make the two line up visually,
 * these fail.
 */

import { render } from "@testing-library/react-native";

const mockAppleAvailable = jest.fn();
const mockGoogleAvailable = jest.fn();

jest.mock("../../../auth/providerSheets", () => ({
  appleSignInAvailable: () => mockAppleAvailable(),
  googleSignInAvailable: () => mockGoogleAvailable()
}));

// Stand-ins that are identifiable by name, so a test can assert *which*
// component rendered rather than merely that something did.
jest.mock("expo-apple-authentication", () => {
  const { View } = require("react-native");
  return {
    AppleAuthenticationButton: (props: Record<string, unknown>) => <View {...props} />,
    AppleAuthenticationButtonType: { CONTINUE: 2, SIGN_IN: 0 },
    AppleAuthenticationButtonStyle: { WHITE: 0, BLACK: 2 }
  };
});

jest.mock("@react-native-google-signin/google-signin", () => {
  const { View } = require("react-native");
  const GoogleSigninButton = (props: Record<string, unknown>) => <View {...props} />;
  GoogleSigninButton.Size = { Icon: 0, Standard: 1, Wide: 2 };
  GoogleSigninButton.Color = { Dark: "dark", Light: "light" };
  return { GoogleSigninButton };
});

import { ProviderSignInButtons } from "../ProviderSignInButtons";

beforeEach(() => {
  jest.clearAllMocks();
  mockAppleAvailable.mockResolvedValue(true);
  mockGoogleAvailable.mockReturnValue(true);
});

describe("which buttons may appear", () => {
  it("shows both when both providers are actually usable", async () => {
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-apple")).toBeTruthy();
    expect(screen.getByTestId("continue-with-google")).toBeTruthy();
  });

  it("hides Google when this build has no client id, and keeps Apple", async () => {
    // The exact production state until the owner creates the iOS OAuth client.
    // Apple must not be withheld because Google is not ready.
    mockGoogleAvailable.mockReturnValue(false);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-apple")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-google")).toBeNull();
  });

  it("hides Apple when the device cannot offer it, and keeps Google", async () => {
    mockAppleAvailable.mockResolvedValue(false);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-google")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-apple")).toBeNull();
  });

  it("renders nothing at all -- not a bare divider -- when neither is ready", async () => {
    mockAppleAvailable.mockResolvedValue(false);
    mockGoogleAvailable.mockReturnValue(false);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    // An "or continue with" rule above no buttons reads as a broken screen,
    // which is worse than the absence it is trying to describe.
    expect(screen.queryByTestId("provider-sign-in-buttons")).toBeNull();
  });

  it("treats an availability check that rejects as unavailable", async () => {
    // Fail closed: an unanswerable question is not a yes.
    mockAppleAvailable.mockRejectedValue(new Error("no such module"));
    mockGoogleAvailable.mockReturnValue(true);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-google")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-apple")).toBeNull();
  });
});

describe("the provider marks", () => {
  it("uses Apple's own control, asking it for the Continue wording", async () => {
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);
    const apple = await screen.findByTestId("continue-with-apple");

    // CONTINUE, not SIGN_IN: the flow resolves to a sign-in or a signup by
    // itself, so the button must not ask the member to classify themselves.
    expect(apple.props.buttonType).toBe(2);
  });

  it("uses Google's own control rather than a redrawn G", async () => {
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);
    const google = await screen.findByTestId("continue-with-google");

    // Props only the vendor component accepts. A hand-built replacement would
    // not carry them, which is the point: this fails if somebody swaps the
    // official asset for one that matches PulseSoc's palette.
    expect(google.props.size).toBe(2);
    expect(google.props.color).toBe("light");
  });
});

describe("while a sheet is opening", () => {
  it("disables Google so a second tap cannot start a second attempt", async () => {
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} busyProvider="apple" />);

    const google = await screen.findByTestId("continue-with-google");
    expect(google.props.disabled).toBe(true);
  });

  it("leaves both enabled when nothing is in flight", async () => {
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    const google = await screen.findByTestId("continue-with-google");
    expect(google.props.disabled).toBe(false);
  });
});

describe("choosing a provider", () => {
  it("reports which one was tapped", async () => {
    const onSelect = jest.fn();
    const screen = render(<ProviderSignInButtons onSelect={onSelect} />);

    const apple = await screen.findByTestId("continue-with-apple");
    apple.props.onPress();
    expect(onSelect).toHaveBeenCalledWith("apple");

    screen.getByTestId("continue-with-google").props.onPress();
    expect(onSelect).toHaveBeenCalledWith("google");
  });

  it("ignores a tap on Apple while another provider's sheet is open", async () => {
    // Apple's native control has no `disabled`, so the guard has to live in the
    // handler. Without it, a tap during Google's sheet starts a second flow.
    const onSelect = jest.fn();
    const screen = render(<ProviderSignInButtons onSelect={onSelect} busyProvider="google" />);

    const apple = await screen.findByTestId("continue-with-apple");
    apple.props.onPress();

    expect(onSelect).not.toHaveBeenCalled();
  });
});
