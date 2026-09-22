/**
 * The recovery screen's two buttons used to disagree about what an email is.
 *
 * "Reset password" accepted anything non-empty and answered "Check your email"
 * -- the server's generic line, which is a true statement about an address that
 * parses and a false one about a string with no `@` in it, since no lookup ran
 * and nothing could ever be addressed. "Resend email verification" refused the
 * same input with a 400. So one button told a user who mistyped their address to
 * go wait for mail, and the other, on the same screen, corrected them.
 *
 * All 102 `forgot_password_invalid_email` events in production came through the
 * first of those buttons.
 *
 * The screen also now accepts an `intent`: login sends someone here when their
 * account is unconfirmed, and resending is then the action they need, so it
 * leads rather than sitting underneath the password reset they did not ask for.
 */
import React from "react";
import { Alert } from "react-native";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("../../api/auth", () => ({
  requestPasswordRecovery: jest.fn(),
  resendEmailConfirmation: jest.fn()
}));

import { requestPasswordRecovery, resendEmailConfirmation } from "../../api/auth";
import { AccountRecoveryScreen } from "../AccountRecoveryScreen";

const mockedRecover = requestPasswordRecovery as jest.Mock;
const mockedResend = resendEmailConfirmation as jest.Mock;

const goBack = jest.fn();

function renderScreen(params?: { email?: string; intent?: "password" | "verification" }) {
  const navigation = { goBack, navigate: jest.fn() } as any;
  return render(<AccountRecoveryScreen navigation={navigation} route={{ params } as any} />);
}

/** What the user is shown, as one string, regardless of which Alert fired. */
function alertText(spy: jest.SpyInstance) {
  return spy.mock.calls.map((call) => `${call[0]} ${call[1] ?? ""}`).join(" | ");
}

