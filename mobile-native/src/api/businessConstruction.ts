import { pulseApi } from "./pulseApi";

export type BusinessConstructionAccess = {
  ok: boolean;
  mode: "construction" | "development" | "public";
  can_access_private_business_os: boolean;
  construction_mode: boolean;
  developer_mode: boolean;
  developer_badge: boolean;
};

export function getBusinessConstructionAccess() {
  return pulseApi<BusinessConstructionAccess>("/api/pulse/business/construction-access");
}

const CANONICAL_OWNER_EMAILS = new Set([
  "cherieroody@gmail.com",
  "coinpilotxai@gmail.com"
]);

/**
 * Compatibility authority for production clients while the dedicated access
 * endpoint rolls out. The email comes from the authenticated server session;
 * editable username/display-name fields are deliberately ignored.
 */
export function authenticatedOwnerAccess(email?: string | null): BusinessConstructionAccess | null {
  if (!CANONICAL_OWNER_EMAILS.has(String(email || "").trim().toLowerCase())) return null;
  return {
    ok: true,
    mode: "development",
    can_access_private_business_os: true,
    construction_mode: true,
    developer_mode: true,
    developer_badge: true
  };
}
