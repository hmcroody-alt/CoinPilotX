/**
 * The data layer for the Business Profile — the authoritative seller identity.
 *
 * Three rules hold this module together, and each one is a defect the old screen
 * shipped.
 *
 * **The screen reads one source.** The previous version assembled its header from
 * four: the Pulse profile for the handle, the *seller application* for name,
 * category, email and phone, the account-wide verification request for the badge,
 * and `users.verified_badge` as a tiebreak. Four sources produce four answers, which
 * is how the same screen came to print "your application is in review" beside
 * "Verification · Approved", and how `@Pilot-8919` — typed with an `@` into an
 * application form — became `@@Pilot-8919` when the screen prefixed another one.
 * `GET /api/pulse/business/profile` returns the whole owner view already reconciled,
 * and this module does not second-guess it.
 *
 * **Nothing is derived here that the server decided.** Completeness percentage,
 * lock state, verification state and sync state all arrive computed. The temptation
 * is to recompute the percentage on the phone so the checklist animates; doing so is
 * how a headline and the list beneath it come to disagree.
 *
 * **Absent is not zero and not "coming soon".** A field the seller has not filled in
 * reads as empty with an instruction; a metric the platform cannot measure is named
 * in `unavailable` and omitted. The string "Opening hours aren't stored yet" was an
 * implementation note that reached production, and {@link BUSINESS_PROFILE_MOCK_DATA_GAPS}
 * exists so that the next one is caught by a test instead of by a screenshot.
 *
 * Saves are partial by design: `POST /api/pulse/business/profile` returns `saved` and
 * `rejected` together with a 200, because one mistyped URL must not discard four good
 * edits, and one field awaiting verification review must not freeze the other twelve.
 */

import { pulseApi, PulseApiError } from "./pulseApi";
import { failureFrom, type FailureCopy } from "./stateLanguage";
import { readJsonCache, writeJsonCache } from "../core/cache";

/* ------------------------------------------------------------- vocabularies */

/**
 * The ten states the server may report. Mirrors `VERIFICATION_STATES` in
 * `services/business_os/profile/service.py`; a test pins the two together.
 *
 * There is exactly one of these per business at any moment. The old screen could
 * show two at once because it read two state machines — the seller application's
 * lifecycle and the account's verification request — and rendered both as if each
 * were the answer.
 */
export const VERIFICATION_STATES = [
  "not_started",
  "draft",
  "submitted",
  "needs_information",
  "under_review",
  "approved",
  "rejected",
  "suspended",
  "expired",
  "revoked"
] as const;
export type VerificationState = (typeof VERIFICATION_STATES)[number];

/** Reader-facing label per state. Written once so no screen invents its own. */
export const VERIFICATION_LABELS: Record<VerificationState, string> = {
  not_started: "Not started",
  draft: "Draft",
  submitted: "Submitted",
  needs_information: "Needs information",
  under_review: "Under review",
  approved: "Verified",
  rejected: "Rejected",
  suspended: "Suspended",
  expired: "Expired",
  revoked: "Revoked"
};

/**
 * Live Sync. The server asserts only the first three; the last three describe this
 * client's own request and are set here.
 *
 * The distinction is the point of the badge. A server that reported "Synced" to a
 * phone with no signal would be stating something it cannot know, and the old badge's
 * failure was subtler but identical in kind: it implied that whatever was on screen
 * was already public, which stopped being true the moment anyone typed.
 */
export const SYNC_STATES = [
  "synced",
  "changes_pending",
  "review_required",
  "saving",
  "offline",
  "sync_failed"
] as const;
export type SyncState = (typeof SYNC_STATES)[number];

export const SYNC_LABELS: Record<SyncState, string> = {
  synced: "Synced",
  changes_pending: "Changes pending",
  review_required: "Review required",
  saving: "Saving",
  offline: "Offline",
  sync_failed: "Sync failed"
};

/** Contact visibility. `private` is the default for every field, phone included. */
export const CONTACT_VISIBILITY = ["private", "after_purchase", "public"] as const;
export type ContactVisibility = (typeof CONTACT_VISIBILITY)[number];

export const CONTACT_VISIBILITY_LABELS: Record<ContactVisibility, string> = {
  private: "Private",
  after_purchase: "Visible after purchase",
  public: "Visible to all buyers"
};

export const HOURS_MODES = ["unset", "weekly", "by_appointment", "temporarily_closed"] as const;
export type HoursMode = (typeof HOURS_MODES)[number];

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

