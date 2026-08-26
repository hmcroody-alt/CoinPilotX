import { fireEvent, render, screen } from "@testing-library/react-native";

import { BusinessOsModules } from "../BusinessOsModules";
import { businessOsModules, lockedBusinessOsModules, readyBusinessOsModules } from "../../../core/businessOsReadiness";

/**
 * RNTL's first render in a file costs about a second (host-component detection
 * plus the first failed matcher), and it is billed to whichever test runs first.
 * Warming it at module scope keeps that cost out of any individual test's
 * budget, which is what makes these stable when the suite runs under load.
 */
render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
screen.unmount();

const CUSTOMERS_LOCKED = lockedBusinessOsModules("customers");
const CUSTOMERS_READY = readyBusinessOsModules("customers");

describe("the roadmap panel", () => {
  it("renders nothing at all for a section with no modules", () => {
    // Not an empty panel with a heading: a "coming soon" box containing nothing
    // reads as a load that failed.
    const view = render(<BusinessOsModules section="messages" />);
    expect(view.toJSON()).toBeNull();
  });

  it("shows every module the section declares", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    businessOsModules("customers").forEach((module) => {
      expect(view.getByTestId(`business-module-${module.key}`)).toBeTruthy();
    });
  });

  it("separates what works from what does not", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    expect(view.queryByText("Available now")).toBeTruthy();
    expect(view.queryByText("Coming to this section")).toBeTruthy();
  });

  it("badges each locked module with its own state", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    // Customers carries both locked states, so this proves the badge follows the
    // module rather than being one hardcoded string for everything locked.
    expect(view.queryAllByText("COMING SOON").length).toBe(
      CUSTOMERS_LOCKED.filter((m) => m.state === "COMING_SOON").length
    );
    expect(view.queryAllByText("BUILDING").length).toBe(
      CUSTOMERS_LOCKED.filter((m) => m.state === "BUILDING").length
    );
  });

  it("gives a READY module no badge", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    const ready = view.getByTestId(`business-module-${CUSTOMERS_READY[0].key}`);
    expect(ready).toBeTruthy();
    // Its label carries no lock wording either.
    expect(ready.props.accessibilityLabel).toBe(CUSTOMERS_READY[0].label);
  });
});

describe("opening a module", () => {
  it("hands a READY module to the caller", () => {
    const onOpen = jest.fn();
    const view = render(<BusinessOsModules section="customers" onOpen={onOpen} />);
    fireEvent.press(view.getByTestId(`business-module-${CUSTOMERS_READY[0].key}`));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0][0].key).toBe(CUSTOMERS_READY[0].key);
  });

  it("never hands a locked module to the caller", () => {
    const onOpen = jest.fn();
    const view = render(<BusinessOsModules section="customers" onOpen={onOpen} />);
    CUSTOMERS_LOCKED.forEach((module) => {
      fireEvent.press(view.getByTestId(`business-module-${module.key}`));
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("explains itself when a locked module is tapped", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    const locked = CUSTOMERS_LOCKED[0];
    fireEvent.press(view.getByTestId(`business-module-${locked.key}`));

    expect(view.queryByText(/still being built/i)).toBeTruthy();
    // Named, not abstract: the sheet says which capability is coming.
    expect(view.queryAllByText(locked.label).length).toBeGreaterThan(0);
  });

  it("closes the explanation again", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    fireEvent.press(view.getByTestId(`business-module-${CUSTOMERS_LOCKED[0].key}`));
    expect(view.queryByText(/still being built/i)).toBeTruthy();

    fireEvent.press(view.getByTestId("coming-soon-dismiss"));
    expect(view.queryByText(/still being built/i)).toBeNull();
  });

  it("shows nothing before anything is tapped", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    expect(view.queryByText(/still being built/i)).toBeNull();
  });
});

describe("what a locked module must never be", () => {
  /**
   * The trap this guards. Marking a locked row `disabled` is the obvious
   * implementation and it produces exactly the dead button the layer forbids:
   * the press is swallowed, nothing explains why, and the member taps again.
   * `getByLabelText` is used rather than walking `.parent`, because a wrapper
   * View has no accessibilityState and the assertion would pass vacuously.
   */
  it("is not disabled", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    CUSTOMERS_LOCKED.forEach((module) => {
      const row = view.getByTestId(`business-module-${module.key}`);
      expect(row.props.accessibilityState?.disabled).toBeFalsy();
      expect(row.props.accessibilityRole).toBe("button");
    });
  });

  it("carries its lock state in the screen-reader label", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    // Greying and a lock glyph are both invisible to a screen reader, so the
    // state has to be in the label or it does not exist for those members.
    const comingSoon = CUSTOMERS_LOCKED.find((m) => m.state === "COMING_SOON")!;
    expect(view.getByLabelText(`${comingSoon.label} — coming soon`)).toBeTruthy();
    const building = CUSTOMERS_LOCKED.find((m) => m.state === "BUILDING")!;
    expect(view.getByLabelText(`${building.label} — building`)).toBeTruthy();
  });

  it("never tells the member something went wrong", () => {
    const view = render(<BusinessOsModules section="customers" onOpen={() => undefined} />);
    fireEvent.press(view.getByTestId(`business-module-${CUSTOMERS_LOCKED[0].key}`));
    [/error/i, /failed/i, /unavailable/i, /not implemented/i, /404/, /undefined/].forEach((word) => {
      expect(view.queryByText(word)).toBeNull();
    });
  });
});
