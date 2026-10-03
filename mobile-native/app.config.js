// The reversed-client-id URL scheme Google's iOS SDK is called back on. Derived
// from the client id rather than configured as its own variable so there is only
// one value to get right: the scheme is always the client id with its two halves
// swapped, and asking for both invites a mismatch whose only symptom is a
// sign-in sheet that opens and never comes back.
const googleIosUrlScheme = (clientId) => {
  const id = String(clientId || "").trim();
  const suffix = ".apps.googleusercontent.com";
  if (!id.endsWith(suffix) || id.length <= suffix.length) return "";
  return `com.googleusercontent.apps.${id.slice(0, -suffix.length)}`;
};

module.exports = ({ config }) => {
  const extra = config.extra || {};
  const profile = process.env.EAS_BUILD_PROFILE || "";
  const googleIosClientId = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || "";
  const googleIosScheme = googleIosUrlScheme(googleIosClientId);
  const explicitIosBundleId = process.env.PULSESOC_IOS_BUNDLE_ID || process.env.EXPO_PUBLIC_PULSESOC_IOS_BUNDLE_ID;
  const isDevelopmentProfile = profile === "development" || profile === "development-simulator";
  const iosBundleIdentifier = explicitIosBundleId || (isDevelopmentProfile ? "com.pulsesoc.nativeapp.dev" : "com.pulsesoc.app");
  const appName = isDevelopmentProfile ? "PulseSoc Dev" : "PulseSoc";

  // Appended here rather than listed in app.json's `plugins` because the plugin
  // throws when `iosUrlScheme` is missing or malformed, and the iOS OAuth client
  // does not exist yet. A bare entry in app.json would therefore not degrade to
  // "Google sign-in unavailable" -- it would fail `expo prebuild` outright and
  // take every other native build down with it. Absent until the client id is
  // configured, present automatically once it is. The sign-in screen gates the
  // Google button on the same value, so the binary and the UI cannot disagree
  // about whether the provider is available.
  const plugins = [...(config.plugins || [])];
  if (googleIosScheme) {
    plugins.push(["@react-native-google-signin/google-signin", { iosUrlScheme: googleIosScheme }]);
  }

  return {
    ...config,
    name: appName,
    plugins,
    ios: {
      ...(config.ios || {}),
      bundleIdentifier: iosBundleIdentifier
    },
    extra: {
      ...extra,
      pulseApiBaseUrl: process.env.EXPO_PUBLIC_PULSE_API_BASE_URL || extra.pulseApiBaseUrl || "https://pulsesoc.com",
      expoProjectId: process.env.EXPO_PUBLIC_EXPO_PROJECT_ID || extra.expoProjectId || "",
      googleIosClientId,
      // The commit the bundle was built from, so an installed binary can be tied
      // back to a source tree. `buildNumber` cannot do this: it is a literal in
      // app.json that changes only when someone remembers, so a stale build and
      // a fresh one report the same number. Empty when the builder does not
      // supply it, which is every local `expo start` — absent is honest, a
      // guessed SHA would not be.
      sourceSha: process.env.PULSESOC_SOURCE_SHA || ""
    }
  };
};
