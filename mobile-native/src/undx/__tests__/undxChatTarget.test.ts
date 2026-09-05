/**
 * The two ways into UNDX, and the one way back out.
 *
 * These tests exist because the bug they cover was invisible to every check the
 * repo already had: the types were satisfied, the screen rendered, the chip said
 * the right words, and the member still could not leave UNDX without killing the
 * app. What was wrong was the *shape of the stack*, which nothing asserted.
 */

import { PULSE_AI_CONVERSATION_ID, PULSE_AI_DISPLAY_NAME } from "../../api/messenger";
import { assetReturnTarget, undxChatTarget } from "../undxChatTarget";

describe("undxChatTarget", () => {
  it("sends both entry points to the same conversation", () => {
    // One conversation, two doors. A second copy of these params is how one
    // door quietly acquires a different title or loses the task id.
    const tab = undxChatTarget();
    const drillIn = undxChatTarget({ returnTo: assetReturnTarget({ symbol: "BTC", name: "Bitcoin" })! });
    expect(tab.conversationId).toBe(PULSE_AI_CONVERSATION_ID);
    expect(drillIn.conversationId).toBe(PULSE_AI_CONVERSATION_ID);
    expect(tab.title).toBe(PULSE_AI_DISPLAY_NAME);
    expect(drillIn.title).toBe(tab.title);
  });

  it("omits the return target for the tab entry, so Back is not sent to a screen the member never opened", () => {
    expect(undxChatTarget().undxReturn).toBeUndefined();
    expect(undxChatTarget({ taskId: "t-1" }).undxTaskId).toBe("t-1");
  });

  it("records the originating asset for a contextual drill-in", () => {
    const target = undxChatTarget({ returnTo: assetReturnTarget({ symbol: "btc", name: "Bitcoin" })! });
    expect(target.undxReturn).toEqual({
      screen: "AssetDetail",
      params: { symbol: "BTC", name: "Bitcoin", title: "Bitcoin" }
    });
  });
});

describe("assetReturnTarget", () => {
  it("normalises the symbol, because it is an identity and not a caption", () => {
    expect(assetReturnTarget({ symbol: " eth " })).toEqual({
      screen: "AssetDetail",
      params: { symbol: "ETH" }
    });
  });

  it("returns null rather than a broken destination when there is no asset", () => {
    // A return target that cannot be navigated to is worse than none: the
    // fallback is a working screen, a malformed target is a second dead end.
    expect(assetReturnTarget({ symbol: "" })).toBeNull();
    expect(assetReturnTarget({ symbol: "   ", name: "Bitcoin" })).toBeNull();
  });

  it("is a fixed destination, not a route the caller can choose", () => {
    // Stage 21: the handoff describes a subject. It travels through route
    // state, which is not a place to accept "navigate here" from anyone.
    expect(assetReturnTarget({ symbol: "SOL" })!.screen).toBe("AssetDetail");
  });
});
