const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return {
    pulseApi: (...args: unknown[]) => mockPulseApi(...args),
    PulseApiError
  };
});

import { dismissWelcome, fetchWelcomeState } from "../welcome";
import { PulseApiError } from "../pulseApi";

describe("fetchWelcomeState", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("maps an eligible welcome payload into camelCase state", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      should_show: true,
      welcome_type: "welcome_back",
      event_id: 42,
      name: "Nova",
      title: "Welcome back",
      body: "The galaxy missed you",
      subtext: "Let's go",
      cta: "Enter",
      animation: "ufo",
      app_version: "2026.07",
      settings: {
        welcome_experience: true,
        welcome_sound: false,
        welcome_haptics: true,
        reduced_motion: "system"
      }
    });
    const state = await fetchWelcomeState();
    expect(state).toEqual({
      shouldShow: true,
      welcomeType: "welcome_back",
      eventId: 42,
      name: "Nova",
      title: "Welcome back",
      body: "The galaxy missed you",
      subtext: "Let's go",
      cta: "Enter",
      animation: "ufo",
      appVersion: "2026.07",
      settings: {
        welcomeExperience: true,
        welcomeSound: false,
        welcomeHaptics: true,
        reducedMotion: "system"
      }
    });
  });

  it("returns shouldShow=false when the backend suppresses the welcome", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, should_show: false, reason: "cooldown_active" });
    const state = await fetchWelcomeState();
    expect(state).toEqual({ shouldShow: false, reason: "cooldown_active" });
  });

  it("coerces an unknown welcome type to generic", async () => {
    mockPulseApi.mockResolvedValueOnce({ should_show: true, welcome_type: "mystery", event_id: 1 });
    const state = await fetchWelcomeState();
    expect(state.shouldShow).toBe(true);
    if (state.shouldShow) expect(state.welcomeType).toBe("generic");
  });

  it("treats a 401 as unauthenticated rather than throwing", async () => {
    mockPulseApi.mockRejectedValueOnce(new PulseApiError("nope", 401));
    const state = await fetchWelcomeState();
    expect(state).toEqual({ shouldShow: false, reason: "unauthenticated" });
  });

  it("swallows transport failures and reports unavailable", async () => {
    mockPulseApi.mockRejectedValueOnce(new Error("boom"));
    const state = await fetchWelcomeState();
    expect(state).toEqual({ shouldShow: false, reason: "welcome_unavailable" });
  });

  it("defaults haptics on and sound off when settings are omitted", async () => {
    mockPulseApi.mockResolvedValueOnce({ should_show: true, welcome_type: "first_login", event_id: 5 });
    const state = await fetchWelcomeState();
    if (!state.shouldShow) throw new Error("expected visible welcome");
    expect(state.settings).toEqual({
      welcomeExperience: true,
      welcomeSound: false,
      welcomeHaptics: true,
      reducedMotion: "system"
    });
  });
});

describe("dismissWelcome", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("posts the welcome type and event id", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, dismissed: true });
    const ok = await dismissWelcome("welcome_back", 42);
    expect(ok).toBe(true);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/welcome-dismiss", {
      method: "POST",
      body: JSON.stringify({ welcome_type: "welcome_back", event_id: 42 })
    });
  });

  it("returns false when the request fails", async () => {
    mockPulseApi.mockRejectedValueOnce(new Error("network"));
    expect(await dismissWelcome("first_login", 1)).toBe(false);
  });
});
