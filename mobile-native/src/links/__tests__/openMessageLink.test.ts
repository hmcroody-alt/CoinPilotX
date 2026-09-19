/**
 * Which of the three doors a tapped link goes through.
 *
 * The navigation assertions check that the app's *existing* router is what
 * runs — `openNativeRoute` resolving `/pulse/post/2432` to `PostDetail` is the
 * same resolution a push notification and a Universal Link get. If these ever
 * had to be updated independently of `navigation/__tests__/routeResolution`,
 * that would be the signal that a second deep-link system had appeared.
 */

import { openMessageLink } from "../openMessageLink";

function harness() {
  const navigate = jest.fn();
  const openExternal = jest.fn().mockResolvedValue(true);
  return { navigation: { navigate }, navigate, openExternal };
}

describe("openMessageLink", () => {
  it("opens a PulseSoc post in the app, not the browser", () => {
    const { navigation, navigate, openExternal } = harness();
    const outcome = openMessageLink(navigation, "https://pulsesoc.com/pulse/post/2432", { openExternal });
    expect(outcome).toBe("internal");
    expect(navigate).toHaveBeenCalledWith("PostDetail", expect.objectContaining({ postId: 2432 }));
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("opens the site root on the Home tab", () => {
    const { navigation, navigate } = harness();
    expect(openMessageLink(navigation, "https://pulsesoc.com/")).toBe("internal");
    expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Home" });
  });

  it("opens a conversation link on the Chat screen", () => {
    const { navigation, navigate } = harness();
    openMessageLink(navigation, "https://pulsesoc.com/pulse/messages/88");
    expect(navigate).toHaveBeenCalledWith("Chat", expect.objectContaining({ conversationId: 88 }));
  });

  it("opens a reel link on the reel", () => {
    const { navigation, navigate } = harness();
    openMessageLink(navigation, "https://pulsesoc.com/pulse/reels/12");
    expect(navigate).toHaveBeenCalledWith("ReelDetail", expect.objectContaining({ reelId: 12 }));
  });

  it("opens a profile link on the profile", () => {
    const { navigation, navigate } = harness();
    openMessageLink(navigation, "https://pulsesoc.com/pulse/profile/pilot");
    expect(navigate).toHaveBeenCalledWith("ProfileDetail", expect.anything());
  });

  it("hands an external link to the OS and never to the navigator", () => {
    const { navigation, navigate, openExternal } = harness();
    const outcome = openMessageLink(navigation, "https://apple.com", { openExternal });
    expect(outcome).toBe("external");
    expect(openExternal).toHaveBeenCalledWith("https://apple.com/");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("hands an invite link to the OS so deferred attribution still works", () => {
    const { navigation, navigate, openExternal } = harness();
    expect(openMessageLink(navigation, "https://pulsesoc.com/r/ab12cd", { openExternal })).toBe("external");
    expect(openExternal).toHaveBeenCalledWith("https://pulsesoc.com/r/ab12cd");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does nothing at all for a blocked scheme", () => {
    const { navigation, navigate, openExternal } = harness();
    ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd", "ftp://x.com"].forEach((url) => {
      expect(openMessageLink(navigation, url, { openExternal })).toBe("blocked");
    });
    expect(navigate).not.toHaveBeenCalled();
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("sends a lookalike host to the browser rather than into the app", () => {
    const { navigation, navigate, openExternal } = harness();
    openMessageLink(navigation, "https://pulsesoc.com.evil.net/pulse/post/1", { openExternal });
    expect(navigate).not.toHaveBeenCalled();
    expect(openExternal).toHaveBeenCalledWith("https://pulsesoc.com.evil.net/pulse/post/1");
  });

  it("survives an opener that rejects", async () => {
    const { navigation } = harness();
    const openExternal = jest.fn().mockRejectedValue(new Error("no handler"));
    expect(openMessageLink(navigation, "https://apple.com", { openExternal })).toBe("external");
    await Promise.resolve();
  });

  it("survives a navigator that throws", () => {
    const navigate = jest.fn(() => {
      throw new Error("navigator not ready");
    });
    expect(openMessageLink({ navigate }, "https://pulsesoc.com/pulse/post/1")).toBe("internal");
  });
});
