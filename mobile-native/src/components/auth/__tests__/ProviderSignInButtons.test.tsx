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
 *
 * The third is that the device's yes is not sufficient. `isAvailableAsync()`
 * is true on every iPhone since iOS 13 whether or not this server holds a
 * single Apple credential, so a device-only gate would put a live Apple button
 * on every phone the moment this screen shipped -- opening the real Apple
 * sheet, taking a real credential, and then eating the 503 that the federated
 * endpoint answers for an unconfigured provider. The server's answer is the
 * second gate, and the cases below hold the device capable so that the
 * server's veto is the only thing under test.
 */

import { act, render } from "@testing-library/react-native";

const mockAppleAvailable = jest.fn();
const mockGoogleAvailable = jest.fn();
const mockGetFederatedProviders = jest.fn();

jest.mock("../../../auth/providerSheets", () => ({
  appleSignInAvailable: () => mockAppleAvailable(),
  googleSignInAvailable: () => mockGoogleAvailable()
}));

jest.mock("../../../api/auth", () => ({
  getFederatedProviders: () => mockGetFederatedProviders()
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
  // The default is a server that honours both, so that every test above this
  // concern keeps asserting the *device* gate in isolation. A test that wants
  // the server's veto says so explicitly.
  mockGetFederatedProviders.mockResolvedValue({ ok: true, available: ["apple", "google"] });
});

/**
 * Let both availability probes settle and let React commit the result.
 *
 * Needed only by the tests that assert a button is *absent*. The positive
 * tests use `findByTestId`, which retries until the element appears; absence
 * has no such affordance, so an unflushed assertion would be measuring render
 * latency rather than the gate. Wrapped in `act` so the state update this
 * causes is the test's own doing rather than an unhandled one.
 */
async function flushProbe() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

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

describe("the server's veto", () => {
  // Every test here holds BOTH device answers at yes, so the only thing that
  // can hide a button is the server. That is the whole point of the second
  // gate: device capability and server capability are different questions, and
  // the device cannot answer the second one.

  it("shows no Apple button on a capable iPhone when the server holds no Apple credential", async () => {
    // The defect this exists to prevent, stated as a test. `isAvailableAsync()`
    // says yes on this phone and says nothing about PulseSoc's configuration,
    // so a device-only gate ships a button that opens Apple's real sheet and
    // then fails with a 503 -- after the member has committed an identity.
    mockGetFederatedProviders.mockResolvedValue({ ok: true, available: [] });

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    // Flushed through `act` rather than a bare `await Promise.resolve()`: the
    // probe resolves on a later tick, and a negative assertion made before
    // that tick passes whether the gate works or not. This is the difference
    // between a test and a test-shaped delay.
    await flushProbe();
    expect(screen.queryByTestId("continue-with-apple")).toBeNull();
    expect(screen.queryByTestId("provider-sign-in-buttons")).toBeNull();
  });

  it("withholds only the provider the server vetoes, not both", async () => {
    // A server configured for Apple and not Google is the likely first state
    // after an owner sets up one provider. Google's absence must not cost
    // Apple, and vice versa -- the gates are per-provider.
    mockGetFederatedProviders.mockResolvedValue({ ok: true, available: ["apple"] });

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-apple")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-google")).toBeNull();
  });

  it("withholds Apple when the server honours only Google", async () => {
    // The mirror of the case above, because a gate that is stuck on one
    // provider's name looks identical to a working one until you ask it about
    // the other.
    mockGetFederatedProviders.mockResolvedValue({ ok: true, available: ["google"] });

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    expect(await screen.findByTestId("continue-with-google")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-apple")).toBeNull();
  });

  it("offers nothing when the server cannot be reached", async () => {
    // Unreachable server, timeout, 500: "cannot tell" resolves to the answer
    // that cannot mislead. Email/password is untouched by this.
    mockGetFederatedProviders.mockRejectedValue(new Error("Network request failed"));

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    await flushProbe();
    expect(screen.queryByTestId("provider-sign-in-buttons")).toBeNull();
  });

  it("offers nothing when the answer is a 200 that is not this shape", async () => {
    // A captive portal or a misrouted proxy answers 200 with its own body. An
    // absent `available` is not an empty permission -- it is no answer.
    mockGetFederatedProviders.mockResolvedValue({ ok: true } as never);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    await flushProbe();
    expect(screen.queryByTestId("provider-sign-in-buttons")).toBeNull();
  });

  it("does not let a string answer become a substring match", async () => {
    // `"unavailable: apple, google".includes("apple")` is true. If `available`
    // arrives as a string rather than an array, `.includes` silently changes
    // from a membership test to a substring test and this prose would
    // authorise both providers.
    mockGetFederatedProviders.mockResolvedValue({ ok: true, available: "unavailable: apple, google" } as never);

    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} />);

    await flushProbe();
    expect(screen.queryByTestId("continue-with-apple")).toBeNull();
    expect(screen.queryByTestId("continue-with-google")).toBeNull();
  });

  it("asks the server exactly once per mount", async () => {
    // A login screen must not sit in a retry loop against an endpoint that
    // answers configuration. One question, and the fail-closed answer stands.
    render(<ProviderSignInButtons onSelect={jest.fn()} />);

    await flushProbe();
    expect(mockGetFederatedProviders).toHaveBeenCalledTimes(1);
  });

  it("does not ask the server when a test pins the honoured set", async () => {
    // The seam exists so a test can hold the device capable and still assert
    // the veto. If it silently fell through to the real probe, every test
    // above would depend on network ordering.
    const screen = render(<ProviderSignInButtons onSelect={jest.fn()} serverProvidersOverride={["apple"]} />);

    expect(await screen.findByTestId("continue-with-apple")).toBeTruthy();
    expect(screen.queryByTestId("continue-with-google")).toBeNull();
    expect(mockGetFederatedProviders).not.toHaveBeenCalled();
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
