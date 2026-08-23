import * as DocumentPicker from "expo-document-picker";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const VERIFICATION_CACHE_KEY = "pulsesoc.native.verification.state";

export type VerificationStatus = "not_started" | "draft" | "submitted" | "in_review" | "needs_more_info" | "approved" | "rejected" | "suspended" | "appealed";

export type VerificationTrackKey = "identity" | "blue_check" | "business" | "government_id";

export type VerificationSubsystem = {
  key?: string;
  state?: string;
  score?: number;
  status?: VerificationStatus | string;
  primary_action?: string;
  recommendations?: string[];
  metrics?: {
    request_id?: number;
    status?: string;
    type?: string;
    [key: string]: unknown;
  };
};

export type VerificationState = {
  status: VerificationStatus;
  score: number;
  requestId: number;
  verificationType: VerificationTrackKey;
  primaryAction: string;
  recommendations: string[];
  profilePreview: {
    displayName: string;
    username: string;
    verifiedBadge: boolean;
    verificationStatus: string;
  };
  premiumBadges: {
    premiumActive: boolean;
    founderActive: boolean;
    founderNumber: number;
    plan: string;
  };
  /** Server's answer to "may this account submit a Blue Check request".
   *
   *  Read from `/api/premium/status`, which resolves it through the same
   *  entitlement function the submit endpoint gates on, so the button this
   *  drives cannot promise something the POST then refuses. It is deliberately
   *  separate from `premiumBadges.premiumActive`: holding a membership is not
   *  the same fact as holding this capability, and the client must not infer
   *  one from the other. Absent or unparseable means locked. */
  blueCheckApplyAllowed: boolean;
  checklist: VerificationChecklistItem[];
  tracks: VerificationTrack[];
  loadedAt: string;
};

export type VerificationChecklistItem = {
  key: string;
  label: string;
  complete: boolean;
  detail: string;
};

export type VerificationTrack = {
  key: VerificationTrackKey;
  label: string;
  detail: string;
  documentRequired: boolean;
  /** True when submitting on this track requires an active PulseSoc Premium
   *  membership. It gates the *application*, never the decision. */
  premiumGated: boolean;
};

export type VerificationActionResponse = {
  ok?: boolean;
  message?: string;
  request_id?: number;
  status?: string;
  verification_type?: string;
  document_type?: string;
  /** Set when the server matched an application already open on this track and
   *  returned it instead of creating a second one. */
  duplicate?: boolean;
};

export const verificationTracks: VerificationTrack[] = [
  {
    key: "identity",
    label: "Identity",
    detail: "Strengthens account trust and connected profile safety signals.",
    documentRequired: true,
    premiumGated: false
  },
  {
    key: "blue_check",
    label: "Blue Check",
    detail: "Asks for the public checkmark shown next to your name.",
    documentRequired: false,
    premiumGated: true
  },
  {
    key: "business",
    label: "Business",
    detail: "Supports marketplace, organization, advertiser, and business profile trust.",
    documentRequired: true,
    premiumGated: false
  },
  {
    key: "government_id",
    label: "Government ID",
    detail: "For proving who you are with a passport, licence, or ID card.",
    documentRequired: true,
    premiumGated: false
  }
];

export async function loadVerificationState() {
  const data = await pulseApi<{ ok?: boolean; account?: { subsystems?: { verification?: VerificationSubsystem }; account_score?: number; verification_status?: string } }>("/api/dashboard/account/state");
  const profile = await pulseApi<{ ok?: boolean; user?: Record<string, unknown> }>("/api/pulse/profile/me").catch(() => ({ user: {} }));
  const premium = await pulseApi<Record<string, unknown>>("/api/premium/status").catch(() => ({}));
  const state = normalizeVerificationState(data.account?.subsystems?.verification || {}, data.account || {}, profile.user || {}, premium || {});
  await writeJsonCache(VERIFICATION_CACHE_KEY, state).catch(() => undefined);
  return state;
}

export async function loadCachedVerificationState() {
  return readJsonCache<VerificationState>(VERIFICATION_CACHE_KEY, (state) => normalizeVerificationStateFromCache(state));
}

export async function startVerificationRequest(verificationType: VerificationTrackKey) {
  return pulseApi<VerificationActionResponse>("/api/dashboard/account/verification/request", {
    method: "POST",
    body: JSON.stringify({
      verification_type: verificationType,
      source: "mobile_native_verification_center"
    })
  });
}

