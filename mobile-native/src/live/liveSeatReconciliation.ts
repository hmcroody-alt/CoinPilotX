/**
 * What a `connect()` call should actually do to a Live that is already running.
 *
 * A broadcast is a single continuous Agora session. Guests arriving and leaving
 * are changes *within* that session, not reasons to start a new one. But the
 * connect path is called from several places — the host screen's mount effect, a
 * credential refresh, a guest's promotion to the stage, a viewer re-entering —
 * and every one of them hands over a full credential set. Deciding "is this the
 * same seat?" inline, at each call site, is how a Live ends up being torn down
 * and rebuilt because someone's token was refreshed.
 *
 * So the decision is made here, once, as a pure function. The important
 * consequence is negative: `rejoin` — the only outcome that destroys the engine,
 * stops the camera, drops the microphone and restarts the audio session — is
 * reachable *only* when the channel or the RTC uid genuinely changed. A guest
 * joining changes neither, so no sequence of guest arrivals can restart a host's
 * broadcast. That property is what the tests in this module's suite pin down.
 *
 * No Agora imports, no React: the ordering rules can be checked exhaustively
 * without a device.
 */

/** The identity of a seat in a Live: which channel, as whom, publishing or not. */
export type LiveSeat = {
  provider: string;
  channelName: string;
  uid: number;
  /** Whether this seat publishes media. Intent and authority combined. */
  publishing: boolean;
  /** The token currently held for this seat. */
  token: string;
};

export type SeatAction =
  /** No engine yet. Create one and join the channel. */
  | "join"
  /** Same seat, same role, same token. Touch nothing. */
  | "noop"
  /** Same seat and role, but a fresher token. Renew in place. */
  | "renew_token"
  /** Same seat, audience -> broadcaster. Role change in place, no rejoin. */
  | "promote"
  /** Same seat, broadcaster -> audience. Role change in place, no rejoin. */
  | "demote"
  /** A genuinely different seat. The only action that restarts the session. */
  | "rejoin";

/**
 * Actions that leave the engine, the camera, the microphone and the audio
 * session untouched. Everything a running broadcast must survive.
 */
const NON_DISRUPTIVE: ReadonlySet<SeatAction> = new Set<SeatAction>([
  "noop",
  "renew_token",
  "promote",
  "demote"
]);

export function isDisruptive(action: SeatAction): boolean {
  return !NON_DISRUPTIVE.has(action);
}

/** Whether the local camera and microphone keep running across this action. */
export function preservesLocalCapture(action: SeatAction, wasPublishing: boolean): boolean {
  if (action === "rejoin" || action === "join") return false;
  // Demotion deliberately stops the local capture: an audience member's camera
  // must not stay on. That is a role change, not a session restart.
  if (action === "demote") return false;
  if (action === "promote") return true;
  return wasPublishing;
}

function sameEndpoint(active: LiveSeat, next: LiveSeat): boolean {
  return (
    active.provider === next.provider &&
    active.channelName === next.channelName &&
    active.uid === next.uid
  );
}

/**
 * Decide what to do. `active` is null when nothing is connected.
 *
 * Note that the comparison is on the *endpoint* — provider, channel, uid — and
 * never on the token, the participant name, the guest id or anything else the
 * server may legitimately re-issue mid-broadcast. A refreshed token for the same
 * seat is a renewal, not a new session.
 */
export function reconcileLiveSeat(active: LiveSeat | null | undefined, next: LiveSeat): SeatAction {
  if (!active) return "join";
  if (!sameEndpoint(active, next)) return "rejoin";
  if (active.publishing !== next.publishing) return next.publishing ? "promote" : "demote";
  if (next.token && next.token !== active.token) return "renew_token";
  return "noop";
}

/**
 * Whether a change to the set of people on stage should touch the local
 * connection at all.
 *
 * Always false, and deliberately written as a function rather than left implied,
 * because this is the single rule the whole multi-guest feature rests on: remote
 * participants arriving and leaving are subscription events handled by Agora's
 * own callbacks. Nothing about them belongs on the connect path. A caller that
 * wants to reconnect "because the roster changed" has to route through here and
 * be told no.
 */
export function rosterChangeRequiresReconnect(): boolean {
  return false;
}
