import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

import { LiveChatMessageRow, LiveChatModerationAction } from "../LiveChatOverlay";
import type { PulseLiveChatMessage } from "../../api/live";

function makeMessage(overrides: Partial<PulseLiveChatMessage> = {}): PulseLiveChatMessage {
  return {
    id: 42,
    live_id: 7,
    user_id: 99,
    body: "hello from a viewer",
    display_name: "Ada Viewer",
    message_type: "text",
    moderation_status: "approved",
    pinned: false,
    ...overrides
  };
}

describe("LiveChatMessageRow host moderation", () => {
  it("renders no moderation bar for the ambient compact overlay", () => {
    const onModerate = jest.fn();
    const { queryByLabelText } = render(
      <LiveChatMessageRow message={makeMessage()} compact moderation={{ canModerate: true, onModerate }} />
    );
    expect(queryByLabelText("Pin comment")).toBeNull();
    expect(queryByLabelText("Remove comment")).toBeNull();
    expect(queryByLabelText("Report comment")).toBeNull();
  });

  it("exposes pin, remove and report to a host in the expanded sheet", () => {
    const onModerate = jest.fn();
    const { getByLabelText } = render(
      <LiveChatMessageRow message={makeMessage()} moderation={{ canModerate: true, onModerate }} />
    );

    fireEvent.press(getByLabelText("Pin comment"));
    fireEvent.press(getByLabelText("Remove comment"));
    fireEvent.press(getByLabelText("Report comment"));

    const actions = onModerate.mock.calls.map((call) => call[1] as LiveChatModerationAction);
    expect(actions).toEqual(["pin", "delete", "report"]);
    expect(onModerate.mock.calls[0][0].id).toBe(42);
  });

  it("offers Unpin when the comment is already pinned", () => {
    const onModerate = jest.fn();
    const { getByLabelText, queryByLabelText } = render(
      <LiveChatMessageRow message={makeMessage({ pinned: true })} moderation={{ canModerate: true, onModerate }} />
    );
    expect(queryByLabelText("Pin comment")).toBeNull();
    fireEvent.press(getByLabelText("Unpin comment"));
    expect(onModerate).toHaveBeenCalledWith(expect.objectContaining({ id: 42 }), "unpin");
  });

  it("shows only Report to a non-host viewer (no pin/remove)", () => {
    const onModerate = jest.fn();
    const { getByLabelText, queryByLabelText } = render(
      <LiveChatMessageRow message={makeMessage()} moderation={{ canModerate: false, onModerate }} />
    );
    expect(queryByLabelText("Pin comment")).toBeNull();
    expect(queryByLabelText("Remove comment")).toBeNull();
    fireEvent.press(getByLabelText("Report comment"));
    expect(onModerate).toHaveBeenCalledWith(expect.objectContaining({ id: 42 }), "report");
  });

  it("blocks actions while a moderation call for that message is in flight", () => {
    const onModerate = jest.fn();
    const { getByLabelText } = render(
      <LiveChatMessageRow
        message={makeMessage()}
        moderation={{ canModerate: true, onModerate, busyMessageId: 42 }}
      />
    );
    fireEvent.press(getByLabelText("Remove comment"));
    expect(onModerate).not.toHaveBeenCalled();
  });
});
