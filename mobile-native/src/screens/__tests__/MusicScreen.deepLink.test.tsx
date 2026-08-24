/**
 * Tapping a song on an artist's presence is a request for *that* song.
 *
 * The library search returns a ranked slice of the catalog, so a song that is
 * real, approved and playable can still be missing from it. When that happened
 * the screen rendered the general browse pool with no sign of the song and no
 * explanation — a silent substitution. These tests pin the two honest answers:
 * show the song, or say why it is not there.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react-native";

jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn().mockResolvedValue(undefined), Sound: { createAsync: jest.fn() } }
}));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));

const mockSearchPulseMusic = jest.fn();
const mockGetPulseMusicTrack = jest.fn();
jest.mock("../../api/music", () => ({
  searchPulseMusic: (...args: unknown[]) => mockSearchPulseMusic(...args),
  getPulseMusicTrack: (...args: unknown[]) => mockGetPulseMusicTrack(...args),
  loadCachedPulseMusicSnapshot: () => Promise.resolve([]),
  recordPulseMusicEvent: () => Promise.resolve(),
  reportPulseMusic: () => Promise.resolve({}),
  selectPulseMusicForSurface: () => Promise.resolve(),
  uploadPulseMusic: () => Promise.resolve({}),
  pulseMusicWebUrl: (id: string) => `https://pulsesoc.com/pulse/music?track=${id}`
}));

jest.mock("../../api/profile", () => ({
  getMyProfile: () => Promise.resolve({ user_id: "9", username: "artistfan", display_name: "Artist Fan" })
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

import { MusicScreen } from "../MusicScreen";

function track(id: string, title: string) {
  return {
    id,
    title,
    artist: "Night Signal",
    artistUserId: 4,
    durationSeconds: 180,
    previewUrl: `https://cdn.pulsesoc.com/${id}.m4a`,
    audioUrl: `https://cdn.pulsesoc.com/${id}.m4a`,
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
}

const POOL = [track("101", "Pool Song A"), track("102", "Pool Song B")];

function show(params: Record<string, unknown>) {
  return render(
    <MusicScreen
      route={{ key: "m", name: "Music", params } as never}
      navigation={{ navigate: jest.fn(), setOptions: jest.fn() } as never}
    />
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSearchPulseMusic.mockResolvedValue({ tracks: POOL, surfaces: [], provider: {} });
  mockGetPulseMusicTrack.mockResolvedValue(null);
});

it("shows a deep-linked track that the general search pool does not contain", async () => {
  mockGetPulseMusicTrack.mockResolvedValue(track("777", "Deep Cut"));

  const view = show({ trackId: "777", title: "Deep Cut" });

  await waitFor(() => expect(view.queryByText("Deep Cut")).not.toBeNull());
  expect(mockGetPulseMusicTrack).toHaveBeenCalledWith("777");
});

it("tells the visitor when the deep-linked track is no longer in the catalog", async () => {
  mockGetPulseMusicTrack.mockResolvedValue(null);

  const view = show({ trackId: "778", title: "Pulled Song" });

  await waitFor(() => expect(view.queryByText("That song is no longer available in PulseSoc Music.")).not.toBeNull());
});

it("does not fetch by id when the pool already contains the deep-linked track", async () => {
  const view = show({ trackId: "102", title: "Pool Song B" });

  await waitFor(() => expect(view.queryByText("Pool Song B")).not.toBeNull());
  expect(mockGetPulseMusicTrack).not.toHaveBeenCalled();
});
