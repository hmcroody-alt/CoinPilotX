const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = read("App.tsx");
const push = read("services/push.ts");
const pkg = read("package.json");
const failures = [];

const checks = [
  ["WebView dependency installed", pkg, "react-native-webview"],
  ["App root uses WebView", app, "react-native-webview"],
  ["Loads live PulseSoc website by default", app, "https://pulsesoc.com"],
  ["No native AppNavigator root", app, "AppNavigator", true],
  ["Shared cookies enabled", app, "sharedCookiesEnabled"],
  ["DOM storage enabled", app, "domStorageEnabled"],
  ["Media playback allowed inline", app, "allowsInlineMediaPlayback"],
  ["Autoplay/user-action config present", app, "mediaPlaybackRequiresUserAction={false}"],
  ["Pull-to-refresh enabled", app, "pullToRefreshEnabled"],
  ["External links open outside shell", app, "Linking.openURL(request.url)"],
  ["PulseSoc internal hosts stay in WebView", app, "PULSESOC_HOSTS"],
  ["Deep links map to website routes", app, "toPulseSocWebUrl"],
  ["Offline fallback screen", app, "PulseSoc is offline"],
  ["Native bridge injected", app, "PulseSocNative"],
  ["Native share bridge", app, "PULSESOC_SHARE"],
  ["Native push bridge", app, "PULSESOC_REGISTER_PUSH"],
  ["Push token can be requested without native navigator", push, "getNativePushToken"],
  ["Push token posts back through website cookies", app, "credentials: 'include'"],
  ["Native notification deep links can route into WebView", push, "wireNotificationLinks(onUrl"]
];

for (const [label, text, needle, shouldBeMissing] of checks) {
  const found = text.includes(needle);
  if (shouldBeMissing ? found : !found) failures.push(`${label} failed`);
}

const forbidden = [
  ["Token logging", /console\.(log|info|warn|error)\([^)]*token/i],
  ["Password logging", /console\.(log|info|warn|error)\([^)]*password/i]
];

for (const [label, pattern] of forbidden) {
  for (const file of walk(root).filter(item => /\.(ts|tsx|js)$/.test(item))) {
    if (file.includes(`${path.sep}node_modules${path.sep}`) || file.includes(`${path.sep}scripts${path.sep}`)) continue;
    const text = fs.readFileSync(file, "utf8");
    if (pattern.test(text)) failures.push(`${label} found in ${path.relative(root, file)}`);
  }
}

if (failures.length > 0) {
  console.error("PulseSoc mobile WebView shell audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile WebView shell audit passed.");

function read(relativePath) {
  const target = path.join(root, relativePath);
  return fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
}

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}
