import Constants from "expo-constants";

const extra = Constants.expoConfig?.extra as { apiBaseUrl?: string; eas?: { projectId?: string } } | undefined;

export const PULSE_API_BASE_URL =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  extra?.apiBaseUrl ||
  "https://pulsesoc.com";

export const PULSE_LINK_PREFIXES = [
  "pulse://",
  "https://pulsesoc.com",
  "https://coinpilotx.app"
];

export const EXPO_PROJECT_ID =
  process.env.EXPO_PUBLIC_EAS_PROJECT_ID ||
  extra?.eas?.projectId ||
  "";
