/**
 * The Live Studio dashboard, after the studio/camera split.
 *
 * The bug this screen had was not a crash. It rendered a live `CameraView`
 * above a setup form, so the screen was simultaneously a control panel and a
 * viewfinder, and a creator could not tell whether they were already
 * broadcasting. The fix is structural — management here, capture on the host
 * screen — and structural fixes rot quietly: someone adds "just a small
 * preview" back and nothing fails.
 *
 * So the first test below is an absence. The rest cover the two states that
 * were previously indistinguishable (ready vs. on air) and the one the old
 * screen handled worst: a simulator, where the camera genuinely cannot work and
 * the whole studio used to read as broken because of it.
 */

import React from "react";
import { readFileSync } from "fs";
import { join } from "path";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

// `Device.isDevice` is read during render, so it is a getter rather than a
// fixed value — a test that flips it between renders needs the property to be
// re-read, not captured once at mock time.
let mockIsDevice = true;
jest.mock("expo-device", () => ({
  get isDevice() {
    return mockIsDevice;
  }
}));

let mockCameraGranted = true;
let mockMicGranted = true;
const mockRequestCamera = jest.fn(async () => ({ granted: true, canAskAgain: true }));
const mockRequestMic = jest.fn(async () => ({ granted: true, canAskAgain: true }));
jest.mock("expo-camera", () => ({
  useCameraPermissions: () => [{ granted: mockCameraGranted, canAskAgain: true }, mockRequestCamera],
  useMicrophonePermissions: () => [{ granted: mockMicGranted, canAskAgain: true }, mockRequestMic]
}));

// Standard for screen tests here: there is no `SafeAreaProvider` above a unit
// render, and the inset only feeds bottom padding.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("expo-battery", () => ({
  getBatteryLevelAsync: jest.fn().mockResolvedValue(0.9),
  isLowPowerModeEnabledAsync: jest.fn().mockResolvedValue(false)
}));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useNavigation: () => ({ navigate: mockNavigate })
}));

const mockStartLive = jest.fn();
jest.mock("../../api/live", () => ({
  ...jest.requireActual("../../api/live"),
  startLive: (...args: unknown[]) => mockStartLive(...args)
}));

import { LiveStudioScreen } from "../LiveStudioScreen";
import { AuthContext, authenticatedState, unauthenticatedState, type AuthState } from "../../session/auth";
import { claimMediaPlayback, resetMediaPlayback } from "../../core/mediaPlaybackCoordinator";
import { livePlaybackOwnerId } from "../../live/livePlaybackOwnership";

const member = { user_id: 4, username: "nova", account_status: "active" } as never;

function show(authState: AuthState = authenticatedState(member)) {
  return render(
    <AuthContext.Provider value={{ authState, setAuthState: jest.fn(), requestReauthentication: jest.fn() }}>
      <LiveStudioScreen />
    </AuthContext.Provider>
  );
}

/** The dashboard is READY only once the network probe has come back. */
async function showReady() {
  const view = show();
  await waitFor(() => expect(view.queryAllByText("READY").length).toBe(1));
  return view;
}

beforeEach(() => {
  mockIsDevice = true;
  mockCameraGranted = true;
  mockMicGranted = true;
  mockNavigate.mockClear();
  mockStartLive.mockReset().mockResolvedValue({ liveId: 12, room: "r-12", tokenUrl: "https://x/t" });
  // The suite disables `fetch` globally; the readiness probe is a real request
  // this screen makes on mount, and leaving it rejected would pin every test to
  // the offline branch. A resolved probe is the ordinary case.
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200 }) as never;
});

afterEach(async () => {
  await resetMediaPlayback();
});

describe("the studio is a dashboard, not a camera", () => {
  /**
   * The regression guard for the whole mission. `CameraView` mounting here is
   * what made the studio and the host screen indistinguishable; nothing else in
   * the suite would notice it coming back, because a preview renders perfectly
   * well in the wrong place.
   */
  it("mounts no camera view and offers no capture controls", async () => {
    const view = await showReady();

    expect(view.queryByText("Flip")).toBeNull();
    expect(view.queryByText("Preview only")).toBeNull();
    expect(view.queryByText(/Camera preview needs/)).toBeNull();
    expect(view.queryByLabelText("Flip camera")).toBeNull();
  });

  /**
   * The render assertions above only catch the preview as it was written last
   * time — different copy, or a preview behind a permission branch this test
   * does not enter, would slip past all of them. This reads the source instead,
   * in the style of `navigation/__tests__/backgroundSurfaces.test.ts`, so the
   * guard is on the import rather than on any particular arrangement of it.
   *
   * `useCameraPermissions` is still imported and must stay: the readiness card
   * reports camera access, it just never renders a viewfinder.
   */
  it("does not import a camera view at all", () => {
    const source = readFileSync(join(__dirname, "..", "LiveStudioScreen.tsx"), "utf8");
    const imports = source.slice(0, source.indexOf("export function LiveStudioScreen"));
    expect(imports).toContain("useCameraPermissions");
    expect(imports).not.toMatch(/^import .*\bCameraView\b/m);
  });

  it("leads with the studio's name, purpose and current status", async () => {
    const view = await showReady();

    expect(view.getByText("Live Studio")).toBeTruthy();
    expect(view.getByText("Your live broadcasts, setup, and creator tools.")).toBeTruthy();
    expect(view.getByLabelText("Current status: READY")).toBeTruthy();
  });

  it("keeps the broadcast setup that already works", async () => {
    const view = await showReady();

    expect(view.getByLabelText("Broadcast title")).toBeTruthy();
    expect(view.getByText("Set up your broadcast")).toBeTruthy();
  });
});

