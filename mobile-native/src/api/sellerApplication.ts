/**
 * The native seller application contract.
 *
 * Every shape here mirrors `seller_lifecycle.applicant_view` on the server.
 * That view is a whitelist, not a redaction: it builds only the keys an
 * applicant is allowed to see, so reviewer notes, risk scores, and assignment
 * cannot leak into this file by being forgotten. Nothing in this module tries
 * to reconstruct them, and nothing here decides status — status is only ever
 * what the server just told us it is.
 */
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const SELLER_APPLICATION_CACHE_KEY = "pulsesoc.native.seller.application";

export type SellerApplicationStatus =
  | "draft"
  | "submitted"
  | "under_review"
  | "information_requested"
  | "resubmitted"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "expired"
  | "suspended";

export type SellerApplicationNextAction = {
  action: "continue" | "wait" | "respond" | "open_seller_tools" | "reapply" | "contact_support" | "restart";
  label: string;
};

export type SellerApplicationStep = {
  key: string;
  title: string;
  summary: string;
  fields: string[];
  complete: boolean;
  errors: Record<string, string>;
};

export type SellerApplicationDocument = {
  id: number;
  type: string;
  label: string;
  filename: string;
  size_kb: number;
  uploaded_at: string;
  state: string;
};

export type SellerApplicationFields = {
  seller_type?: string;
  seller_intent?: string[];
  full_name?: string;
  display_name?: string;
  country?: string;
  state_region?: string;
  email?: string;
  phone?: string;
  pulse_username?: string;
  business_name?: string;
  website?: string;
  social_links?: string;
  years_experience?: string;
  business_description?: string;
  sold_online_before?: string;
  banned_elsewhere?: string;
  guaranteed_profits?: string;
  comply_rules?: string;
  understand_claims?: string;
  marketplace_rules?: string;
  anti_scam_agreement?: string;
  no_profit_guarantees?: string;
};

export type SellerApplicationView = {
  application_id: number;
  status: SellerApplicationStatus;
  status_title: string;
  status_message: string;
  next_action: SellerApplicationNextAction;
  editable: boolean;
  completeness: number;
  fields: SellerApplicationFields;
  documents: SellerApplicationDocument[];
  steps: SellerApplicationStep[];
  can_submit: boolean;
  information_request: string;
  submitted_at: string;
  updated_at: string;
  seller_types: { key: string; label: string }[];
  selling_intents: string[];
  required_documents: { key: string; label: string }[];
  optional_documents: { key: string; label: string }[];
};

export type SellerApplicationResponse = {
  ok?: boolean;
  message?: string;
  application?: SellerApplicationView;
};

const EMPTY_NEXT_ACTION: SellerApplicationNextAction = { action: "continue", label: "Continue application" };

export function emptySellerApplication(): SellerApplicationView {
  return {
    application_id: 0,
    status: "draft",
    status_title: "Draft",
    status_message: "Your application has not been submitted yet.",
    next_action: EMPTY_NEXT_ACTION,
    editable: true,
    completeness: 0,
    fields: {},
    documents: [],
    steps: [],
    can_submit: false,
    information_request: "",
    submitted_at: "",
    updated_at: "",
    seller_types: [],
    selling_intents: [],
    required_documents: [],
    optional_documents: []
  };
}

function labelledList(value: unknown): { key: string; label: string }[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (typeof entry === "string") return { key: entry, label: entry };
      // The server names this key `value` for seller types and `type` for
      // documents. Accept both rather than making the two endpoints agree,
      // because those names are meaningful on their own side.
      const record = (entry || {}) as Record<string, unknown>;
      const key = String(record.key || record.value || record.type || "");
      if (!key) return null;
      return { key, label: String(record.label || key) };
    })
    .filter((entry): entry is { key: string; label: string } => Boolean(entry));
}

/**
 * Read a percentage that is always renderable.
 *
 * `Number("later")` is `NaN`, and `NaN` survives both `Math.min` and `Math.max`
 * unchanged — so a single non-numeric completeness would flow straight into the
 * progress bar's width and the label an assistive reader announces. Anything
 * that is not a real number is read as no progress at all.
 */
function percentage(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(100, Math.round(parsed)));
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => String(entry || "")).filter(Boolean);
}

/**
 * Coerce whatever the server sent into the shape the screen renders.
 *
 * Deliberately total: a missing or malformed key becomes a safe default rather
 * than an exception, because a half-parsed response must not be able to strand
 * an applicant on a blank screen mid-application.
 */
export function normalizeSellerApplication(raw: unknown): SellerApplicationView {
  const source = (raw || {}) as Record<string, any>;
  const base = emptySellerApplication();
  const nextAction = (source.next_action || {}) as Record<string, unknown>;
  return {
    application_id: Number(source.application_id || 0),
    status: (String(source.status || "draft") as SellerApplicationStatus) || "draft",
    status_title: String(source.status_title || base.status_title),
    status_message: String(source.status_message || base.status_message),
    next_action: {
      action: (String(nextAction.action || "continue") as SellerApplicationNextAction["action"]) || "continue",
      label: String(nextAction.label || EMPTY_NEXT_ACTION.label)
    },
    editable: Boolean(source.editable),
    completeness: percentage(source.completeness),
    fields: (source.fields || {}) as SellerApplicationFields,
    documents: Array.isArray(source.documents)
      ? source.documents.map((doc: Record<string, unknown>) => ({
          id: Number(doc.id || 0),
          type: String(doc.type || ""),
          label: String(doc.label || doc.type || "Document"),
          filename: String(doc.filename || ""),
          size_kb: Number(doc.size_kb || 0),
          uploaded_at: String(doc.uploaded_at || ""),
          state: String(doc.state || "pending")
        }))
      : [],
    steps: Array.isArray(source.steps)
      ? source.steps.map((step: Record<string, unknown>) => ({
          key: String(step.key || ""),
          title: String(step.title || ""),
          summary: String(step.summary || ""),
          fields: stringList(step.fields),
          complete: Boolean(step.complete),
          errors: (step.errors || {}) as Record<string, string>
        }))
      : [],
    can_submit: Boolean(source.can_submit),
    information_request: String(source.information_request || ""),
    submitted_at: String(source.submitted_at || ""),
    updated_at: String(source.updated_at || ""),
    seller_types: labelledList(source.seller_types),
    selling_intents: stringList(source.selling_intents),
    required_documents: labelledList(source.required_documents),
    optional_documents: labelledList(source.optional_documents)
  };
}

