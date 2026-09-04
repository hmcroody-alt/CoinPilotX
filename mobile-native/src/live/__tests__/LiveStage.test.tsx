/**
 * Stage rendering regression tests.
 *
 * `liveStageLayout` proves the arrangement is correct; this suite proves the
 * component draws that arrangement and does not quietly form a second opinion
 * about it. The failures it is written to catch are the ones that would turn a
 * broadcast back into a conference call: tiles reordered by who is speaking, a
 * host demoted to one cell among equals, and a joining guest rendered as an
 * empty black rectangle that an audience reads as a broken stream.
 */
import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: "Ionicons" }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));

// Identity translator: the assertions below check that a *key* reached the
// translator, which is what keeps hardcoded copy out of the component and the
// i18n gate green.
jest.mock("../../i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

// The Agora surface is a native view. Standing in a plain host element for it
// lets the tree assert *whether* a video surface was mounted, which is the
// difference between a live tile and an avatar placeholder.
jest.mock("../RtcVideoView", () => ({ RtcVideoView: "RtcVideoView" }));

import type { LiveStageParticipant } from "../liveParticipantRegistry";
import { LiveStage } from "../LiveStage";

function person(overrides: Partial<LiveStageParticipant> = {}): LiveStageParticipant {
  const rtcUid = overrides.rtcUid ?? 1;
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

function stage(count: number): LiveStageParticipant[] {
  return Array.from({ length: count }, (_, index) =>
    person({
      rtcUid: index + 1,
      isHost: index === 0,
      role: index === 0 ? "host" : "guest",
      displayName: index === 0 ? "Host Nova" : `Guest ${index}`,
      layoutPosition: index
    })
  );
}

function videoUids(tree: ReturnType<typeof render>): number[] {
  return tree.UNSAFE_getAllByType("RtcVideoView" as any).map((node: any) => Number(node.props.videoTrack?.uid));
}

describe("LiveStage", () => {
  it("mounts one video surface per publisher", () => {
    expect(videoUids(render(<LiveStage participants={stage(4)} />))).toHaveLength(4);
  });

  it("renders names in the order the layout planned, host first", () => {
    const tree = render(<LiveStage participants={stage(3)} />);
    expect(tree.getByText("Host Nova")).toBeTruthy();
    expect(tree.getByText("Guest 1")).toBeTruthy();
    expect(tree.getByText("Guest 2")).toBeTruthy();
  });

  it("labels the host with the host key, not a hardcoded string", () => {
    const tree = render(<LiveStage participants={stage(2)} />);
    expect(tree.getAllByText("extended:live.stage.hostLabel")).toHaveLength(1);
    expect(tree.getAllByText("extended:live.stage.guestLabel")).toHaveLength(1);
  });

  it("shows an avatar placeholder rather than a black tile for a guest still joining", () => {
    const roster = stage(2);
    roster[1] = person({ ...roster[1], phase: "joining", hasVideo: false });
    const tree = render(<LiveStage participants={roster} />);
    // The host still has a surface; the joining guest does not.
    expect(videoUids(tree)).toEqual([1]);
    expect(tree.getByText("extended:live.guest.state.joining")).toBeTruthy();
  });

  it("does not reorder tiles when the active speaker changes", () => {
    // The rule the whole stage rests on: a highlight is a ring, never a move.
    const quiet = stage(4);
    const before = render(<LiveStage participants={quiet} />)
      .UNSAFE_getAllByType("RtcVideoView" as any)
      .map((node: any) => node.props.videoTrack.uid);

    const loud = quiet.map((participant, index) => ({ ...participant, speaking: index === 3 }));
    const after = render(<LiveStage participants={loud} />)
      .UNSAFE_getAllByType("RtcVideoView" as any)
      .map((node: any) => node.props.videoTrack.uid);

    expect(after).toEqual(before);
  });

  it("does not ring a muted participant even if the registry marks them speaking", () => {
    const roster = stage(2).map((participant) => ({ ...participant, speaking: true, audioMuted: true }));
    const tree = render(<LiveStage participants={roster} />);
    const rings = tree.UNSAFE_getAllByType("Ionicons" as any).filter((node: any) => node.props.name === "volume-medium");
    expect(rings).toHaveLength(0);
    expect(tree.UNSAFE_getAllByType("Ionicons" as any).filter((n: any) => n.props.name === "mic-off")).toHaveLength(2);
  });

  it("renders the local publisher with Agora's local uid convention", () => {
    // Agora addresses the local preview as uid 0; passing the real uid renders
    // a remote canvas that will never receive frames.
    const roster = stage(2);
    roster[0] = person({ ...roster[0], isLocal: true, isHost: true, role: "host" });
    expect(videoUids(render(<LiveStage participants={roster} />))[0]).toBe(0);
  });

  it("surfaces overflow rather than silently hiding people", () => {
    expect(render(<LiveStage participants={stage(16)} />).getByText("+3")).toBeTruthy();
  });

  it("falls back to the placeholder on an empty stage", () => {
    const tree = render(<LiveStage participants={[]} placeholder={<></>} />);
    expect(tree.UNSAFE_queryAllByType("RtcVideoView" as any)).toHaveLength(0);
  });

  it("drops people who have left", () => {
    const roster = stage(3);
    roster[2] = person({ ...roster[2], phase: "left" });
    expect(videoUids(render(<LiveStage participants={roster} />))).toHaveLength(2);
  });
});
