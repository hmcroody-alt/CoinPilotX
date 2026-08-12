import React from "react";
import { act, render } from "@testing-library/react-native";
import { AccessibilityInfo, AppState } from "react-native";

let mockFocused = true;

jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => mockFocused
}));

import { LivingPulseSocWordmark } from "../LivingPulseSocWordmark";

const WELCOME_INTERVAL_MS = 10000;
const ANIMATION_DURATION_MS = 2200;

function phaseOf(view: ReturnType<typeof render>) {
  return view.getByTestId("pulsesoc-wordmark").props.accessibilityValue.text;
}

describe("LivingPulseSocWordmark", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockFocused = true;
    jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(false);
    jest.spyOn(AccessibilityInfo, "addEventListener").mockReturnValue({ remove: jest.fn() } as never);
    jest.spyOn(AppState, "addEventListener").mockReturnValue({ remove: jest.fn() } as never);
    Object.defineProperty(AppState, "currentState", { value: "active", configurable: true });
  });

  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it("renders the normal PulseSoc state at rest", async () => {
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("idle");
  });

  it("transitions to the welcome state after ~60s and back to idle, scheduling exactly one next cycle", async () => {
    const setTimeoutSpy = jest.spyOn(global, "setTimeout");
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("welcome");

    await act(async () => {
      jest.advanceTimersByTime(ANIMATION_DURATION_MS);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("idle");

    const pendingTopLevelTimers = setTimeoutSpy.mock.calls.filter((call) => call[1] === WELCOME_INTERVAL_MS).length;
    expect(pendingTopLevelTimers).toBe(2); // initial schedule + the one rescheduled after the cycle completed
  });

  it("shows and then clears the particle burst during the welcome sequence", async () => {
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS);
      await Promise.resolve();
    });
    expect(view.queryByTestId("pulsesoc-wordmark-particles")).not.toBeNull();

    await act(async () => {
      jest.advanceTimersByTime(ANIMATION_DURATION_MS);
      await Promise.resolve();
    });
    expect(view.queryByTestId("pulsesoc-wordmark-particles")).toBeNull();
  });

  it("does not animate while Home is unfocused", async () => {
    mockFocused = false;
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS * 2);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("idle");
  });

  it("does not animate while the app is backgrounded", async () => {
    let appStateHandler: ((state: string) => void) | undefined;
    (AppState.addEventListener as jest.Mock).mockImplementation((_event: string, handler: (state: string) => void) => {
      appStateHandler = handler;
      return { remove: jest.fn() };
    });
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      appStateHandler?.("background");
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS * 2);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("idle");
  });

  it("respects reduced motion by skipping the dance but still surfacing the welcome state", async () => {
    (AccessibilityInfo.isReduceMotionEnabled as jest.Mock).mockResolvedValue(true);
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("welcome");
    expect(view.queryByTestId("pulsesoc-wordmark-particles")).toBeNull();

    await act(async () => {
      jest.advanceTimersByTime(3500);
      await Promise.resolve();
    });
    expect(phaseOf(view)).toBe("idle");
  });

  it("cleans up its timer on unmount without leaking a scheduled animation", async () => {
    const clearTimeoutSpy = jest.spyOn(global, "clearTimeout");
    const view = render(<LivingPulseSocWordmark />);
    await act(async () => {
      await Promise.resolve();
    });
    view.unmount();
    expect(clearTimeoutSpy).toHaveBeenCalled();

    const consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
    await act(async () => {
      jest.advanceTimersByTime(WELCOME_INTERVAL_MS * 2);
      await Promise.resolve();
    });
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