export async function submitVerificationAppeal(requestId: number, appealNote: string) {
  return pulseApi<VerificationActionResponse>("/api/dashboard/account/verification/appeal", {
    method: "POST",
    body: JSON.stringify({
      request_id: requestId,
      appeal_note: appealNote,
      source: "mobile_native_verification_center"
    })
  });
}

export async function pickAndUploadVerificationDocument(requestId: number, documentType: "government_id" | "business_document" | "selfie" = "government_id") {
  const result = await DocumentPicker.getDocumentAsync({
    copyToCacheDirectory: true,
    multiple: false,
    type: ["image/jpeg", "image/png", "image/webp", "application/pdf"]
  });
  if (result.canceled || !result.assets?.[0]) return { ok: false, message: "Document upload cancelled." };
  return uploadVerificationDocument(requestId, {
    uri: result.assets[0].uri,
    name: result.assets[0].name || "verification-document",
    mimeType: result.assets[0].mimeType || "application/octet-stream",
    documentType
  });
}

export async function uploadVerificationDocument(
  requestId: number,
  input: { uri: string; name: string; mimeType: string; documentType?: "government_id" | "business_document" | "selfie" }
) {
  const form = new FormData();
  form.append("request_id", String(requestId || 0));
  form.append("document_type", input.documentType || "government_id");
  form.append("file", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  return pulseApi<VerificationActionResponse>("/api/dashboard/account/verification/document", {
    method: "POST",
    body: form
  });
}

export function verificationStatusLabel(status?: string) {
  const value = String(status || "not_started").replace(/_/g, " ");
  return value.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function verificationTrackLabel(track: VerificationTrackKey | string) {
  return String(track || "identity")
    .replace(/_/g, " ")
    .replace(/\bid\b/i, "ID")
    .replace(/\b(?!ID\b)\w/g, (letter) => letter.toUpperCase());
}

export type VerificationActionKey = "start" | "continue" | "update" | "add_another" | "document" | "appeal";

export type VerificationAction = {
  key: VerificationActionKey;
  label: string;
  variant: "primary" | "secondary";
};

export type VerificationActionSet = {
  /** One sentence saying where the request actually stands, in the person's
   *  own terms. Shown in place of a call to action when there is nothing for
   *  them to do. */
  headline: string;
  /** Offered in the "Choose verification path" panel. */
  path: VerificationAction[];
  /** Offered in the "Private document handoff" panel. */
  document: VerificationAction[];
  /** Offered in the "Appeal or review" panel. */
  appeal: VerificationAction[];
  /** False once a request is with reviewers: the track is fixed until they
   *  decide, so the chooser must not invite a change it cannot make. */
  canChooseTrack: boolean;
  /** True when the selected track needs Premium and this account does not have
   *  it. The screen shows the membership panel in place of the submit button.
   *  Never set while a request is already with reviewers — see below. */
  premiumLocked: boolean;
};

/**
 * What the Verification Center offers at each status.
 *
 * This is derived here, once, rather than in the screen, because "may this
 * person start a request" is a fact about verification rather than about a
 * layout. The screen previously derived nothing at all — it rendered "Start
 * verification request" unconditionally — so someone already approved was
 * invited to begin a request they had finished, and someone whose request was
 * under review could fire a duplicate.
 *
 * Two actions the review asked for are deliberately absent. "View" has no
 * destination: the status and badge panels above the button *are* the view, and
 * a button that scrolls nowhere is the same dead control this work exists to
 * remove. "Update" is not a separate action either — re-submitting the approved
 * track is the only update the request endpoint supports, so it is the same
 * control as "Add another" with a different label, chosen by which track is
 * selected. Both are recorded as gaps rather than shipped as buttons.
 */
export function verificationActions(input: {
  status: VerificationStatus;
  requestId?: number;
  selectedTrack?: VerificationTrackKey;
  currentTrack?: VerificationTrackKey;
  /** Whether this account may submit on the selected track. Only meaningful for
   *  Premium-gated tracks; pass true for everything else. */
  canSubmitSelectedTrack?: boolean;
}): VerificationActionSet {
  const base = unlockedVerificationActions(input);
  if (input.canSubmitSelectedTrack !== false) return { ...base, premiumLocked: false };

  // Every action in `path` posts to the gated submit endpoint, so all of them —
  // start, continue, update, add another — must go when the capability is
  // missing. Leaving one would render a button the server answers with 403.
  //
  // `document` and `appeal` deliberately survive. A membership that lapses
  // mid-review must not strand a request that is already with reviewers: the
  // person can still send the evidence they were asked for and still appeal a
  // refusal. The server agrees — only submission is gated.
  //
  // The headline is only replaced when there was something to offer. When the
  // switch already returned an empty path the request is with reviewers, and
  // its real status is the honest thing to say; overwriting it with a sales
  // message would be the "shows a state that isn't real" failure this screen
  // exists to avoid.
  return {
    ...base,
    headline: base.path.length ? PREMIUM_LOCKED_HEADLINE : base.headline,
    path: [],
    premiumLocked: true
  };
}

/** Copy for a locked track. States the exchange exactly: membership buys the
 *  application, not the checkmark. Must never imply a purchased outcome. */
export const PREMIUM_LOCKED_HEADLINE = "Blue Check applications are included with PulseSoc Premium. Premium opens the application — a reviewer still decides.";

function unlockedVerificationActions(input: {
  status: VerificationStatus;
  requestId?: number;
  selectedTrack?: VerificationTrackKey;
  currentTrack?: VerificationTrackKey;
}): Omit<VerificationActionSet, "premiumLocked"> {
  const status = normalizeStatus(input.status);
  const requestId = Number(input.requestId || 0);
  const selected = normalizeTrack(String(input.selectedTrack || "identity"));
  const current = normalizeTrack(String(input.currentTrack || "identity"));
  const track = verificationTrackLabel(selected);

  // Evidence can only be attached to a request that exists and is still open.
  const documentable = ["draft", "submitted", "in_review", "needs_more_info", "appealed"].includes(status) && requestId > 0;
  const document: VerificationAction[] = documentable
    ? [{ key: "document", label: "Choose a document", variant: status === "needs_more_info" ? "primary" : "secondary" }]
    : [];

  // An appeal needs a decision to appeal against, and one that has not already
  // been appealed.
  const appealable = ["rejected", "suspended", "needs_more_info"].includes(status) && requestId > 0;
  const appeal: VerificationAction[] = appealable ? [{ key: "appeal", label: "Submit appeal", variant: status === "needs_more_info" ? "secondary" : "primary" }] : [];

  switch (status) {
    case "draft":
      return {
        headline: "Your request is saved but has not been sent yet.",
        path: [{ key: "continue", label: `Send your ${track} request`, variant: "primary" }],
        document,
        appeal,
        canChooseTrack: true
      };
    case "submitted":
      return {
        headline: "Your request has been sent and is waiting to be picked up.",
        path: [],
        document,
        appeal,
        canChooseTrack: false
      };
    case "in_review":
      return {
        headline: "Someone is reviewing your request. Nothing is needed from you.",
        path: [],
        document,
        appeal,
        canChooseTrack: false
      };
    case "needs_more_info":
      return {
        headline: "Reviewers need something more from you before they can decide.",
        path: [],
        document,
        appeal,
        canChooseTrack: false
      };
    case "approved":
      return {
        headline:
          selected === current
            ? `You are verified. You can send your ${track} details again if they have changed.`
            : `You are verified. You can add ${track} verification as well.`,
        path:
          selected === current
            ? [{ key: "update", label: `Update your ${track} details`, variant: "secondary" }]
            : [{ key: "add_another", label: `Add ${track} verification`, variant: "secondary" }],
        document,
        appeal,
        canChooseTrack: true
      };
    case "rejected":
      return {
        headline: "Your request was not approved. You can appeal, or start again.",
        path: [{ key: "start", label: `Start a new ${track} request`, variant: "secondary" }],
        document,
        appeal,
        canChooseTrack: true
      };
    case "suspended":
      return {
        headline: "Your verification is suspended. Appealing is the only way to reopen it.",
        path: [],
        document,
        appeal,
        canChooseTrack: false
      };
    case "appealed":
      return {
        headline: "Your appeal has been sent and is waiting on a decision.",
        path: [],
        document,
        appeal,
        canChooseTrack: false
      };
    default:
      return {
        headline: "You have not started a verification request yet.",
        path: [{ key: "start", label: `Start ${track} verification`, variant: "primary" }],
        document,
        appeal,
        canChooseTrack: true
      };
  }
}

function normalizeVerificationState(
  subsystem: VerificationSubsystem,
  account: Record<string, unknown>,
  profile: Record<string, unknown>,
  premium: Record<string, unknown>
): VerificationState {
  const metrics = subsystem.metrics || {};
  const status = normalizeStatus(String(metrics.status || subsystem.status || account.verification_status || "not_started"));
  const type = normalizeTrack(String(metrics.type || "identity"));
  const requestId = Number(metrics.request_id || 0);
  const profileComplete = Boolean(profile.display_name || profile.full_name) && Boolean(profile.username) && Boolean(profile.avatar_url || profile.avatar_thumbnail_url);
  const emailVerified = Boolean(profile.email_verified || account.email_verified);
  const hasRequest = requestId > 0 || status !== "not_started";
  const approved = status === "approved";
  const needsDocument = ["identity", "government_id", "business"].includes(type);
  return {
    status,
    score: Number(subsystem.score || (approved ? 100 : hasRequest ? 55 : 25)),
    requestId,
    verificationType: type,
    primaryAction: String(subsystem.primary_action || (hasRequest ? "Review Verification" : "Continue Verification")),
    recommendations: normalizeStringList(subsystem.recommendations || []),
    profilePreview: {
      displayName: String(profile.display_name || profile.full_name || profile.username || "PulseSoc member"),
      username: String(profile.username || "").replace(/^@/, ""),
      verifiedBadge: Boolean(profile.verified_badge || approved),
      verificationStatus: status
    },
    premiumBadges: {
      premiumActive: Boolean(premium.premium_active || premium.founder_active),
      founderActive: Boolean(premium.founder_active),
      founderNumber: Number(premium.founder_number || 0),
      plan: String(premium.plan || (premium.founder_active ? "founder_premium" : premium.premium_active ? "premium" : "free"))
    },
    blueCheckApplyAllowed: premium.blue_check_apply === true,
    checklist: [
      { key: "profile", label: "Your profile is filled in", complete: profileComplete, detail: "Your name, handle, and photo are set." },
      { key: "email", label: "Your email is confirmed", complete: emailVerified, detail: "You have confirmed the email address on your account." },
      { key: "request", label: "You have sent a request", complete: hasRequest, detail: "Reviewers cannot begin until a request is sent." },
      { key: "document", label: "Your documents are in", complete: !needsDocument || status === "approved" || status === "in_review", detail: "Documents you send are held privately and are seen only by the review team." },
      { key: "review", label: "A reviewer has decided", complete: approved, detail: "A person reviews every request. Approvals and refusals are theirs to make, not this app's." }
    ],
    tracks: verificationTracks,
    loadedAt: new Date().toISOString()
  };
}

function normalizeVerificationStateFromCache(state: VerificationState): VerificationState {
  return {
    ...state,
    status: normalizeStatus(state.status),
    score: Number(state.score || 0),
    requestId: Number(state.requestId || 0),
    verificationType: normalizeTrack(state.verificationType),
    recommendations: normalizeStringList(state.recommendations || []),
    // A cache written before this field existed has no opinion, and "no
    // opinion" must read as locked rather than as permission.
    blueCheckApplyAllowed: state.blueCheckApplyAllowed === true,
    checklist: Array.isArray(state.checklist) ? state.checklist : [],
    // Always the current catalog, never the cached copy. The tracks are a static
    // constant that no server populates, and a cache written before Blue Check
    // was gated would carry entries with no `premiumGated` flag — restoring it
    // would show the submit button unlocked until the network call landed.
    tracks: verificationTracks,
    loadedAt: state.loadedAt || ""
  };
}

function normalizeStatus(status: string): VerificationStatus {
  const value = String(status || "not_started").toLowerCase();
  if (["draft", "submitted", "in_review", "needs_more_info", "approved", "rejected", "suspended", "appealed"].includes(value)) return value as VerificationStatus;
  return value === "pending" || value === "review" ? "submitted" : "not_started";
}

function normalizeTrack(track: string): VerificationTrackKey {
  const value = String(track || "identity").toLowerCase();
  if (value === "business" || value === "government_id" || value === "blue_check") return value;
  return "identity";
}

function normalizeStringList(items: unknown[]) {
  return items.map((item) => String(item || "").trim()).filter(Boolean);
}
