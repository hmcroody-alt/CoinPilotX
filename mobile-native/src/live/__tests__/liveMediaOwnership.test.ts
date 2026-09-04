import type { LiveStageParticipant } from "../liveParticipantRegistry";
import {
  activatesTargetCaptureWithoutConsent,
  applyMediaCommandLocally,
  canLeaveStage,
  canModerate,
  guestActionEndpointVerb,
  moderationOptionsFor,
  resolveMediaCommand,
  type MediaActor,
  type MediaCommand,
  type MediaKind
} from "../liveMediaOwnership";

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
    layoutPosition: 1,
    unidentified: false,
    ...overrides
  };
}

const host: MediaActor = { key: "uid-1", role: "host", isHost: true };
const cohost: MediaActor = { key: "uid-9", role: "cohost", isHost: false };
const guest: MediaActor = { key: "uid-2", role: "guest", isHost: false };
const viewer: MediaActor = { key: "uid-50", role: "audience", isHost: false };

const guestTarget = person({ rtcUid: 2 });
const hostTarget = person({ rtcUid: 1, key: "uid-1", role: "host", isHost: true });

describe("who may moderate", () => {
  it("allows the host and a co-host, and nobody else", () => {
    expect([host, cohost, guest, viewer].map((actor) => canModerate(actor))).toEqual([true, true, false, false]);
  });

  it("refuses a guest trying to mute another guest", () => {
    expect(resolveMediaCommand(guest, person({ rtcUid: 3 }), "mute")).toMatchObject({
      effect: "denied",
      code: "LIVE_MODERATION_FORBIDDEN"
    });
  });

  it("refuses an audience member outright", () => {
    expect(resolveMediaCommand(viewer, guestTarget, "remove")).toMatchObject({ effect: "denied" });
  });

  it("will not let a co-host mute or remove the host", () => {
    // A co-host who could mute the host could take the broadcast from them.
    expect(resolveMediaCommand(cohost, hostTarget, "mute")).toMatchObject({
      effect: "denied",
      code: "LIVE_CANNOT_MODERATE_HOST"
    });
    expect(resolveMediaCommand(cohost, hostTarget, "remove")).toMatchObject({ effect: "denied" });
  });

  it("refuses to act on somebody who is only watching", () => {
    expect(resolveMediaCommand(host, person({ rtcUid: 60, role: "audience" }), "mute")).toMatchObject({
      effect: "denied",
      code: "LIVE_TARGET_NOT_ON_STAGE"
    });
  });
});

describe("the mute / unmute asymmetry", () => {
  const kinds: MediaKind[] = ["microphone", "camera"];

  it("mutes immediately and without consent, because muting is a safety action", () => {
    for (const kind of kinds) {
      expect(resolveMediaCommand(host, guestTarget, "mute", kind)).toEqual({
        effect: "enforced",
        kind,
        targetKey: "uid-2"
      });
      expect(applyMediaCommandLocally(resolveMediaCommand(host, guestTarget, "mute", kind))).toEqual({
        setPublishing: false,
        permitted: false,
        prompt: false
      });
    }
  });

  it("treats unmute as a grant the guest must accept, never as a switch", () => {
    for (const kind of kinds) {
      expect(resolveMediaCommand(host, guestTarget, "unmute", kind)).toEqual({
        effect: "requested",
        kind,
        targetKey: "uid-2",
        requiresTargetConsent: true
      });
      expect(applyMediaCommandLocally(resolveMediaCommand(host, guestTarget, "unmute", kind))).toEqual({
        setPublishing: null,
        permitted: true,
        prompt: true
      });
    }
  });

  it("NEVER activates a target's camera or microphone without consent, for any actor or command", () => {
    // The single sentence this module defends. A host who could turn on a
    // guest's mic has a remote listening device, whatever the UI calls it.
    const actors = [host, cohost, guest, viewer];
    const targets = [guestTarget, hostTarget, person({ rtcUid: 3, role: "cohost" })];
    const commands: MediaCommand[] = ["mute", "unmute", "remove"];
    for (const actor of actors) {
      for (const target of targets) {
        for (const command of commands) {
          for (const kind of kinds) {
            expect(activatesTargetCaptureWithoutConsent(actor, target, command, kind)).toBe(false);
          }
        }
      }
    }
  });

  it("takes a removed guest off air as well as off the stage", () => {
    expect(applyMediaCommandLocally(resolveMediaCommand(host, guestTarget, "remove"))).toEqual({
      setPublishing: false,
      permitted: false,
      prompt: false
    });
  });

  it("leaves capture untouched when the command was denied", () => {
    expect(applyMediaCommandLocally(resolveMediaCommand(guest, guestTarget, "unmute"))).toEqual({
      setPublishing: null,
      permitted: false,
      prompt: false
    });
  });
});

describe("moderation menu", () => {
  it("offers nothing to someone who cannot moderate", () => {
    expect(moderationOptionsFor(guest, person({ rtcUid: 3 }))).toEqual([]);
    expect(moderationOptionsFor(viewer, guestTarget)).toEqual([]);
  });

  it("offers mute for an unmuted guest and ask-to-unmute for a muted one", () => {
    expect(moderationOptionsFor(host, guestTarget)[0]).toMatchObject({ command: "mute" });
    expect(moderationOptionsFor(host, person({ ...guestTarget, audioMuted: true }))[0]).toMatchObject({
      command: "unmute",
      // Wording is the guest's protection made visible: the host is asking.
      labelKey: "extended:live.moderation.askToUnmute"
    });
  });

  it("marks removal destructive so the UI confirms before it fires", () => {
    const remove = moderationOptionsFor(host, guestTarget).find((option) => option.command === "remove");
    expect(remove).toMatchObject({ destructive: true });
  });

  it("emits i18n keys rather than copy, so the gate can see them", () => {
    for (const option of moderationOptionsFor(host, guestTarget)) {
      expect(option.labelKey).toMatch(/^extended:live\.moderation\./);
    }
  });

  it("offers a co-host the same actions on a guest as the host", () => {
    expect(moderationOptionsFor(cohost, guestTarget).map((option) => option.command)).toEqual(
      moderationOptionsFor(host, guestTarget).map((option) => option.command)
    );
  });

  it("offers nothing on the host", () => {
    expect(moderationOptionsFor(cohost, hostTarget)).toEqual([]);
  });
});

describe("backend contract", () => {
  it("maps every command to a verb the guest-action route accepts", () => {
    const accepted = new Set(["mute", "unmute", "remove"]);
    for (const command of ["mute", "unmute", "remove"] as MediaCommand[]) {
      expect(accepted.has(guestActionEndpointVerb(command))).toBe(true);
    }
  });

  it("keeps leaving self-only, the way the server does", () => {
    // `leave` and `remove` are distinct on the server and must stay distinct
    // here: a moderation log that cannot tell them apart is not a log.
    expect(canLeaveStage(guest, guestTarget)).toBe(true);
    expect(canLeaveStage(host, guestTarget)).toBe(false);
    expect(canLeaveStage(viewer, person({ rtcUid: 50, key: "uid-50", role: "audience" }))).toBe(false);
  });
});
