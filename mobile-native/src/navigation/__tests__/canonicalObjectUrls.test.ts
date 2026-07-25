import { pulsePostUrl } from "../../api/feed";
import { liveWebUrl } from "../../api/live";
import { reelWebUrl } from "../../api/reels";
import { pulseStatusUrl } from "../../api/status";
import { linking } from "../linking";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

describe("canonical PulseSoc object URLs", () => {
  it("uses native-linkable canonical paths for engagement objects", () => {
    expect(pulsePostUrl(11)).toBe("https://pulsesoc.com/pulse/post/11");
    expect(reelWebUrl(12)).toBe("https://pulsesoc.com/pulse/reels/12");
    expect(pulseStatusUrl(13)).toBe("https://pulsesoc.com/pulse/status/13");
    expect(liveWebUrl(14)).toBe("https://pulsesoc.com/pulse/live/14");
  });

  it("accepts both the custom scheme and production universal-link origin", () => {
    expect(linking.prefixes).toEqual(expect.arrayContaining([
      "pulsesoc://",
      "https://pulsesoc.com"
    ]));
  });
});