describe("start live hands off to the camera experience", () => {
  it("creates the broadcast and navigates to the full-screen host screen", async () => {
    const view = await showReady();

    fireEvent.press(view.getByTestId("live-studio-go-live"));

    await waitFor(() => expect(mockStartLive).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(1));
    // The capture experience is a separate screen. That is the entire point of
    // the split: this one never opens the camera itself.
    expect(mockNavigate.mock.calls[0][0]).toBe("NativeLiveHost");
    expect(mockNavigate.mock.calls[0][1]).toMatchObject({ liveId: 12, room: "r-12" });
  });

  /**
   * Reaching the dashboard while already hosting is easy — background the app,
   * or navigate back out of the host screen. Pressing the button again would
   * open a second broadcast, so it is closed off and says why.
   */
  it("refuses to start a second broadcast while one is running", async () => {
    await claimMediaPlayback({ id: livePlaybackOwnerId("host", 3), kind: "live", pause: () => undefined });
    const view = show();

    await waitFor(() => expect(view.queryAllByText("LIVE").length).toBe(1));
    expect(view.getByText("You're on air right now")).toBeTruthy();

    fireEvent.press(view.getByTestId("live-studio-go-live"));
    expect(mockStartLive).not.toHaveBeenCalled();
  });

  /**
   * Host and viewer claim media playback with the same kind. If the dashboard
   * read the kind alone, watching someone else's Live would lock the creator
   * out of starting their own.
   */
  it("does not treat watching someone else's Live as being on air", async () => {
    await claimMediaPlayback({ id: livePlaybackOwnerId("viewer", 3), kind: "live", pause: () => undefined });
    const view = await showReady();

    expect(view.queryAllByText("LIVE").length).toBe(0);
    fireEvent.press(view.getByTestId("live-studio-go-live"));
    await waitFor(() => expect(mockStartLive).toHaveBeenCalledTimes(1));
  });
});

describe("when something is missing", () => {
  it("names what is blocked in one line instead of replacing the screen", async () => {
    mockCameraGranted = false;
    const view = show();

    await waitFor(() => expect(view.queryAllByText("BLOCKED").length).toBe(1));
    expect(view.getByTestId("live-studio-headline").props.children).toBe("Complete setup before going live");
    // The specific check, not just the fact that something is wrong.
    expect(view.getByTestId("live-studio-blocked-notice").props.children.join("")).toContain("Camera");

    // The rest of the studio is still there — this is a notice, not an error
    // page, and the creator can carry on writing their title while they fix it.
    expect(view.getByText("Set up your broadcast")).toBeTruthy();
    expect(view.getByText("Upcoming")).toBeTruthy();
  });

  it("blocks a signed-out creator on the account check, not on a blank screen", async () => {
    const view = show(unauthenticatedState());

    await waitFor(() => expect(view.queryAllByText("BLOCKED").length).toBe(1));
    expect(view.getByText("Sign in to PulseSoc to broadcast.")).toBeTruthy();
    expect(view.getByText("Set up your broadcast")).toBeTruthy();
  });

  it("offers the fix next to the thing that is broken", async () => {
    mockMicGranted = false;
    const view = show();

    await waitFor(() => expect(view.queryAllByText("BLOCKED").length).toBe(1));
    fireEvent.press(view.getByLabelText("Allow for Microphone"));
    await waitFor(() => expect(mockRequestMic).toHaveBeenCalledTimes(1));
  });
});

describe("on a simulator", () => {
  /**
   * The failure this replaces: the old screen's whole top half was a camera
   * that cannot exist here, so the studio read as broken to everyone testing on
   * a simulator. The device limitation is real and is still stated — it just no
   * longer stops anything.
   */
  it("says the camera needs a device without making the studio look broken", async () => {
    mockIsDevice = false;
    const view = show();

    await waitFor(() => expect(view.queryAllByText("READY").length).toBe(1));
    expect(view.getByText(/Camera capture requires a physical device/)).toBeTruthy();
    expect(view.getByTestId("live-studio-go-live")).toBeTruthy();
    expect(view.getByText("Set up your broadcast")).toBeTruthy();
  });
});

describe("the upcoming tools", () => {
  it("lists each one with an explanation rather than a bare label", async () => {
    const view = await showReady();

    expect(view.getByText("Schedule Live")).toBeTruthy();
    expect(view.getByText("Moderation tools")).toBeTruthy();
    expect(view.getByText("Analytics")).toBeTruthy();
    expect(view.getByLabelText(/Schedule Live\. Coming soon\./)).toBeTruthy();
  });

  /**
   * A row that responds to touch promises a destination. These have none, and
   * the confusion this mission is fixing is precisely controls that look
   * tappable and land nowhere.
   */
  it("does not present them as controls", async () => {
    const view = await showReady();
    const row = view.getByTestId("live-studio-upcoming-analytics");
    expect(row.props.accessibilityRole).not.toBe("button");
    expect(row.props.onStartShouldSetResponder).toBeUndefined();
  });
});
