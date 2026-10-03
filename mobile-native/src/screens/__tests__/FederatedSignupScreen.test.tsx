/**
 * What these tests are for.
 *
 * This screen exists for one reason: Apple and Google can prove who somebody
 * is, and neither can say whether that person is old enough or agrees to the
 * Terms. The server refuses to create the account without those two answers,
 * and this is the only place they are collected.
 *
 * So the invariant worth pinning is narrow and absolute: an account must not be
 * creatable without somebody having actually answered.
 *
 * What holds that, and what cannot: the gate itself is pinned from both sides
 * -- neither answer alone reaches the server, and turning an answer back off
 * re-closes the gate, so a one-way latch fails too. A pre-ticked switch fails
 * two tests below.
 *
 * What is NOT caught here, stated plainly because it was mutation-tested and
 * survived: a literal `age_confirmed: true` at the call site passes all of
 * these. It cannot be observed through this screen, because submit is
 * unreachable while either answer is false, so there is no reachable state in
 * which the literal and the member's answer disagree. That makes the literal
 * harmless only for as long as the gate tests above it stay alive -- it is the
 * gate, not the payload, that is load-bearing, and the pair (literal + loosened
 * gate) is what would silently record unfelt consent. `email_opt_in` is the
 * exception: it is reachable in both states, so a literal there does fail.
 */

import { render, fireEvent, waitFor } from "@testing-library/react-native";

const mockCompleteFederatedSignup = jest.fn();
const mockSetAuthState = jest.fn();
const mockNavigate = jest.fn();

// Defined inside the factory, not above it: `jest.mock` is hoisted above every
// const in the file, so a class declared out here is in the temporal dead zone
// when the factory runs. It has to be a real class rather than a plain object
// because the screen narrows on `instanceof`.
jest.mock("../../api/pulseApi", () => ({
  PulseApiError: class PulseApiError extends Error {
    status: number;
    code?: string;
    constructor(message: string, status: number, code?: string) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
    }
  }
}));

jest.mock("../../session/auth", () => ({
  completeFederatedSignup: (...args: unknown[]) => mockCompleteFederatedSignup(...args),
  useAuth: () => ({ setAuthState: mockSetAuthState })
}));

let mockTicket: Record<string, unknown> = {};
jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useNavigation: () => ({ navigate: mockNavigate }),
  useRoute: () => ({ params: { ticket: mockTicket } })
}));

jest.mock("../../components/auth/LoginBackground", () => ({ LoginBackground: () => null }));

// There is no `<SafeAreaProvider>` in a bare RNTL render, and the real hook
// throws rather than guessing. Every screen test in this repo stubs it the same
// way; the inset values are irrelevant to anything asserted here.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

import { FederatedSignupScreen } from "../FederatedSignupScreen";
// The same class the screen will narrow against, so a rejection built here is
// recognised there.
import { PulseApiError } from "../../api/pulseApi";

const SESSION = { status: "signedIn", phase: "AUTHENTICATED", user: { user_id: 9, username: "newcomer" } };

beforeEach(() => {
  jest.clearAllMocks();
  mockTicket = {
    signup_ticket: "tkt.abc",
    provider: "apple",
    email: "zq8f2x@privaterelay.appleid.com"
  };
  mockCompleteFederatedSignup.mockResolvedValue(SESSION);
});

/** Turn on both required consents, the only way to reach the button. */
function consentToEverything(screen: ReturnType<typeof render>) {
  fireEvent(screen.getByTestId("federated-age-switch"), "valueChange", true);
  fireEvent(screen.getByTestId("federated-terms-switch"), "valueChange", true);
}

