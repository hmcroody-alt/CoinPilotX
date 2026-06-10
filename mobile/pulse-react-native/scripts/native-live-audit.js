const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");
const pkg = JSON.parse(read("package.json"));
const app = read("app.json");
const appTsx = read("App.tsx");
const indexTs = read("index.ts");
const nativeLive = read("components/NativeLiveBroadcast.tsx");

const requiredDeps = [
  "react-native-vision-camera",
  "@livekit/react-native",
  "@livekit/react-native-webrtc",
  "@livekit/react-native-expo-plugin",
  "@config-plugins/react-native-webrtc",
  "livekit-client"
];

const failures = [];
for (const dep of requiredDeps) {
  if (!pkg.dependencies[dep]) failures.push(`Missing dependency: ${dep}`);
}
if (!app.includes("@livekit/react-native-expo-plugin")) failures.push("LiveKit Expo plugin is not configured.");
if (!app.includes("@config-plugins/react-native-webrtc")) failures.push("React Native WebRTC config plugin is not configured.");
if (!app.includes("NSCameraUsageDescription") || !app.includes("NSMicrophoneUsageDescription")) failures.push("iOS camera/mic permission copy is missing.");
if (!indexTs.includes("registerGlobals()")) failures.push("LiveKit registerGlobals() is missing from startup.");
if (!appTsx.includes("PULSESOC_OPEN_NATIVE_LIVE")) failures.push("Native live WebView bridge message is missing.");
if (!appTsx.includes("PULSESOC_WEB_API_RESULT")) failures.push("Authenticated WebView API bridge is missing.");
if (!nativeLive.includes("useCameraDevice") || !nativeLive.includes("Room")) failures.push("Native live screen is not using VisionCamera and LiveKit.");
if (/LIVEKIT_API_SECRET|LIVEKIT_API_KEY|MUX_TOKEN_SECRET|APNS_PRIVATE_KEY|FCM_PRIVATE_KEY/.test(nativeLive + appTsx + indexTs)) failures.push("A secret variable name appears in mobile source.");

if (failures.length) {
  console.error("PulseSoc native live audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc native live audit passed.");