describe("AccountRecoveryScreen", () => {
  let alertSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.clearAllMocks();
    alertSpy = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    mockedRecover.mockResolvedValue({ ok: true, message: "If an account exists, password recovery has been sent." });
    mockedResend.mockResolvedValue({ ok: true, message: "If the account needs confirmation, a new email has been sent." });
  });

  describe("neither button promises mail for a string that is not an address", () => {
    const MALFORMED = ["roody", "roody@", "@example.com", "roody example.com", "   ", "", "roody@example"];

    it.each(MALFORMED)("refuses %j without calling the password route", async (value) => {
      const { getByTestId, getByLabelText } = renderScreen();
      fireEvent.changeText(getByLabelText("Existing account email"), value);
      fireEvent.press(getByTestId("reset-password-button"));
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());
      expect(mockedRecover).not.toHaveBeenCalled();
      expect(alertText(alertSpy)).not.toMatch(/check your email/i);
    });

    it.each(MALFORMED)("refuses %j without calling the resend route", async (value) => {
      const { getByTestId, getByLabelText } = renderScreen();
      fireEvent.changeText(getByLabelText("Existing account email"), value);
      fireEvent.press(getByTestId("resend-verification-button"));
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());
      expect(mockedResend).not.toHaveBeenCalled();
    });

    it("gives the two buttons the identical refusal", async () => {
      // The defect was not that one button was wrong in isolation -- it was that
      // the two disagreed, on the same screen, about the same typed string.
      const first = renderScreen();
      fireEvent.changeText(first.getByLabelText("Existing account email"), "roody");
      fireEvent.press(first.getByTestId("reset-password-button"));
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());
      const fromPassword = alertText(alertSpy);

      alertSpy.mockClear();
      const second = renderScreen();
      fireEvent.changeText(second.getByLabelText("Existing account email"), "roody");
      fireEvent.press(second.getByTestId("resend-verification-button"));
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());

      expect(alertText(alertSpy)).toBe(fromPassword);
    });
  });

  describe("an address that parses still reaches the server", () => {
    it("sends the trimmed address to the password route", async () => {
      // The control for the group above: a shape check that refused everything
      // would satisfy every assertion there.
      const { getByTestId, getByLabelText } = renderScreen();
      fireEvent.changeText(getByLabelText("Existing account email"), "  roody@example.com  ");
      fireEvent.press(getByTestId("reset-password-button"));
      await waitFor(() => expect(mockedRecover).toHaveBeenCalledWith("roody@example.com"));
      expect(alertText(alertSpy)).toMatch(/check your email/i);
    });

    it("sends the trimmed address to the resend route", async () => {
      const { getByTestId, getByLabelText } = renderScreen();
      fireEvent.changeText(getByLabelText("Existing account email"), "  roody@example.com  ");
      fireEvent.press(getByTestId("resend-verification-button"));
      await waitFor(() => expect(mockedResend).toHaveBeenCalledWith("roody@example.com"));
    });

    it("accepts the address shapes the server would have accepted", async () => {
      // A client rule stricter than `is_valid_email` would lock out accounts
      // that were created with these, which is a worse failure than the one
      // being fixed: there is no way for the user to work around it.
      const real = ["a@b.co", "first.last+tag@sub.domain.example", "UPPER@EXAMPLE.COM", "x_y-z@e.io"];
      for (const value of real) {
        mockedRecover.mockClear();
        const { getByTestId, getByLabelText } = renderScreen();
        fireEvent.changeText(getByLabelText("Existing account email"), value);
        fireEvent.press(getByTestId("reset-password-button"));
        await waitFor(() => expect(mockedRecover).toHaveBeenCalledWith(value));
      }
    });

    it("surfaces a server refusal rather than claiming mail was sent", async () => {
      // The route now answers 400 `invalid_email` for input the client's mirror
      // of the rule lets through. Whatever the disagreement, the user must not
      // be told to go and wait.
      mockedRecover.mockRejectedValue(new Error("Enter the email address for your PulseSoc account."));
      const { getByTestId, getByLabelText } = renderScreen();
      fireEvent.changeText(getByLabelText("Existing account email"), "roody@example.com");
      fireEvent.press(getByTestId("reset-password-button"));
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());
      const shown = alertText(alertSpy);
      expect(shown).toMatch(/Enter the email address for your PulseSoc account/);
      expect(shown).not.toMatch(/check your email/i);
    });
  });

  describe("arriving from an unconfirmed login", () => {
    it("prefills the address login already knew", async () => {
      const { getByLabelText } = renderScreen({ email: "roody@example.com", intent: "verification" });
      expect(getByLabelText("Existing account email").props.value).toBe("roody@example.com");
    });

    it("can resend without the user retyping anything", async () => {
      const { getByTestId } = renderScreen({ email: "roody@example.com", intent: "verification" });
      fireEvent.press(getByTestId("resend-verification-button"));
      await waitFor(() => expect(mockedResend).toHaveBeenCalledWith("roody@example.com"));
    });

    it("names the state the user is actually in", async () => {
      const { getByText, queryByText } = renderScreen({ intent: "verification" });
      expect(getByText("Verify your email")).toBeTruthy();
      expect(queryByText("Recover access")).toBeNull();
    });

    it("keeps the password-reset heading when arriving from forgot-password", async () => {
      // The control for the heading swap. `intent` is absent on the ordinary
      // path, and that path is the one every other user takes.
      const { getByText, queryByText } = renderScreen();
      expect(getByText("Recover access")).toBeTruthy();
      expect(queryByText("Verify your email")).toBeNull();
    });

    it("starts with an empty field when login had no address to pass", async () => {
      const { getByLabelText } = renderScreen();
      expect(getByLabelText("Existing account email").props.value).toBe("");
    });
  });

  describe("one request at a time", () => {
    it("does not fire a second send while one is in flight", async () => {
      let release: (value: unknown) => void = () => undefined;
      mockedResend.mockReturnValue(new Promise((resolve) => { release = resolve; }));
      const { getByTestId } = renderScreen({ email: "roody@example.com", intent: "verification" });
      fireEvent.press(getByTestId("resend-verification-button"));
      await waitFor(() => expect(mockedResend).toHaveBeenCalledTimes(1));
      fireEvent.press(getByTestId("resend-verification-button"));
      fireEvent.press(getByTestId("reset-password-button"));
      expect(mockedResend).toHaveBeenCalledTimes(1);
      expect(mockedRecover).not.toHaveBeenCalled();
      // Settling inside `act` so the `setBusy("")` in the `finally` lands during
      // the test rather than after it, where React reports it as an unwrapped
      // update against an already-torn-down tree.
      await act(async () => {
        release({ ok: true, message: "sent" });
      });
    });
  });
});