/**
 * `unset` and `closed` are different facts and must not share a representation.
 * A new seller who has configured nothing is not a business that is shut on Mondays,
 * and a buyer told "Closed" when the truth is "we never said" has been misinformed.
 */
export type DayState = "unset" | "open" | "closed";

export const LINK_KINDS = [
  "website",
  "instagram",
  "tiktok",
  "youtube",
  "facebook",
  "x",
  "custom"
] as const;
export type LinkKind = (typeof LINK_KINDS)[number];

export const LINK_LABELS: Record<LinkKind, string> = {
  website: "Website",
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  facebook: "Facebook",
  x: "X",
  custom: "Custom link"
};

/** Operational only. There is no `legal` kind: a registered address is review
 *  evidence and lives with the verification documents, not in the profile editor. */
export const ADDRESS_KINDS = ["pickup", "shipping_origin"] as const;
export type AddressKind = (typeof ADDRESS_KINDS)[number];

export const ADDRESS_LABELS: Record<AddressKind, string> = {
  pickup: "Pickup address",
  shipping_origin: "Shipping origin"
};

/* -------------------------------------------------------------------- types */

export type VerificationInfo = {
  state: VerificationState;
  /** Which store decided it — `verification_request` outranks the rest. Surfaced so
   *  the status sheet can say *why* it says what it says. */
  source: "verification_request" | "seller_application" | "verified_badge" | "none";
  requestId: number | null;
  decidedAt: string | null;
  note: string;
};

/**
 * What review costs the seller — never the whole profile.
 *
 * `requiresReview` fields save immediately and are queued for a reviewer.
 * `blocked` fields refuse the write, and only under enforcement.
 */
export type ProfileLocks = {
  requiresReview: string[];
  blocked: string[];
  explainer: string;
};

export type CompletionItem = { key: string; label: string; section: string };

export type ProfileCompletion = {
  percent: number;
  completed: CompletionItem[];
  missing: CompletionItem[];
  total: number;
  /** The single next step. A completeness card with no next step is a scold. */
  nextKey: string | null;
  nextLabel: string | null;
};

export type OpeningDay = {
  weekday: Weekday;
  label: string;
  state: DayState;
  opens: string | null;
  closes: string | null;
};

export type HoursOverride = {
  date: string;
  closed: boolean;
  opens: string | null;
  closes: string | null;
  label: string | null;
};

export type ProfileLink = {
  kind: LinkKind;
  url: string;
  label: string | null;
  position: number;
};

export type ProfileAddress = {
  kind: AddressKind;
  line1: string;
  line2: string;
  city: string;
  region: string;
  postalCode: string;
  country: string;
};

export type OwnerContact = {
  email: string;
  emailVisibility: ContactVisibility;
  phone: string;
  phoneVisibility: ContactVisibility;
  preferred: string;
};

export type SyncInfo = {
  state: SyncState;
  publishedAt: string | null;
  updatedAt: string | null;
};

/** The owner view: everything the editor needs, in one round trip. */
export type OwnerProfile = {
  userId: number;
  /** Exactly one leading `@`, normalised on the server. */
  handle: string;
  businessName: string;
  legalName: string;
  businessCategory: string;
  businessCategoryLabel: string;
  /**
   * A reviewer's classification of the *account* — "individual", "business".
   * Kept beside the category, never in place of it. Printing "Individual" where a
   * buyer looks for "Electronics" was the original category defect.
   */
  sellerType: string;
  tagline: string;
  about: string;
  whatYouSell: string;
  serviceArea: string;
  shippingSummary: string;
  returnSummary: string;
  responseExpectations: string;
  responseHours: string;
  languages: string[];
  accessibility: string[];
  publicLocation: { city: string; region: string; country: string };
  contact: OwnerContact;
  hoursMode: HoursMode;
  hours: OpeningDay[];
  hoursOverrides: HoursOverride[];
  links: ProfileLink[];
  addresses: ProfileAddress[];
  verification: VerificationInfo;
  locks: ProfileLocks;
  completion: ProfileCompletion;
  sync: SyncInfo;
  publishedAt: string | null;
  updatedAt: string | null;
};

/**
 * The buyer view. Structurally a different type from {@link OwnerProfile}, not a
 * `Partial<OwnerProfile>` — so that a component written against the preview cannot
 * accidentally read `legalName`, `addresses` or `verification.note`, because those
 * properties do not exist on this type at all. The server assembles it from an
 * allowlist for the same reason.
 */
