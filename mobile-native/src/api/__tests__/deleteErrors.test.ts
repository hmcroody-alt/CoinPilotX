jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import { describeDeleteError } from "../deleteErrors";
import { PulseApiError } from "../pulseApi";

describe("describeDeleteError", () => {
  it("maps 401 to a re-auth message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 401), "Post")).toMatch(/session expired/i);
  });

  it("maps 403 to an ownership message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 403), "Post")).toMatch(/only delete your own posts/i);
    expect(describeDeleteError(new PulseApiError("nope", 403), "Reel")).toMatch(/only delete your own reels/i);
  });

  it("maps 404 to an already-removed message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 404), "Post")).toMatch(/already removed/i);
  });

  it("maps 409 to a conflict/refresh message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 409), "Reel")).toMatch(/changed elsewhere/i);
  });

  it("maps 422 to the server-provided message when present", () => {
    expect(describeDeleteError(new PulseApiError("Validation failed.", 422), "Post")).toBe("Validation failed.");
  });

  it("maps 429 to a rate-limit message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 429), "Post")).toMatch(/too many attempts/i);
  });

  it("maps 5xx to a retry-later message", () => {
    expect(describeDeleteError(new PulseApiError("nope", 500), "Post")).toMatch(/could not be deleted right now/i);
    expect(describeDeleteError(new PulseApiError("nope", 503), "Reel")).toMatch(/could not be deleted right now/i);
  });

  it("falls back to the server message for other statuses", () => {
    expect(describeDeleteError(new PulseApiError("Odd status.", 418), "Post")).toBe("Odd status.");
  });

  it("maps network/offline errors to an offline message", () => {
    expect(describeDeleteError(new TypeError("Network request failed"), "Post")).toMatch(/offline/i);
    expect(describeDeleteError(new Error("network error"), "Reel")).toMatch(/offline/i);
  });

  it("falls back to a generic message for unknown errors", () => {
    expect(describeDeleteError("boom", "Post")).toBe("Post could not be deleted.");
    expect(describeDeleteError(new Error("Something broke"), "Reel")).toBe("Something broke");
  });
});
