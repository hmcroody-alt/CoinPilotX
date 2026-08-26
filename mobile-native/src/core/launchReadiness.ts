/**
 * How far into a subsystem the public build is allowed to go.
 *
 * ## The defect this closes
 *
 * Presence ships its landing page before the layers underneath it are finished.
 * Every control on that page navigated somewhere the moment it was tapped, so
 * the only way to stop a member reaching an unfinished workflow was to delete
 * the control — which also deletes the answer to "what is this section going to
 * be", the one thing the landing page is genuinely good at right now.
 *
 * A readiness state separates those two questions. `READY` means the surface is
 * navigable. `COMING_SOON` and `BUILDING` both mean *do not navigate*, and they
 * differ only in what the member is told: `COMING_SOON` is work that has not
 * started, `BUILDING` is work in progress. Neither one hides the control. The
 * door stays visible, greyed, labelled, and it does not open.
 *
 * ## Why this is a constant and not a flag
 *
 * `envFlag` exists for gates an operator flips per build. This is not that: a
 * layer is finished or it is not, and the answer is the same in every build,
 * for every member, on every device. Routing it through the environment would
 * add a way for the gate to be *off* in a build nobody meant it to be off in —
 * which is the exact failure `envFlagOn` documents at length for release
 * bundles, where a computed `process.env` read is dead and every flag answers
 * false. A gate that fails open is not a gate.
 *
 * So: a literal map, edited in a commit, reviewed like code. Opening a layer is
 * one line here plus whatever made it ready.
 *
 * ## Scope
 *
 * Presence only. This deliberately does not grow into a registry of every
 * subsystem's maturity — Business OS, Marketplace, Premium, Crypto, Calls and
 * Livestream are shipped and answer for themselves, and adding them here would
 * put a second, staler opinion about their state next to the real one.
 */

/**
 * What the public build may do with a surface.
 *
 * - `READY` — navigable.
 * - `COMING_SOON` — not navigable, work not started.
 * - `BUILDING` — not navigable, work underway.
 *
 * The two locked states are kept apart on purpose. "Coming soon" on something
 * being actively built reads as vague; "building" on something nobody has
 * started reads as a promise. Both are cheap to get wrong and free to get right.
 */
export type ReadinessState = "READY" | "COMING_SOON" | "BUILDING";

/**
 * The Presence surfaces this gate has an opinion about.
 *
 * Named after what a member is trying to do, not after the route, because the
 * same route is reached from more than one door: `PageCreate` is behind the
 * artist card, the business card and the bare "Create New" button, and each one
 * deserves its own answer if they ever diverge.
 */
export type PresenceSurface =
  | "presenceHub"
  | "artistPresenceCreate"
  | "businessPresenceCreate"
  | "presenceCreate"
  | "presenceManage";

/**
 * The gate itself.
 *
 * `presenceHub` is `READY` and is the whole point: the landing page is the
 * public preview boundary, so it must keep working while everything below it is
 * shut. Anything reachable *from* it is locked until its workflow is finished.
 *
 * `presenceManage` is `BUILDING` rather than `COMING_SOON` because the
 * management views exist and are being worked on; the creation flows have not
 * started, so they say "coming soon".
 */
const PRESENCE_READINESS: Record<PresenceSurface, ReadinessState> = {
  presenceHub: "READY",
  artistPresenceCreate: "COMING_SOON",
  businessPresenceCreate: "COMING_SOON",
  presenceCreate: "COMING_SOON",
  presenceManage: "BUILDING"
};

/**
 * What is behind a locked door, so the member can see the shape of it.
 *
 * These are the layers that surface will open onto — not features being claimed
 * as present. Everything listed here is rendered greyed, under a lock, beneath
 * a state badge that says it is not open; the list answers "what am I waiting
 * for" rather than "what do I have". That distinction is the reason it is safe
 * to name them at all: the hub's creation pitch is held to naming only things
 * that exist today, and this is explicitly the other thing.
 *
 * `presenceManage` names the three routes that already exist behind the
 * management view (`PageEdit`, `PageTeam`, `PageConnections`), so the preview
 * and the product cannot drift apart.
 */
export const PRESENCE_NEXT_LAYERS: Record<PresenceSurface, readonly string[]> = {
  presenceHub: [],
  artistPresenceCreate: ["Profile Setup", "Content Management", "Analytics", "Monetization"],
  businessPresenceCreate: ["Business Dashboard", "Customer Tools", "Advanced Management"],
  /*
    Deliberately empty. "Create New" is the same creation flow the two cards
    above it lead to, so listing anything here would restate what the member has
    just read, two inches lower, in grey. The panel shows the note alone.
  */
  presenceCreate: [],
  presenceManage: ["Edit Presence", "Team & Roles", "Connections"]
};

/** The state of a Presence surface. */
export function presenceReadiness(surface: PresenceSurface): ReadinessState {
  return PRESENCE_READINESS[surface];
}

/**
 * Whether a Presence surface may be navigated to.
 *
 * Phrased as "is it ready" rather than "is it locked" so a surface added to
 * {@link PresenceSurface} without an entry cannot read as open — `Record`
 * requires the entry, and the comparison is against `READY` rather than against
 * a list of locked states that a new state could be missing from.
 */
export function isPresenceSurfaceReady(surface: PresenceSurface): boolean {
  return presenceReadiness(surface) === "READY";
}

/**
 * The badge text for a state, or `null` when there is nothing to say.
 *
 * A `READY` surface gets no badge: a label on everything is a label people stop
 * reading, and the absence of one is what makes the greyed cards legible.
 */
export function readinessBadge(state: ReadinessState): string | null {
  if (state === "COMING_SOON") return "COMING SOON";
  if (state === "BUILDING") return "BUILDING";
  return null;
}

/**
 * The one line shown under a locked layer.
 *
 * Product copy, deliberately: a locked door is not an error and must never read
 * like one. No route names, no status codes, no "not implemented" — a member
 * who taps this has done nothing wrong and there is nothing for them to fix.
 */
export function readinessNote(state: ReadinessState): string | null {
  if (state === "COMING_SOON") return "Opening soon. Everything you already have stays exactly as it is.";
  if (state === "BUILDING") return "We're building this now. It'll open here when it's ready.";
  return null;
}
