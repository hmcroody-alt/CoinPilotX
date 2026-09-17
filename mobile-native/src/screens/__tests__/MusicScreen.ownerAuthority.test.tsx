/**
 * The removal controls are drawn from the server's answer, never from the profile.
 *
 * The tempting implementation is one line shorter and wrong: read `role` or
 * `premium_status` off the profile the screen already fetches and show the owner
 * section when it says "owner". That fails in both directions and only one of
 * them is visible. An owner whose role string is spelled differently silently
 * loses the surface; anyone who can influence that payload gains a set of
 * buttons that look authoritative.
 *
 * So the gate is `capabilities.hasAuthority`, which comes from
 * `/api/admin/music/capabilities` and is computed server-side from the same
 * permission system the endpoints enforce with. These tests pin that: a profile
 * that claims to be the owner changes nothing, and a capability answer of "no"
 * removes the control even for a viewer the rest of the screen treats as its
 * subject.
 *
 * None of this is the security boundary — every endpoint re-resolves the actor
 * and re-checks its own permission, and `tests/music_authority/test_routes.py`
 * is where that is proven. This is the boundary between an honest UI and a
 * misleading one.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react-native";

jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn().mockResolvedValue(undefined), Sound: { createAsync: jest.fn() } }
}));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));

const mockSearchPulseMusic = jest.fn();
jest.mock("../../api/music", () => ({
  searchPulseMusic: (...args: unknown[]) => mockSearchPulseMusic(...args),
  getPulseMusicTrack: () => Promise.resolve(null),
  loadCachedPulseMusicSnapshot: () => Promise.resolve([]),
  recordPulseMusicEvent: () => Promise.resolve(),
  reportPulseMusic: () => Promise.resolve({}),
  selectPulseMusicForSurface: () => Promise.resolve(),
  uploadPulseMusic: () => Promise.resolve({}),
  pulseMusicWebUrl: (id: string) => `https://pulsesoc.com/pulse/music?track=${id}`
}));

const mockProfile = jest.fn();
jest.mock("../../api/profile", () => ({
  getMyProfile: () => mockProfile()
}));

const mockFetchCapabilities = jest.fn();
const mockFetchImpact = jest.fn();
jest.mock("../../api/musicAuthority", () => ({
  fetchMusicCapabilities: () => mockFetchCapabilities(),
  fetchMusicTrackImpact: (...args: unknown[]) => mockFetchImpact(...args),
  fetchMusicTrackAudit: () => Promise.resolve([]),
  takedownMusicTrack: jest.fn(),
  restoreMusicTrack: jest.fn(),
  scheduleMusicTrackPurge: jest.fn(),
  cancelMusicTrackPurge: jest.fn(),
  purgeMusicTrack: jest.fn(),
  requestMusicStepUp: jest.fn(),
  describeMusicAuthorityError: () => "Something went wrong."
}));

jest.mock("../../core/pulseRadio", () => ({
  getPulseRadioState: () => ({ status: "idle", message: "", track: null, shuffle: false, repeatMode: "off", userWantsPlayback: false, interruptedBy: "" }),
  subscribePulseRadio: () => () => undefined,
  togglePulseRadio: () => Promise.resolve(),
  togglePulseRadioShuffle: () => undefined,
  cyclePulseRadioRepeatMode: () => undefined,
  playNextTrack: () => Promise.resolve(),
  playPreviousTrack: () => Promise.resolve(),
  seekPulseRadioBy: () => Promise.resolve()
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: () => Promise.resolve(true),
  releaseMediaPlayback: () => Promise.resolve()
}));

jest.mock("../../session/auth", () => ({
  useAuth: () => ({ authState: { user: { user_id: "9" } } })
}));

import { preloadNamespaces } from "../../i18n/engine";
import { MusicScreen } from "../MusicScreen";

const PERMISSIONS = {
  "music.view_all": true,
  "music.moderate": true,
  "music.takedown": true,
  "music.restore": true,
  "music.purge": true,
  "music.manage_rights": true
};

const NOTHING = {
  "music.view_all": false,
  "music.moderate": false,
  "music.takedown": false,
  "music.restore": false,
  "music.purge": false,
  "music.manage_rights": false
};

function capabilities(hasAuthority: boolean) {
  return {
    hasAuthority,
    permissions: hasAuthority ? PERMISSIONS : NOTHING,
    states: ["ACTIVE", "TAKEN_DOWN", "QUARANTINED", "PURGE_PENDING", "PURGED"],
    reasonCodes: ["COPYRIGHT", "OWNER_DECISION", "OTHER"],
    stepUpTtlSeconds: 300
  };
}

const TRACK = {
  id: "101",
  title: "Pool Song A",
  artist: "Night Signal",
  artistUserId: 4,
  durationSeconds: 180,
  previewUrl: "https://cdn.pulsesoc.com/101.m4a",
  audioUrl: "https://cdn.pulsesoc.com/101.m4a",
  coverArtUrl: "",
  waveform: [0.2, 0.4],
  genre: "drill",
  language: "en",
  mood: "dark",
  licenseLabel: "approved",
  moderationStatus: "approved",
  approvedByAdmin: true,
  active: true,
  playCount: 1,
  usageCount: 0,
  trendScore: 0,
  saveCount: 0,
  shareCount: 0
};

function show() {
  return render(
    <MusicScreen
      route={{ key: "m", name: "Music", params: {} } as never}
      navigation={{ navigate: jest.fn(), setOptions: jest.fn() } as never}
    />
  );
}

// Without this the discovery tier is unloaded and every label degrades to a
// humanized key, so "Manage" is absent from a screen that renders it correctly
// and the negative assertions below pass for a reason that has nothing to do
// with authority.
beforeAll(async () => {
  await preloadNamespaces("en", ["discovery", "common"]);
});

beforeEach(() => {
  jest.clearAllMocks();
  mockSearchPulseMusic.mockResolvedValue({ tracks: [TRACK], surfaces: [], provider: {} });
  mockProfile.mockResolvedValue({ user_id: "9", username: "listener", display_name: "Listener" });
  mockFetchImpact.mockResolvedValue({
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
    openReports: 0,
    playCount: 1,
    usageCount: 0,
    cachedCopiesRemainUntilPurge: true,
    reasonCodes: ["COPYRIGHT", "OWNER_DECISION", "OTHER"]
  });
});

it("draws the owner control when the server says the viewer holds authority", async () => {
  mockFetchCapabilities.mockResolvedValue(capabilities(true));

  const view = show();

  await waitFor(() => expect(view.queryByText("Pool Song A")).not.toBeNull());
  await waitFor(() => expect(view.queryByLabelText("Manage")).not.toBeNull());
});

it("hides the owner control when the server says the viewer holds nothing", async () => {
  mockFetchCapabilities.mockResolvedValue(capabilities(false));

  const view = show();

  // Positive control first: without it a screen that rendered no tracks at all
  // would pass this test for the wrong reason.
  await waitFor(() => expect(view.queryByText("Pool Song A")).not.toBeNull());
  await waitFor(() => expect(view.queryByLabelText("Report")).not.toBeNull());
  expect(view.queryByLabelText("Manage")).toBeNull();
});

it("ignores a profile that calls itself the owner", async () => {
  // Everything a client-side guess would key on says "owner"; the server's
  // answer says no. The server wins.
  mockProfile.mockResolvedValue({
    user_id: "9",
    username: "roody",
    display_name: "Roody",
    role: "owner",
    is_owner: true,
    is_admin: true,
    premium_status: "founder"
  });
  mockFetchCapabilities.mockResolvedValue(capabilities(false));

  const view = show();

  await waitFor(() => expect(view.queryByText("Pool Song A")).not.toBeNull());
  expect(view.queryByLabelText("Manage")).toBeNull();
});

it("hides the owner control when the capability call fails", async () => {
  // `fetchMusicCapabilities` degrades a failure to "no authority" rather than
  // throwing, and this pins which way that degradation points. Failing open
  // here would draw a takedown button on a dropped connection.
  mockFetchCapabilities.mockRejectedValue(new Error("network"));

  const view = show();

  await waitFor(() => expect(view.queryByText("Pool Song A")).not.toBeNull());
  expect(view.queryByLabelText("Manage")).toBeNull();
});

it("does not ask for an impact report until the owner opens the panel", async () => {
  // The list is the hot path. Fetching the blast radius of every track on
  // render would be a per-row query multiplied by the page size, paid by the
  // one viewer who did not ask for it.
  mockFetchCapabilities.mockResolvedValue(capabilities(true));

  const view = show();

  await waitFor(() => expect(view.queryByLabelText("Manage")).not.toBeNull());
  expect(mockFetchImpact).not.toHaveBeenCalled();
});
