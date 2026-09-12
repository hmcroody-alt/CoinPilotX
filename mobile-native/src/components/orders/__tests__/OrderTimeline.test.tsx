/**
 * The order timeline, rendered.
 *
 * `ordersDashboard.test.ts` pins the derivation — which strip each fulfilment
 * kind gets, and which step a status reaches. That is not the same claim as
 * "the buyer does not read the word 'packed'", because between the derivation
 * and the phone sits a component that used to pick its own steps with a second
 * `variant === "pickup" ? … : …`. Two copies of one choice is how a two-step
 * digital strip ends up drawn against a four-step reached index.
 *
 * So this file asserts on rendered text: the actual strings on the screen for
 * each variant, from each perspective, at each status. It is the difference
 * between a served field and a delivered one.
 */
import React from "react";
import { render } from "@testing-library/react-native";

import { OrderTimeline } from "../OrderTimeline";
import {
  SHIPPING_STEPS,
  stepsForVariant,
  timelineVariantOf,
  type OrderTimelineVariant
} from "../../../api/ordersDashboard";

function labels(variant: OrderTimelineVariant, perspective: "buyer" | "seller", status = "paid") {
  const view = render(<OrderTimeline status={status} variant={variant} perspective={perspective} />);
  return stepsForVariant(variant).map((step) => {
    const text = perspective === "buyer" ? step.buyerLabel : step.sellerLabel;
    // Throws if the label is not on screen, which is the point: the strip the
    // derivation chose must be the strip the component drew.
    view.getByText(text);
    return text;
  });
}

describe("OrderTimeline rendering", () => {
  it("never tells a digital buyer their file is being packed or on its way", () => {
    const view = render(
      <OrderTimeline status="paid" variant={timelineVariantOf("digital")} perspective="buyer" />
    );
    expect(view.queryByText("Being packed")).toBeNull();
    expect(view.queryByText("On its way")).toBeNull();
    expect(view.getByText("Delivered to your account")).toBeTruthy();
  });

  it("never asks an appointment buyer to wait for a parcel", () => {
    for (const kind of ["service_remote", "service_in_person", "event_online", "booking_remote"]) {
      const view = render(
        <OrderTimeline status="paid" variant={timelineVariantOf(kind)} perspective="buyer" />
      );
      expect(view.queryByText("Being packed")).toBeNull();
      expect(view.queryByText("On its way")).toBeNull();
      expect(view.getByText("Booked")).toBeTruthy();
    }
  });

  it("draws exactly the strip the derivation chose, for every variant and both ends", () => {
    // The component used to choose its own steps. If it ever does again, one of
    // these getByText calls fails rather than the mismatch shipping silently.
    const variants: OrderTimelineVariant[] = ["shipping", "pickup", "digital", "scheduled"];
    for (const variant of variants) {
      expect(labels(variant, "buyer")).toHaveLength(stepsForVariant(variant).length);
      expect(labels(variant, "seller")).toHaveLength(stepsForVariant(variant).length);
    }
  });

  it("swaps only the wording between perspectives, never the shape", () => {
    // Same order, two ends: same number of dots, same reached point. The buyer
    // reads "Order placed" where the seller reads "Paid".
    const buyer = render(<OrderTimeline status="shipped" variant="shipping" perspective="buyer" />);
    const seller = render(<OrderTimeline status="shipped" variant="shipping" perspective="seller" />);
    expect(buyer.getByText("On its way")).toBeTruthy();
    expect(seller.getByText("Shipped")).toBeTruthy();
    expect(seller.queryByText("On its way")).toBeNull();
    expect(buyer.queryByText("Shipped")).toBeNull();
    expect(SHIPPING_STEPS).toHaveLength(4);
  });

  it("tags only the steps the live surface cannot confirm", () => {
    // "Preview" is the honesty marker: a mock step is drawn dimmed and labelled
    // rather than presented as known progress. A digital sale has no such step,
    // so the marker must not appear there at all.
    expect(render(<OrderTimeline status="paid" variant="scheduled" perspective="buyer" />)
      .queryAllByText("Preview")).toHaveLength(1);
    expect(render(<OrderTimeline status="paid" variant="pickup" perspective="buyer" />)
      .queryAllByText("Preview")).toHaveLength(2);
    expect(render(<OrderTimeline status="paid" variant="digital" perspective="buyer" />)
      .queryAllByText("Preview")).toHaveLength(0);
  });

  it("announces the reached step to assistive technology in the reader's own words", () => {
    const buyer = render(<OrderTimeline status="paid" variant="digital" perspective="buyer" />);
    expect(buyer.getByLabelText("Order progress: Delivered to your account")).toBeTruthy();
    const seller = render(<OrderTimeline status="paid" variant="digital" perspective="seller" />);
    expect(seller.getByLabelText("Order progress: Delivered")).toBeTruthy();
  });

  it("reports no progress at all on a cancelled order rather than a false step", () => {
    const view = render(<OrderTimeline status="cancelled" variant="shipping" perspective="buyer" />);
    expect(view.getByLabelText("Order timeline not started")).toBeTruthy();
  });
});
