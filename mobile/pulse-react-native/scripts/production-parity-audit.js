const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = read("App.tsx");
const appJson = read("app.json");
const easJson = read("eas.json");
const push = read("services/push.ts");
const failures = [];

const required = [
  ["Production API base remains PulseSoc", easJson, "\"EXPO_PUBLIC_PULSE_API_BASE_URL\": \"https://pulsesoc.com\""],
  ["Expo API base remains PulseSoc", appJson, "\"apiBaseUrl\": \"https://pulsesoc.com\""],
  ["Bundle identifier preserved", appJson, "\"bundleIdentifier\": \"com.pulsesoc.app\""],
  ["Android package preserved", appJson, "\"package\": \"com.pulsesoc.app\""],
  ["App name preserved", appJson, "\"name\": \"PulseSoc\""],
  ["Deep link scheme preserved", appJson, "\"scheme\": \"pulse\""],
  ["Associated domains preserved", appJson, "applinks:pulsesoc.com"],
  ["Camera permission preserved", appJson, "NSCameraUsageDescription"],
  ["Microphone permission preserved", appJson, "NSMicrophoneUsageDescription"],
  ["Photo library permission preserved", appJson, "NSPhotoLibraryUsageDescription"],
  ["Android media permissions preserved", appJson, "READ_MEDIA_VIDEO"],
  ["WebView source of truth", app, "PULSESOC_ORIGIN = \"https://pulsesoc.com\""],
  ["Website screens are not native root", app, "AppNavigator", true],
  ["Internal PulseSoc navigation remains inside WebView", app, "PULSESOC_HOSTS.has(nextUrl.hostname)"],
  ["External links leave shell", app, "Linking.openURL(request.url)"],
  ["Native bridge exists", app, "window.PulseSocNative"],
  ["Push token bridge exists", app, "PULSESOC_REGISTER_PUSH"],
  ["Share bridge exists", app, "PULSESOC_SHARE"],
  ["Push endpoint preserved", push, "/api/push/subscribe"],
  ["Push unsubscribe preserved", push, "/api/push/unsubscribe"]
];

for (const [label, text, needle, shouldBeMissing] of required) {
  const found = text.includes(needle);
  if (shouldBeMissing ? found : !found) failures.push(`${label} failed`);
}

if (failures.length) {
  console.error("PulseSoc mobile production parity audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile production parity audit passed.");

function read(relativePath) {
  const target = path.join(root, relativePath);
  return fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
}
