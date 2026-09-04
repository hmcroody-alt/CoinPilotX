/**
 * Who publishes audio, who hears it, and who is allowed to own the microphone.
 *
 * A single-host Live has one audio question: is the host's mic on. A multi-guest
 * Live has a matrix, and every cell of it is a bug somebody has shipped before:
 *
 *   - A guest who can hear the host but whom the host cannot hear.
 *   - Two guests who are each audible to the audience but not to each other,
 *     so they talk over one another for a minute before noticing.
 *   - A guest whose own voice is played back to them at 200ms delay.
 *   - An audience member whose microphone is captured because the code that
 *     configures publishing did not distinguish "on stage" from "watching".
 *
 * None of these show up in a build, a type check, or a screenshot. They show up
 * on a device, once, in front of an audience. So the matrix is declared here as
 * data, derived by pure functions, and asserted exhaustively in tests — the only
 * form in which "everyone can hear everyone" is a checkable claim rather than a
 * hope.
 *
 * There are no Agora imports here, and there must never be. This module decides
 * *what* the audio topology should be; `useAgoraLiveBroadcastRoom` is the single
 * place that tells the SDK about it. Keeping those apart is what stops a second
 * opinion about publishing from appearing somewhere else in the app.
 */

import type { LiveRole, LiveStageParticipant } from "./liveParticipantRegistry";

// ---------------------------------------------------------------------------
// Stage 15 — the audio matrix
// ---------------------------------------------------------------------------

/**
 * What one client does with audio, given the role the *server* assigned it.
 *
 * Note that `publishMicrophone` is derived from role alone and never from a
 * local preference. A client cannot decide it is a publisher; it can only be
 * told it is one. That is the same rule the token contract enforces on the
 * server, restated on the client so the two cannot disagree quietly.
 */
export type LiveAudioPlan = {
  /** Whether this client captures and sends its microphone at all. */
  publishMicrophone: boolean;
  /** Whether this client receives remote audio. Everyone does — a Live is audible. */
  subscribeRemoteAudio: boolean;
  /**
   * Whether the local microphone is played back to the local speaker.
   * Always false. Hearing yourself on a 200ms delay makes speech impossible,
   * and no role has ever wanted it.
   */
  localAudioPlayback: false;
  /** The Agora client role this plan implies. */
  clientRole: "broadcaster" | "audience";
};

/** Roles that occupy a stage slot and are therefore audible to everyone else. */
const PUBLISHING_ROLES: ReadonlySet<LiveRole> = new Set<LiveRole>(["host", "cohost", "guest"]);

export function roleParticipatesInAudio(role: LiveRole): boolean {
  return PUBLISHING_ROLES.has(role);
}

/**
 * The audio plan for one client.
 *
 * `authorized` is the server's answer, carried on the RTC credentials. A role of
 * `guest` with `authorized: false` is someone whose invite has not been honoured
 * by the token service yet, and they must stay an audience member — this is the
 * client half of "the client may never self-promote".
 */
export function resolveLiveAudioPlan(role: LiveRole, authorized: boolean): LiveAudioPlan {
  const publishes = roleParticipatesInAudio(role) && authorized;
  return {
    publishMicrophone: publishes,
    subscribeRemoteAudio: true,
    localAudioPlayback: false,
    clientRole: publishes ? "broadcaster" : "audience"
  };
}

/**
 * Whether `listener` should be able to hear `speaker` on a correctly working Live.
 *
 * This is the specification the matrix test asserts against, written once so the
 * test cannot drift into asserting whatever the implementation happens to do.
 * The rules, in full:
 *
 *   - Nobody hears themselves.
 *   - A muted speaker is inaudible to everyone, including the host. Host mute is
 *     a moderation action and must not have an exception carved into it.
 *   - A speaker who is not yet `live` is inaudible: they are still joining, and
 *     leaking a half-connected guest's room audio onto a broadcast is worse than
 *     a second of silence.
 *   - Otherwise everyone on the stage is audible to everyone in the channel,
 *     stage and audience alike. There is no partial mesh.
 */
export function shouldHear(listener: LiveStageParticipant, speaker: LiveStageParticipant): boolean {
  if (listener.key === speaker.key) return false;
  if (!roleParticipatesInAudio(speaker.role)) return false;
  if (speaker.phase !== "live") return false;
  if (speaker.audioMuted) return false;
  return speaker.hasAudio;
}

