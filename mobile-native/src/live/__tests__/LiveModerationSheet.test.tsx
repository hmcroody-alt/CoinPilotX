/**
 * Stage 19 — the moderation sheet, tested as a rendering of the permission
 * module rather than as a menu of its own.
 *
 * `liveMediaOwnership.test.ts` proves the rules. This suite proves the sheet
 * does not add to them, and that the two protections the sheet is responsible
 * for actually hold in the tree:
 *
 *   1. The unmute row is an *ask*, and carries the hint that says so. If this
 *      ever renders as a plain "unmute", a host will believe they opened a
 *      guest's microphone and report the silence as a bug.
 *
 *   2. Removal does not fire on the first tap. The failure this catches is a
 *      guest thrown off stage by a mis-tap during a live broadcast, which is
 *      not recoverable by undo.
 *
 * The translator is the identity function, so every assertion below is on the
 * i18n *key* — which is also what keeps hardcoded copy out of the component and
 * the i18n gate green.
 */
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: "Ionicons" }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

import type { LiveStageParticipant } from "../liveParticipantRegistry";
import type { MediaActor } from "../liveMediaOwnership";
import { LiveModerationSheet } from "../LiveModerationSheet";

function person(overrides: Partial<LiveStageParticipant> = {}): LiveStageParticipant {
  const rtcUid = overrides.rtcUid ?? 2;
  return {
    rtcUid,
    userId: rtcUid,
    guestId: 0,
    key: `uid-${rtcUid}`,
    displayName: `User ${rtcUid}`,
    avatarUrl: "",
    role: "guest",
    roleLabel: "Guest",
    phase: "live",
    isLocal: false,
    isHost: false,
    hasVideo: true,
    hasAudio: true,
    audioMuted: false,
    speaking: false,
    layoutPosition: 0,
    unidentified: false,
    ...overrides
  };
}

const host: MediaActor = { key: "uid-1", role: "host", isHost: true };
const cohost: MediaActor = { key: "uid-9", role: "cohost", isHost: false };
const guest: MediaActor = { key: "uid-2", role: "guest", isHost: false };
const viewer: MediaActor = { key: "uid-7", role: "audience", isHost: false };

function open(actor: MediaActor, target: LiveStageParticipant | null, onCommand = jest.fn()) {
  const view = render(
    <LiveModerationSheet visible onClose={jest.fn()} actor={actor} target={target} onCommand={onCommand} />
  );
  return { ...view, onCommand };
}

describe("what the sheet offers", () => {
  it("offers mute and remove for a guest who is speaking", () => {
    const { queryByLabelText } = open(host, person());
    expect(queryByLabelText("extended:live.moderation.mute")).not.toBeNull();
    expect(queryByLabelText("extended:live.moderation.remove")).not.toBeNull();
    expect(queryByLabelText("extended:live.moderation.askToUnmute")).toBeNull();
  });

  it("offers an ask-to-unmute, never an unmute, for a muted guest", () => {
    const { queryByLabelText, queryByText } = open(host, person({ audioMuted: true }));
    expect(queryByLabelText("extended:live.moderation.askToUnmute")).not.toBeNull();
    expect(queryByLabelText("extended:live.moderation.mute")).toBeNull();
    // The hint is the part that stops a host reading this as a switch.
    expect(queryByText("extended:live.moderation.unmuteHint")).not.toBeNull();
  });

  it("gives a co-host the same actions over a guest as the host has", () => {
    const { queryByLabelText } = open(cohost, person());
    expect(queryByLabelText("extended:live.moderation.mute")).not.toBeNull();
    expect(queryByLabelText("extended:live.moderation.remove")).not.toBeNull();
  });
});

