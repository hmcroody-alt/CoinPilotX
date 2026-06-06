import Constants from "expo-constants";

type PulseExpoExtra = {
  apiBaseUrl?: string;
  eas?: { projectId?: string };
};

const extra = Constants.expoConfig?.extra as PulseExpoExtra | undefined;

export const PULSE_API_BASE_URL =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  extra?.apiBaseUrl ||
  "https://pulsesoc.com";

export const PULSE_DEEP_LINK_PREFIXES = [
  "pulse://",
  "https://pulsesoc.com",
  "https://www.pulsesoc.com",
  "https://coinpilotx.app",
  "https://www.coinpilotx.app"
];

export const EXPO_PROJECT_ID =
  process.env.EXPO_PUBLIC_EAS_PROJECT_ID ||
  extra?.eas?.projectId ||
  "";
