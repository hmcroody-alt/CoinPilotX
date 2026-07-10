const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

config.resolver.extraNodeModules = {
  ...(config.resolver.extraNodeModules || {}),
  "@ide/backoff": path.resolve(__dirname, "node_modules/@ide/backoff/build/backoff.js")
};

module.exports = config;
