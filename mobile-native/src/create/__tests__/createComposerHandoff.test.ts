import {
  consumeCreateCameraCaptureResult,
  createComposerModeFromCameraTarget,
  saveCreateCameraCaptureResult
} from "../createComposerHandoff";

const mockStore = new Map<string, string>();

jest.mock("@react-native-async-storage/async-storage", () => ({
  setItem: jest.fn((key: string, value: string) => {
    mockStore.set(key, value);
    return Promise.resolve();
  }),
  getItem: jest.fn((key: string) => Promise.resolve(mockStore.get(key) || null)),
  removeItem: jest.fn((key: string) => {
    mockStore.delete(key);
    return Promise.resolve();
  })
}));

describe("createComposerHandoff", () => {
  beforeEach(() => {
    mockStore.clear();
    jest.clearAllMocks();
  });

  it("persists a camera capture for the composer and consumes it once", async () => {
    const asset = {
      uri: "file:///tmp/pulsesoc-photo.jpg",
      name: "pulsesoc-photo.jpg",
      mimeType: "image/jpeg",
      mediaType: "image" as const,
      size: 1200
    };

    const saved = await saveCreateCameraCaptureResult({
      id: "capture-1",
      asset,
      composerMode: "status",
      captureMode: "photo"
    });

    expect(saved.composerMode).toBe("status");
    expect(saved.source).toBe("native_camera");

    const consumed = await consumeCreateCameraCaptureResult();
    expect(consumed?.asset.uri).toBe(asset.uri);
    expect(consumed?.composerMode).toBe("status");

    await expect(consumeCreateCameraCaptureResult()).resolves.toBeNull();
  });

  it("maps camera targets to safe composer modes", () => {
    expect(createComposerModeFromCameraTarget("status", "photo")).toBe("status");
    expect(createComposerModeFromCameraTarget("reel", "reel")).toBe("reel");
    expect(createComposerModeFromCameraTarget("feed", "photo")).toBe("post");
    expect(createComposerModeFromCameraTarget("message", "photo")).toBe("post");
  });
});
