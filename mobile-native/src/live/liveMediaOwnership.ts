/**
 * Who may turn whose camera and microphone on, and who may remove whom.
 *
 * The rule that shapes this whole module is an asymmetry that is easy to miss
 * and serious to get wrong:
 *
 *   A host may silence a guest immediately. A host may NOT make a guest speak.
 *
 * Muting is a safety action. If a guest starts saying something the host must
 * stop, that has to take effect at once, without a dialog and without the
 * guest's cooperation — and it takes effect on the server, so a guest running a
 * patched client cannot ignore it.
 *
 * Unmuting is the opposite. "Host can turn on a guest's microphone" means a
 * PulseSoc user can be made to broadcast the sound of their room to an audience
 * without touching their phone. The same for the camera, more so. Whatever the
 * product argument for convenience, the feature is a remote listening device,
 * and no amount of good intent in the host's UI changes what the capability is.
 *
 * So a host's unmute is modelled here as a *grant*: it restores the guest's
 * permission to publish, and the guest's own control is what actually opens the
 * capture. The guest sees an invitation. The host sees "asked to unmute" rather
 * than a mic that flipped on. That difference is the entire point of the module.
 *
 * Pure: no network, no Agora, no React. Every decision below is a function of
 * the actor, the target, and the command.
 */

import type { LiveRole, LiveStageParticipant } from "./liveParticipantRegistry";

export type MediaKind = "microphone" | "camera";

export type MediaCommand =
  /** Take the media off air. Immediate, enforced, needs no consent. */
  | "mute"
  /** Restore permission to publish. Never opens the capture by itself. */
  | "unmute"
  /** Remove someone from the stage entirely. */
  | "remove";

export type MediaActor = {
  key: string;
  role: LiveRole;
  isHost: boolean;
};

export type MediaCommandOutcome =
  /** Applies immediately to the target's media. */
  | { effect: "enforced"; kind: MediaKind; targetKey: string }
  /** Restores permission; the target must act before anything is captured. */
  | { effect: "requested"; kind: MediaKind; targetKey: string; requiresTargetConsent: true }
  /** The target leaves the stage. */
  | { effect: "removed"; targetKey: string }
  /** Refused, with a reason safe to render and safe to log. */
  | { effect: "denied"; reason: string; code: string };

/**
 * Roles permitted to moderate other people on the stage.
 *
 * Co-host is included deliberately and is not an alias for host: a co-host may
 * mute and remove guests, which is what makes them useful for running a panel,
 * but the separation of powers that matters — ending the Live, promoting people
 * — lives elsewhere and is not granted here.
 */
const MODERATOR_ROLES: ReadonlySet<LiveRole> = new Set<LiveRole>(["host", "cohost"]);

export function canModerate(actor: MediaActor): boolean {
  return actor.isHost || MODERATOR_ROLES.has(actor.role);
}

/**
 * Resolve a moderation command.
 *
 * Reading order matters here: the denials come first, so that the permissive
 * branches at the bottom can only be reached by an actor who has already been
 * checked. A structure that granted first and validated afterwards would make a
 * missing check silently permissive.
 */
export function resolveMediaCommand(
  actor: MediaActor,
  target: LiveStageParticipant,
  command: MediaCommand,
  kind: MediaKind = "microphone"
): MediaCommandOutcome {
  if (!canModerate(actor)) {
    return {
      effect: "denied",
      code: "LIVE_MODERATION_FORBIDDEN",
      reason: "Only the host or a co-host can manage people on stage."
    };
  }

  if (target.isHost && actor.key !== target.key) {
    // A co-host who could mute the host could take over the broadcast. The host
    // controls their own media and nobody else's controls it for them.
    return {
      effect: "denied",
      code: "LIVE_CANNOT_MODERATE_HOST",
      reason: "The host cannot be muted or removed from their own broadcast."
    };
  }

  if (target.role === "audience") {
    return {
      effect: "denied",
      code: "LIVE_TARGET_NOT_ON_STAGE",
      reason: "That person is watching, not on stage."
    };
  }

  if (command === "remove") {
    return { effect: "removed", targetKey: target.key };
  }

  if (command === "mute") {
    return { effect: "enforced", kind, targetKey: target.key };
  }

  // Unmute. The grant, never the switch.
  return { effect: "requested", kind, targetKey: target.key, requiresTargetConsent: true };
}

