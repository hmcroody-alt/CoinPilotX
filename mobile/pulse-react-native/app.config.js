const appJson = require("./app.json");

module.exports = ({ config }) => {
  const baseConfig = { ...config, ...appJson.expo };
  return {
    ...baseConfig,
    ios: {
      ...baseConfig.ios,
      googleServicesFile:
        process.env.EAS_GOOGLE_SERVICE_INFO_PLIST ||
        process.env.GOOGLE_SERVICE_INFO_PLIST ||
        baseConfig.ios.googleServicesFile
    },
    android: {
      ...baseConfig.android,
      googleServicesFile:
        process.env.EAS_GOOGLE_SERVICES_JSON ||
        process.env.GOOGLE_SERVICES_JSON ||
        baseConfig.android.googleServicesFile
    }
  };
};
