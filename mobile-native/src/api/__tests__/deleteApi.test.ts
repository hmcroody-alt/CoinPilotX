const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

import { deletePost } from "../feed";
import { deleteReel } from "../reels";

describe("deletePost", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
  });

  it("calls DELETE on the existing production post route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Post deleted.", post_id: 123 });
    const result = await deletePost(123);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/posts/123", { method: "DELETE" });
    expect(result.ok).toBe(true);
    expect(result.post_id).toBe(123);
  });

  it("propagates errors from the API client", async () => {
    mockPulseApi.mockRejectedValue(new Error("boom"));
    await expect(deletePost(123)).rejects.toThrow("boom");
  });
});

describe("deleteReel", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
  });

  it("calls DELETE on the existing production reel route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Reel deleted.", reel_id: 456, trace_id: "abc" });
    const result = await deleteReel(456);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/reels/456", { method: "DELETE" });
    expect(result.ok).toBe(true);
    expect(result.reel_id).toBe(456);
  });

  it("propagates errors from the API client", async () => {
    mockPulseApi.mockRejectedValue(new Error("boom"));
    await expect(deleteReel(456)).rejects.toThrow("boom");
  });
});
