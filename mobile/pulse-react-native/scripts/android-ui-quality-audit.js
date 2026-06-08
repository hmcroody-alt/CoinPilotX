const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = read("App.tsx");
const pkg = read("package.json");
const failures = [];

const checks = [
  ["Android app renders PulseSoc website through WebView", app, "WebView"],
  ["Android shell has no placeholder bottom tabs", app, "MainTabs", true],
  ["Android shell has no native debug cards", app, "ApiPreview", true],
  ["Android WebView keeps cookies", app, "thirdPartyCookiesEnabled"],
  ["Android hardware back maps to WebView back", app, "BackHandler.addEventListener"],
  ["Android external links open browser", app, "Linking.openURL(request.url)"],
  ["Android offline fallback exists", app, "OfflineScreen"],
  ["Android WebView package installed", pkg, "react-native-webview"],
  ["Native upload permissions remain in app config", read("app.json"), "READ_MEDIA_IMAGES"],
  ["Native notification permission remains in app config", read("app.json"), "POST_NOTIFICATIONS"]
];

for (const [label, text, needle, shouldBeMissing] of checks) {
  const found = text.includes(needle);
  if (shouldBeMissing ? found : !found) failures.push(`${label} failed`);
}

if (failures.length) {
  console.error("PulseSoc Android WebView quality audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc Android WebView quality audit passed.");

function read(relativePath) {
  const target = path.join(root, relativePath);
  return fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
}