export type AudibilityPair = { listener: string; speaker: string; audible: boolean };

/**
 * The full matrix, as a flat list. Used by tests and by the device acceptance
 * script, which reads it to tell a QA engineer exactly which pairs to check.
 */
export function audibilityMatrix(participants: LiveStageParticipant[]): AudibilityPair[] {
  const roster = participants || [];
  const pairs: AudibilityPair[] = [];
  for (const listener of roster) {
    for (const speaker of roster) {
      if (listener.key === speaker.key) continue;
      pairs.push({ listener: listener.key, speaker: speaker.key, audible: shouldHear(listener, speaker) });
    }
  }
  return pairs;
}

/**
 * The people who should be audible right now but are not, with a reason.
 *
 * Deliberately reports rather than repairs. A client that "fixes" its own audio
 * topology is a client with a second opinion about publishing, which is the
 * thing this module exists to prevent. Diagnosing is useful; acting is not.
 */
export function silentPublishers(participants: LiveStageParticipant[]): Array<{ key: string; reason: string }> {
  return (participants || [])
    .filter((participant) => roleParticipatesInAudio(participant.role) && participant.phase === "live")
    .filter((participant) => participant.audioMuted || !participant.hasAudio)
    .map((participant) => ({
      key: participant.key,
      reason: participant.audioMuted ? "muted" : "no_audio_track"
    }));
}

// ---------------------------------------------------------------------------
// Stage 16 — one engine, one session policy, one microphone
// ---------------------------------------------------------------------------

/**
 * The invariant the whole Live audio path rests on.
 *
 * The device has one microphone and one audio session. The failure this guards
 * against is not subtle in its effects but is very easy to introduce: a second
 * engine, created "just for guests", captures the same microphone, and the
 * result is either silence or an echo that no amount of DSP will remove,
 * depending on which one wins the session.
 *
 * The named modules below are the ones that have historically been proposed.
 * They are listed by name so that a code review has something concrete to point
 * at, and so the architecture test can fail on the name rather than on a vague
 * notion of "a second stack".
 */
export const FORBIDDEN_SECOND_AUDIO_OWNERS: readonly string[] = [
  "guestAudioEngine",
  "secondaryLiveAudioSession",
  "liveGuestRtcEngine",
  "cohostAudioEngine",
  "multiGuestAudioManager"
];

export type LiveAudioOwnership = {
  /** Identifier of the single engine instance permitted to exist. */
  engineOwnerId: string;
  /** Identifier of the single client permitted to capture the microphone. */
  microphoneOwnerId: string;
};

export type OwnershipViolation = { code: string; detail: string };

/**
 * Check that a proposed set of Live audio owners is legal.
 *
 * Takes lists rather than single values on purpose: the question worth asking is
 * not "who owns the mic" — any implementation can answer that — but "how many
 * things believe they own it", which is the question that catches the bug.
 */
export function inspectLiveAudioOwnership(
  engineOwners: string[],
  microphoneOwners: string[]
): OwnershipViolation[] {
  const violations: OwnershipViolation[] = [];
  const engines = [...new Set((engineOwners || []).filter(Boolean))];
  const microphones = [...new Set((microphoneOwners || []).filter(Boolean))];

  if (engines.length > 1) {
    violations.push({
      code: "MULTIPLE_LIVE_ENGINES",
      detail: `A Live may run exactly one RTC engine; found ${engines.length}: ${engines.join(", ")}.`
    });
  }
  if (microphones.length > 1) {
    violations.push({
      code: "MULTIPLE_MICROPHONE_OWNERS",
      detail: `One device has one microphone; found ${microphones.length} owners: ${microphones.join(", ")}.`
    });
  }
  for (const name of [...engines, ...microphones]) {
    if (FORBIDDEN_SECOND_AUDIO_OWNERS.includes(name)) {
      violations.push({
        code: "FORBIDDEN_AUDIO_OWNER",
        detail: `${name} is an alternate audio stack; the Live engine is the only owner.`
      });
    }
  }
  return violations;
}

