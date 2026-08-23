import { readFileSync } from "fs";
import { join } from "path";

const source = readFileSync(join(__dirname, "..", "MarketplaceCheckoutScreen.tsx"), "utf8");

describe("checkout asks before it charges", () => {
  it("opens on the details step whenever the order type asks for anything", () => {
    expect(source).toContain('useState<Stage>(needsDetailsStep ? "details" : "review")');
    expect(source).toContain("const needsDetailsStep = mustChooseLane || fulfillmentFields(declaredKind, tickets).length");
  });

  it("no longer defers the delivery address to the payment page", () => {
    // The sentence this replaces was the architecture: the address was first
    // asked for by Stripe, after the buyer had committed to paying.
    expect(source).not.toContain("delivery address on the secure payment page");
    expect(source).toContain("collectAddress: false");
  });

  it("will not leave the details step with a required field blank", () => {
    expect(source).toContain("firstMissingFulfillmentField(kind, tickets, details)");
  });

  it("sends what the buyer typed to both checkout lanes", () => {
    expect(source.match(/mustChooseLane \? lane : "",\s*paymentMode,\s*details/g)).toHaveLength(2);
  });

  it("returns to the details step to edit rather than restarting checkout", () => {
    expect(source).toContain('setStage("details")');
    expect(source).toContain("Edit order details");
  });
});
