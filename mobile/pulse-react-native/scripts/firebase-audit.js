const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = JSON.parse(read("app.json")).expo;
const push = read("services/push.ts");
const authStore = read("store/authStore.ts");
const linking = read("navigation/linking.ts");
const failures = [];

const ios = app.ios || {};
const android = app.android || {};

[
  ["app display name is Pulse", app.name === "Pulse"],
  ["deep link scheme is pulse", app.scheme === "pulse"],
  ["iOS bundle identifier", ios.bundleIdentifier === "com.pulsesoc.app"],
  ["Android package name", android.package === "com.pulsesoc.app"],
  ["iOS Firebase config reference", ios.googleServicesFile === "./credentials/firebase/GoogleService-Info.plist"],
  ["Android Firebase config reference", android.googleServicesFile === "./credentials/firebase/google-services.json"],
  ["notification permission configured", JSON.stringify(android.permissions || []).includes("POST_NOTIFICATIONS")],
  ["iOS app links configured", JSON.stringify(ios.associatedDomains || []).includes("applinks:pulsesoc.com")],
  ["Android app links configured", JSON.stringify(android.intentFilters || []).includes("pulsesoc.com")],
  ["push token capture", push.includes("getExpoPushTokenAsync")],
  ["push permission request", push.includes("requestPermissionsAsync")],
  ["push token subscribe", push.includes("/api/push/subscribe")],
  ["push token unsubscribe", push.includes("/api/push/unsubscribe")],
  ["logout push cleanup", authStore.includes("unregisterPushNotifications")],
  ["notification tap deep link routing", push.includes("addNotificationResponseReceivedListener") && push.includes("Linking.openURL")],
  ["pulse deep link prefix", linking.includes("pulse://")]
].forEach(([label, ok]) => {
  if (!ok) failures.push(`${label} missing`);
});

if (failures.length) {
  console.error("Pulse mobile Firebase audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Pulse mobile Firebase audit passed.");

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}