describe("what the sheet refuses to offer", () => {
  it("gives a guest nothing to do to another guest", () => {
    const { queryByLabelText, queryByText } = open(guest, person({ rtcUid: 3 }));
    expect(queryByLabelText("extended:live.moderation.mute")).toBeNull();
    expect(queryByLabelText("extended:live.moderation.remove")).toBeNull();
    expect(queryByText("extended:live.moderation.noActions")).not.toBeNull();
  });

  it("gives an audience member nothing at all", () => {
    const { queryByText } = open(viewer, person());
    expect(queryByText("extended:live.moderation.noActions")).not.toBeNull();
  });

  it("does not let a co-host mute or remove the host", () => {
    // A co-host who could silence the host could take the broadcast.
    const { queryByLabelText, queryByText } = open(cohost, person({ rtcUid: 1, isHost: true, role: "host" }));
    expect(queryByLabelText("extended:live.moderation.mute")).toBeNull();
    expect(queryByLabelText("extended:live.moderation.remove")).toBeNull();
    expect(queryByText("extended:live.moderation.noActions")).not.toBeNull();
  });

  it("offers nothing against someone who is only watching", () => {
    const { queryByText } = open(host, person({ role: "audience", phase: "live" }));
    expect(queryByText("extended:live.moderation.noActions")).not.toBeNull();
  });

  it("renders no options and does not crash when there is no target", () => {
    const { queryByText } = open(host, null);
    expect(queryByText("extended:live.moderation.noActions")).not.toBeNull();
  });
});

describe("acting on an option", () => {
  it("sends a non-destructive command on the first tap", () => {
    const { getByLabelText, onCommand } = open(host, person());
    fireEvent.press(getByLabelText("extended:live.moderation.mute"));
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(onCommand.mock.calls[0][0]).toBe("mute");
    expect(onCommand.mock.calls[0][1].key).toBe("uid-2");
  });

  it("sends unmute as its own command, so the caller asks rather than switches", () => {
    const { getByLabelText, onCommand } = open(host, person({ audioMuted: true }));
    fireEvent.press(getByLabelText("extended:live.moderation.askToUnmute"));
    expect(onCommand.mock.calls[0][0]).toBe("unmute");
  });

  it("does not remove anyone on the first tap", () => {
    const { getByLabelText, onCommand, queryByText } = open(host, person());
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    expect(onCommand).not.toHaveBeenCalled();
    // The row turns into a confirmation instead of doing nothing visible.
    expect(queryByText("extended:live.moderation.confirmRemove")).not.toBeNull();
    expect(queryByText("extended:live.moderation.removeConfirm")).not.toBeNull();
  });

  it("removes on the second tap", () => {
    const { getByLabelText, onCommand } = open(host, person());
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(onCommand.mock.calls[0][0]).toBe("remove");
  });

  it("lets the host back out of a pending removal", () => {
    const { getByLabelText, getByText, onCommand, queryByText } = open(host, person());
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    fireEvent.press(getByText("extended:live.moderation.cancel"));
    expect(queryByText("extended:live.moderation.confirmRemove")).toBeNull();
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("does not let a pending removal bleed onto the mute row", () => {
    // The confirmation is held per-command precisely so that arming remove and
    // then tapping mute performs a mute, not a removal.
    const { getByLabelText, onCommand } = open(host, person());
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    fireEvent.press(getByLabelText("extended:live.moderation.mute"));
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(onCommand.mock.calls[0][0]).toBe("mute");
  });

  it("clears a pending removal when the sheet is closed", () => {
    const onClose = jest.fn();
    const onCommand = jest.fn();
    const target = person();
    const { getByLabelText, getByText, queryByText, rerender } = render(
      <LiveModerationSheet visible onClose={onClose} actor={host} target={target} onCommand={onCommand} />
    );
    fireEvent.press(getByLabelText("extended:live.moderation.remove"));
    expect(queryByText("extended:live.moderation.confirmRemove")).not.toBeNull();

    // Dismissing the sheet must disarm it; a confirmation that survives a close
    // turns the next visit's first tap into a removal.
    fireEvent.press(getByLabelText("Dismiss"));
    rerender(
      <LiveModerationSheet visible onClose={onClose} actor={host} target={target} onCommand={onCommand} />
    );
    expect(onClose).toHaveBeenCalled();
    expect(queryByText("extended:live.moderation.confirmRemove")).toBeNull();
    expect(onCommand).not.toHaveBeenCalled();
  });
});

describe("who is being managed", () => {
  it("shows the target's name and role so the host knows who they are acting on", () => {
    const { queryByText } = open(host, person({ displayName: "Ada", roleLabel: "Co-host" }));
    expect(queryByText("Ada")).not.toBeNull();
    expect(queryByText("Co-host")).not.toBeNull();
  });

  it("falls back to an initial when the target has no avatar", () => {
    const { queryByText } = open(host, person({ displayName: "ada", avatarUrl: "" }));
    expect(queryByText("A")).not.toBeNull();
  });
});
