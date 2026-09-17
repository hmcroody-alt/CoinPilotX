/**
 * The router's job is to decide, and these tests are about the decisions rather
 * than about translation. The assertion that matters most is negative: for a
 * private message, and for a cancelled request, `translatePulseContent` must
 * not be called at all. A test that only checked the returned code would pass
 * against an implementation that made the billable call and then discarded it.
 */

import type { AppleTranslationResult } from "pulse-apple-translation";
import type { TranslationRequest } from "../types";

const mockAppleState = {
  available: true,
  osVersion: "18.7.3",
  translate: jest.fn<Promise<AppleTranslationResult>, [Record<string, unknown>]>(),
  cancelRequests: jest.fn().mockResolvedValue(undefined),
  cancelContent: jest.fn().mockResolvedValue(undefined),
  getLanguageStatus: jest.fn().mockResolvedValue({ status: "installed", target: "fr" })
};

jest.mock("pulse-apple-translation", () => ({
  get isAppleTranslationAvailable() {
    return mockAppleState.available;
  },
  get isAppleTranslationModuleLinked() {
    return mockAppleState.available;
  },
  get appleTranslationLimits() {
    return { maxTextLength: 5000, minimumOSVersion: "18.0", osVersion: mockAppleState.osVersion };
  },
  translate: (request: Record<string, unknown>) => mockAppleState.translate(request),
  cancelRequests: (...args: unknown[]) => mockAppleState.cancelRequests(...args),
  cancelContent: (...args: unknown[]) => mockAppleState.cancelContent(...args),
  getLanguageStatus: (...args: unknown[]) => mockAppleState.getLanguageStatus(...args),
  getSupportedLanguages: jest.fn().mockResolvedValue([]),
  resetAppleTranslation: jest.fn().mockResolvedValue(undefined),
  getDiagnostics: jest.fn().mockResolvedValue({ isAvailable: true, isHostMounted: true, activeHosts: 1 }),
  AppleTranslationHostView: null
}));

const mockTranslatePulseContent = jest.fn();
jest.mock("../../../api/translation", () => ({
  translatePulseContent: (...args: unknown[]) => mockTranslatePulseContent(...args)
}));

import {
  flushTranslationCache,
  resetTranslationCacheForTests,
  setTranslationCacheScope,
  translationCacheSizeForTests
} from "../cache";
import { resetCloudGuardForTests } from "../cloudGuard";
import { resetTranslationMetrics, translationMetricsSnapshot } from "../metrics";
import { cancelTranslationRequests, resetTranslationRouter, translateText } from "../router";

function appleSuccess(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    requestId: "r1",
    contentId: "post:1",
    translatedText: "Bonjour",
    detectedSourceLanguage: "en",
    targetLanguage: "fr",
    durationMs: 12,
    deduplicated: false,
    downloadPrepared: false,
    ...overrides
  } as AppleTranslationResult;
}

function appleFailure(code: string, permitsCloudFallback: boolean, recoverable = false) {
  return {
    ok: false,
    requestId: "r1",
    code,
    recoverable,
    permitsCloudFallback
  } as unknown as AppleTranslationResult;
}

function request(overrides: Partial<TranslationRequest> = {}): TranslationRequest {
  return {
    requestId: "r1",
    contentId: "post:1",
    contentType: "post",
    text: "Hello",
    sourceLanguage: "en",
    targetLanguage: "fr",
    userInitiated: true,
    ...overrides
  };
}

/**
 * The public namespace persists on a 1.5s debounce. Rather than waiting it out,
 * run the write path now: `schedulePersist` calls `flushTranslationCache` with
 * no arguments, so a pending debounce would write the same snapshot this does.
 * The private namespace is declared `persist: false` and never schedules at
 * all, which is why forcing the only writer is enough to see a leak.
 */
async function flushDebouncedWrites() {
  await flushTranslationCache();
}

const FLAG_NAMES = [
  "EXPO_PUBLIC_APPLE_ON_DEVICE_TRANSLATION_ENABLED",
  "EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED",
  "EXPO_PUBLIC_GOOGLE_TRANSLATION_ENABLED",
  "EXPO_PUBLIC_TRANSLATION_AUTOMATIC_ENABLED"
] as const;

