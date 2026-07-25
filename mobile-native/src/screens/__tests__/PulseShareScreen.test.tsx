import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockSetString = jest.fn();
const mockSearchUsers = jest.fn();
const mockOpenConversation = jest.fn();
const mockSendMessage = jest.fn();
const mockOpenSystemShare = jest.fn();
const mockSaveComposerHandoff = jest.fn();

jest.mock("expo-clipboard", () => ({
  setStringAsync: (...args: unknown[]) => mockSetString(...args)
}));

jest.mock("react-native-qrcode-svg", () => ({
  __esModule: true,
  default: () => null
}));

jest.mock("../../api/messenger", () => ({
  searchMessengerUsers: (...args: unknown[]) => mockSearchUsers(...args),
  openDirectConversation: (...args: unknown[]) => mockOpenConversation(...args),
  sendConversationMessage: (...args: unknown[]) => mockSendMessage(...args)
}));

jest.mock("../../sharing/nativeShare", () => {
  const actual = jest.requireActual("../../sharing/nativeShare");
  return {
    ...actual,
    openSystemShare: (...args: unknown[]) => mockOpenSystemShare(...args)
  };
});

jest.mock("../../sharing/shareComposerHandoff", () => ({
  saveShareComposerHandoff: (...args: unknown[]) => mockSaveComposerHandoff(...args)
}));

import { PulseShareScreen } from "../PulseShareScreen";

const metadata = {
  kind: "post" as const,
  url: "https://pulsesoc.com/pulse/post/42",
  title: "Launch update",
  author: "Ada",
  description: "A canonical PulseSoc post."
};

function renderScreen() {
  const navigation = { goBack: jest.fn(), navigate: jest.fn() };
  const view = render(
    <PulseShareScreen
      route={{ key: "share", name: "PulseShare", params: metadata } as never}
      navigation={navigation as never}
    />
  );
  return { ...view, navigation };
}

describe("PulseSoc native share center", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    mockSetString.mockResolvedValue(undefined);
    mockSearchUsers.mockResolvedValue([{
      user_id: 7,
      display_name: "Grace Hopper",
      public_pulse_id: "@grace",
      avatar_url: ""
    }]);
    mockOpenConversation.mockResolvedValue({ conversation_id: 91 });
    mockSendMessage.mockResolvedValue({ ok: true, message_id: 4 });
    mockOpenSystemShare.mockResolvedValue({ action: "sharedAction" });
    mockSaveComposerHandoff.mockResolvedValue({ id: "share-handoff-1" });
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("copies the canonical link and renders an offline QR payload", async () => {
    const view = renderScreen();
    await act(async () => {
      fireEvent.press(view.getByText("Copy link"));
    });
    expect(mockSetString).toHaveBeenCalledWith(metadata.url);
    expect(view.getByText("Link copied.")).toBeTruthy();

    fireEvent.press(view.getByText("QR code"));
    expect(view.getByTestId("pulse-share-qr")).toBeTruthy();
  });

  it("sends metadata and the canonical link through the existing Messenger backend", async () => {
    const view = renderScreen();
    fireEvent.press(view.getByText("Send in PulseSoc"));
    fireEvent.changeText(view.getByLabelText("Search PulseSoc recipients"), "Grace");
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    const recipient = await view.findByLabelText("Send to Grace Hopper");

    await act(async () => {
      fireEvent.press(recipient);
    });

    expect(mockOpenConversation).toHaveBeenCalledWith(expect.objectContaining({ user_id: 7 }));
    expect(mockSendMessage).toHaveBeenCalledWith(91, expect.objectContaining({
      body: expect.stringContaining(metadata.url),
      message_type: "text",
      client_message_id: expect.stringContaining("native-share-post-7")
    }));
    await waitFor(() => expect(view.getByText("Sent to Grace Hopper.")).toBeTruthy());
  });

  it("reuses the same Messenger client id when a send is retried", async () => {
    mockSendMessage.mockRejectedValueOnce(new Error("Temporary delivery interruption."));
    const view = renderScreen();
    fireEvent.press(view.getByText("Send in PulseSoc"));
    fireEvent.changeText(view.getByLabelText("Search PulseSoc recipients"), "Grace");
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    const recipient = await view.findByLabelText("Send to Grace Hopper");

    await act(async () => {
      fireEvent.press(recipient);
    });
    await waitFor(() => expect(view.getByText("Temporary delivery interruption.")).toBeTruthy());
    const firstClientId = mockSendMessage.mock.calls[0][1].client_message_id;

    await act(async () => {
      fireEvent.press(recipient);
    });
    const secondClientId = mockSendMessage.mock.calls[1][1].client_message_id;
    expect(secondClientId).toBe(firstClientId);
  });

  it("keeps OS destinations available through More apps", async () => {
    const view = renderScreen();
    await act(async () => {
      fireEvent.press(view.getByText("More apps"));
    });
    expect(mockOpenSystemShare).toHaveBeenCalledWith(metadata);
  });

  it("opens Status / Story and Reel as reviewable internal composer drafts", async () => {
    const view = renderScreen();
    await act(async () => {
      fireEvent.press(view.getByText("Status / Story"));
    });
    expect(mockSaveComposerHandoff).toHaveBeenCalledWith(expect.objectContaining({
      mode: "status",
      url: metadata.url,
      kind: metadata.kind
    }));
    expect(view.navigation.navigate).toHaveBeenCalledWith("Tabs", {
      screen: "Home",
      params: {
        openComposer: true,
        composerMode: "status",
        shareHandoffNonce: "share-handoff-1"
      }
    });

    mockSaveComposerHandoff.mockResolvedValueOnce({ id: "share-handoff-2" });
    await act(async () => {
      fireEvent.press(view.getByText("Create Reel"));
    });
    expect(view.navigation.navigate).toHaveBeenLastCalledWith("Tabs", {
      screen: "Home",
      params: {
        openComposer: true,
        composerMode: "reel",
        shareHandoffNonce: "share-handoff-2"
      }
    });
  });
});
