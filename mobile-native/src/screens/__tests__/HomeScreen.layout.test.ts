import { readFileSync } from "fs";
import { join } from "path";

// NRB-058: the Home feed must reserve bottom-dock clearance from the device
// safe-area inset plus the shared dock constant — never a device-specific magic
// number (previously a fixed `paddingBottom: 172` that overlapped the composer/
// last row on devices whose home-indicator inset differed from the baseline).
const homeSource = readFileSync(join(__dirname, "..", "HomeScreen.tsx"), "utf8");
const screenSource = readFileSync(join(__dirname, "..", "..", "components", "Screen.tsx"), "utf8");

describe("Home bottom-dock clearance (NRB-058)", () => {
  it("does not hardcode the old device-specific 172pt offset", () => {
    expect(homeSource).not.toMatch(/paddingBottom:\s*172/);
  });

  it("derives the feed's bottom padding from the shared dynamic dock hook", () => {
    expect(homeSource).toContain("useBottomNavContentPadding");
    expect(homeSource).toContain("paddingBottom: bottomContentPadding");
  });

  it("shares the same clearance constant with the standard scroll shell", () => {
    expect(screenSource).toContain("BOTTOM_NAV_CONTENT_CLEARANCE");
    expect(screenSource).not.toMatch(/\+\s*92\b/);
  });
});
