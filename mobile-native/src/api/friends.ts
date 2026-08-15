/**
 * The PulseSoc friend graph, as the client needs it for Home's "People You May
 * Know" module.
 *
 * ## Why a person here has no numeric id
 *
 * `GET /api/pulse/friends` builds every person through
 * `pulse_person_public_payload`, and that payload deliberately does not include
 * `user_id` — it returns `public_player_id`, `username`, `display_name`,
 * `avatar_url` and the identity labels. So a suggestion's destination is a
 * *profile key*, not a user id, and `PulseSuggestedPerson` says so rather than
 * carrying a `userId?: number` that is always `undefined` and quietly turns
 * every card into a navigation no-op.
 *
 * ## Why `suggested` is filtered here rather than at the card
 *
 * The endpoint returns the whole graph — `friends`, `following`, `followers`,
 * `requests` and `suggested` — in one response. Everything except `suggested`
 * exists so the caller can *exclude* people it must not recommend (§13: never
 * recommend an existing friend, a pending request, or the viewer). Doing that
 * subtraction at the boundary means a card can never render a person the graph
 * already disqualified, and there is exactly one place to audit the rule.
 */
import { pulseApi } from "./pulseApi";

export type PulseSuggestedPerson = {
  /** Stable navigation target. Non-empty by construction — see `normalizeSuggestedPeople`. */
  profileKey: string;
  displayName: string;
  username: string;
  publicPlayerId: string;
  avatarUrl: string;
  /** Identity label the server already computed ("Member", "Creator", …). Never invented. */
  rank: string;
  premiumVerified: boolean;
};

type RawPerson = {
  public_player_id?: string;
  display_name?: string;
  username?: string;
  avatar_url?: string;
  rank?: string;
  primary_label?: string;
  premium_verified?: boolean;
};

type RawFriendRequest = { requester?: RawPerson | null };

export type FriendGraphResponse = {
  ok?: boolean;
  message?: string;
  requests?: RawFriendRequest[];
  friends?: RawPerson[];
  following?: RawPerson[];
  followers?: RawPerson[];
  suggested?: RawPerson[];
};

/** A person is addressable by username first, then public player id. Anything else has no destination. */
function personKey(person: RawPerson | null | undefined): string {
  if (!person) return "";
  return String(person.username || person.public_player_id || "").trim();
}

function toSuggestedPerson(person: RawPerson): PulseSuggestedPerson | null {
  const profileKey = personKey(person);
  if (!profileKey) return null;
  return {
    profileKey,
    displayName: String(person.display_name || "").trim() || profileKey,
    username: String(person.username || "").trim(),
    publicPlayerId: String(person.public_player_id || "").trim(),
    avatarUrl: String(person.avatar_url || "").trim(),
    rank: String(person.primary_label || person.rank || "").trim(),
    premiumVerified: Boolean(person.premium_verified)
  };
}

/**
 * Suggested people, with everyone the graph already disqualifies removed.
 *
 * Excluded: existing friends, accounts the viewer already follows, and anyone
 * with an inbound pending request. Followers are *not* excluded — a follower the
 * viewer has not followed back is a legitimate recommendation.
 */
export function normalizeSuggestedPeople(data: FriendGraphResponse): PulseSuggestedPerson[] {
  const excluded = new Set<string>();
  for (const person of data.friends || []) {
    const key = personKey(person);
    if (key) excluded.add(key);
  }
  for (const person of data.following || []) {
    const key = personKey(person);
    if (key) excluded.add(key);
  }
  for (const request of data.requests || []) {
    const key = personKey(request?.requester);
    if (key) excluded.add(key);
  }

  const seen = new Set<string>();
  const out: PulseSuggestedPerson[] = [];
  for (const raw of data.suggested || []) {
    const person = toSuggestedPerson(raw);
    if (!person) continue;
    if (excluded.has(person.profileKey) || seen.has(person.profileKey)) continue;
    seen.add(person.profileKey);
    out.push(person);
  }
  return out;
}

export async function listSuggestedPeople(): Promise<PulseSuggestedPerson[]> {
  const data = await pulseApi<FriendGraphResponse>("/api/pulse/friends");
  // The route answers 200 with `ok:false` when the graph query failed, so a
  // truthy response is not on its own a usable one.
  if (data && data.ok === false) return [];
  return normalizeSuggestedPeople(data || {});
}

export type FriendRequestResponse = {
  ok?: boolean;
  message?: string;
  status?: string;
  pending?: boolean;
};

/**
 * Send a friend request.
 *
 * The server owns duplicate suppression and eligibility (§13 says sensitive
 * actions are revalidated server-side), so this states the intent and reports
 * what came back rather than deciding locally whether the request is allowed.
 */
export async function sendFriendRequest(profileKey: string) {
  return pulseApi<FriendRequestResponse>("/api/pulse/friends/request", {
    method: "POST",
    body: JSON.stringify({ profile_key: profileKey, username: profileKey })
  });
}
