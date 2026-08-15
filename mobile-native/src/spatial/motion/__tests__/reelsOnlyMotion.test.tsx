/**
 * Sensor-subscription eligibility for the Reels-only motion experience.
 *
 * The architecture guard in `spatial/__tests__/homeFeedRollback.test.ts` proves
 * no surface other than Reels can reach this hook. This file proves the other
 * half: that even from Reels the accelerometer starts only when every gate
 * agrees, and stops the moment any one of them stops agreeing.
 *
 * The claim being pinned is a battery and privacy claim as much as a behavioral
 * one. "Motion is enabled" must never mean "the sensor is running": leaving
 * DeviceMotion subscribed on a backgrounded app, a blurred screen, or a user who
 * chose Swipe Only is invisible in QA and expensive on a real phone. Each test
 * therefore asserts on `addListener` / `remove` call counts against a fake
 * DeviceMotion module rather than on anything the user could see.
 */
import { act, render } from "@testing-library/react-native";
import React from "react";
import { AppState, Text, type AppStateStatus } from "react-native";

import {
  __clearSpatialFlagOverrides,
  __setSpatialFlagOverride
} from "../../flags";
import { __setDeviceMotionModuleForTests, type DeviceMotionModule } from "../motionAvailability";
import { __resetMotionSettingsForTests, updateMotionSettings } from "../motionSettings";
import { useTiltNavigation } from "../useTiltNavigation";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined),
  clear: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Medium: "medium" }
}));

let mockReduceMotion = false;
jest.mock("../../../theme/ThemeContext", () => ({
  useTheme: () => ({ reduceMotion: mockReduceMotion })
}));

/**
 * jest-expo's React Native mock leaves `AppState.currentState` as a jest mock
 * FUNCTION rather than a lifecycle string. Every gate below reads it, a function
 * is not `"active"`, and so the sensor would stay off in all of these tests for
 * a reason that does not exist on any real device. Pin it to what a foregrounded
 * app actually reports so the foreground gate is exercised rather than faked.
 */
(AppState as { currentState: AppStateStatus }).currentState = "active";

/** Records subscribe/unsubscribe so tests can assert on sensor lifetime. */
function createFakeDeviceMotion() {
  const remove = jest.fn();
  const module: DeviceMotionModule & { addListener: jest.Mock; setUpdateInterval: jest.Mock } = {
    isAvailableAsync: jest.fn().mockResolvedValue(true),
    requestPermissionsAsync: jest.fn().mockResolvedValue({ granted: true }),
    setUpdateInterval: jest.fn(),
    addListener: jest.fn(() => ({ remove }))
  };
  return { module, remove };
}

function Probe({ enabled, suspended = false }: { enabled: boolean; suspended?: boolean }) {
  const tilt = useTiltNavigation({
    surface: "reels",
    enabled,
    suspended,
    currentIndex: 0,
    pageCount: 5,
    onCommit: () => undefined
  });
  return <Text testID="state">{tilt.state}</Text>;
}

/** Turn on every flag Reels motion needs. Individual tests switch one back off. */
function allFlagsOn() {
  __setSpatialFlagOverride("spatialConsoleEnabled", true);
  __setSpatialFlagOverride("spatialReelsEnabled", true);
  __setSpatialFlagOverride("spatialMotionEnabled", true);
  __setSpatialFlagOverride("tiltNavigationEnabled", true);
  __setSpatialFlagOverride("tiltParallaxEnabled", true);
}

/** Consent: onboarded, with a real motion mode chosen. */
async function consentTo(mode: "tilt" | "parallax" | "swipe-only") {
  await act(async () => {
    await updateMotionSettings({ mode, onboarded: true, neutralBaselineRad: 0.1 });
  });
}

/**
 * Let the availability probe settle.
 *
 * `isMotionAvailable()` awaits a permission check and then an availability
 * check, and the resulting state update re-runs the subscription effect. That
 * is three chained microtask turns before `addListener` can have been called,
 * so a single flush would read the pre-subscription world and every positive
 * assertion here would be a false negative.
 */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/**
 * Capture the hook's AppState subscriber so a test can drive the lifecycle.
 *
 * There is no way to background a phone from jest, and the foreground gate is
 * precisely the one whose failure mode is invisible: a sensor left running
 * behind a backgrounded app costs battery and reports nothing. Calling the
 * registered handler is the closest honest stand-in for the OS event.
 */
function captureAppStateHandler() {
  const handlers: ((next: AppStateStatus) => void)[] = [];
  jest.spyOn(AppState, "addEventListener").mockImplementation(((
    _event: string,
    handler: (next: AppStateStatus) => void
  ) => {
    handlers.push(handler);
    return { remove: () => undefined };
  }) as never);
  return async (next: AppStateStatus) => {
    await act(async () => {
      handlers.forEach((handler) => handler(next));
    });
  };
}