export type PublicProfile = {
  handle: string;
  businessName: string;
  businessCategory: string;
  businessCategoryLabel: string;
  verified: boolean;
  tagline: string;
  about: string;
  whatYouSell: string;
  location: string;
  shippingSummary: string;
  returnSummary: string;
  responseExpectations: string;
  languages: string[];
  accessibility: string[];
  hoursMode: HoursMode;
  hours: OpeningDay[];
  hoursOverrides: HoursOverride[];
  links: ProfileLink[];
  /** Only the channels the seller chose to publish. Absent keys mean "not published". */
  contact: { preferred: string; email?: string; phone?: string };
  memberSince: string;
  policies: { returns?: string; shipping?: string; response?: string };
};

export type PreviewBanner = {
  active: boolean;
  title: string;
  subtitle: string;
  exitLabel: string;
  /** Actions the preview must simulate rather than perform. A Follow button that
   *  really follows would have the owner following themselves from a preview. */
  simulatedActions: string[];
};

export type SaveResult = {
  saved: Record<string, unknown>;
  /** Field name → the one sentence explaining the rejection, rendered inline. */
  rejected: Record<string, string>;
  /** Saved, and a reviewer will look at it. Not the same as rejected. */
  queuedForReview: string[];
  /** Keys the server declined to treat as writable — server-owned state. */
  ignored: string[];
  profile: OwnerProfile;
};

export type HandleCheck = {
  candidate: string;
  handle: string;
  available: boolean;
  reason: string;
  isCurrent: boolean;
};

export type SyncStatus = {
  sync: SyncInfo;
  verification: VerificationInfo;
  publishedAt: string | null;
  updatedAt: string | null;
  reviewProtectedFields: string[];
  blockedFields: string[];
  completion: { percent: number; completed: number; total: number };
};

export type OwnerProfileLoad =
  | { state: "ready"; profile: OwnerProfile; fromCache: boolean; savedAt: number | null }
  | { state: "failed"; failure: FailureCopy };

/* --------------------------------------------------------------- gap register
 *
 * Fields the brief asks for that this platform has no source for. Named here and
 * pinned by a test so the screen omits them deliberately rather than rendering a
 * placeholder that reads like a measurement. The old screen's "Opening hours aren't
 * stored yet — this field is coming" is what an unregistered gap looks like once it
 * reaches a buyer-facing surface.
 */
export const BUSINESS_PROFILE_MOCK_DATA_GAPS = [
  // MOCK-DATA: no per-track verification status; the platform stores one
  // account-wide request per type, so "Verification · Business" cannot yet be
  // distinguished from "Verification · Identity" beyond verification_type.
  "verification_track_detail",
  // MOCK-DATA: no handle-change cooldown or previous-handle redirect exists.
  // Availability and character rules are real; the cooldown warning is not, and the
  // handle editor must not promise a redirect the platform will not perform.
  "handle_change_cooldown",
  "handle_previous_redirect",
  // MOCK-DATA: cover image and logo are owned by the Pulse profile image editor.
  // The profile editor links to it rather than storing a second image, so there is
  // no business-specific cover to report on here.
  "business_cover_image"
] as const;

/* ------------------------------------------------------------ normalisation */

const str = (value: unknown): string => (typeof value === "string" ? value : "");
const num = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
const bool = (value: unknown): boolean => value === true;

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/**
 * Normalise a handle to exactly one leading `@`.
 *
 * The server already does this, and doing it again here is not redundancy for its
 * own sake: the header also renders handles that arrive from the Pulse profile and
 * from cached payloads written before the server fix, and `@@Pilot-8919` reaching a
 * buyer once is one time too many. Cheap, total, idempotent.
 */
export function normalizeHandle(value: unknown): string {
  const text = str(value).trim().replace(/^@+/, "");
  return text ? `@${text}` : "";
}

function normalizeCompletionItems(value: unknown): CompletionItem[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return {
      key: str(item.key),
      label: str(item.label),
      section: str(item.section)
    };
  });
}

function normalizeHours(value: unknown): OpeningDay[] {
  const byDay = new Map<string, Record<string, unknown>>();
  if (Array.isArray(value)) {
    for (const raw of value) {
      const day = (raw ?? {}) as Record<string, unknown>;
      byDay.set(str(day.weekday), day);
    }
  }
  // Seven entries, always, in week order — a partial week would let the editor
  // silently drop the days the seller never touched.
  return WEEKDAYS.map((weekday) => {
    const day = byDay.get(weekday) ?? {};
    return {
      weekday,
      label: str(day.label) || weekday,
      state: oneOf<DayState>(day.state, ["unset", "open", "closed"], "unset"),
      opens: str(day.opens) || null,
      closes: str(day.closes) || null
    };
  });
}

