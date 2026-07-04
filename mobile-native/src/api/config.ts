import Constants from "expo-constants";

const extra = Constants.expoConfig?.extra || {};
const easConfig = Constants.easConfig || {};

const configuredBaseUrl =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  (typeof extra.pulseApiBaseUrl === "string" ? extra.pulseApiBaseUrl : "https://pulsesoc.com");

export const PULSE_API_BASE_URL = normalizeApiBaseUrl(configuredBaseUrl);
export const EXPO_PROJECT_ID = normalizeOptionalString(
  process.env.EXPO_PUBLIC_EXPO_PROJECT_ID ||
    (typeof easConfig.projectId === "string" ? easConfig.projectId : "") ||
    (typeof extra.expoProjectId === "string" ? extra.expoProjectId : "")
);

function normalizeApiBaseUrl(value: string) {
  const url = String(value || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(url)) return "https://pulsesoc.com";
  return url;
}

function normalizeOptionalString(value: string) {
  const text = String(value || "").trim();
  return text || "";
}