describe("Reels motion subscribes only when every gate agrees", () => {
  let fake: ReturnType<typeof createFakeDeviceMotion>;

  beforeEach(() => {
    mockReduceMotion = false;
    fake = createFakeDeviceMotion();
    __setDeviceMotionModuleForTests(fake.module);
    __resetMotionSettingsForTests();
    __clearSpatialFlagOverrides();
    allFlagsOn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
    __clearSpatialFlagOverrides();
    __setDeviceMotionModuleForTests(undefined);
  });

  it("subscribes when flags, consent and focus all hold", async () => {
    await consentTo("tilt");
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).toHaveBeenCalledTimes(1);
    expect(fake.module.setUpdateInterval).toHaveBeenCalled();
  });

  it("does not subscribe while Reels is unfocused", async () => {
    await consentTo("tilt");
    render(<Probe enabled={false} />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });

  it("unsubscribes when Reels loses focus", async () => {
    await consentTo("tilt");
    const view = render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).toHaveBeenCalledTimes(1);
    view.rerender(<Probe enabled={false} />);
    await settle();
    expect(fake.remove).toHaveBeenCalled();
  });

  it("unsubscribes on unmount, leaving no sensor behind", async () => {
    await consentTo("tilt");
    const view = render(<Probe enabled />);
    await settle();
    await act(async () => {
      view.unmount();
    });
    expect(fake.remove).toHaveBeenCalled();
  });

  it("unsubscribes when the app is backgrounded, and resubscribes on return", async () => {
    const sendAppState = captureAppStateHandler();
    await consentTo("tilt");
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).toHaveBeenCalledTimes(1);

    await sendAppState("background");
    expect(fake.remove).toHaveBeenCalledTimes(1);
    expect(fake.module.addListener).toHaveBeenCalledTimes(1);

    await sendAppState("active");
    await settle();
    expect(fake.module.addListener).toHaveBeenCalledTimes(2);
  });

  it("treats 'inactive' as backgrounded — the sensor stops for the app switcher too", async () => {
    const sendAppState = captureAppStateHandler();
    await consentTo("tilt");
    render(<Probe enabled />);
    await settle();
    await sendAppState("inactive");
    expect(fake.remove).toHaveBeenCalledTimes(1);
  });

  it("Swipe Only never touches the sensor", async () => {
    await consentTo("swipe-only");
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });

  it("does not subscribe before onboarding, even with a motion mode stored", async () => {
    await act(async () => {
      await updateMotionSettings({ mode: "tilt", onboarded: false, neutralBaselineRad: 0.1 });
    });
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });

  it("Reduce Motion blocks the sensor outright", async () => {
    await consentTo("tilt");
    mockReduceMotion = true;
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });

  it("a device with no sensor stays silent and reports unavailable", async () => {
    await consentTo("tilt");
    fake.module.isAvailableAsync = jest.fn().mockResolvedValue(false);
    const view = render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
    expect(view.getByTestId("state").props.children).toBe("unavailable");
  });

  it("denied motion permission leaves swipe working and the sensor off", async () => {
    await consentTo("tilt");
    fake.module.requestPermissionsAsync = jest.fn().mockResolvedValue({ granted: false });
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });
});

describe("flag gates on the Reels motion sensor", () => {
  let fake: ReturnType<typeof createFakeDeviceMotion>;

  beforeEach(async () => {
    mockReduceMotion = false;
    fake = createFakeDeviceMotion();
    __setDeviceMotionModuleForTests(fake.module);
    __resetMotionSettingsForTests();
    __clearSpatialFlagOverrides();
    allFlagsOn();
    await consentTo("tilt");
  });

  afterEach(() => {
    __clearSpatialFlagOverrides();
    __setDeviceMotionModuleForTests(undefined);
  });

  it.each([
    ["spatialConsoleEnabled", "the console master"],
    ["spatialReelsEnabled", "spatial Reels"],
    ["spatialMotionEnabled", "the motion master"],
    ["tiltNavigationEnabled", "tilt navigation"]
  ] as const)("no sensor when %s is off (%s)", async (flag, _description) => {
    __setSpatialFlagOverride(flag, false);
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });

  it("parallax mode runs on the parallax flag, not the navigation flag", async () => {
    await consentTo("parallax");
    __setSpatialFlagOverride("tiltNavigationEnabled", false);
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).toHaveBeenCalledTimes(1);
  });

  it("parallax mode stops when the parallax flag goes off", async () => {
    await consentTo("parallax");
    __setSpatialFlagOverride("tiltParallaxEnabled", false);
    render(<Probe enabled />);
    await settle();
    expect(fake.module.addListener).not.toHaveBeenCalled();
  });
});
