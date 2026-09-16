import { act, renderHook, waitFor } from "@testing-library/react-native";

const mockStore = new Map<string, string>();
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async (key: string) => (mockStore.has(key) ? mockStore.get(key)! : null)),
  setItem: jest.fn(async (key: string, value: string) => {
    mockStore.set(key, value);
  }),
  removeItem: jest.fn(async (key: string) => {
    mockStore.delete(key);
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

/**
 * Spelled out rather than built from `DEFAULT_CHAT_WALLPAPER`, so that changing
 * the default is visible here as a deliberate edit instead of silently moving
 * every key this suite asserts on.
 */
const key = (userId: number, conversationId: number) =>
  `pulsesoc.native.messenger.wallpaper.v1.pulsesoc_cosmic.${userId}.${conversationId}`;

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
    mockStore.set(key(7, 42), "minimal_black");
    // The server is slow; the cache must not be gated behind it.
    mockControlCenter.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(result.current.wallpaper).toBe("minimal_black"));
  });

  it("adopts the viewer's stored choice from the server and caches it", async () => {
    mockControlCenter.mockResolvedValue(settings("aurora_signal"));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(result.current.wallpaper).toBe("aurora_signal"));
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(key(7, 42), "aurora_signal");
  });

  it.each([
    ["absent", undefined],
    ["null", null],
    ["the sentinel 'default'", "default"],
    ["an id this build does not know", "wallpaper_from_a_later_release"]
  ])("keeps the default when the server reports %s", async (_label, value) => {
    mockControlCenter.mockResolvedValue(settings(value));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(mockControlCenter).toHaveBeenCalled());
    expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    // "No choice" must not be written to the cache as if it were a choice.
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
  });

  it.each([
    ["the sentinel 'default'", "default"],
    ["absent", undefined],
    ["an id this build does not know", "wallpaper_from_a_later_release"]
  ])("drops a cached choice the server no longer reports — %s", async (_label, value) => {
    // Someone chose a wallpaper on this device and then cleared it in the web
    // control centre. The cache still paints the old choice, so the server's
    // answer has to be allowed to take it away again; otherwise this device
    // keeps a background the account no longer has.
    mockStore.set(key(7, 42), "minimal_black");
    mockControlCenter.mockResolvedValue(settings(value));
    const { result } = renderHook(() => useConversationWallpaper(7, 42));
    await waitFor(() => expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER));
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith(key(7, 42));
    // And the next launch must not resurrect it from the cache.
    expect(mockStore.has(key(7, 42))).toBe(false);
  });

  describe("scoping", () => {
    it("does not let one account inherit another's background on a shared device", async () => {
      mockStore.set(key(7, 42), "minimal_black");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      // Same conversation, different viewer.
      const { result } = renderHook(() => useConversationWallpaper(8, 42));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
      expect(AsyncStorage.getItem).toHaveBeenCalledWith(key(8, 42));
    });

    it("does not carry a background between conversations", async () => {
      mockStore.set(key(7, 42), "minimal_black");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      const { result } = renderHook(() => useConversationWallpaper(7, 99));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    });

    it("ignores an entry cached under a previous default", async () => {
      // The server answers every conversation with a value, using its own
      // defaults to fill the gap where there is no stored choice, so the cache
      // necessarily holds gap-fillers as well as real choices. Were the key not
      // scoped to the default, an entry left by an earlier default would
      // out-rank the new one: an untouched conversation would paint the new
      // default, swap to the stale entry, then swap back once the server replied.
      //
      // The assertion on the key read is what makes this test mean anything. On
      // its own, "the stale entry was ignored" would also hold if the cache were
      // simply never consulted, so the key is pinned directly and the positive
      // control below proves a correctly-scoped entry is still honoured.
      mockStore.set("pulsesoc.native.messenger.wallpaper.v1.deep_space.7.42", "deep_space");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(AsyncStorage.getItem).toHaveBeenCalled());
      expect(AsyncStorage.getItem).toHaveBeenCalledWith(key(7, 42));
      expect(AsyncStorage.getItem).not.toHaveBeenCalledWith(
        "pulsesoc.native.messenger.wallpaper.v1.deep_space.7.42"
      );
      expect(result.current.wallpaper).toBe(DEFAULT_CHAT_WALLPAPER);
    });

    it("still honours an entry cached under the current default", async () => {
      // The positive control for the test above: same shape, same conversation,
      // only the default in the key differs, and this one must paint.
      mockStore.set(key(7, 42), "deep_space");
      mockControlCenter.mockReturnValue(new Promise(() => {}));
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(result.current.wallpaper).toBe("deep_space"));
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
      mockStore.set(key(7, 42), "minimal_black");
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
      expect(AsyncStorage.setItem).toHaveBeenCalledWith(key(7, 42), "galaxy_grid");
    });

    it("survives a server read that lands after the pick", async () => {
      // Opening the control centre and choosing a wallpaper immediately can
      // easily beat the control-centre read that the chat screen kicked off on
      // mount. That read reflects the state from *before* the pick, so letting
      // it apply would revert the background the person just chose — and, on the
      // "no choice" path, delete it from the cache too.
      let release: (value: unknown) => void = () => {};
      mockControlCenter.mockReturnValue(new Promise((resolve) => {
        release = resolve;
      }));
      const { result } = renderHook(() => useConversationWallpaper(7, 42));
      await waitFor(() => expect(mockControlCenter).toHaveBeenCalled());

      await act(async () => {
        result.current.applyWallpaper("galaxy_grid");
      });
      expect(result.current.wallpaper).toBe("galaxy_grid");

      // The stale read now arrives, reporting no choice.
      await act(async () => {
        release(settings(undefined));
      });

      expect(result.current.wallpaper).toBe("galaxy_grid");
      expect(mockStore.get(key(7, 42))).toBe("galaxy_grid");
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
