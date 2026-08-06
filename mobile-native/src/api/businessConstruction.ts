import { pulseApi } from "./pulseApi";

export type BusinessConstructionAccess = {
  ok: boolean;
  mode: "construction" | "development" | "public";
  can_access_private_business_os: boolean;
  construction_mode: boolean;
  developer_mode: boolean;
  developer_badge: boolean;
  /** True when the server opened this sector via an engineer-access grant. */
  engineer_access?: boolean;
};

export function getBusinessConstructionAccess() {
  return pulseApi<BusinessConstructionAccess>("/api/pulse/business/construction-access");
}

/** Closed by default. Only a server answer may widen this. */
export const CONSTRUCTION_LOCKED: BusinessConstructionAccess = {
  ok: false,
  mode: "construction",
  can_access_private_business_os: false,
  construction_mode: true,
  developer_mode: false,
  developer_badge: false
};

/*
 * A client-side owner allowlist used to live here, keyed on two hardcoded email
 * addresses. It was removed deliberately: strings in the JS bundle are readable
 * by anyone who unzips the IPA, so the check both disclosed the owner accounts
 * and could be satisfied by patching one comparison. Owner status is now
 * resolved server-side only, from the immutable numeric user ID and the admin
 * table — see services/business_os/construction_access.py.
 */