function normalizeOverrides(value: unknown): HoursOverride[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return {
        date: str(item.date),
        closed: bool(item.closed),
        opens: str(item.opens) || null,
        closes: str(item.closes) || null,
        label: str(item.label) || null
      };
    })
    .filter((item) => item.date);
}

function normalizeLinks(value: unknown): ProfileLink[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return {
        kind: oneOf<LinkKind>(item.kind, LINK_KINDS, "custom"),
        url: str(item.url),
        label: str(item.label) || null,
        position: num(item.position)
      };
    })
    .filter((link) => link.url)
    .sort((a, b) => a.position - b.position);
}

function normalizeAddresses(value: unknown): ProfileAddress[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return {
        kind: oneOf<AddressKind>(item.kind, ADDRESS_KINDS, "pickup"),
        line1: str(item.line1),
        line2: str(item.line2),
        city: str(item.city),
        region: str(item.region),
        postalCode: str(item.postal_code),
        country: str(item.country)
      };
    })
    .filter((address) => address.line1 || address.city || address.country);
}

function normalizeVerification(value: unknown): VerificationInfo {
  const raw = (value ?? {}) as Record<string, unknown>;
  const source = str(raw.source);
  return {
    state: oneOf<VerificationState>(raw.state, VERIFICATION_STATES, "not_started"),
    source:
      source === "verification_request" ||
      source === "seller_application" ||
      source === "verified_badge"
        ? source
        : "none",
    requestId: typeof raw.request_id === "number" ? raw.request_id : null,
    decidedAt: str(raw.decided_at) || null,
    note: str(raw.note)
  };
}

function normalizeLocks(value: unknown): ProfileLocks {
  const raw = (value ?? {}) as Record<string, unknown>;
  return {
    requiresReview: stringList(raw.requires_review),
    blocked: stringList(raw.blocked),
    explainer: str(raw.explainer)
  };
}

function normalizeCompletion(value: unknown): ProfileCompletion {
  const raw = (value ?? {}) as Record<string, unknown>;
  const completed = normalizeCompletionItems(raw.completed);
  const missing = normalizeCompletionItems(raw.missing);
  return {
    // Carried through, never recomputed: a locally derived percentage is how the
    // headline and the checklist under it come to disagree.
    percent: num(raw.percent),
    completed,
    missing,
    total: num(raw.total) || completed.length + missing.length,
    nextKey: str(raw.next_key) || null,
    nextLabel: str(raw.next_label) || null
  };
}

function normalizeSync(value: unknown): SyncInfo {
  const raw = (value ?? {}) as Record<string, unknown>;
  return {
    state: oneOf<SyncState>(raw.state, SYNC_STATES, "changes_pending"),
    publishedAt: str(raw.published_at) || null,
    updatedAt: str(raw.updated_at) || null
  };
}

export function normalizeOwnerProfile(value: unknown): OwnerProfile {
  const raw = (value ?? {}) as Record<string, unknown>;
  const contact = (raw.contact ?? {}) as Record<string, unknown>;
  const location = (raw.public_location ?? {}) as Record<string, unknown>;
  return {
    userId: num(raw.user_id),
    handle: normalizeHandle(raw.handle),
    businessName: str(raw.business_name),
    legalName: str(raw.legal_name),
    businessCategory: str(raw.business_category),
    businessCategoryLabel: str(raw.business_category_label),
    sellerType: str(raw.seller_type),
    tagline: str(raw.tagline),
    about: str(raw.about),
    whatYouSell: str(raw.what_you_sell),
    serviceArea: str(raw.service_area),
    shippingSummary: str(raw.shipping_summary),
    returnSummary: str(raw.return_summary),
    responseExpectations: str(raw.response_expectations),
    responseHours: str(raw.response_hours),
    languages: stringList(raw.languages),
    accessibility: stringList(raw.accessibility),
    publicLocation: {
      city: str(location.city),
      region: str(location.region),
      country: str(location.country)
    },
    contact: {
      email: str(contact.email),
      emailVisibility: oneOf<ContactVisibility>(
        contact.email_visibility,
        CONTACT_VISIBILITY,
        "private"
      ),
      phone: str(contact.phone),
      // Falls back to `private`, never to `public`. If the server ever sends a value
      // this client does not recognise, the safe reading is the closed one.
      phoneVisibility: oneOf<ContactVisibility>(
        contact.phone_visibility,
        CONTACT_VISIBILITY,
        "private"
      ),
      preferred: str(contact.preferred) || "message"
    },
    hoursMode: oneOf<HoursMode>(raw.hours_mode, HOURS_MODES, "unset"),
    hours: normalizeHours(raw.hours),
    hoursOverrides: normalizeOverrides(raw.hours_overrides),
    links: normalizeLinks(raw.links),
    addresses: normalizeAddresses(raw.addresses),
    verification: normalizeVerification(raw.verification),
    locks: normalizeLocks(raw.locks),
    completion: normalizeCompletion(raw.completion),
    sync: normalizeSync(raw.sync),
    publishedAt: str(raw.published_at) || null,
    updatedAt: str(raw.updated_at) || null
  };
}

