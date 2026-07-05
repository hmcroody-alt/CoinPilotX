import * as DocumentPicker from "expo-document-picker";
import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
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
};

export type VerificationActionResponse = {
  ok?: boolean;
  message?: string;
  request_id?: number;
  status?: string;
  verification_type?: string;
  document_type?: string;
};

export const verificationTracks: VerificationTrack[] = [
  {
    key: "identity",
    label: "Identity",
    detail: "Strengthens account trust and connected profile safety signals.",
    documentRequired: true
  },
  {
    key: "blue_check",
    label: "Blue Check",
    detail: "Requests the public verification badge path already owned by PulseSoc account review.",
    documentRequired: false
  },
  {
    key: "business",
    label: "Business",
    detail: "Supports marketplace, organization, advertiser, and business profile trust.",
    documentRequired: true
  },
  {
    key: "government_id",
    label: "Government ID",
    detail: "Uses the existing private document review route for sensitive identity evidence.",
    documentRequired: true
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

export async function openVerificationWebFallback(path = "/dashboard/account/verification") {
  const safePath = path.startsWith("/") && !path.startsWith("//") ? path : "/dashboard/account/verification";
  await Linking.openURL(`${PULSE_API_BASE_URL}${safePath}`).catch(() => undefined);
}

export function verificationStatusLabel(status?: string) {
  const value = String(status || "not_started").replace(/_/g, " ");
  return value.replace(/\b\w/g, (letter) => letter.toUpperCase());
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
    checklist: [
      { key: "profile", label: "Profile identity completed", complete: profileComplete, detail: "Display name, handle, and avatar are loaded from the existing Profile APIs." },
      { key: "email", label: "Email trust confirmed", complete: emailVerified, detail: "Email verification remains owned by Account/Security APIs." },
      { key: "request", label: "Verification request submitted", complete: hasRequest, detail: "Requests use `/api/dashboard/account/verification/request`." },
      { key: "document", label: "Private evidence ready", complete: !needsDocument || status === "approved" || status === "in_review", detail: "Sensitive documents use the existing private verification upload route." },
      { key: "review", label: "Admin review completed", complete: approved, detail: "Review, approval, rejection, revocation, and audit logs remain admin/server-owned." }
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
    checklist: Array.isArray(state.checklist) ? state.checklist : [],
    tracks: Array.isArray(state.tracks) && state.tracks.length ? state.tracks : verificationTracks,
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
