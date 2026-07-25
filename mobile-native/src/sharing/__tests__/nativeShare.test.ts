import { Share } from "react-native";
import { buildNativeSharePayload, sharePulseObject } from "../nativeShare";

jest.mock("react-native", () => ({
  Share: {
    share: jest.fn(async () => ({ action: "sharedAction" }))
  }
}));

describe("native PulseSoc sharing", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("builds a readable metadata-rich payload with the canonical link", () => {
    expect(buildNativeSharePayload({
      kind: "post",
      url: "https://pulsesoc.com/pulse/post/42",
      title: "  Product launch  ",
      author: "Ada   Lovelace",
      description: "A production update for everyone."
    })).toEqual({
      title: "Product launch",
      message: "Product launch\nBy Ada Lovelace\nA production update for everyone.\nhttps://pulsesoc.com/pulse/post/42",
      url: "https://pulsesoc.com/pulse/post/42"
    });
  });

  it("uses an object-specific fallback title and opens the OS share sheet", async () => {
    await sharePulseObject({
      kind: "status",
      url: "https://pulsesoc.com/pulse/status/7"
    });

    expect(Share.share).toHaveBeenCalledWith(
      {
        title: "PulseSoc Status",
        message: "PulseSoc Status\nhttps://pulsesoc.com/pulse/status/7",
        url: "https://pulsesoc.com/pulse/status/7"
      },
      {
        dialogTitle: "Share Status",
        subject: "PulseSoc Status"
      }
    );
  });

  it("bounds untrusted metadata before it reaches a native target", () => {
    const payload = buildNativeSharePayload({
      kind: "reel",
      url: "https://pulsesoc.com/pulse/reels/5",
      title: "x".repeat(300),
      description: "y".repeat(500)
    });

    expect(payload.title).toHaveLength(120);
    expect(payload.message).toContain("y".repeat(320));
    expect(payload.message).not.toContain("y".repeat(321));
  });
});

