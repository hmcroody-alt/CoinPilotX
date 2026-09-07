/**
 * Capability-aware section readiness.
 *
 * The layer above this one already tests that a locked module says so and does
 * not navigate. What it could not test is the case where the two facts a row
 * depends on disagree: the readiness audit says READY, and there is nowhere to
 * go. `readinessOf` defaults unregistered ids to READY on purpose — the table is
 * a deny-list, and an allow-list would silently lock every feature nobody
 * remembered to register — so "the audit found nothing wrong" is not the same
 * claim as "this opens something". A module in that gap rendered with a chevron
 * and an accessibility label saying it worked, and did nothing when pressed.
 *
 * `resolveSectionCapability` is the conjunction of both facts in one place, and
 * these are the cases that pin it. The ones that matter most are the ones the
 * real registry does not currently contain: an invariant that holds only because
 * of today's data is an invariant that holds until someone adds a row.
 *
 * The registry is spied rather than replaced wholesale, so the tests that read
 * the real one below still read the real one.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

import * as businessOsApi from "../../api/businessOs";
import { BUSINESS_OS_SECTION_MODULES, type BusinessOsModule } from "../../api/businessOs";
import { preloadNamespaces, translate } from "../../i18n/engine";
import { BusinessOsSectionScreen } from "../../screens/BusinessOsSectionScreen";
import { businessSubmoduleId, readinessOf } from "../readiness";
import {
  capabilityCopyState,
  resolveSectionCapabilities,
  resolveSectionCapability,
  sectionCapabilityLists
} from "../sectionCapabilities";

beforeAll(async () => {
  await preloadNamespaces("en", ["commerce", "common"]);
});

const SECTION_KEYS = Object.keys(BUSINESS_OS_SECTION_MODULES);

/** A registry row, built here so a case can exist that the real registry lacks. */
function moduleFixture(overrides: Partial<BusinessOsModule> & { key: string }): BusinessOsModule {
  return {
    label: "Fixture",
    blurb: "A capability invented by this test.",
    icon: "cube-outline",
    ...overrides
  };
}

describe("a capability's verdict", () => {
  /** Case A — the audit is happy and there is somewhere to go. */
  it("is available when readiness allows it and it has a destination", () => {
    const capability = resolveSectionCapability(
      "customers",
      moduleFixture({ key: "conversations", route: "BusinessOsMessages", params: { filter: "all" } })
    );

    expect(capability.availability).toBe("READY");
    expect(capability.available).toBe(true);
    // The route is read off the verdict rather than off the module, so a caller
    // cannot navigate with one it was not granted.
    expect(capability.route).toBe("BusinessOsMessages");
    expect(capability.params).toEqual({ filter: "all" });
  });

  /** Case B — the audit is holding it. The row is locked whatever its route says. */
  it("is unavailable while the audit is holding it, even with a route", () => {
    expect(readinessOf(businessSubmoduleId("customers", "segments"))).toBe("BUILDING");
    const capability = resolveSectionCapability(
      "customers",
      moduleFixture({ key: "segments", route: "BusinessOsMessages" })
    );

    // A route does not overrule the audit; the audit is the stricter fact.
    expect(capability.availability).toBe("BUILDING");
    expect(capability.available).toBe(false);
    expect(capability.route).toBeUndefined();
  });

  /** Case C — the gap. READY, and nothing behind it. */
  it("is unavailable when the audit allows it but nothing is wired up", () => {
    expect(readinessOf(businessSubmoduleId("customers", "conversations"))).toBe("READY");
    const capability = resolveSectionCapability("customers", moduleFixture({ key: "conversations" }));

    expect(capability.availability).toBe("NO_DESTINATION");
    expect(capability.available).toBe(false);
    expect(capability.route).toBeUndefined();
  });

  /**
   * Case E — an id the readiness table has never heard of.
   *
   * The default stays READY, deliberately, and `launchGate.test.tsx` pins that.
   * What must NOT follow from it is availability: absence from an audit is not
   * evidence that a screen exists. The destination is what supplies that
   * evidence, so an unknown id with nowhere to go resolves to the safe state.
   */
  it("does not treat an unaudited capability as usable", () => {
    const id = businessSubmoduleId("sectionNobodyRegistered", "capabilityNobodyRegistered");
    expect(readinessOf(id)).toBe("READY");

    const capability = resolveSectionCapability(
      "sectionNobodyRegistered",
      moduleFixture({ key: "capabilityNobodyRegistered" })
    );

    expect(capability.id).toBe(id);
    expect(capability.available).toBe(false);
    expect(capability.availability).toBe("NO_DESTINATION");
  });

  /**
   * The other half of case E, stated so the deny-list is not quietly narrowed
   * later: an unaudited capability that DOES open a real screen still opens. The
   * mission forbids blindly locking a feature that works, and this is the line
   * between the two — evidence of a destination, not membership of a list.
   */
  it("still opens an unaudited capability that has a real destination", () => {
    const capability = resolveSectionCapability(
      "sectionNobodyRegistered",
      moduleFixture({ key: "capabilityNobodyRegistered", route: "BusinessOsMessages" })
    );

    expect(capability.available).toBe(true);
    expect(capability.availability).toBe("READY");
  });
});