/**
 * Whether a guest joining requires any change to the local audio ownership.
 *
 * No. This is the audio counterpart of `rosterChangeRequiresReconnect`, and it
 * is a function rather than a constant for the same reason: a caller that wants
 * to re-establish audio "because a guest arrived" has to come through here and
 * be told no, in a call site a reviewer can see.
 */
export function guestArrivalRequiresAudioReconfiguration(): boolean {
  return false;
}

// ---------------------------------------------------------------------------
// Stage 17 — echo control
// ---------------------------------------------------------------------------

/**
 * Echo cancellation settings, expressed as intent rather than as DSP.
 *
 * Multi-guest Lives are where echo appears, because guests use speakerphone and
 * each guest's speaker output is another guest's microphone input. The temptation
 * is to write a filter. That temptation must be refused: Agora's AEC is tuned
 * against the device's own hardware paths and is fed a reference signal that a
 * JavaScript layer does not have. A hand-rolled canceller sitting on top of it
 * makes things worse, not better, and it is unfixable once it ships because
 * nobody can tell which layer is causing an artefact.
 *
 * So the policy is: use what the SDK already has, choose the right scenario for
 * the role, and change nothing else.
 */
export type LiveEchoControl = {
  /** Agora's acoustic echo canceller. Always on for anyone publishing. */
  echoCancellation: boolean;
  /** Agora's noise suppression. */
  noiseSuppression: boolean;
  /** Agora's automatic gain control. */
  automaticGainControl: boolean;
  /**
   * Which Agora audio scenario to request.
   *
   * `chatroom` engages the more aggressive echo control path, which is what a
   * stage of several people on speakerphone needs. A solo host gets the default
   * scenario, which preserves the music-quality profile their broadcast may be
   * mixing into.
   */
  scenario: "default" | "chatroom";
  /** True when this client never captures audio, so echo control is moot. */
  listenerOnly: boolean;
};

/**
 * The echo-control policy for a client, given the size of the stage.
 *
 * The switch to the chatroom scenario happens at two publishers, not three:
 * two people on speakerphone is already a feedback loop, and waiting for a third
 * means the first guest's arrival is the moment the Live starts echoing.
 */
export function resolveLiveEchoControl(plan: LiveAudioPlan, publisherCount: number): LiveEchoControl {
  if (!plan.publishMicrophone) {
    return {
      echoCancellation: false,
      noiseSuppression: false,
      automaticGainControl: false,
      scenario: "default",
      listenerOnly: true
    };
  }
  return {
    echoCancellation: true,
    noiseSuppression: true,
    automaticGainControl: true,
    scenario: publisherCount >= 2 ? "chatroom" : "default",
    listenerOnly: false
  };
}

/**
 * The scenario to apply to the SDK right now, or null to apply nothing.
 *
 * Split out from `resolveLiveEchoControl` because the hook needs an answer to a
 * narrower question than "what should the settings be": it needs to know whether
 * to *call* the SDK at all. Reapplying an audio scenario on a live broadcast is
 * an audible glitch, and guest churn on a busy Live would otherwise reapply it
 * several times a minute for no change in outcome.
 *
 * Returning null rather than the unchanged scenario is deliberate. A caller that
 * receives a value naturally passes it on; a caller that receives null has to
 * handle "nothing to do" explicitly, which is the branch that must not be
 * forgotten.
 */
export function nextEchoScenario(
  applied: LiveEchoControl["scenario"],
  plan: LiveAudioPlan,
  publisherCount: number
): LiveEchoControl["scenario"] | null {
  const control = resolveLiveEchoControl(plan, Math.max(0, Math.floor(Number(publisherCount) || 0)));
  // A listener has no capture to cancel against, so its scenario is not ours to
  // move — and moving it would be a client with a second opinion about audio.
  if (control.listenerOnly) return null;
  return control.scenario === applied ? null : control.scenario;
}

/**
 * Whether a proposed audio-processing change is custom DSP.
 *
 * Kept as an explicit predicate so the architecture test has something to assert
 * and so a future contributor reads the refusal rather than inferring it.
 */
export function isCustomAudioProcessing(name: string): boolean {
  const normalized = String(name || "").toLowerCase();
  return (
    normalized.includes("echocanceller") ||
    normalized.includes("noisegate") ||
    normalized.includes("audiofilter") ||
    normalized.includes("dsp") ||
    normalized.includes("audioworklet")
  );
}
