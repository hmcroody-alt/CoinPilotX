/**
 * The hook's job is identity and cleanup, not translation, so these tests all
 * ask the same question in different shapes: can an answer reach a component
 * that is no longer asking the question?
 *
 * That is the failure Stage 7 names and the one the router structurally cannot
 * prevent — it has no way to know a feed cell was recycled onto a different
 * post while its request was in flight. If this file is wrong, the visible
 * symptom is one item showing another item's translated text, which is worse
 * than no translation at all because it looks correct.
 */

import { act, renderHook, waitFor } from "@testing-library/react-native";

const mockTranslateText = jest.fn();
const mockCancelTranslationRequests = jest.fn();
jest.mock("../router", () => ({
  translateText: (...args: unknown[]) => mockTranslateText(...args),
  cancelTranslationRequests: (...args: unknown[]) => mockCancelTranslationRequests(...args)
}));

import { useContentTranslation } from "../useContentTranslation";

type Deferred = {
  resolve: (value: unknown) => void;
  promise: Promise<unknown>;
};

function deferred(): Deferred {
  let resolve: (value: unknown) => void = () => {};
  const promise = new Promise(inner => {
    resolve = inner;
  });
  return { resolve, promise };
}

function success(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    requestId: "post:1#1",
    contentId: "post:1",
    provider: "apple_on_device",
    translatedText: "Bonjour",
    sourceLanguage: "en",
    targetLanguage: "fr",
    cached: false,
    downloadPrepared: false,
    durationMs: 8,
    ...overrides
  };
}

const BASE = {
  contentType: "post" as const,
  contentId: "post:1",
  text: "Hello",
  sourceLanguage: "en",
  targetLanguage: "fr"
};

beforeEach(() => {
  mockTranslateText.mockReset();
  mockCancelTranslationRequests.mockReset();
});

describe("useContentTranslation", () => {
  it("shows the translation and where it came from", async () => {
    mockTranslateText.mockResolvedValue(success());
    const { result } = renderHook(() => useContentTranslation(BASE));

    await act(async () => {
      await result.current.translate();
    });

    expect(result.current.status).toBe("translated");
    expect(result.current.translatedText).toBe("Bonjour");
    expect(result.current.provider).toBe("apple_on_device");
    expect(result.current.detectedSourceLanguage).toBe("en");
  });

  it("asks on behalf of the user by default, and says so when it is not", async () => {
    // `userInitiated` is what decides whether a request may ever become
    // billable, so a hook that quietly defaulted it the wrong way would move
    // the whole cost-control decision without touching the router.
    mockTranslateText.mockResolvedValue(success());
    const { result } = renderHook(() => useContentTranslation(BASE));

    await act(async () => {
      await result.current.translate();
    });
    await act(async () => {
      await result.current.translate({ userInitiated: false });
    });

    expect(mockTranslateText.mock.calls[0][0].userInitiated).toBe(true);
    expect(mockTranslateText.mock.calls[1][0].userInitiated).toBe(false);
  });

  it("never asks for a model download unless the caller explicitly did", async () => {
    mockTranslateText.mockResolvedValue(success());
    const { result } = renderHook(() => useContentTranslation(BASE));

    await act(async () => {
      await result.current.translate();
    });

    expect(mockTranslateText.mock.calls[0][0].allowDownload).toBe(false);
  });

  it("drops an answer that arrives after the cell was recycled", async () => {
    const pending = deferred();
    mockTranslateText.mockReturnValue(pending.promise);
    const { result, rerender } = renderHook(
      (props: { contentId: string; text: string }) =>
        useContentTranslation({ ...BASE, ...props }),
      { initialProps: { contentId: "post:1", text: "Hello" } }
    );

    act(() => {
      void result.current.translate();
    });
    rerender({ contentId: "post:2", text: "Goodbye" });
    await act(async () => {
      pending.resolve(success());
      await pending.promise;
    });

    // The answer for post:1 must not appear under post:2. Asserting on the
    // text as well as the status, because a status of "idle" with the previous
    // translation still in state would render the wrong words on the next
    // toggle.
    expect(result.current.status).toBe("idle");
    expect(result.current.translatedText).toBe("");
  });

  it("cancels the in-flight request when the cell is recycled", async () => {
    mockTranslateText.mockReturnValue(deferred().promise);
    const { result, rerender } = renderHook(
      (props: { contentId: string }) => useContentTranslation({ ...BASE, ...props }),
      { initialProps: { contentId: "post:1" } }
    );

    act(() => {
      void result.current.translate();
    });
    rerender({ contentId: "post:2" });

    await waitFor(() =>
      expect(mockCancelTranslationRequests).toHaveBeenCalledWith(["post:1#1"], "content_changed")
    );
  });

  it("cancels the in-flight request when the screen goes away", async () => {
    mockTranslateText.mockReturnValue(deferred().promise);
    const { result, unmount } = renderHook(() => useContentTranslation(BASE));

    act(() => {
      void result.current.translate();
    });
    unmount();

    expect(mockCancelTranslationRequests).toHaveBeenCalledWith(["post:1#1"], "unmounted");
  });

  it("cancels nothing when there was nothing in flight", async () => {
    // A scroll past a hundred untranslated cells should not be a hundred
    // cancellation calls across the bridge.
    const { unmount } = renderHook(() => useContentTranslation(BASE));

    unmount();

    expect(mockCancelTranslationRequests).not.toHaveBeenCalled();
  });

  it("gives each attempt its own id so a retry cannot accept the first answer", async () => {
    const first = deferred();
    const second = deferred();
    mockTranslateText.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useContentTranslation(BASE));

    act(() => {
      void result.current.translate();
    });
    act(() => {
      void result.current.translate();
    });

    expect(mockTranslateText.mock.calls[0][0].requestId).toBe("post:1#1");
    expect(mockTranslateText.mock.calls[1][0].requestId).toBe("post:1#2");

    await act(async () => {
      first.resolve(success({ translatedText: "Stale" }));
      await first.promise;
    });
    expect(result.current.status).toBe("translating");

    await act(async () => {
      second.resolve(success({ translatedText: "Fresh" }));
      await second.promise;
    });
    expect(result.current.translatedText).toBe("Fresh");
  });

  it("keeps the translation when the user looks at the original", async () => {
    // Re-translating to switch back would be a second billable request on the
    // cloud path for words the user has already been shown.
    mockTranslateText.mockResolvedValue(success());
    const { result } = renderHook(() => useContentTranslation(BASE));

    await act(async () => {
      await result.current.translate();
    });
    act(() => result.current.showOriginal());

    expect(result.current.status).toBe("idle");
    expect(result.current.hasTranslation).toBe(true);

    act(() => result.current.showTranslation());
    expect(result.current.status).toBe("translated");
    expect(mockTranslateText).toHaveBeenCalledTimes(1);
  });

  it("surfaces a typed failure rather than throwing into the render", async () => {
    mockTranslateText.mockResolvedValue({
      ok: false,
      requestId: "post:1#1",
      contentId: "post:1",
      provider: "apple_on_device",
      code: "model_not_installed",
      recoverable: true,
      downloadAvailable: true
    });
    const { result } = renderHook(() => useContentTranslation(BASE));

    await act(async () => {
      await result.current.translate();
    });

    expect(result.current.status).toBe("failed");
    expect(result.current.failure?.code).toBe("model_not_installed");
    expect(result.current.failure?.downloadAvailable).toBe(true);
  });

  it("does not translate on mount", async () => {
    renderHook(() => useContentTranslation(BASE));

    expect(mockTranslateText).not.toHaveBeenCalled();
  });
});
