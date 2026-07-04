import Constants from "expo-constants";

const extra = Constants.expoConfig?.extra || {};

const configuredBaseUrl =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  (typeof extra.pulseApiBaseUrl === "string" ? extra.pulseApiBaseUrl : "https://pulsesoc.com");

export const PULSE_API_BASE_URL = normalizeApiBaseUrl(configuredBaseUrl);

function normalizeApiBaseUrl(value: string) {
  const url = String(value || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(url)) return "https://pulsesoc.com";
  return url;
}
