import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

/**
 * Regression guard for Issue 2: the Status Studio music picker must let the user
 * LISTEN before selecting (audible preview-before-select), and preview playback
 * must stop when switching tracks or selecting one. Before the fix the picker
 * only supported selection (setSelectedMusic) with no Audio.Sound playback.
 */

const mockCreateAsync = jest.fn();
const mockUnloadAsync = jest.fn().mockResolvedValue(undefined);
const mockSetOnPlaybackStatusUpdate = jest.fn();

jest.mock("expo-av", () => ({
  Audio: {
    Sound: {
      createAsync: (...args: unknown[]) => mockCreateAsync(...args)
    }
  }
}));

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined)
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../media/useNativeMediaUpload", () => ({
  useNativeMediaUpload: () => ({
    asset: null,
    result: null,
    progress: { stage: "idle", percent: 0, message: "" },
    error: "",
    uploading: false,
    upload: jest.fn(),
    reset: jest.fn(),
    retry: jest.fn(),
    cancel: jest.fn(),
    chooseImage: jest.fn(),
    chooseVideo: jest.fn(),
    openCamera: jest.fn()
  })
}));

jest.mock("../../media/MediaUploadPreview", () => ({ MediaUploadPreview: () => null }));

jest.mock("../../api/status", () => ({
  listTrendingStatusMusic: jest.fn().mockResolvedValue({
    items: [
      { id: "t1", track_id: "t1", title: "Late Night Receipt", artist: "PulseSoc Music", preview_url: "https://cdn/t1.mp3", audio_url: "https://cdn/t1.mp3" },
      { id: "t2", track_id: "t2", title: "Good Days Again", artist: "PulseSoc Music", preview_url: "https://cdn/t2.mp3", audio_url: "https://cdn/t2.mp3" }
    ]
  }),
  searchStatusMusic: jest.fn().mockResolvedValue({ items: [] }),
  createStatus: jest.fn(),
  generateStatusAiStory: jest.fn()
}));

jest.mock("../../api/music", () => ({
  consumePulseMusicSelection: jest.fn().mockResolvedValue(null)
}));

import { StatusCreator } from "../StatusCreator";

function makeSound() {
  return { unloadAsync: mockUnloadAsync, setOnPlaybackStatusUpdate: mockSetOnPlaybackStatusUpdate };
}

describe("StatusCreator music preview-before-select", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockCreateAsync.mockResolvedValue({ sound: makeSound() });
  });

  it("plays a dedicated preview without selecting, then stops the first when switching tracks", async () => {
    const { getByLabelText } = render(<StatusCreator visible onClose={jest.fn()} onCreated={jest.fn()} />);

    // Trending tracks load asynchronously; wait for the first preview control.
    const previewT1 = await waitFor(() => getByLabelText("Preview Late Night Receipt"));

    fireEvent.press(previewT1);
    await waitFor(() => expect(mockCreateAsync).toHaveBeenCalledTimes(1));
    // Previewed with the track's preview URL and auto-play on.
    expect(mockCreateAsync).toHaveBeenCalledWith({ uri: "https://cdn/t1.mp3" }, expect.objectContaining({ shouldPlay: true }));

    // Switching to another track's preview must stop/unload the first preview.
    const previewT2 = getByLabelText("Preview Good Days Again");
    fireEvent.press(previewT2);
    await waitFor(() => expect(mockUnloadAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockCreateAsync).toHaveBeenCalledTimes(2));
  });

  it("stops preview playback when a track is selected", async () => {
    const { getByLabelText } = render(<StatusCreator visible onClose={jest.fn()} onCreated={jest.fn()} />);

    const previewT1 = await waitFor(() => getByLabelText("Preview Late Night Receipt"));
    fireEvent.press(previewT1);
    await waitFor(() => expect(mockCreateAsync).toHaveBeenCalledTimes(1));

    // Selecting the track (separate control) stops the preview sound.
    fireEvent.press(getByLabelText("Select Late Night Receipt"));
    await waitFor(() => expect(mockUnloadAsync).toHaveBeenCalled());
  });
});
