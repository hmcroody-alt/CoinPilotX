const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  clearTranslationPreferenceCacheForTests,
  getTranslationPreference,
  updateTranslationPreference
} from "../translation";

describe("translation API preference cache", () => {
  beforeEach(() => {
    clearTranslationPreferenceCacheForTests();
    mockPulseApi.mockReset();
  });

  it("deduplicates simultaneous preference reads for feed-scale rendering", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      result: {
        source_language: "auto",
        target_language: "fr-fr",
        policy: "ask"
      }
    });

    const [first, second] = await Promise.all([
      getTranslationPreference("auto", "fr-fr"),
      getTranslationPreference("auto", "fr-fr")
    ]);

    expect(first.policy).toBe("ask");
    expect(second.policy).toBe("ask");
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    await getTranslationPreference("auto", "fr-fr");
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });

  it("updates the read cache after a preference mutation", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      result: {
        source_language: "auto",
        target_language: "es",
        policy: "always"
      }
    });

    await updateTranslationPreference("auto", "es", "always");
    const preference = await getTranslationPreference("auto", "es");

    expect(preference.policy).toBe("always");
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });
});
