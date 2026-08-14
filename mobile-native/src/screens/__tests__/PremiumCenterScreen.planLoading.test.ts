import fs from "fs";
import path from "path";

describe("PremiumCenterScreen plan loading state machine", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../PremiumCenterScreen.tsx"), "utf8");

  it("does not cancel its own request by depending on offersLoading", () => {
    expect(source).not.toMatch(/\[sells, offers, offersLoading\]/);
    expect(source).toContain("offerRequestActive.current");
    expect(source).toContain("setOffersLoading(false)");
  });

  it("renders a bounded retry action without coupling it to membership failure", () => {
    expect(source).toContain("onRetry={loadOffers}");
    expect(source).toContain('t("premium:plans.unavailable")');
    expect(source).toContain('t("premium:retry")');
    expect(source).not.toContain('trackPremium("premium_product_fetch_failure")');
  });
});
