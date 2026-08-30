/**
 * The capability layer and the landing that renders it.
 *
 * The landing exists to stop a section's gaps from reading as the user's
 * failure — an absent view count as "nobody looked", an empty reply-rate as
 * "you never answer". That only holds while three things stay true, and each
 * one of them fails silently:
 *
 *   - The two halves agree. States live in `readiness.ts` and labels live in
 *     `sectionCapabilities.ts`, so a typo in either produces a row that gates
 *     nothing, or a lock with nothing to show for it. Neither throws.
 *   - "Available now" is honest. A capability listed as working while its row
 *     says otherwise is the exact claim this whole mechanism exists to avoid.
 *   - A finished section never gets a landing. The failure mode there is not a
 *     bug report, it is a page of text that quietly appears between every
 *     operator and their work.
 *
 * Plus the presentational promise the brief makes and the rest of the launch
 * module already keeps: locked reads as *early*, never as *broken*.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { View } from "react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));

import { BUSINESS_OS_SECTIONS, businessOsNavigationArgs, businessOsSection } from "../../api/businessOs";
import { preloadNamespaces } from "../../i18n/engine";
import { BusinessOsSectionScreen } from "../../screens/BusinessOsSectionScreen";
import {
  businessOsSectionCapabilities,
  businessOsSectionHasLanding,
  businessOsSectionLists,
  businessOsSectionOverview
} from "../sectionCapabilities";
import { LAUNCH_READINESS, businessModuleId, isLaunchGated, readinessOf } from "../readiness";

/**
 * The two one-time process costs, paid at module scope rather than by whichever
 * test runs first. See `screens/__tests__/PageScreen.test.tsx` for the
 * measurement: RNTL's first `render` in a worker mounts a probe tree to learn
 * what its queries match, and the first failing assertion pays for Jest's
 * message machinery. Together they are most of a test's budget on a loaded
 * machine, and `beforeAll` cannot absorb them because hooks are bounded by the
 * same timeout the tests are.
 */
render(<View />).unmount();
try {
  expect(null).toBeTruthy();
} catch {
  // The throw is the point; the message is discarded.
}

beforeAll(async () => {
  await preloadNamespaces("en", ["commerce", "common"]);
});

const nav = () => ({ navigate: jest.fn() });

describe("the capability registry and the gate agree", () => {
  it("gives every section an overview", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const overview = businessOsSectionOverview(section.key);
      expect(overview).toBeTruthy();
      // The landing's subtitle. A section that arrives without one renders a
      // heading over two lists and never says what it is for.
      expect(overview!.purpose.length).toBeGreaterThan(20);
    });
  });

  it("locks nothing it cannot name", () => {
    // A `business:x.y` row whose `y` is not a registered capability gates a
    // module that is never rendered anywhere: invisible, permanent, and
    // indistinguishable from a capability that simply shipped.
    Object.keys(LAUNCH_READINESS)
      .filter((id) => id.startsWith("business:") && id.includes("."))
      .forEach((id) => {
        const [sectionKey, capabilityKey] = id.slice("business:".length).split(".");
        const section = businessOsSection(sectionKey as never);
        expect(section).toBeTruthy();
        const keys = businessOsSectionCapabilities(section!.key).map((capability) => capability.key);
        expect(keys).toContain(capabilityKey);
      });
  });

  it("keeps capability keys unique inside a section", () => {
    // Duplicates collapse to one gate id, so the second one silently inherits
    // the first one's state.
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const keys = businessOsSectionCapabilities(section.key).map((capability) => capability.key);
      expect(new Set(keys).size).toBe(keys.length);
    });
  });

  it("puts a capability in exactly one list, decided by the gate", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const { available, upcoming } = businessOsSectionLists(section.key);
      expect(available.length + upcoming.length).toBe(businessOsSectionCapabilities(section.key).length);
      // "Available now" is a claim about the backend, so it has to be the
      // gate's claim and not a second opinion held in the copy file.
      available.forEach((capability) => expect(readinessOf(capability.id)).toBe("READY"));
      upcoming.forEach((capability) => expect(isLaunchGated(capability.id)).toBe(true));
    });
  });

  it("describes every capability in the operator's language, not the developer's", () => {
    // Same vocabulary rule the Coming Soon copy is held to. These strings are
    // the ones most likely to drift, because they are written while reading the
    // code that is missing.
    const forbidden = /broken|not implemented|unimplemented|unavailable|disabled|endpoint|backend|stub|mock|todo|wip|failed/i;
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const overview = businessOsSectionOverview(section.key)!;
      expect(overview.purpose).not.toMatch(forbidden);
      overview.capabilities.forEach((capability) => {
        expect(capability.label.length).toBeGreaterThan(0);
        expect(capability.blurb.length).toBeGreaterThan(0);
        expect(capability.label).not.toMatch(forbidden);
        expect(capability.blurb).not.toMatch(forbidden);
      });
    });
  });
});

