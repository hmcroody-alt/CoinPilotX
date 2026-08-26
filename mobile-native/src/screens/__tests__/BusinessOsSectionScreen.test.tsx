/**
 * The second layer — a Business OS section landing page.
 *
 * The layer makes one promise with two halves, and each half fails silently on
 * its own:
 *
 *   - A section you can enter. The whole point is that the door opens; a landing
 *     that rendered nothing, or that locked its working rows too, would be the
 *     old dead end wearing a new screen.
 *   - Depth that stays shut. A locked row must not navigate. "Shows a badge" is
 *     not the assertion — "shows the message AND did not navigate" is, because a
 *     row that did both would look gated while dropping the user straight in.
 *
 * The rosters are read from the real registry rather than a fixture, so a module
 * added later is covered the moment it is registered.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

// `Screen` reads the safe-area inset. Without a provider in the tree the real
// hook throws, which would fail every case here for a reason that has nothing
// to do with the layer under test.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

import { businessOsSectionModules, BUSINESS_OS_SECTION_MODULES, businessOsSection } from "../../api/businessOs";
import { preloadNamespaces, translate } from "../../i18n/engine";
import { businessSubmoduleId, isLaunchReady, readinessOf } from "../../launch/readiness";
import { BusinessOsSectionScreen } from "../BusinessOsSectionScreen";

beforeAll(async () => {
  await preloadNamespaces("en", ["commerce", "common"]);
});

const SECTION_KEYS = Object.keys(BUSINESS_OS_SECTION_MODULES);

function renderSection(section?: string) {
  const navigation = { navigate: jest.fn(), goBack: jest.fn() };
  const view = render(
    <BusinessOsSectionScreen navigation={navigation} route={{ params: { section } }} />
  );
  return { ...view, navigation };
}

describe("every section with a landing page opens", () => {
  it("has at least one section to open", () => {
    expect(SECTION_KEYS.length).toBeGreaterThan(0);
  });

  it.each(SECTION_KEYS)("renders %s with every module it declares, locked or not", (key) => {
    const view = renderSection(key);
    const modules = businessOsSectionModules(key);
    expect(modules.length).toBeGreaterThan(0);
    for (const module of modules) {
      // Matched by testID rather than by label: a locked row's accessibility
      // label is "<name>. Coming soon.", so a label lookup would silently pass
      // for the wrong reason if the lock were dropped.
      expect(view.getByTestId(`launch-module-${businessSubmoduleId(key, module.key)}`)).toBeTruthy();
    }
  });

  /**
   * The mission's premise: unfinished work stays *visible*. A section that has
   * hidden its locked modules has removed the roadmap, which is the failure the
   * whole layer exists to prevent — and it would still pass every test above.
   */
  it.each(SECTION_KEYS)("shows %s's upcoming modules rather than hiding them", (key) => {
    const view = renderSection(key);
    const locked = businessOsSectionModules(key).filter(
      (module) => !isLaunchReady(businessSubmoduleId(key, module.key))
    );
    expect(locked.length).toBeGreaterThan(0);
    for (const module of locked) {
      expect(view.getByLabelText(`${module.label}. Coming soon.`)).toBeTruthy();
    }
  });
});

describe("a locked module", () => {
  const lockedCases = SECTION_KEYS.flatMap((key) =>
    businessOsSectionModules(key)
      .filter((module) => !isLaunchReady(businessSubmoduleId(key, module.key)))
      .map((module) => [key, module.key, module.label] as const)
  );

  it("has locked modules to assert on", () => {
    expect(lockedCases.length).toBeGreaterThan(0);
  });

  it.each(lockedCases)("explains %s.%s instead of entering it", (key, moduleKey, label) => {
    const view = renderSection(key);
    const id = businessSubmoduleId(key, moduleKey);
    fireEvent.press(view.getByTestId(`launch-module-${id}`));

    // The conjunction is the test. Either half alone is satisfied by a bug.
    expect(view.getByTestId(`coming-soon-${id}`)).toBeTruthy();
    expect(view.navigation.navigate).not.toHaveBeenCalled();
    expect(view.getByText(label)).toBeTruthy();
  });

  /**
   * A locked row is dimmed and badged, but it is NOT `disabled`. Marking it
   * disabled would tell assistive tech there is nothing to press — while the
   * press is precisely how a user gets the explanation.
   */
  it.each(lockedCases)("stays a live control for %s.%s", (key, moduleKey) => {
    const view = renderSection(key);
    const row = view.getByTestId(`launch-module-${businessSubmoduleId(key, moduleKey)}`);
    expect(row.props.accessibilityState?.disabled).toBeFalsy();
    expect(row.props.accessibilityRole).toBe("button");
  });
});

describe("an available module", () => {
  const readyCases = SECTION_KEYS.flatMap((key) =>
    businessOsSectionModules(key)
      .filter((module) => isLaunchReady(businessSubmoduleId(key, module.key)))
      .map((module) => [key, module.key, module.route] as const)
  );

  /**
   * The half that stops this layer becoming a wall. If every row in every
   * section were locked, the landing page would be a prettier dead end — so at
   * least one section has to offer something that actually opens.
   */
  it("offers at least one capability that works today", () => {
    expect(readyCases.length).toBeGreaterThan(0);
  });

  it.each(readyCases)("sends %s.%s to its real screen", (key, moduleKey, route) => {
    const view = renderSection(key);
    fireEvent.press(view.getByTestId(`launch-module-${businessSubmoduleId(key, moduleKey)}`));
    expect(view.navigation.navigate).toHaveBeenCalledWith(route, undefined);
    expect(view.queryByTestId(`coming-soon-${businessSubmoduleId(key, moduleKey)}`)).toBeNull();
  });

  /**
   * A READY module with no route would navigate to `undefined` — a crash or a
   * silent no-op depending on the navigator. The pairing is an invariant of the
   * registry, so it is asserted against the registry rather than the screen.
   */
  it("never declares a ready module without somewhere to send it", () => {
    for (const key of SECTION_KEYS) {
      for (const module of businessOsSectionModules(key)) {
        if (isLaunchReady(businessSubmoduleId(key, module.key))) {
          expect(module.route).toBeTruthy();
        }
      }
    }
  });
});

describe("the landing page's own edges", () => {
  /**
   * Reached with a key that has no roster — a stale deep link, or a section
   * that has since earned its own screen. The requirement is a sentence, not a
   * blank shell that reads as a failed load.
   */
  it.each([undefined, "marketplace", "nonsenseKeyNobodyRegistered"])(
    "explains itself rather than rendering an empty shell for %s",
    (key) => {
      const view = renderSection(key as string | undefined);
      expect(view.getByText(translate("commerce:launch.sectionFallbackBody"))).toBeTruthy();
    }
  );

  /**
   * A section that already opens a real, working screen must NOT acquire a
   * landing page: that would put a menu in front of a working feature. The
   * registry is the only thing preventing it, so the registry is what is pinned.
   */
  it("gives a landing page only to sections that need one", () => {
    for (const key of SECTION_KEYS) {
      const section = businessOsSection(key as never);
      expect(section).toBeTruthy();
      // Every section with a landing is one the gate is holding at the tile.
      expect(readinessOf(`business:${key}`)).not.toBe("READY");
    }
  });
});