export function normalizePublicProfile(value: unknown): PublicProfile {
  const raw = (value ?? {}) as Record<string, unknown>;
  const contact = (raw.contact ?? {}) as Record<string, unknown>;
  const policies = (raw.policies ?? {}) as Record<string, unknown>;

  // Built key by key rather than spread, for the same reason the server builds it
  // that way: a spread would carry through whatever the server sent, and the whole
  // point of this type is that it cannot carry `legal_name` or `addresses`.
  const out: PublicProfile = {
    handle: normalizeHandle(raw.handle),
    businessName: str(raw.business_name),
    businessCategory: str(raw.business_category),
    businessCategoryLabel: str(raw.business_category_label),
    verified: bool(raw.verified),
    tagline: str(raw.tagline),
    about: str(raw.about),
    whatYouSell: str(raw.what_you_sell),
    location: str(raw.location),
    shippingSummary: str(raw.shipping_summary),
    returnSummary: str(raw.return_summary),
    responseExpectations: str(raw.response_expectations),
    languages: stringList(raw.languages),
    accessibility: stringList(raw.accessibility),
    hoursMode: oneOf<HoursMode>(raw.hours_mode, HOURS_MODES, "unset"),
    hours: normalizeHours(raw.hours),
    hoursOverrides: normalizeOverrides(raw.hours_overrides),
    links: normalizeLinks(raw.links),
    contact: { preferred: str(contact.preferred) || "message" },
    memberSince: str(raw.member_since),
    policies: {}
  };

  // Absent keys are the signal for "not published". Copying them across as empty
  // strings would make every buyer profile look like it has a hidden email.
  if (str(contact.email)) out.contact.email = str(contact.email);
  if (str(contact.phone)) out.contact.phone = str(contact.phone);
  if (str(policies.returns)) out.policies.returns = str(policies.returns);
  if (str(policies.shipping)) out.policies.shipping = str(policies.shipping);
  if (str(policies.response)) out.policies.response = str(policies.response);

  return out;
}

/* ------------------------------------------------------------------- derived */

/** `"New York, United States"` — the coarse public line, never an address. */
export function publicLocationLine(profile: OwnerProfile): string {
  return [profile.publicLocation.city, profile.publicLocation.region, profile.publicLocation.country]
    .filter(Boolean)
    .join(", ");
}

/**
 * `Open now · Closes at 17:30`, `Closed`, or `Hours not provided`.
 *
 * `now` is injected so this is testable at any hour rather than only at the hour the
 * suite happens to run. Dated overrides are checked ahead of the weekly pattern:
 * a Christmas closure that the weekly grid overrules is a buyer standing outside a
 * locked door.
 */
