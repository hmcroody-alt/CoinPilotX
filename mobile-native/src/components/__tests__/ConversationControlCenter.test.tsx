import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetConversationControlCenter = jest.fn();
const mockUpdateConversationControlSetting = jest.fn();
const mockListConversationMembers = jest.fn();

jest.mock("../../api/messenger", () => ({
  PULSE_AI_CONVERSATION_ID: -101,
  PULSE_AI_DISPLAY_NAME: "UNDX",
  getConversationControlCenter: (...args: unknown[]) => mockGetConversationControlCenter(...args),
  updateConversationControlSetting: (...args: unknown[]) => mockUpdateConversationControlSetting(...args),
  listConversationMembers: (...args: unknown[]) => mockListConversationMembers(...args),
  listConversationControlMedia: jest.fn(),
  listConversationControlLinks: jest.fn(),
  listConversationPinnedMessages: jest.fn(),
  exportConversationControlData: jest.fn(),
  runConversationControlAction: jest.fn(),
  muteConversation: jest.fn(),
  archiveConversation: jest.fn(),
  markConversationUnread: jest.fn(),
  pinConversation: jest.fn(),
  searchConversationMessages: jest.fn()
}));

jest.mock("../PulseCommand", () => ({
  PulseCommandPanel: ({ children }: { children: React.ReactNode }) => children
}));

import { ConversationControlCenter } from "../ConversationControlCenter";

const controlData = {
  ok: true,
  conversation: { id: 42, conversation_id: 42, title: "Maria Cherie", conversation_type: "direct", member_count: 2 },
  stats: { messages: 12, media_files: 3, members: 2, connection: "Connected", security_label: "Secured session" },
  settings: { appearance: { theme: "deep_space" } },
  capabilities: { search: true, members: true, shared_media: true, message_stats: true, pin: true, archive: true, mark_unread: true, mute: true, report: true, block: true, voice_call: true, video_call: true, export_chat: true }
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGetConversationControlCenter.mockResolvedValue(controlData);
  mockListConversationMembers.mockResolvedValue([{ user_id: 2, display_name: "Maria Cherie", role: "member", presence: "offline" }]);
});

function renderCenter() {
  return render(
    <ConversationControlCenter
      visible
      conversationId={42}
      title="Maria Cherie"
      messages={[]}
      onClose={jest.fn()}
      onOpenSafety={jest.fn()}
      onStartCall={jest.fn()}
    />
  );
}

describe("ConversationControlCenter", () => {
  it("loads server-authorized data and opens member details without a stuck busy state", async () => {
    const view = renderCenter();
    await waitFor(() => expect(view.getByText("12 server-visible messages")).toBeTruthy());
    expect(view.getByLabelText("View Members").props.accessibilityState.busy).toBe(false);

    fireEvent.press(view.getByText("View Members"));
    await waitFor(() => expect(view.getByText("Maria Cherie · Member · Offline")).toBeTruthy());
    expect(mockListConversationMembers).toHaveBeenCalledWith(42);
    expect(view.queryAllByTestId("activity-indicator")).toHaveLength(0);
  });

  it("clears a failed setting busy state and shows the real server error", async () => {
    mockUpdateConversationControlSetting.mockRejectedValueOnce(new Error("Preference service unavailable."));
    const view = renderCenter();
    await waitFor(() => expect(view.getByText("Appearance")).toBeTruthy());

    fireEvent.press(view.getByText("Appearance"));
    fireEvent.press(view.getByText("Theme"));

    await waitFor(() => expect(view.getByText("Preference service unavailable.")).toBeTruthy());
    expect(mockUpdateConversationControlSetting).toHaveBeenCalledWith(42, "appearance", "theme", "nebula");
    expect(view.getByText("Deep Space")).toBeTruthy();
  });

  it("filters settings without dispatching a server mutation", async () => {
    const view = renderCenter();
    await waitFor(() => expect(view.getByLabelText("Search conversation settings")).toBeTruthy());

    fireEvent.changeText(view.getByLabelText("Search conversation settings"), "danger");
    expect(view.getByText("Danger Zone")).toBeTruthy();
    expect(view.queryByText("Appearance")).toBeNull();
    expect(mockUpdateConversationControlSetting).not.toHaveBeenCalled();
  });
});
