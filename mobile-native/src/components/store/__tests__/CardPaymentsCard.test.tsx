/**
 * The card-payments row, rendered.
 *
 * `cardPaymentState` already pins *which* states get a door. This file pins
 * that the door is actually drawn and actually goes somewhere — the two things
 * a pure state machine cannot promise, and the two things that were missing
 * when an approved seller with no Stripe account was shown the single word
 * "Unavailable" and left there.
 */

jest.mock("../../../i18n", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

import { fireEvent, render } from "@testing-library/react-native";
import { CardPaymentsCard } from "../CardPaymentsCard";
import type { CardPaymentStatus } from "../../../api/sellerAccess";

const onSetUpPayments = jest.fn();

beforeEach(() => onSetUpPayments.mockReset());

function draw(status: CardPaymentStatus) {
  return render(
    <CardPaymentsCard status={status} onSetUpPayments={onSetUpPayments} testID="card" />
  );
}

describe("CardPaymentsCard", () => {
  it("gives a seller with no Stripe account a button, not just a word", () => {
    const view = draw("SETUP_REQUIRED");
    expect(view.getByTestId("card-label").props.children).toBe(
      "commerce:sellerPayments.setupRequiredLabel"
    );
    fireEvent.press(view.getByTestId("card-cta"));
    expect(onSetUpPayments).toHaveBeenCalledTimes(1);
  });

  it("keeps the door open for every recoverable state", () => {
    for (const status of [
      "SETUP_REQUIRED",
      "SETUP_IN_PROGRESS",
      "ACTION_REQUIRED",
      "RESTRICTED"
    ] as const) {
      const view = draw(status);
      expect(view.queryByTestId("card-cta")).toBeTruthy();
      view.unmount();
    }
  });

  it("draws no button where pressing one would achieve nothing", () => {
    for (const status of ["UNDER_REVIEW", "UNAVAILABLE"] as const) {
      const view = draw(status);
      expect(view.queryByTestId("card")).toBeTruthy();
      expect(view.queryByTestId("card-cta")).toBeNull();
      view.unmount();
    }
  });

  it("still explains itself when there is nothing to press", () => {
    // UNAVAILABLE is the state that most needs its sentence: the word alone
    // reads as "your store is broken" rather than "cards specifically are off,
    // and your other payment methods are unaffected".
    const view = draw("UNAVAILABLE");
    expect(view.getByTestId("card-label").props.children).toBe(
      "commerce:sellerPayments.unavailableLabel"
    );
    expect(view.queryByText("commerce:sellerPayments.unavailableBody")).toBeTruthy();
  });

  it("stays off the dashboard entirely when card payments are already on", () => {
    // A seller taking cards does not need a permanent row telling them so on a
    // screen whose job is to surface what needs attention.
    expect(draw("READY").queryByTestId("card")).toBeNull();
  });

  it("does not call the handler just for rendering", () => {
    draw("ACTION_REQUIRED");
    expect(onSetUpPayments).not.toHaveBeenCalled();
  });
});