export function openingStatus(
  profile: Pick<OwnerProfile, "hoursMode" | "hours" | "hoursOverrides">,
  now: Date = new Date()
): { state: "open" | "closed" | "unknown" | "appointment" | "temporarily_closed"; label: string } {
  if (profile.hoursMode === "temporarily_closed") {
    return { state: "temporarily_closed", label: "Temporarily closed" };
  }
  if (profile.hoursMode === "by_appointment") {
    return { state: "appointment", label: "By appointment" };
  }
  if (profile.hoursMode !== "weekly") {
    return { state: "unknown", label: "Hours not provided" };
  }

  const iso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const minutes = now.getHours() * 60 + now.getMinutes();

  const override = profile.hoursOverrides.find((entry) => entry.date === iso);
  if (override) {
    if (override.closed) {
      return { state: "closed", label: override.label ? `Closed · ${override.label}` : "Closed" };
    }
    if (override.opens && override.closes) {
      return withinWindow(minutes, override.opens, override.closes);
    }
  }

  const today = profile.hours[(now.getDay() + 6) % 7]; // JS weeks start Sunday; ours start Monday.
  if (!today || today.state === "unset") return { state: "unknown", label: "Hours not provided" };
  if (today.state === "closed") return { state: "closed", label: "Closed today" };
  if (!today.opens || !today.closes) return { state: "unknown", label: "Hours not provided" };
  return withinWindow(minutes, today.opens, today.closes);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function toMinutes(value: string): number | null {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function withinWindow(
  minutes: number,
  opens: string,
  closes: string
): { state: "open" | "closed" | "unknown"; label: string } {
  const from = toMinutes(opens);
  const to = toMinutes(closes);
  if (from === null || to === null) return { state: "unknown", label: "Hours not provided" };
  if (minutes < from) return { state: "closed", label: `Closed · Opens at ${opens}` };
  if (minutes >= to) return { state: "closed", label: "Closed" };
  return { state: "open", label: `Open now · Closes at ${closes}` };
}

/**
 * Whether editing this field will send it to a reviewer.
 *
 * Note the deliberate asymmetry with {@link fieldIsBlocked}: "a reviewer will look at
 * this" is not "you cannot change this". The old screen collapsed the two and froze
 * thirteen fields because one was under review.
 */
export function fieldRequiresReview(locks: ProfileLocks, field: string): boolean {
  return locks.requiresReview.includes(field);
}

export function fieldIsBlocked(locks: ProfileLocks, field: string): boolean {
  return locks.blocked.includes(field);
}

/* ------------------------------------------------------------------- fetches */

const OWNER_CACHE_KEY = "pulse.business.profile.v1";
const DRAFT_CACHE_PREFIX = "pulse.business.profile.draft.v1.";

type CachedOwner = { savedAt: number; profile: OwnerProfile };

export async function fetchOwnerProfile(): Promise<OwnerProfile> {
  const payload = await pulseApi<{ ok?: boolean; profile?: unknown }>(
    "/api/pulse/business/profile"
  );
  return normalizeOwnerProfile(payload?.profile);
}

export async function readCachedOwnerProfile(): Promise<CachedOwner | null> {
  return readJsonCache<CachedOwner>(OWNER_CACHE_KEY, (value) => ({
    savedAt: num(value?.savedAt),
    profile: normalizeOwnerProfile(value?.profile)
  }));
}

/**
 * Load the owner view, falling back to the last good copy when the network is gone.
 *
 * The cached copy is served with `fromCache: true` so the screen can say how old it
 * is. It is never served silently: an identity screen that shows a stale business
 * name without saying so invites the seller to "fix" a change they already made.
 */
export async function loadOwnerProfile(): Promise<OwnerProfileLoad> {
  try {
    const profile = await fetchOwnerProfile();
    await writeJsonCache<CachedOwner>(OWNER_CACHE_KEY, {
      savedAt: Date.now(),
      profile
    }).catch(() => undefined);
    return { state: "ready", profile, fromCache: false, savedAt: Date.now() };
  } catch (error) {
    const cached = await readCachedOwnerProfile().catch(() => null);
    if (cached?.profile && (error instanceof PulseApiError ? error.status !== 401 : true)) {
      return {
        state: "ready",
        profile: cached.profile,
        fromCache: true,
        savedAt: cached.savedAt || null
      };
    }
    return { state: "failed", failure: failureFrom(error, "Your business profile") };
  }
}

/**
 * Save changed fields only.
 *
 * Callers pass the diff, not the whole form. Sending every field would make the audit
 * trail — which exists so a reviewer can see what a verified business changed — a log
 * of thirteen no-op writes per save, and would defeat the server's own
 * unchanged-value check.
 */
export async function saveProfileFields(fields: Record<string, unknown>): Promise<SaveResult> {
  const payload = await pulseApi<Record<string, unknown>>("/api/pulse/business/profile", {
    method: "POST",
    body: JSON.stringify(fields)
  });
  return {
    saved: (payload?.saved ?? {}) as Record<string, unknown>,
    rejected: (payload?.rejected ?? {}) as Record<string, string>,
    queuedForReview: stringList(payload?.queued_for_review),
    ignored: stringList(payload?.ignored),
    profile: normalizeOwnerProfile(payload?.profile)
  };
}

export async function saveHours(
  mode: HoursMode,
  days: Array<{ weekday: Weekday; closed?: boolean; opens?: string | null; closes?: string | null }>
): Promise<OwnerProfile> {
  const payload = await pulseApi<{ profile?: unknown }>("/api/pulse/business/profile/hours", {
    method: "POST",
    body: JSON.stringify({ mode, days })
  });
  return normalizeOwnerProfile(payload?.profile);
}

export async function saveHoursOverride(entry: {
  date: string;
  closed?: boolean;
  opens?: string | null;
  closes?: string | null;
  label?: string | null;
}): Promise<OwnerProfile> {
  const payload = await pulseApi<{ profile?: unknown }>("/api/pulse/business/profile/hours", {
    method: "POST",
    body: JSON.stringify(entry)
  });
  return normalizeOwnerProfile(payload?.profile);
}

/** An empty `url` removes the link — "remove" and "set" are the same call. */
export async function saveLink(
  kind: LinkKind,
  url: string,
  options: { label?: string | null; position?: number } = {}
): Promise<OwnerProfile> {
  const payload = await pulseApi<{ profile?: unknown }>("/api/pulse/business/profile/link", {
    method: "POST",
    body: JSON.stringify({ kind, url, label: options.label ?? null, position: options.position ?? 0 })
  });
  return normalizeOwnerProfile(payload?.profile);
}

export async function saveAddress(
  kind: AddressKind,
  address: Partial<Omit<ProfileAddress, "kind">>
): Promise<OwnerProfile> {
  const payload = await pulseApi<{ profile?: unknown }>("/api/pulse/business/profile/address", {
    method: "POST",
    body: JSON.stringify({
      kind,
      line1: address.line1 ?? "",
      line2: address.line2 ?? "",
      city: address.city ?? "",
      region: address.region ?? "",
      postal_code: address.postalCode ?? "",
      country: address.country ?? ""
    })
  });
  return normalizeOwnerProfile(payload?.profile);
}

/**
 * Ask before committing. Returns `available: false` with a reason rather than
 * throwing — "that handle is taken" is an answer, not an error, and the editor
 * renders it inline while the seller is still typing.
 */
export async function checkHandle(candidate: string): Promise<HandleCheck> {
  const payload = await pulseApi<{ handle?: unknown }>(
    `/api/pulse/business/profile/handle-check?handle=${encodeURIComponent(candidate)}`
  );
  const raw = (payload?.handle ?? {}) as Record<string, unknown>;
  return {
    candidate: str(raw.candidate),
    handle: normalizeHandle(raw.handle),
    available: bool(raw.available),
    reason: str(raw.reason),
    isCurrent: bool(raw.is_current)
  };
}

export async function publishProfile(): Promise<OwnerProfile> {
  const payload = await pulseApi<{ profile?: unknown }>("/api/pulse/business/profile/publish", {
    method: "POST"
  });
  return normalizeOwnerProfile(payload?.profile);
}

export async function fetchSyncStatus(): Promise<SyncStatus> {
  const payload = await pulseApi<Record<string, unknown>>("/api/pulse/business/profile/sync");
  const completion = (payload?.completion ?? {}) as Record<string, unknown>;
  return {
    sync: normalizeSync(payload?.sync),
    verification: normalizeVerification(payload?.verification),
    publishedAt: str(payload?.published_at) || null,
    updatedAt: str(payload?.updated_at) || null,
    reviewProtectedFields: stringList(payload?.review_protected_fields),
    blockedFields: stringList(payload?.blocked_fields),
    completion: {
      percent: num(completion.percent),
      completed: num(completion.completed),
      total: num(completion.total)
    }
  };
}

/** "View as buyer": the owner's own public profile, at its strictest. */
export async function fetchBuyerPreview(): Promise<{ profile: PublicProfile; preview: PreviewBanner }> {
  const payload = await pulseApi<{ profile?: unknown; preview?: unknown }>(
    "/api/pulse/business/profile/preview"
  );
  const banner = (payload?.preview ?? {}) as Record<string, unknown>;
  return {
    profile: normalizePublicProfile(payload?.profile),
    preview: {
      active: bool(banner.active),
      title: str(banner.title) || "Buyer preview",
      subtitle: str(banner.subtitle) || "This is how your public business profile appears.",
      exitLabel: str(banner.exit_label) || "Exit preview",
      simulatedActions: stringList(banner.simulated_actions)
    }
  };
}

export async function fetchPublicProfile(
  sellerUserId: number
): Promise<{ profile: PublicProfile; isSelf: boolean }> {
  const payload = await pulseApi<{ profile?: unknown; is_self?: unknown }>(
    `/api/pulse/business/profile/${sellerUserId}`
  );
  return { profile: normalizePublicProfile(payload?.profile), isSelf: bool(payload?.is_self) };
}

/* --------------------------------------------------------------- vocabulary */

export type ProfileVocabularies = {
  businessCategories: { value: string; label: string }[];
  contactVisibility: { value: string; label: string }[];
  preferredContact: string[];
  hoursModes: { value: string; label: string }[];
  weekdays: { value: string; label: string }[];
  linkKinds: string[];
  addressKinds: string[];
};

const VOCAB_CACHE_KEY = "pulse.business.profile.vocab.v1";

function optionList(value: unknown): { value: string; label: string }[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return { value: str(item.value), label: str(item.label) || str(item.value) };
    })
    .filter((item) => Boolean(item.value));
}