function unwrap(response: SellerApplicationResponse): SellerApplicationView {
  return normalizeSellerApplication(response.application);
}

export async function loadSellerApplication() {
  const response = await pulseApi<SellerApplicationResponse>("/api/pulse/seller/application");
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return view;
}

export async function cacheSellerApplication(view: SellerApplicationView) {
  await writeJsonCache(SELLER_APPLICATION_CACHE_KEY, view);
}

export async function loadCachedSellerApplication() {
  return readJsonCache<SellerApplicationView>(SELLER_APPLICATION_CACHE_KEY, normalizeSellerApplication);
}

/**
 * Autosave a partial answer set.
 *
 * The server treats this as a patch over the whitelisted writable fields and
 * refuses to touch status, so an autosave can never advance an application.
 * That is why it is safe to fire this on every step change without guarding it.
 */
export async function saveSellerApplicationDraft(fields: SellerApplicationFields) {
  const response = await pulseApi<SellerApplicationResponse>("/api/pulse/seller/application/draft", {
    method: "POST",
    body: JSON.stringify({ fields })
  });
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return view;
}

export async function submitSellerApplication() {
  const response = await pulseApi<SellerApplicationResponse>("/api/pulse/seller/application/submit", {
    method: "POST",
    body: JSON.stringify({})
  });
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return { view, message: String(response.message || "") };
}

export async function withdrawSellerApplication() {
  const response = await pulseApi<SellerApplicationResponse>("/api/pulse/seller/application/withdraw", {
    method: "POST",
    body: JSON.stringify({})
  });
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return { view, message: String(response.message || "") };
}

export type SellerDocumentAsset = { uri: string; name: string; mimeType: string };

/**
 * Upload one verification document.
 *
 * The file is sent as multipart directly to the private-document endpoint and
 * is never read into JS memory, logged, cached, or written to the application
 * cache — this module only ever holds the server's metadata for it.
 */
export async function uploadSellerApplicationDocument(documentType: string, asset: SellerDocumentAsset) {
  const form = new FormData();
  form.append("document_type", documentType);
  form.append("file", {
    uri: asset.uri,
    name: asset.name || `${documentType}.jpg`,
    type: asset.mimeType || "application/octet-stream"
  } as unknown as Blob);
  const response = await pulseApi<SellerApplicationResponse>("/api/pulse/seller/application/documents", {
    method: "POST",
    body: form
  });
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return { view, message: String(response.message || "") };
}

export async function removeSellerApplicationDocument(documentId: number) {
  const response = await pulseApi<SellerApplicationResponse>(
    `/api/pulse/seller/application/documents/${documentId}/remove`,
    { method: "POST", body: JSON.stringify({}) }
  );
  const view = unwrap(response);
  await cacheSellerApplication(view).catch(() => undefined);
  return { view, message: String(response.message || "") };
}

/**
 * Choose a document from the file picker.
 *
 * Restricted to the three formats the reviewer's viewer can actually open, so
 * an applicant cannot spend their attention uploading a file the reviewer will
 * have to ask them to replace.
 */
export async function pickSellerApplicationFile(): Promise<SellerDocumentAsset | null> {
  const result = await DocumentPicker.getDocumentAsync({
    copyToCacheDirectory: true,
    multiple: false,
    type: ["image/jpeg", "image/png", "application/pdf"]
  });
  if (result.canceled || !result.assets?.[0]) return null;
  const asset = result.assets[0];
  return {
    uri: asset.uri,
    name: asset.name || "document",
    mimeType: asset.mimeType || "application/octet-stream"
  };
}

export async function captureSellerApplicationPhoto(): Promise<SellerDocumentAsset | null> {
  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) return null;
  const result = await ImagePicker.launchCameraAsync({ quality: 0.75, allowsEditing: false });
  if (result.canceled || !result.assets?.[0]) return null;
  const asset = result.assets[0];
  return {
    uri: asset.uri,
    name: asset.fileName || "capture.jpg",
    mimeType: asset.mimeType || "image/jpeg"
  };
}

/** Statuses in which the applicant may still change their answers. */
export function sellerApplicationIsEditable(view: SellerApplicationView) {
  return view.editable;
}

/** Statuses in which the applicant is waiting on us rather than the reverse. */
export function sellerApplicationIsPending(view: SellerApplicationView) {
  return ["submitted", "under_review", "resubmitted"].includes(view.status);
}

export function sellerApplicationStatusTone(status: SellerApplicationStatus): "positive" | "warning" | "critical" | "neutral" {
  if (status === "approved") return "positive";
  if (status === "rejected" || status === "suspended") return "critical";
  if (status === "information_requested") return "warning";
  if (status === "submitted" || status === "under_review" || status === "resubmitted") return "neutral";
  return "neutral";
}
