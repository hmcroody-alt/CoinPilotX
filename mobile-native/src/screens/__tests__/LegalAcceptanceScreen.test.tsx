/**
 * The step itself: what the member can read, what they can do, and -- the part
 * worth a test -- what they cannot.
 *
 * The screen has exactly two exits, accept and sign out, and no route to the
 * application. That is enforced in App.tsx by rendering it above
 * `NavigationContainer`, so the assertions here are about the other half: that
 * the documents it offers are the canonical pages on pulsesoc.com and not the
 * copy bundled in this app, and that a mid-flow revision re-enters the step
 * against the new versions instead of recording agreement to superseded text.
 *
 * Run: npx jest src/screens/__tests__/LegalAcceptanceScreen.test.tsx
 */
import React from "react";
import { Linking } from "react-native";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

const mockComplete = jest.fn();
const mockSignOut = jest.fn();

jest.mock("../../session/auth", () => {
  const actual = jest.requireActual("../../session/auth");
  return {
    ...actual,
    completeLegalAcceptance: (...args: unknown[]) => mockComplete(...args),
    signOut: (...args: unknown[]) => mockSignOut(...args)
  };
});

import { LegalAcceptanceScreen } from "../LegalAcceptanceScreen";
import { AuthContext, stateFor } from "../../session/auth";
import { PulseApiError } from "../../api/pulseApi";
import { activateLocale } from "../../i18n/engine";

const documents = [
  { document: "terms", version: "PULSESOC_TERMS_2026_05", title: "Terms of Service", path: "/terms" },
  { document: "privacy", version: "PULSESOC_PRIVACY_2026_05", title: "Privacy Policy", path: "/privacy" }
];

const challenge = { documents, ticket: "signed.ticket", ttl_seconds: 900 };

const setAuthState = jest.fn();

function renderStep(override = challenge) {
  const auth = {
    authState: stateFor("LEGAL_ACCEPTANCE_REQUIRED", null, override),
    setAuthState,
    requestReauthentication: jest.fn()
  };
  return render(
    <AuthContext.Provider value={auth}>
      <LegalAcceptanceScreen challenge={override} user={{ user_id: 77, display_name: "Marla" }} />
    </AuthContext.Provider>
  );
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockComplete.mockResolvedValue(stateFor("AUTHENTICATED", { user_id: 77 } as never));
  mockSignOut.mockResolvedValue(stateFor("UNAUTHENTICATED", null));
});

describe("what the member is shown", () => {
  it("names every outstanding document", () => {
    const { getByText } = renderStep();
    expect(getByText("Terms of Service")).toBeTruthy();
    expect(getByText("Privacy Policy")).toBeTruthy();
  });

  it("opens the canonical page, not the copy bundled in this app", async () => {
    // `settings/legalContent.ts` carries a snapshot dated months behind the
    // documents in force. Accepting version X while reading X-1 is exactly the
    // defect a version ledger exists to prevent, so the only acceptable target
    // is the path the server returned.
    const openURL = jest.spyOn(Linking, "openURL").mockResolvedValue(true as never);
    const { getByLabelText } = renderStep();

    fireEvent.press(getByLabelText("Read the Terms of Service"));

    expect(openURL).toHaveBeenCalledWith("https://pulsesoc.com/terms");
  });

  it("shows no version string -- the ledger's identifiers are not member-facing copy", () => {
    const { queryByText } = renderStep();
    expect(queryByText(/PULSESOC_TERMS/)).toBeNull();
    expect(queryByText(/2026_05/)).toBeNull();
  });
});

describe("accepting", () => {
  it("records against the challenge the server issued", async () => {
    const { getByLabelText } = renderStep();

    fireEvent.press(getByLabelText("I agree"));

    await waitFor(() => expect(mockComplete).toHaveBeenCalledWith(challenge));
    expect(setAuthState).toHaveBeenCalledWith(expect.objectContaining({ phase: "AUTHENTICATED" }));
  });

  it("re-enters the step when the documents move mid-flow", async () => {
    // A revision landed while the member was reading. The server refused the
    // stale ticket and described the new versions; agreeing to the old ones
    // would put a row on file asserting consent to text that no longer exists.
    const fresh = {
      documents: [{ document: "terms", version: "PULSESOC_TERMS_2027_01", title: "Terms of Service", path: "/terms" }],
      ticket: "fresh.ticket"
    };
    mockComplete.mockRejectedValueOnce(
      new PulseApiError("Updated again.", 403, "legal_acceptance_required", { legal_acceptance: fresh })
    );
    const { getByLabelText, getByText } = renderStep();

    fireEvent.press(getByLabelText("I agree"));

    await waitFor(() => expect(getByText(/changed while you were reading/)).toBeTruthy());
    expect(setAuthState).not.toHaveBeenCalled();

    fireEvent.press(getByLabelText("I agree"));
    await waitFor(() => expect(mockComplete).toHaveBeenLastCalledWith(fresh));
  });

  it("returns the member to sign-in when the credential no longer answers", async () => {
    // An expired ticket, a revoked token, a restriction applied since sign-in.
    // None of those are retryable from this screen.
    mockComplete.mockRejectedValueOnce(new PulseApiError("Sign in again.", 401, "session_expired"));
    const { getByLabelText } = renderStep();

    fireEvent.press(getByLabelText("I agree"));

    await waitFor(() => expect(setAuthState).toHaveBeenCalledWith(expect.objectContaining({ phase: "UNAUTHENTICATED" })));
  });

  it("stays on the step and reports a network failure, so a retry is safe", async () => {
    // The ledger is unique on (account, document, version), so the retry this
    // invites cannot produce a second row -- which is why the honest thing is
    // to leave the member here with the button live rather than guessing.
    mockComplete.mockRejectedValueOnce(new PulseApiError("PulseSoc is unreachable.", 0, "request_unreachable"));
    const { getByLabelText, getByText } = renderStep();

    fireEvent.press(getByLabelText("I agree"));

    await waitFor(() => expect(getByText("PulseSoc is unreachable.")).toBeTruthy());
    expect(setAuthState).not.toHaveBeenCalled();

    mockComplete.mockResolvedValueOnce(stateFor("AUTHENTICATED", { user_id: 77 } as never));
    fireEvent.press(getByLabelText("I agree"));
    await waitFor(() => expect(setAuthState).toHaveBeenCalledWith(expect.objectContaining({ phase: "AUTHENTICATED" })));
  });
});

describe("declining", () => {
  it("signs out rather than leaving the member on a step with no exit", async () => {
    const { getByLabelText } = renderStep();

    fireEvent.press(getByLabelText("Sign out"));

    await waitFor(() => expect(setAuthState).toHaveBeenCalledWith(expect.objectContaining({ phase: "UNAUTHENTICATED" })));
  });

  it("still ends signed out when the sign-out call itself fails", async () => {
    mockSignOut.mockRejectedValueOnce(new Error("keychain unavailable"));
    const { getByLabelText } = renderStep();

    fireEvent.press(getByLabelText("Sign out"));

    await waitFor(() => expect(setAuthState).toHaveBeenCalledWith(expect.objectContaining({ phase: "UNAUTHENTICATED" })));
  });
});