/**
 * The pickers, from the server.
 *
 * Cached, and the cache is read first: a category picker that cannot open because the
 * network is slow is worse than one showing the list from an hour ago, and this list
 * changes at the rate of a deploy. If both the network and the cache come back empty
 * the editor renders the seller's *current* value as the only option rather than an
 * empty list, so they can still back out without being told to pick from nothing.
 */
export async function fetchVocabularies(): Promise<ProfileVocabularies> {
  const cached = await readJsonCache<ProfileVocabularies>(VOCAB_CACHE_KEY, (value) => value as ProfileVocabularies);
  try {
    const payload = await pulseApi<{ vocabularies?: unknown }>(
      "/api/pulse/business/profile/vocabularies"
    );
    const raw = (payload?.vocabularies ?? {}) as Record<string, unknown>;
    const parsed: ProfileVocabularies = {
      businessCategories: optionList(raw.business_categories),
      contactVisibility: optionList(raw.contact_visibility),
      preferredContact: stringList(raw.preferred_contact),
      hoursModes: optionList(raw.hours_modes),
      weekdays: optionList(raw.weekdays),
      linkKinds: stringList(raw.link_kinds),
      addressKinds: stringList(raw.address_kinds)
    };
    if (parsed.businessCategories.length) await writeJsonCache(VOCAB_CACHE_KEY, parsed);
    return parsed;
  } catch {
    if (cached) return cached;
    return {
      businessCategories: [],
      contactVisibility: [],
      preferredContact: [],
      hoursModes: [],
      weekdays: [],
      linkKinds: [],
      addressKinds: []
    };
  }
}

