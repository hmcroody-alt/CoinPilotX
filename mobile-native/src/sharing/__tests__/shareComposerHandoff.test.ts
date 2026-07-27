jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  consumeShareComposerHandoff,
  mergeShareIntoComposerBody,
  saveShareComposerHandoff
} from "../shareComposerHandoff";

beforeEach(async () => {
  await AsyncStorage.clear();
});

describe("PulseSoc internal share composer handoff", () => {
  it("is bounded, destination-specific, and consumed exactly once", async () => {
    const saved = await saveShareComposerHandoff({
      mode: "status",
      body: `Launch update\n${"x".repeat(4000)}`,
      url: "https://pulsesoc.com/pulse/post/42",
      kind: "post"
    });
    const consumed = await consumeShareComposerHandoff(saved.id);
    expect(consumed).toEqual(expect.objectContaining({
      id: saved.id,
      mode: "status",
      kind: "post",
      url: "https://pulsesoc.com/pulse/post/42"
    }));
    expect(consumed?.body.length).toBeLessThanOrEqual(3000);
    await expect(consumeShareComposerHandoff(saved.id)).resolves.toBeNull();
  });

  it("does not consume a different navigation handoff", async () => {
    const saved = await saveShareComposerHandoff({
      mode: "reel",
      body: "Use this link in a Reel",
      url: "https://pulsesoc.com/pulse/reels/9",
      kind: "reel"
    });
    await expect(consumeShareComposerHandoff("another-handoff")).resolves.toBeNull();
    await expect(consumeShareComposerHandoff(saved.id)).resolves.toEqual(expect.objectContaining({ id: saved.id }));
  });

  it("preserves an existing draft and avoids duplicate insertion", () => {
    const shared = "Launch update\nhttps://pulsesoc.com/pulse/post/42";
    expect(mergeShareIntoComposerBody("", shared)).toBe(shared);
    expect(mergeShareIntoComposerBody("My intro", shared)).toBe(`My intro\n\n${shared}`);
    expect(mergeShareIntoComposerBody(`My intro\n\n${shared}`, shared)).toBe(`My intro\n\n${shared}`);
  });
});
