const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = read("App.tsx");
const failures = [];

const checks = [
  ["Feed comes from website", app, "https://pulsesoc.com"],
  ["Reels come from website", app, "WebView"],
  ["Videos come from website", app, "WebView"],
  ["Messages come from website", app, "WebView"],
  ["Notifications come from website", app, "WebView"],
  ["Profile comes from website", app, "WebView"],
  ["Premium comes from website", app, "WebView"],
  ["Browser chrome hidden", app, "SafeAreaView"],
  ["Website can persist session cookies", app, "sharedCookiesEnabled"],
  ["Website can use local/session storage", app, "domStorageEnabled"],
  ["Website videos can play inline", app, "allowsInlineMediaPlayback"],
  ["Website media upload permissions retained", read("app.json"), "NSPhotoLibraryUsageDescription"],
  ["Native deep links map to web routes", app, "toPulseSocWebUrl"],
  ["Native push bridge available", app, "PulseSocNativeReady"],
  ["External links are not trapped", app, "Linking.openURL(request.url)"]
];

for (const [label, text, needle] of checks) {
  if (!text.includes(needle)) failures.push(`${label} missing`);
}

const forbiddenRoot = [
  ["Native navigation root", /<AppNavigator\s*\/>/],
  ["Native tab root", /<MainTabs\s*\/>/],
  ["Native placeholder preview root", /ApiPreview/]
];

for (const [label, pattern] of forbiddenRoot) {
  if (pattern.test(app)) failures.push(`${label} still present in App.tsx`);
}

if (failures.length) {
  console.error("PulseSoc mobile web parity audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile web parity audit passed.");

function read(relativePath) {
  const target = path.join(root, relativePath);
  return fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
}