describe("what the user is told", () => {
  /**
   * `NO_DESTINATION` is a fourth verdict but not a fourth word. "Coming soon" is
   * true of it and the product already says it; a badge reading "Not wired up"
   * would leak the shape of the registry into the operator's language.
   */
  it("says coming soon for a capability with nowhere to go, not a new word", () => {
    expect(capabilityCopyState("NO_DESTINATION")).toBe("COMING_SOON");
    expect(capabilityCopyState("COMING_SOON")).toBe("COMING_SOON");
    expect(capabilityCopyState("BUILDING")).toBe("BUILDING");
    expect(capabilityCopyState("READY")).toBe("READY");
  });

  it("never reaches for developer language", () => {
    const forbidden = /broken|not implemented|unimplemented|unavailable|disabled|error|todo|wip|failed/i;
    (["READY", "BUILDING", "COMING_SOON", "NO_DESTINATION"] as const).forEach((availability) => {
      const state = capabilityCopyState(availability);
      if (state === "READY") return;
      const key = state === "BUILDING" ? "statusBuilding" : "statusComingSoon";
      expect(translate(`commerce:launch.${key}`)).not.toMatch(forbidden);
    });
  });
});

describe("the lists a landing page renders", () => {
  /** Case F — a section whose capabilities all resolve as before is unchanged. */
  it.each(SECTION_KEYS)("keeps every one of %s's capabilities in registry order", (key) => {
    const capabilities = resolveSectionCapabilities(key);
    const { available, upcoming } = sectionCapabilityLists(key);

    expect(capabilities.map((capability) => capability.module.key)).toEqual(
      businessOsApi.businessOsSectionModules(key).map((module) => module.key)
    );
    // Every capability lands in exactly one list. A module that fell out of both
    // would vanish from the roadmap, which is the failure this layer exists to
    // prevent, and it would still pass a test that only counted the rows shown.
    expect(available.length + upcoming.length).toBe(capabilities.length);
    available.forEach((capability) => expect(capability.available).toBe(true));
    upcoming.forEach((capability) => expect(capability.available).toBe(false));
  });

  /**
   * Cases D, G and H share a mechanism here: this screen has no asynchronous
   * load, so "section unavailable", "still loading" and "error" all arrive as a
   * section with no roster. The lists come back empty, which is what the
   * landing's own fallback sentence is keyed on — never a blank shell.
   */
  it("returns nothing at all for a section it has no roster for", () => {
    expect(resolveSectionCapabilities("sectionNobodyRegistered")).toEqual([]);
    expect(sectionCapabilityLists("sectionNobodyRegistered")).toEqual({ available: [], upcoming: [] });
  });

  /** Read against the real registry: available and routable are the same set. */
  it.each(SECTION_KEYS)("hands out a route for exactly %s's available capabilities", (key) => {
    resolveSectionCapabilities(key).forEach((capability) => {
      expect(capability.available).toBe(Boolean(capability.route));
    });
  });
});

/**
 * The runtime half. The screen suite already asserts this against the registry
 * as it stands; the case below is the one the registry does not contain, which
 * is precisely the one a static check cannot reach.
 */
describe("a section landing given a capability with nowhere to go", () => {
  const ORPHAN = moduleFixture({
    key: "orphan",
    label: "Orphan Capability",
    blurb: "Registered, audited, and pointing at nothing."
  });

  beforeEach(() => {
    jest
      .spyOn(businessOsApi, "businessOsSectionModules")
      .mockImplementation((key: string) => (key === "customers" ? [ORPHAN] : []));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("explains itself instead of answering the tap with nothing", () => {
    const navigation = { navigate: jest.fn(), goBack: jest.fn() };
    const view = render(
      <BusinessOsSectionScreen navigation={navigation} route={{ params: { section: "customers" } }} />
    );

    const id = businessSubmoduleId("customers", "orphan");
    // It is listed, not hidden: the roadmap survives.
    const row = view.getByTestId(`launch-module-${id}`);
    // And it reads as early rather than as working — the state is in the label,
    // not only in the styling, because that is all a screen reader gets.
    expect(row.props.accessibilityLabel).toBe("Orphan Capability. Coming soon.");
    expect(row.props.accessibilityState?.disabled).toBeFalsy();

    fireEvent.press(row);

    // The conjunction is the test. Before this layer the press did neither: no
    // message, no navigation, no way for the user to tell the tap had landed.
    expect(view.getByTestId(`coming-soon-${id}`)).toBeTruthy();
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("puts it under the upcoming heading rather than among the working rows", () => {
    const view = render(
      <BusinessOsSectionScreen
        navigation={{ navigate: jest.fn(), goBack: jest.fn() }}
        route={{ params: { section: "customers" } }}
      />
    );

    expect(view.getByText(translate("commerce:launch.upcomingTitle"))).toBeTruthy();
    expect(view.queryByText(translate("commerce:launch.availableTitle"))).toBeNull();
    // Not the fallback either: the section still has a roster, so the landing
    // describes it rather than apologising for it.
    expect(view.queryByText(translate("commerce:launch.sectionFallbackBody"))).toBeNull();
  });
});
