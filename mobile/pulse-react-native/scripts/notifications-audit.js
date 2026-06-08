const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = read("App.tsx");
const push = read("services/push.ts");
const failures = [];

[
  ["WebView app root is active", app, "WebView"],
  ["Native push token can be requested", push, "getNativePushToken"],
  ["Expo push token retrieval exists", push, "getExpoPushTokenAsync"],
  ["Website push bridge asks native layer", app, "PULSESOC_REGISTER_PUSH"],
  ["Native push token is registered through website session", app, "/api/push/subscribe"],
  ["Push registration uses website cookies", app, "credentials: 'include'"],
  ["Notification tap deep links route into WebView", app, "wireNotificationLinks(url => navigateToAppUrl(url))"],
  ["Foreground notifications can play sound", push, "shouldPlaySound: true"],
  ["Notification presentation is configured on app startup", app, "ensureNotificationPresentation().catch"],
  ["Android notification channel is max importance", push, "AndroidImportance.MAX"],
  ["Android notification channel uses default sound", push, "sound: \"default\""],
  ["Android notification channel can vibrate", push, "enableVibrate: true"],
  ["Foreground notification listener vibrates device", push, "addNotificationReceivedListener"],
  ["Website notification bridge can trigger native alert", app, "PULSESOC_NOTIFY_DEVICE"],
  ["Native alert bridge schedules a sounding notification", push, "scheduleNotificationAsync"],
  ["Native unsubscribe support retained", push, "unregisterPushNotifications"],
  ["Native share bridge exists", app, "PULSESOC_SHARE"]
].forEach(([label, text, needle]) => {
  if (!text.includes(needle)) failures.push(`${label} missing`);
});

if (failures.length) {
  console.error("PulseSoc mobile notifications audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile notifications audit passed.");

function read(relativePath) {
  const file = path.join(root, relativePath);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
}