describe("which sections get a landing", () => {
  it("gives one to every section that is missing something", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const { upcoming } = businessOsSectionLists(section.key);
      expect(businessOsSectionHasLanding(section.key)).toBe(upcoming.length > 0);
    });
  });

  it("leaves a finished section alone", () => {
    // Verification is the one section the audit found complete end to end. It
    // is named rather than derived on purpose: if a gap is ever found in it,
    // this test should fail and make somebody look, not quietly re-derive.
    expect(businessOsSectionHasLanding("verification")).toBe(false);
    expect(isLaunchGated(businessModuleId("verification"))).toBe(false);
  });

  it("gives one to the sections that have nothing at all yet", () => {
    // Customers and Team have no screen and no route. Before the launch gate
    // they were hidden; with only the gate they were a one-line modal. The
    // landing is what lets a user see what is actually coming.
    ["customers", "team"].forEach((key) => {
      expect(businessOsSectionHasLanding(key as never)).toBe(true);
      expect(businessOsSectionLists(key as never).available).toHaveLength(0);
    });
  });
});

describe("a section landing", () => {
  it("lists what works and what is coming, and opens the section", () => {
    const navigation = nav();
    const view = render(<BusinessOsSectionScreen navigation={navigation} route={{ params: { section: "store" } }} />);
    const { available, upcoming } = businessOsSectionLists("store");
    expect(available.length).toBeGreaterThan(0);
    expect(upcoming.length).toBeGreaterThan(0);

    available.forEach((capability) => expect(view.getByTestId(`capability-${capability.id}`)).toBeTruthy());
    upcoming.forEach((capability) => expect(view.getByTestId(`capability-${capability.id}`)).toBeTruthy());

    // Store works. The landing explains it and then gets out of the way — the
    // button dispatches exactly what the registry says the card always did.
    fireEvent.press(view.getByTestId("business-section-open-store"));
    const [route, params] = businessOsNavigationArgs(businessOsSection("store")!);
    expect(navigation.navigate).toHaveBeenCalledWith(route, params);
  });

  it("says a locked capability is coming soon in words, not only in teal", () => {
    const view = render(<BusinessOsSectionScreen navigation={nav()} route={{ params: { section: "store" } }} />);
    const [first] = businessOsSectionLists("store").upcoming;
    const row = view.getByTestId(`capability-${first.id}`);

    // The state is in the accessibility label because colour is not a channel a
    // screen reader has and iOS hints are off by default.
    expect(row.props.accessibilityLabel).toBe(`${first.label}. Coming soon.`);
    // And it stays a live control. Locked is not disabled — the tap is how a
    // user finds out what is happening.
    expect(row.props.accessibilityState?.disabled).toBeFalsy();
  });

  it("answers a tap on a locked capability with the same message every locked card gives", () => {
    const view = render(<BusinessOsSectionScreen navigation={nav()} route={{ params: { section: "insights" } }} />);
    const [first] = businessOsSectionLists("insights").upcoming;

    fireEvent.press(view.getByTestId(`capability-${first.id}`));
    expect(view.getByTestId(`coming-soon-${first.id}`)).toBeTruthy();
    // Named, so the sheet answers "which one?" rather than "something".
    expect(view.getByText(first.label)).toBeTruthy();
    expect(view.getByText("COMING SOON")).toBeTruthy();
  });

  it("offers no way into a section that is still being built", () => {
    // Events is gated at the section level: its three tabs cannot hold a row.
    // The button is absent rather than present-and-disabled, for the same
    // reason a locked tile is not greyed out.
    const view = render(<BusinessOsSectionScreen navigation={nav()} route={{ params: { section: "events" } }} />);
    expect(view.queryByTestId("business-section-open-events")).toBeNull();
    // And the section says which state it is in, in a word, above the lists.
    expect(view.getByTestId("business-section-status-events").props.children).toBe("Building");
  });

  it("offers no way into a section that has no screen at all", () => {
    const view = render(<BusinessOsSectionScreen navigation={nav()} route={{ params: { section: "team" } }} />);
    expect(view.queryByTestId("business-section-open-team")).toBeNull();
    businessOsSectionLists("team").upcoming.forEach((capability) => {
      expect(view.getByTestId(`capability-${capability.id}`)).toBeTruthy();
    });
  });

  it("says so plainly when the section does not exist", () => {
    // A stale deep link or a renamed key. The alternative is an empty page that
    // looks like a section which loaded and had nothing in it.
    const view = render(<BusinessOsSectionScreen navigation={nav()} route={{ params: {} }} />);
    expect(view.getByText("That section is not part of Business OS.")).toBeTruthy();
  });
});