beforeEach(() => {
  for (const name of FLAG_NAMES) delete process.env[name];
  mockAppleState.available = true;
  mockAppleState.osVersion = "18.7.3";
  mockAppleState.translate.mockReset();
  mockAppleState.cancelRequests.mockClear();
  mockAppleState.cancelContent.mockClear();
  mockTranslatePulseContent.mockReset();
  resetTranslationRouter("test");
  resetTranslationCacheForTests();
  resetCloudGuardForTests();
  resetTranslationMetrics();
  setTranslationCacheScope("user-1");
});

describe("provider order", () => {
  it("prefers Apple and never touches the cloud when Apple answers", async () => {
    mockAppleState.translate.mockResolvedValue(appleSuccess());

    const outcome = await translateText(request());

    expect(outcome.ok).toBe(true);
    expect(outcome.ok && outcome.provider).toBe("apple_on_device");
    expect(outcome.ok && outcome.translatedText).toBe("Bonjour");
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });

  it("serves the second identical request from the device cache", async () => {
    mockAppleState.translate.mockResolvedValue(appleSuccess());
    await translateText(request());

    const second = await translateText(request({ requestId: "r2" }));

    expect(second.ok && second.provider).toBe("cache");
    expect(second.ok && second.cached).toBe(true);
    expect(second.requestId).toBe("r2");
    expect(mockAppleState.translate).toHaveBeenCalledTimes(1);
  });

  it("falls back to the cloud when Apple cannot do the pair", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));
    mockTranslatePulseContent.mockResolvedValue({
      status: "translated",
      translated_text: "Bonjou",
      source_language: "en",
      target_language: "ht",
      original_text: "Hello",
      translated: true,
      policy: "ask"
    });

    const outcome = await translateText(request({ targetLanguage: "ht" }));

    expect(outcome.ok && outcome.provider).toBe("cloud_fallback");
    expect(outcome.ok && outcome.translatedText).toBe("Bonjou");
    expect(mockTranslatePulseContent).toHaveBeenCalledTimes(1);
  });

  it("answers same-language itself without asking any provider", async () => {
    const outcome = await translateText(request({ sourceLanguage: "fr", targetLanguage: "fr-CA" }));

    expect(outcome.ok).toBe(false);
    expect(!outcome.ok && outcome.code).toBe("same_language");
    expect(mockAppleState.translate).not.toHaveBeenCalled();
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });

  it("rejects an unparseable target language without asking any provider", async () => {
    const outcome = await translateText(request({ targetLanguage: "9" }));

    expect(!outcome.ok && outcome.code).toBe("invalid_language");
    expect(mockAppleState.translate).not.toHaveBeenCalled();
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });
});

