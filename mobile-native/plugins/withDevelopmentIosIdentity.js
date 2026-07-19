const { withInfoPlist } = require("@expo/config-plugins");

module.exports = function withDevelopmentIosIdentity(config) {
  return withInfoPlist(config, (nextConfig) => {
    // Xcode supplies a release-safe default, while the guarded device installer
    // overrides this value for the side-by-side development application.
    nextConfig.modResults.CFBundleDisplayName = "$(PULSESOC_DISPLAY_NAME)";
    return nextConfig;
  });
};