/* ---------------------------------------------------------------- local draft
 *
 * An unsent edit survives a backgrounded app. Kept per-field rather than as a whole
 * profile snapshot so that restoring a draft cannot revert a field the seller
 * successfully saved from another device in the meantime.
 */

export type ProfileDraft = { savedAt: number; fields: Record<string, unknown> };

export async function readDraft(scope: string): Promise<ProfileDraft | null> {
  return readJsonCache<ProfileDraft>(`${DRAFT_CACHE_PREFIX}${scope}`, (value) => ({
    savedAt: num(value?.savedAt),
    fields: (value?.fields ?? {}) as Record<string, unknown>
  }));
}

export async function writeDraft(scope: string, fields: Record<string, unknown>): Promise<void> {
  await writeJsonCache<ProfileDraft>(`${DRAFT_CACHE_PREFIX}${scope}`, {
    savedAt: Date.now(),
    fields
  }).catch(() => undefined);
}

export async function clearDraft(scope: string): Promise<void> {
  await writeJsonCache<ProfileDraft>(`${DRAFT_CACHE_PREFIX}${scope}`, {
    savedAt: 0,
    fields: {}
  }).catch(() => undefined);
}

/** The diff a save should send. Unchanged fields are omitted — see `saveProfileFields`. */
export function changedFields(
  original: Record<string, unknown>,
  edited: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(edited)) {
    const before = original[key];
    if (Array.isArray(value) || Array.isArray(before)) {
      if (JSON.stringify(value ?? []) !== JSON.stringify(before ?? [])) out[key] = value;
      continue;
    }
    if (String(value ?? "") !== String(before ?? "")) out[key] = value;
  }
  return out;
}