describe("private content never reaches the billable provider", () => {
  it("does not call the cloud for a chat message even when Apple fails and permits it", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    const outcome = await translateText(request({ contentType: "chat", contentId: "chat:9" }));

    expect(outcome.ok).toBe(false);
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    // Apple's cause, not the policy: the router reports why translation could
    // not happen rather than which fence stopped it.
    expect(!outcome.ok && outcome.code).toBe("unsupported_language_pair");
  });

  it("names the privacy refusal when that is the only reason", async () => {
    process.env.EXPO_PUBLIC_APPLE_ON_DEVICE_TRANSLATION_ENABLED = "0";

    const outcome = await translateText(request({ contentType: "chat", contentId: "chat:9" }));

    expect(mockAppleState.translate).not.toHaveBeenCalled();
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    expect(!outcome.ok && outcome.code).toBe("cloud_not_permitted_for_private_content");
  });

  it("does not call the cloud for a support ticket either", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    await translateText(request({ contentType: "support", contentId: "support:3" }));

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });

  it("stays private even with every cloud flag explicitly turned on", async () => {
    process.env.EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED = "1";
    process.env.EXPO_PUBLIC_GOOGLE_TRANSLATION_ENABLED = "1";
    process.env.EXPO_PUBLIC_TRANSLATION_AUTOMATIC_ENABLED = "1";
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    await translateText(request({ contentType: "chat", contentId: "chat:9" }));

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });

  it("writes no private text to disk, under any key", async () => {
    const AsyncStorage = require("@react-native-async-storage/async-storage");
    (AsyncStorage.setItem as jest.Mock).mockClear();
    mockAppleState.translate.mockResolvedValue(
      appleSuccess({ contentId: "chat:9", translatedText: "Bonjour le monde" })
    );

    await translateText(
      request({ contentType: "chat", contentId: "chat:9", text: "Hello the world" })
    );
    // The public namespace flushes on a debounce, so give any scheduled write
    // time to land before concluding that nothing was written.
    await flushDebouncedWrites();

    // Asserted on the payloads rather than the keys: a leak through a key this
    // test did not predict would still be a leak, and an assertion about
    // `translation:public:*` would not see it.
    const payloads = (AsyncStorage.setItem as jest.Mock).mock.calls.map(call => String(call[1]));
    for (const payload of payloads) {
      expect(payload).not.toContain("Bonjour le monde");
      expect(payload).not.toContain("Hello the world");
    }
    // It was cached, just not at rest. Without this the test would also pass
    // against a router that cached nothing for private content.
    expect(translationCacheSizeForTests().private).toBe(1);
  });

  it("does write a public translation to disk, which proves the check above is not vacuous", async () => {
    const AsyncStorage = require("@react-native-async-storage/async-storage");
    (AsyncStorage.setItem as jest.Mock).mockClear();
    mockAppleState.translate.mockResolvedValue(
      appleSuccess({ contentId: "post:1", translatedText: "Bonjour le monde" })
    );

    await translateText(request({ contentType: "post", contentId: "post:1" }));
    await flushDebouncedWrites();

    const payloads = (AsyncStorage.setItem as jest.Mock).mock.calls.map(call => String(call[1]));
    expect(payloads.some(payload => payload.includes("Bonjour le monde"))).toBe(true);
  });
});

describe("a request that must not become billable", () => {
  it("does not call the cloud when Apple reports cancellation", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("request_canceled", false));

    const outcome = await translateText(request());

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    expect(!outcome.ok && outcome.code).toBe("request_canceled");
  });

  it("does not call the cloud for an automatic request by default", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    const outcome = await translateText(request({ userInitiated: false }));

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    // Apple's cause is what the user is shown; the refusal is what the
    // operator reads. Both are asserted because either alone would let the
    // other regress silently.
    expect(!outcome.ok && outcome.code).toBe("unsupported_language_pair");
    expect(!outcome.ok && outcome.cloudExclusion).toBe("automatic_request");
  });

  it("does call the cloud for an automatic request once the operator opts in", async () => {
    process.env.EXPO_PUBLIC_TRANSLATION_AUTOMATIC_ENABLED = "1";
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));
    mockTranslatePulseContent.mockResolvedValue({
      status: "translated",
      translated_text: "Bonjour",
      source_language: "en",
      target_language: "fr",
      original_text: "Hello",
      translated: true,
      policy: "ask"
    });

    await translateText(request({ userInitiated: false }));

    expect(mockTranslatePulseContent).toHaveBeenCalledTimes(1);
  });

  it("does not call the cloud when the fallback flag is off", async () => {
    process.env.EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED = "0";
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    const outcome = await translateText(request());

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    expect(!outcome.ok && outcome.cloudExclusion).toBe("flag_disabled");
  });

  it("does not call the cloud when the request alone exceeds the device budget", async () => {
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    const outcome = await translateText(request({ text: "x".repeat(20_001) }));

    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
    expect(!outcome.ok && outcome.cloudExclusion).toBe("budget_exhausted");
  });

  it("prefers Apple's own actionable failure over the cloud being switched off", async () => {
    process.env.EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED = "0";
    mockAppleState.translate.mockResolvedValue({
      ok: false,
      requestId: "r1",
      code: "model_not_installed",
      recoverable: true,
      permitsCloudFallback: true
    } as unknown as AppleTranslationResult);

    const outcome = await translateText(request());

    expect(!outcome.ok && outcome.code).toBe("model_not_installed");
    expect(!outcome.ok && outcome.downloadAvailable).toBe(true);
  });
});

