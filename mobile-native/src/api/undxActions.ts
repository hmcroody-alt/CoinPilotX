import { pulseApi } from "./pulseApi";

export type UndxActionEffect = "allow" | "deny" | "require_approval";
export type UndxActionRisk = "low" | "medium" | "high" | "critical";

export type UndxActionPolicyPayload = {
  org_id: string;
  action_type: string;
  effect: UndxActionEffect;
  name?: string;
  max_risk?: UndxActionRisk;
  active?: boolean;
  priority?: number;
  source?: string;
  external_ref?: string;
  meta?: Record<string, unknown>;
};

export type UndxActionRequestPayload = {
  org_id: string;
  actor: string;
  action_type: string;
  subject_ref?: string;
  risk?: UndxActionRisk;
  params?: Record<string, unknown>;
  requested_at?: string;
  source?: string;
  external_ref?: string;
  meta?: Record<string, unknown>;
};

export type UndxToolPayload = {
  tool_name: string;
  action_type: string;
  version?: string;
  product_area?: string;
  risk?: UndxActionRisk;
  confirmation_required?: boolean;
  feature_flag?: string;
  enabled?: boolean;
  allowed_modes?: string[];
  meta?: Record<string, unknown>;
};

export type UndxPermissionPayload = {
  org_id: string;
  actor: string;
  action_type: string;
  effect?: "allow" | "deny";
  scope_ref?: string;
  max_risk?: UndxActionRisk;
  active?: boolean;
  priority?: number;
  source?: string;
  external_ref?: string;
  expires_at?: string;
  meta?: Record<string, unknown>;
};

export type UndxConfirmationPayload = {
  org_id: string;
  request_id: string;
  actor: string;
  payload_hash: string;
  status?: "pending" | "confirmed" | "rejected" | "expired";
  expires_at?: string;
  confirmed_at?: string;
  meta?: Record<string, unknown>;
};

export type UndxReceiptPayload = {
  org_id: string;
  action_type: string;
  actor: string;
  status: "verified" | "failed" | "blocked" | "cancelled";
  request_id?: string;
  canonical_ref?: string;
  verification?: Record<string, unknown>;
  result?: Record<string, unknown>;
};

export type UndxEmergencyStopPayload = {
  org_id: string;
  actor: string;
  reason: string;
  action_type?: string;
  active?: boolean;
  meta?: Record<string, unknown>;
};

export type UndxMarketplaceListingPayload = {
  title: string;
  description?: string;
  source_text?: string;
  price_cents: number;
  currency?: string;
  fulfillment_type?: "physical" | "digital";
  inventory_qty?: number;
};

export type UndxMarketplaceDraftPayload = {
  org_id: string;
  actor: string;
  listing: UndxMarketplaceListingPayload;
  source?: string;
  external_ref?: string;
};

export type UndxMarketplacePublishPlanPayload = {
  org_id: string;
  actor: string;
  product_id: string | number;
  source?: string;
  external_ref?: string;
};

export type UndxMarketplacePublishExecutePayload = {
  org_id: string;
  actor: string;
  request_id: string;
  product_id: string | number;
  confirmation_token: string;
};

export type UndxActionCenterParams = {
  orgId: string;
  limit?: number;
};

export type UndxActionResponse<T = Record<string, unknown>> = {
  ok?: boolean;
  result?: T;
  code?: string;
  error?: string;
  message?: string;
};

function postUndxAction<T>(path: string, payload: Record<string, unknown>) {
  return pulseApi<UndxActionResponse<T>>(path, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

function undxQuery(params: Record<string, string | number | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const raw = query.toString();
  return raw ? `?${raw}` : "";
}

export function recordUndxActionPolicy(payload: UndxActionPolicyPayload) {
  return postUndxAction("/api/business-os/undx/policies", payload);
}

export function recordUndxActionRequest(payload: UndxActionRequestPayload) {
  return postUndxAction("/api/business-os/undx/requests", payload);
}

export function registerUndxActionTool(payload: UndxToolPayload) {
  return postUndxAction("/api/business-os/undx/tools", payload);
}

export function grantUndxActionPermission(payload: UndxPermissionPayload) {
  return postUndxAction("/api/business-os/undx/permissions", payload);
}

export function recordUndxActionConfirmation(payload: UndxConfirmationPayload) {
  return postUndxAction("/api/business-os/undx/confirmations", payload);
}

export function recordUndxActionReceipt(payload: UndxReceiptPayload) {
  return postUndxAction("/api/business-os/undx/receipts", payload);
}

export function activateUndxEmergencyStop(payload: UndxEmergencyStopPayload) {
  return postUndxAction("/api/business-os/undx/emergency-stop", payload);
}

export function fetchUndxActionCenter(params: UndxActionCenterParams) {
  return pulseApi<UndxActionResponse>(`/api/business-os/undx/action-center${undxQuery({
    org_id: params.orgId,
    limit: params.limit
  })}`);
}

export function fetchUndxTools(params: { productArea?: string; limit?: number } = {}) {
  return pulseApi<UndxActionResponse>(`/api/business-os/undx/tools${undxQuery({
    product_area: params.productArea,
    limit: params.limit
  })}`);
}

export function fetchUndxPermissions(params: { orgId: string; actor?: string; limit?: number }) {
  return pulseApi<UndxActionResponse>(`/api/business-os/undx/permissions${undxQuery({
    org_id: params.orgId,
    actor: params.actor,
    limit: params.limit
  })}`);
}

export function createUndxMarketplaceListingDraft(payload: UndxMarketplaceDraftPayload) {
  return postUndxAction("/api/business-os/undx/marketplace/listings/draft", payload);
}

export function planUndxMarketplaceListingPublish(payload: UndxMarketplacePublishPlanPayload) {
  return postUndxAction("/api/business-os/undx/marketplace/listings/publish/plan", payload);
}

export function executeUndxMarketplaceListingPublish(payload: UndxMarketplacePublishExecutePayload) {
  return postUndxAction("/api/business-os/undx/marketplace/listings/publish/execute", payload);
}
