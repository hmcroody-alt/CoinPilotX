module.exports = ({ config }) => {
  const extra = config.extra || {};
  const profile = process.env.EAS_BUILD_PROFILE || "";
  const explicitIosBundleId = process.env.PULSESOC_IOS_BUNDLE_ID || process.env.EXPO_PUBLIC_PULSESOC_IOS_BUNDLE_ID;
  const isDevelopmentProfile = profile === "development" || profile === "development-simulator";
  const iosBundleIdentifier = explicitIosBundleId || (isDevelopmentProfile ? "com.pulsesoc.nativeapp.dev" : "com.pulsesoc.app");
  const appName = isDevelopmentProfile ? "PulseSoc Dev" : "PulseSoc";

  return {
    ...config,
    name: appName,
    ios: {
      ...(config.ios || {}),
      bundleIdentifier: iosBundleIdentifier
    },
    extra: {
      ...extra,
      pulseApiBaseUrl: process.env.EXPO_PUBLIC_PULSE_API_BASE_URL || extra.pulseApiBaseUrl || "https://pulsesoc.com",
      expoProjectId: process.env.EXPO_PUBLIC_EXPO_PROJECT_ID || extra.expoProjectId || ""
    }
  };
};
