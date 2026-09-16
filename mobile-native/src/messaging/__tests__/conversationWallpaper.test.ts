import { act, renderHook, waitFor } from "@testing-library/react-native";

const mockStore = new Map<string, string>();
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async (key: string) => (mockStore.has(key) ? mockStore.get(key)! : null)),
  setItem: jest.fn(async (key: string, value: string) => {
    mockStore.set(key, value);
  })
}));

const mockControlCenter = jest.fn();
jest.mock("../../api/messenger", () => ({
  getConversationControlCenter: (...args: unknown[]) => mockControlCenter(...args)
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  resetConversationWallpaperRefreshes,
  useConversationWallpaper
} from "../conversationWallpaper";
import { DEFAULT_CHAT_WALLPAPER } from "../../theme/chatWallpaper";

const settings = (wallpaper: unknown) => ({ settings: { appearance: { wallpaper } } });

beforeEach(() => {
  mockStore.clear();
  jest.clearAllMocks();
  resetConversationWallpaperRefreshes();
  mockControlCenter.mockResolvedValue(settings(undefined));
});

describe("useConversationWallpaper", () => {
  it("returns the Cosmic default on the very first render, before any await", () => {
    // The no-flash guarantee lives here. If this were null/undefined until the
    // cache read resolved, the first frame of a conversation would have no
    // background at all.
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
  });

  it("paints a previously-seen choice from cache without waiting for the server", async () => {
    mockStore.set("pulsesoc.native.messenger.wallpaper.v1.7.42", "minimal_black");
    // The server is slow; the cache must not be gated behind it.
    mockControlCenter.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(result.current.wallpaper).toBe("minimal_black"));
  });

  it("adopts the viewer's stored choice from the server and caches it", async () => {
    mockControlCenter.mockResolvedValue(settings("aurora_signal"));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(result.current.wallpaper).toBe("aurora_signal"));
    expect(AsyncStorage.setItem).toHaveBeenCalledWith("pulsesoc.native.messenger.wallpaper.v1.7.42", "aurora_signal");
  });

  it.each([
    ["absent", undefined],
    ["null", null],
    ["an id this build does not know", "wallpaper_from_a_later_release"]
  ])("keeps the default when the server reports %s", async (_label, value) => {
    mockControlCenter.mockResolvedValue(settings(value));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(mockControlCenter).toHaveBeenCalled());
    expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    // "No choice" must not be written to the cache as if it were a choice.
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
  });

  describe("scoping", () => {
    it("does not let one account inherit another's background on a shared device", async () => {
      mockStore.set("pulsesoc.native.messenger.wallpaper.v1.7.42", "minimal_black");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      // Same conversation, different viewer.
      const { result } = renderHook(() => useConversationWallpaper(8, 42));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
      expect(AsyncStorage.getItem).toHaveBeenCalledWith("pulsesoc.native.messenger.wallpaper.v1.8.42");
    });

    it("does not carry a background between conversations", async () => {
      mockStore.set("pulsesoc.native.messenger.wallpaper.v1.7.42", "minimal_black");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      const { result } = renderHook(() => useConversationWallpaper(7, 99));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    });
  });

  describe("cost", () => {
    it("reads the server once per conversation per app run", async () => {
      const first = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(mockControlCenter).toHaveBeenCalledTimes(1));
      first.unmount();
      renderHook(() => useConversationWallpaper(7, 42)).unmount();
      renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalledTimes(3));
      expect(mockControlCenter).toHaveBeenCalledTimes(1);
    });

    it("reads nothing at all when disabled for the assistant thread and fixtures", async () => {
      renderHook(() => useConversationWallpaper(7, 42, false));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(mockControlCenter).not.toHaveBeenCalled();
    });

    it("skips the work entirely before a viewer is known", () => {
      renderHook(() => useConversationWallpaper(0, 42));
      expect(AsyncStorage.getItem).not.toHaveBeenCalled();
      expect(mockControlCenter).not.toHaveBeenCalled();
    });
  });

  describe("failure", () => {
    it("keeps whatever is on screen when the server read throws", async () => {
      mockStore.set("pulsesoc.native.messenger.wallpaper.v1.7.42", "minimal_black");
      mockControlCenter.mockRejectedValue(new Error("offline"));
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(result.current.wallpaper).toBe("minimal_black"));
      // Not reset to the default, and no error surfaced.
      expect(result.current.wallpaper).toBe("minimal_black");
    });

    it("allows a later attempt after a failure, unlike a success", async () => {
      // A failed read must not burn the one-per-run budget, or a conversation
      // opened while offline would keep the wrong background for the whole
      // session.
      mockControlCenter.mockRejectedValueOnce(new Error("offline"));
      const first = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(mockControlCenter).toHaveBeenCalledTimes(1));
      first.unmount();

      mockControlCenter.mockResolvedValue(settings("aurora_signal"));
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(result.current.wallpaper).toBe("aurora_signal"));
      expect(mockControlCenter).toHaveBeenCalledTimes(2);
    });
  });

  describe("applyWallpaper", () => {
    it("changes the background under the open control centre and caches it", async () => {
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(mockControlCenter).toHaveBeenCalled());
      await act(async () => {
        result.current.applyWallpaper("galaxy_grid");
      });
      expect(result.current.wallpaper).toBe("galaxy_grid");
      expect(AsyncStorage.setItem).toHaveBeenCalledWith("pulsesoc.native.messenger.wallpaper.v1.7.42", "galaxy_grid");
    });

    it("ignores a value it does not recognise rather than blanking the background", async () => {
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(mockControlCenter).toHaveBeenCalled());
      await act(async () => {
        result.current.applyWallpaper("not_a_wallpaper");
      });
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    });
  });
});