describe("deduplication", () => {
  it("shares one inference between two cells showing the same text", async () => {
    let release: (value: AppleTranslationResult) => void = () => {};
    mockAppleState.translate.mockImplementation(
      () => new Promise<AppleTranslationResult>(resolve => {
        release = resolve;
      })
    );

    const first = translateText(request({ requestId: "a" }));
    const second = translateText(request({ requestId: "b" }));
    release(appleSuccess());
    const [left, right] = await Promise.all([first, second]);

    expect(mockAppleState.translate).toHaveBeenCalledTimes(1);
    expect(left.requestId).toBe("a");
    expect(right.requestId).toBe("b");
    expect(left.ok && left.translatedText).toBe("Bonjour");
    expect(right.ok && right.translatedText).toBe("Bonjour");
  });

  it("gives a follower that was cancelled its own cancellation, not the shared answer", async () => {
    let release: (value: AppleTranslationResult) => void = () => {};
    mockAppleState.translate.mockImplementation(
      () => new Promise<AppleTranslationResult>(resolve => {
        release = resolve;
      })
    );

    const first = translateText(request({ requestId: "a" }));
    const second = translateText(request({ requestId: "b" }));
    await cancelTranslationRequests(["b"], "scrolled_away");
    release(appleSuccess());
    const [left, right] = await Promise.all([first, second]);

    expect(left.ok).toBe(true);
    expect(right.ok).toBe(false);
    expect(!right.ok && right.code).toBe("request_canceled");
  });
});

describe("Apple unavailable on this OS", () => {
  it("routes to the cloud rather than reporting a failure", async () => {
    mockAppleState.available = false;
    mockTranslatePulseContent.mockResolvedValue({
      status: "translated",
      translated_text: "Bonjour",
      source_language: "en",
      target_language: "fr",
      original_text: "Hello",
      translated: true,
      policy: "ask"
    });

    const outcome = await translateText(request());

    expect(mockAppleState.translate).not.toHaveBeenCalled();
    expect(outcome.ok && outcome.provider).toBe("cloud_fallback");
  });

  it("reports unsupported_os_version when the cloud is also unavailable", async () => {
    mockAppleState.available = false;
    process.env.EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED = "0";

    const outcome = await translateText(request());

    expect(!outcome.ok && outcome.code).toBe("unsupported_os_version");
    expect(mockTranslatePulseContent).not.toHaveBeenCalled();
  });
});

describe("metrics", () => {
  it("counts characters kept away from the billable provider", async () => {
    mockAppleState.translate.mockResolvedValue(appleSuccess());

    await translateText(request({ text: "Hello" }));
    await translateText(request({ requestId: "r2", text: "Hello" }));

    const snapshot = translationMetricsSnapshot();
    expect(snapshot.charactersAvoided).toBe(10);
    expect(snapshot.charactersBilled).toBe(0);
    expect(snapshot.onDeviceShare).toBeCloseTo(0.5);
    expect(snapshot.cachedShare).toBeCloseTo(0.5);
  });

  it("attributes a withheld cloud request to the fence that withheld it", async () => {
    // The runbook's "Google cost spike" entry is answered from this counter, so
    // it has to move even when the user-visible code is Apple's cause.
    mockAppleState.translate.mockResolvedValue(appleFailure("unsupported_language_pair", true));

    await translateText(request({ contentType: "chat", contentId: "chat:9" }));
    await translateText(request({ requestId: "r2", userInitiated: false }));

    const snapshot = translationMetricsSnapshot();
    expect(snapshot.cloudExclusions).toEqual({ private_content: 1, automatic_request: 1 });
    expect(snapshot.failureCodes).toEqual({ unsupported_language_pair: 2 });
  });

  it("records no text, only its length", async () => {
    mockAppleState.translate.mockResolvedValue(appleSuccess());
    await translateText(request({ contentType: "chat", contentId: "chat:9", text: "secret words" }));

    const { recentTranslationEvents } = require("../metrics");
    const serialized = JSON.stringify(recentTranslationEvents());
    expect(serialized).not.toContain("secret words");
    expect(serialized).not.toContain("Bonjour");
    expect(serialized).toContain('"characters":12');
  });
});