/**
 * What the target's client does when a moderation outcome arrives.
 *
 * The guest's device is where the asymmetry becomes real, so it is stated as its
 * own function rather than left implicit in a component. `publish` false is
 * obeyed at once; `publish` true is never returned — the most an inbound command
 * can do is clear the block and raise a prompt.
 */
export type LocalMediaResponse = {
  /** Set the local capture to this state, or leave it alone when null. */
  setPublishing: boolean | null;
  /** Whether the server-side block on publishing is now lifted. */
  permitted: boolean;
  /** Whether to ask the person before anything is captured. */
  prompt: boolean;
};

export function applyMediaCommandLocally(outcome: MediaCommandOutcome): LocalMediaResponse {
  if (outcome.effect === "enforced") {
    // Off, now, without asking. This is the safety half of the asymmetry.
    return { setPublishing: false, permitted: false, prompt: false };
  }
  if (outcome.effect === "requested") {
    // Permission restored; capture stays closed until the person opens it.
    return { setPublishing: null, permitted: true, prompt: true };
  }
  if (outcome.effect === "removed") {
    return { setPublishing: false, permitted: false, prompt: false };
  }
  return { setPublishing: null, permitted: false, prompt: false };
}

/**
 * Whether a host action would activate a target's capture without consent.
 *
 * This exists to be called by tests and by review, not by product code. It is
 * the single sentence that the whole module is defending, expressed so that a
 * regression in `resolveMediaCommand` or `applyMediaCommandLocally` fails
 * loudly rather than quietly widening what a host can do.
 */
export function activatesTargetCaptureWithoutConsent(
  actor: MediaActor,
  target: LiveStageParticipant,
  command: MediaCommand,
  kind: MediaKind = "microphone"
): boolean {
  const response = applyMediaCommandLocally(resolveMediaCommand(actor, target, command, kind));
  return response.setPublishing === true && !response.prompt;
}

// ---------------------------------------------------------------------------
// Stage 19 — the host's moderation surface
// ---------------------------------------------------------------------------

export type ModerationOption = {
  command: MediaCommand;
  kind: MediaKind;
  /** i18n key. No user-visible copy is constructed here. */
  labelKey: string;
  /** True when the action is destructive and the UI should confirm first. */
  destructive: boolean;
};

/**
 * The actions a moderator may take on one participant, in the order they should
 * be offered.
 *
 * Returning a list rather than a set of booleans keeps the UI from assembling
 * its own menu: a screen that decides for itself which buttons to show is a
 * screen that can show a button the server will reject, and the user reads that
 * rejection as the feature being broken.
 */
export function moderationOptionsFor(actor: MediaActor, target: LiveStageParticipant): ModerationOption[] {
  if (!canModerate(actor)) return [];
  if (target.role === "audience") return [];
  if (target.isHost && actor.key !== target.key) return [];

  const options: ModerationOption[] = [];
  options.push(
    target.audioMuted
      ? { command: "unmute", kind: "microphone", labelKey: "extended:live.moderation.askToUnmute", destructive: false }
      : { command: "mute", kind: "microphone", labelKey: "extended:live.moderation.mute", destructive: false }
  );
  options.push({
    command: "remove",
    kind: "microphone",
    labelKey: "extended:live.moderation.remove",
    destructive: true
  });
  return options;
}

/**
 * The API action string for a resolved command.
 *
 * The backend accepts exactly `mute`, `unmute`, `remove` and `leave` on
 * `/api/pulse/live/<id>/guests/<guest_id>/<action>`. Mapping here rather than at
 * the call site means an unmapped command is a type error instead of a 400 the
 * user sees as "nothing happened".
 */
export function guestActionEndpointVerb(command: MediaCommand): "mute" | "unmute" | "remove" {
  return command;
}

/**
 * A guest leaving their own slot.
 *
 * Separate from `remove` because the server distinguishes them — `leave` is
 * self-only and `remove` is host-only — and because the resulting states differ
 * in the UI and in the audit log. Collapsing them would make a host's removal
 * indistinguishable from a guest walking off, which is exactly the ambiguity a
 * moderation log exists to resolve.
 */
export function canLeaveStage(actor: MediaActor, target: LiveStageParticipant): boolean {
  return actor.key === target.key && target.role !== "audience";
}
