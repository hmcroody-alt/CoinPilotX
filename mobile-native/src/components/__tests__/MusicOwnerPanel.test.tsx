/**
 * The panel's job is to make an irreversible decision hard to make by accident.
 *
 * The server refuses a purge without a step-up, without a confirmation, under a
 * legal hold, and from any state other than `PURGE_PENDING`; that is where the
 * guarantee lives and `tests/music_authority/test_routes.py` is where it is
 * proven. What is tested here is the other half: that the button is not a trap.
 * A control that is tappable and then 403s teaches the owner to tap through
 * refusals, which is precisely the habit a destructive action should not build.
 *
 * The other thing pinned here is the reference count. `fetchMusicTrackImpact`
 * failing must not leave a stale "0 attached" on screen next to an error --
 * "nothing would be affected" is the one wrong answer that reads as permission
 * to proceed.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockFetchImpact = jest.fn();
const mockFetchAudit = jest.fn();
const mockTakedown = jest.fn();
const mockPurge = jest.fn();
const mockStepUp = jest.fn();
jest.mock("../../api/musicAuthority", () => ({
  fetchMusicTrackImpact: (...args: unknown[]) => mockFetchImpact(...args),
  fetchMusicTrackAudit: (...args: unknown[]) => mockFetchAudit(...args),
  takedownMusicTrack: (...args: unknown[]) => mockTakedown(...args),
  restoreMusicTrack: jest.fn(),
  scheduleMusicTrackPurge: jest.fn(),
  cancelMusicTrackPurge: jest.fn(),
  purgeMusicTrack: (...args: unknown[]) => mockPurge(...args),
  requestMusicStepUp: (...args: unknown[]) => mockStepUp(...args),
  describeMusicAuthorityError: () => "This track is under a legal hold."
}));

import { preloadNamespaces } from "../../i18n/engine";
import { MusicOwnerPanel } from "../MusicOwnerPanel";

const CAPABILITIES = {
  hasAuthority: true,
  permissions: {
    "music.view_all": true,
    "music.moderate": true,
    "music.takedown": true,
    "music.restore": true,
    "music.purge": true,
    "music.manage_rights": true
  },
  states: ["ACTIVE", "TAKEN_DOWN", "QUARANTINED", "PURGE_PENDING", "PURGED"],
  reasonCodes: ["COPYRIGHT", "OWNER_DECISION", "OTHER"],
  stepUpTtlSeconds: 300
};

function impact(overrides: Record<string, unknown> = {}) {
  return {
    trackId: 101,
    title: "Pool Song A",
    artist: "Night Signal",
    uploaderUserId: 4,
    state: "ACTIVE",
    legalHold: false,
    reasonCode: "",
    reasonNote: "",
    purgeScheduledAt: "",
    purgedAt: "",
    references: { reels: 3, content: 1, statuses: 0, total: 4 },
    openReports: 2,
    playCount: 12,
    usageCount: 4,
    cachedCopiesRemainUntilPurge: true,
    reasonCodes: ["COPYRIGHT", "OWNER_DECISION", "OTHER"],
    ...overrides
  };
}

function show() {
  return render(
    <MusicOwnerPanel
      visible
      trackId="101"
      trackTitle="Pool Song A"
      capabilities={CAPABILITIES as never}
      onClose={jest.fn()}
    />
  );
}

beforeAll(async () => {
  await preloadNamespaces("en", ["discovery", "common"]);
});

beforeEach(() => {
  jest.clearAllMocks();
  mockFetchImpact.mockResolvedValue(impact());
  mockFetchAudit.mockResolvedValue([]);
  mockStepUp.mockResolvedValue(300);
  mockTakedown.mockResolvedValue({ action: "takedown", requestId: "r1", changed: true, results: [], deletedObjectCount: 0 });
  mockPurge.mockResolvedValue({ action: "purge", requestId: "r2", changed: true, results: [], deletedObjectCount: 1 });
});

it("shows the blast radius before any button is pressed", async () => {
  const view = show();

  await waitFor(() => expect(view.queryByText(/4 Reels, posts and statuses/)).not.toBeNull());
  expect(view.queryByText(/2 open reports/)).not.toBeNull();
});

it("states the cached-copies caveat when the server says copies remain", async () => {
  const view = show();

  await waitFor(() => expect(view.queryByText(/copies already cached/)).not.toBeNull());
});

it("does not claim the caveat when the server does not", async () => {
  mockFetchImpact.mockResolvedValue(impact({ cachedCopiesRemainUntilPurge: false }));

  const view = show();

  await waitFor(() => expect(view.queryByText(/4 Reels, posts and statuses/)).not.toBeNull());
  expect(view.queryByText(/copies already cached/)).toBeNull();
});

it("sends the state it displayed as the expected state", async () => {
  mockFetchImpact.mockResolvedValue(impact({ state: "TAKEN_DOWN" }));

  const view = show();

  await waitFor(() => expect(view.queryByLabelText("Take down")).not.toBeNull());
  fireEvent.press(view.getByLabelText("Take down"));

  await waitFor(() => expect(mockTakedown).toHaveBeenCalled());
  expect(mockTakedown.mock.calls[0][1]).toMatchObject({ expectedState: "TAKEN_DOWN" });
});

it("reports an unchanged repeat as unchanged, not as a failure", async () => {
  mockTakedown.mockResolvedValue({ action: "takedown", requestId: "r1", changed: false, results: [], deletedObjectCount: 0 });

  const view = show();

  await waitFor(() => expect(view.queryByLabelText("Take down")).not.toBeNull());
  fireEvent.press(view.getByLabelText("Take down"));

  await waitFor(() => expect(view.queryByText(/nothing changed/)).not.toBeNull());
});

it("shows an error instead of a reference count when the impact call fails", async () => {
  // The first load succeeds so there is a real count on screen to go stale.
  // Rejecting from the start would prove nothing: `impact` starts null, and the
  // absence of a count would be the initial state rather than a cleared one.
  mockFetchImpact.mockResolvedValueOnce(impact()).mockRejectedValue(new Error("boom"));

  const view = show();

  await waitFor(() => expect(view.queryByText(/4 Reels, posts and statuses/)).not.toBeNull());
  fireEvent.press(view.getByLabelText("Take down"));

  await waitFor(() => expect(view.queryByText("This track is under a legal hold.")).not.toBeNull());
  // The dangerous rendering is not "an error" — it is an error beside a count.
  expect(view.queryByText(/Reels, posts and statuses/)).toBeNull();
});

it("keeps purge unavailable until the state, the step-up and the typed id all agree", async () => {
  mockFetchImpact.mockResolvedValue(impact({ state: "PURGE_PENDING" }));

  const view = show();

  await waitFor(() => expect(view.queryByLabelText("Purge permanently")).not.toBeNull());
  const button = () => view.getByLabelText("Purge permanently");
  expect(button().props.accessibilityState.disabled).toBe(true);

  fireEvent.changeText(view.getByLabelText("Password"), "hunter2");
  fireEvent.press(view.getByLabelText("Confirm it's you"));
  await waitFor(() => expect(mockStepUp).toHaveBeenCalledWith("hunter2"));

  // Step-up alone is not enough.
  await waitFor(() => expect(view.queryByText(/unlocked for 300 seconds/)).not.toBeNull());
  expect(button().props.accessibilityState.disabled).toBe(true);

  fireEvent.changeText(view.getByLabelText("Type 101 to confirm"), "101");
  await waitFor(() => expect(button().props.accessibilityState.disabled).toBe(false));

  fireEvent.press(button());
  await waitFor(() => expect(mockPurge).toHaveBeenCalled());
});

it("refuses to offer purge under a legal hold however much the owner confirms", async () => {
  mockFetchImpact.mockResolvedValue(impact({ state: "PURGE_PENDING", legalHold: true }));

  const view = show();

  await waitFor(() => expect(view.queryByLabelText("Purge permanently")).not.toBeNull());
  fireEvent.changeText(view.getByLabelText("Password"), "hunter2");
  fireEvent.press(view.getByLabelText("Confirm it's you"));
  await waitFor(() => expect(mockStepUp).toHaveBeenCalled());
  fireEvent.changeText(view.getByLabelText("Type 101 to confirm"), "101");

  expect(view.getByLabelText("Purge permanently").props.accessibilityState.disabled).toBe(true);
  fireEvent.press(view.getByLabelText("Purge permanently"));
  expect(mockPurge).not.toHaveBeenCalled();
  expect(view.queryByText(/under a legal hold/)).not.toBeNull();
});

it("offers no purge controls at all to an actor without the purge permission", async () => {
  mockFetchImpact.mockResolvedValue(impact({ state: "PURGE_PENDING" }));
  const view = render(
    <MusicOwnerPanel
      visible
      trackId="101"
      trackTitle="Pool Song A"
      capabilities={{ ...CAPABILITIES, permissions: { ...CAPABILITIES.permissions, "music.purge": false } } as never}
      onClose={jest.fn()}
    />
  );

  await waitFor(() => expect(view.queryByLabelText("Take down")).not.toBeNull());
  expect(view.queryByLabelText("Purge permanently")).toBeNull();
  expect(view.queryByLabelText("Password")).toBeNull();
});
