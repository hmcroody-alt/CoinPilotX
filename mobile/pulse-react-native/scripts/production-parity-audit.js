const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const failures = [];

const required = [
  ["EAS production API base URL", "eas.json", "\"EXPO_PUBLIC_PULSE_API_BASE_URL\": \"https://pulsesoc.com\""],
  ["Expo extra API base URL", "app.json", "\"apiBaseUrl\": \"https://pulsesoc.com\""],
  ["Runtime config default API base URL", "src/config.ts", "\"https://pulsesoc.com\""],
  ["Legacy runtime config default API base URL", "services/config.ts", "\"https://pulsesoc.com\""],
  ["Feed screen uses real feed API", "services/feed/feedApi.ts", "/api/pulse/feed"],
  ["Reels screen uses real reels API", "screens/main/ReelsScreen.tsx", "/api/pulse/reels/feed"],
  ["Videos screen uses real videos API", "screens/main/VideosScreen.tsx", "/api/pulse/videos"],
  ["Messages screen uses Communications V2", "src/services/communications.ts", "/api/pulse/communications/v2/conversations"],
  ["Messages screen loads thread history", "src/services/communications.ts", "loadConversationMessages"],
  ["Messages screen sends via V2", "src/services/communications.ts", "/api/pulse/communications/v2/conversations/${payload.conversationId}/messages"],
  ["Messages screen has production composer", "src/screens/CommunicationsScreen.tsx", "Type a message"],
  ["Notifications screen uses real notifications API", "src/screens/NotificationsScreen.tsx", "/api/pulse/notifications?limit=80"],
  ["Profile screen uses real profile API", "screens/main/ProfileScreen.tsx", "/api/pulse/profile/me"],
  ["Premium screen uses account status API", "screens/main/PremiumScreen.tsx", "/api/account/status"],
  ["Premium screen links portfolio", "screens/main/PremiumScreen.tsx", "/pulse/portfolio"],
  ["Feed mirrors web Global Pulse Feed hero", "screens/main/HomeFeedScreen.tsx", "Global Pulse Feed"],
  ["Feed uses web status rail endpoint", "services/pulseDiscovery.ts", "/api/pulse/status/rail"],
  ["Feed uses web live-now endpoint", "services/pulseDiscovery.ts", "/api/pulse/live-now"],
  ["Shared PulseSoc top chrome exists", "components/PulseChrome.tsx", "PulseTopBar"],
  ["Reels use web For You pill", "screens/main/ReelsScreen.tsx", "For You"],
  ["Reels default muted for autoplay safety", "screens/main/ReelsScreen.tsx", "useState(true)"],
  ["Reels show original sound metadata", "screens/main/ReelsScreen.tsx", "Original PulseSoc sound"],
  ["Mux playback resolver exists", "services/pulseMedia.ts", "stream.mux.com"],
  ["Native route has Videos tab", "navigation/MainTabs.tsx", "VideosScreen"],
  ["Native route has Messages tab", "navigation/MainTabs.tsx", "MessagesScreen"],
  ["Settings use real security API", "screens/main/SettingsScreen.tsx", "/api/account/security"]
];

const forbidden = [
  ["Retired API preview screens in tab wrappers", "screens/main", /ApiPreview/],
  ["Foundation placeholder language", "screens", /Foundation for/i],
  ["Debug shell import", "App.tsx", /src\/App/],
  ["John Doe placeholder", "screens", /John Doe/],
  ["Diagnostic Messages copy", "src/screens/CommunicationsScreen.tsx", /Production Communications V2/i]
];

for (const [label, relativePath, needle] of required) {
  const text = read(relativePath);
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

for (const [label, relativePath, pattern] of forbidden) {
  for (const file of files(relativePath)) {
    const text = fs.readFileSync(file, "utf8");
    if (pattern.test(text)) failures.push(`${label} found in ${path.relative(root, file)}`);
  }
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

function files(relativePath) {
  const target = path.join(root, relativePath);
  if (!fs.existsSync(target)) return [];
  if (fs.statSync(target).isFile()) return [target];
  return walk(target).filter(file => /\.(tsx?|jsx?)$/.test(file));
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}