describe("consent nobody has given yet", () => {
  it("starts with every answer off", () => {
    const screen = render(<FederatedSignupScreen />);

    // A pre-ticked box records an agreement that was never made, which is both
    // the thing the server's refusal exists to prevent and worthless as
    // evidence if it is ever challenged.
    expect(screen.getByTestId("federated-age-switch").props.value).toBe(false);
    expect(screen.getByTestId("federated-terms-switch").props.value).toBe(false);
    expect(screen.getByTestId("federated-marketing-switch").props.value).toBe(false);
  });

  it("cannot create an account on age alone", async () => {
    const screen = render(<FederatedSignupScreen />);
    fireEvent(screen.getByTestId("federated-age-switch"), "valueChange", true);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).not.toHaveBeenCalled());
  });

  it("cannot create an account on the Terms alone", async () => {
    const screen = render(<FederatedSignupScreen />);
    fireEvent(screen.getByTestId("federated-terms-switch"), "valueChange", true);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).not.toHaveBeenCalled());
  });

  it("re-closes the gate when an answer is taken back", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);
    // Somebody reads the Terms properly and changes their mind. A latch that
    // only ever moves towards `true`, or a gate sampled once on first tick,
    // both look identical to the happy path and both would submit here.
    fireEvent(screen.getByTestId("federated-terms-switch"), "valueChange", false);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).not.toHaveBeenCalled());
  });

  it("sends the member's actual answers rather than literals", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).toHaveBeenCalled());
    const payload = mockCompleteFederatedSignup.mock.calls[0][0];
    expect(payload.age_confirmed).toBe(true);
    expect(payload.terms_accepted).toBe(true);
    // Untouched, so it must be false. A marketing opt-in that defaults on is a
    // consent problem in its own right, and this is the only thing holding it.
    expect(payload.email_opt_in).toBe(false);
  });

  it("carries the marketing opt-in only when it was chosen", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);
    fireEvent(screen.getByTestId("federated-marketing-switch"), "valueChange", true);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).toHaveBeenCalled());
    expect(mockCompleteFederatedSignup.mock.calls[0][0].email_opt_in).toBe(true);
  });
});

describe("the ticket", () => {
  it("spends the exact string the server issued", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).toHaveBeenCalled());
    // The ticket is the only proof the provider ever verified anybody. Any
    // transformation of it makes the signup unverifiable.
    expect(mockCompleteFederatedSignup.mock.calls[0][0].signup_ticket).toBe("tkt.abc");
  });

  it("omits country entirely rather than sending an empty one", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).toHaveBeenCalled());
    expect(mockCompleteFederatedSignup.mock.calls[0][0]).not.toHaveProperty("country");
  });

  it("trims a country before sending it", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);
    fireEvent.changeText(screen.getByTestId("federated-country-input"), "  Ireland  ");

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockCompleteFederatedSignup).toHaveBeenCalled());
    expect(mockCompleteFederatedSignup.mock.calls[0][0].country).toBe("Ireland");
  });
});

describe("Hide My Email", () => {
  it("shows the relay address rather than hiding or rejecting it", () => {
    const screen = render(<FederatedSignupScreen />);

    // A privaterelay.appleid.com address is a real, deliverable address and a
    // legitimate PulseSoc identity. Showing it is what stops it being a
    // surprise the first time the member looks for their email on file.
    expect(screen.getByTestId("federated-signup-email").props.children).toBe(
      "zq8f2x@privaterelay.appleid.com"
    );
  });

  it("renders without an email at all, because Apple may not disclose one", () => {
    mockTicket = { signup_ticket: "tkt.abc", provider: "apple" };

    const screen = render(<FederatedSignupScreen />);

    expect(screen.queryByTestId("federated-signup-email")).toBeNull();
    expect(screen.getByTestId("federated-signup-submit")).toBeTruthy();
  });
});

describe("when the signup is refused", () => {
  it("keeps the member here for a refusal they could still act on", async () => {
    mockCompleteFederatedSignup.mockRejectedValue(new PulseApiError("Too many", 429, "rate_limited"));
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("federated-signup-error")).toBeTruthy());
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockSetAuthState).not.toHaveBeenCalled();
  });

  it("sends the member back to Login when the ticket itself is dead", async () => {
    jest.useFakeTimers();
    // Nothing on this screen can make the next attempt different: a spent or
    // expired ticket needs a fresh provider tap, not a retry.
    mockCompleteFederatedSignup.mockRejectedValue(
      new PulseApiError("Expired", 403, "signup_ticket_expired")
    );
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("federated-signup-error")).toBeTruthy());
    jest.advanceTimersByTime(2000);
    expect(mockNavigate).toHaveBeenCalledWith("Login");
    jest.useRealTimers();
  });

  it("never claims a session it did not get", async () => {
    mockCompleteFederatedSignup.mockRejectedValue(new PulseApiError("Nope", 500));
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("federated-signup-error")).toBeTruthy());
    expect(mockSetAuthState).not.toHaveBeenCalled();
  });
});

describe("when the signup succeeds", () => {
  it("admits the new member", async () => {
    const screen = render(<FederatedSignupScreen />);
    consentToEverything(screen);

    fireEvent.press(screen.getByTestId("federated-signup-submit"));

    await waitFor(() => expect(mockSetAuthState).toHaveBeenCalledWith(SESSION));
  });
});
