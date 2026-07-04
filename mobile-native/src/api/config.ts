import Constants from "expo-constants";

const extra = Constants.expoConfig?.extra || {};

export const PULSE_API_BASE_URL =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  (typeof extra.pulseApiBaseUrl === "string" ? extra.pulseApiBaseUrl : "https://pulsesoc.com");
