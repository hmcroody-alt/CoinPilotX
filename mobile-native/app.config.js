module.exports = ({ config }) => {
  const extra = config.extra || {};

  return {
    ...config,
    extra: {
      ...extra,
      pulseApiBaseUrl: process.env.EXPO_PUBLIC_PULSE_API_BASE_URL || extra.pulseApiBaseUrl || "https://pulsesoc.com",
      expoProjectId: process.env.EXPO_PUBLIC_EXPO_PROJECT_ID || extra.expoProjectId || ""
    }
  };
};
