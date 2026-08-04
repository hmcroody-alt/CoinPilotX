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
