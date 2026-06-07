const appJson = require("./app.json");

module.exports = () => {
  const config = appJson.expo;
  return {
    ...config,
    ios: {
      ...config.ios,
      googleServicesFile: process.env.GOOGLE_SERVICE_INFO_PLIST || config.ios.googleServicesFile
    },
    android: {
      ...config.android,
      googleServicesFile: process.env.GOOGLE_SERVICES_JSON || config.android.googleServicesFile
    }
  };
};
