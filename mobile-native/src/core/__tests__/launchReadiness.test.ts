/**
 * The launch registry is the ONE gate between a hub card and a real feature,
 * so what must hold is a bijection with reality: a module may be `READY` only
 * when a live, backed route exists, and every live, backed route must be
 * `READY`. Either direction failing produces a lie — a locked card in front of
 * a working feature, or an "open" card in front of nothing.
 */
import { BUSINESS_OS_SECTIONS, businessOsHubPresentation } from "../../api/businessOs";
import {
  BUSINESS_MODULE_READINESS,
  PRESENCE_MODULE_READINESS,
  businessModuleReadiness,
  isBusinessModuleReady,
  isPresenceModuleReady,
  presenceModuleReadiness
} from "../launchReadiness";

describe("business module readiness", () => {
  it("registers every Business OS section exactly once — no unregistered module can ship", () => {
    const sectionKeys = BUSINESS_OS_SECTIONS.map((section) => section.key).sort();
    const readinessKeys = Object.keys(BUSINESS_MODULE_READINESS).sort();
    expect(readinessKeys).toEqual(sectionKeys);
  });

  it("READY if and only if a live, backed route exists", () => {
    for (const section of BUSINESS_OS_SECTIONS) {
      const shipped = section.backed && Boolean(section.route);
      expect({ key: section.key, ready: isBusinessModuleReady(section.key) }).toEqual({
        key: section.key,
        ready: shipped
      });
    }
  });

  it("locks exactly the unfinished modules: customers and team", () => {
    expect(businessModuleReadiness("customers")).toBe("COMING_SOON");
    expect(businessModuleReadiness("team")).toBe("COMING_SOON");
    const locked = Object.entries(BUSINESS_MODULE_READINESS)
      .filter(([, status]) => status !== "READY")
      .map(([key]) => key)
      .sort();
    expect(locked).toEqual(["customers", "team"]);
  });

  it("fails closed on unknown keys — a typo can never open an unfinished surface", () => {
    expect(businessModuleReadiness("not-a-module")).toBe("COMING_SOON");
    expect(isBusinessModuleReady("not-a-module")).toBe(false);
  });
});

describe("hub presentation", () => {
  it("keeps every section visible in canonical order — locked ones included, dashboard excluded", () => {
    const presentation = businessOsHubPresentation();
    expect(presentation.map(({ section }) => section.key)).toEqual(
      BUSINESS_OS_SECTIONS.filter((section) => section.key !== "dashboard").map((section) => section.key)
    );
  });

  it("marks exactly customers and team as locked, everything else live", () => {
    const presentation = businessOsHubPresentation();
    const locked = presentation.filter(({ ready }) => !ready).map(({ section }) => section.key).sort();
    expect(locked).toEqual(["customers", "team"]);
    for (const { section, ready } of presentation) {
      if (ready) expect(section.backed && Boolean(section.route)).toBe(true);
    }
  });

  it("never presents a live card without navigation arguments (no dead buttons)", () => {
    for (const { section, ready } of businessOsHubPresentation()) {
      if (ready) expect(section.route).toBeTruthy();
    }
  });
});

describe("presence module readiness", () => {
  it("every presence destination is READY — the Presence hub has no unfinished modules", () => {
    for (const [key, status] of Object.entries(PRESENCE_MODULE_READINESS)) {
      expect({ key, status }).toEqual({ key, status: "READY" });
      expect(isPresenceModuleReady(key)).toBe(true);
    }
  });

  it("fails closed on unknown presence keys", () => {
    expect(presenceModuleReadiness("holograms")).toBe("COMING_SOON");
    expect(isPresenceModuleReady("holograms")).toBe(false);
  });
});
