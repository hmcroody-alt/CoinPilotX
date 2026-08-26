import { fireEvent, render, screen } from "@testing-library/react-native";

// The shared `Screen` shell reads safe-area insets and the bottom-nav scroll
// hook, neither of which has a provider in a bare render. Stubbed the same way
// the hub's own suite stubs them, so this file tests the landing rather than
// the chrome around it.
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

import { BusinessOsSectionScreen } from "../BusinessOsSectionScreen";
import { businessOsSection } from "../../api/businessOs";
import { lockedBusinessOsModules, readyBusinessOsModules } from "../../core/businessOsReadiness";

function show(section?: string) {
  const navigation = { navigate: jest.fn() };
  const view = render(
    <BusinessOsSectionScreen navigation={navigation} route={{ params: { section: section as never } }} />
  );
  return { view, navigation };
}

// Warmed at module scope so RNTL's ~1s first-render cost is not billed to the
// first test in the file. See env note on cold-render cost.
show("customers").view.unmount();
screen.unmount?.();

describe("the roadmap landing", () => {
  it.each([["customers"], ["team"]])("opens for %s instead of refusing", (key) => {
    const { view } = show(key);
    const section = businessOsSection(key as never)!;
    expect(view.queryByText(section.label)).toBeTruthy();
    // The page explains itself rather than being a bare list of locks.
    expect(view.queryByText("What this is for")).toBeTruthy();
  });

  it("shows every locked module for the section", () => {
    const { view } = show("customers");
    lockedBusinessOsModules("customers").forEach((module) => {
      expect(view.getByTestId(`business-module-${module.key}`)).toBeTruthy();
    });
  });

  it("opens a READY module for real", () => {
    const { view, navigation } = show("customers");
    const ready = readyBusinessOsModules("customers")[0];
    fireEvent.press(view.getByTestId(`business-module-${ready.key}`));
    expect(navigation.navigate).toHaveBeenCalledWith(ready.route, ready.params);
  });

  it("navigates nowhere when a locked module is tapped", () => {
    const { view, navigation } = show("customers");
    lockedBusinessOsModules("customers").forEach((module) => {
      fireEvent.press(view.getByTestId(`business-module-${module.key}`));
    });
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("explains a locked module in a sheet rather than a new page", () => {
    const { view, navigation } = show("team");
    fireEvent.press(view.getByTestId(`business-module-${lockedBusinessOsModules("team")[0].key}`));
    expect(view.queryByText(/still being built/i)).toBeTruthy();
    // A sheet, not a push: the section stays behind it and the back stack is
    // not given an entry the member has to unwind.
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  /**
   * The forbidden output. A section landing that renders blank, or that leaks a
   * route name or a stack trace, is worse than the hidden tile it replaced.
   */
  it("never renders blank or leaks developer text", () => {
    ["customers", "team"].forEach((key) => {
      const { view } = show(key);
      expect(view.toJSON()).not.toBeNull();
      [/error/i, /failed/i, /undefined/, /BusinessOsSection/, /\[object/].forEach((word) => {
        expect(view.queryByText(word)).toBeNull();
      });
    });
  });

  it("says so plainly when it is opened with no section", () => {
    // Should be unreachable — the hub always passes a key — but an unreachable
    // state reached in production is how a blank screen ships.
    const { view } = show(undefined);
    expect(view.queryByText(/could not be opened/i)).toBeTruthy();
  });

  it("says so plainly when the section key is not one we know", () => {
    const { view } = show("not-a-section");
    expect(view.queryByText(/could not be opened/i)).toBeTruthy();
  });
});
