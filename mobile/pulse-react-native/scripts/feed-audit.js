const fs = require("fs");
const path = require("path");

const mobileRoot = path.resolve(__dirname, "..");

const checks = [
  ["Feed screen", "src/App.tsx", "endpoint={pulseApiEndpoints.feed}"],
  ["Reels screen", "src/App.tsx", "endpoint={pulseApiEndpoints.reels}"],
  ["Videos screen", "src/App.tsx", "endpoint={pulseApiEndpoints.videos}"],
  ["Messages screen", "src/App.tsx", "CommunicationsScreen"],
  ["Feed API endpoint", "src/App.tsx", "/api/pulse/feed"],
  ["Reels API endpoint", "src/App.tsx", "/api/pulse/reels/feed"],
  ["Videos API endpoint", "src/App.tsx", "/api/pulse/videos"],
  ["Messages API endpoint", "src/App.tsx", "/api/pulse/messages/conversations"],
  ["Messages service endpoint", "src/services/communications.ts", "/api/pulse/messages/conversations"],
  ["Notifications screen", "src/App.tsx", "NotificationsScreen"],
  ["Notification inbox endpoint", "src/screens/NotificationsScreen.tsx", "/api/pulse/notifications?limit=80"],
  ["Notification quick status reply", "src/screens/NotificationsScreen.tsx", "/api/pulse/status/${statusId}/reply"],
  ["Notification quick post reply", "src/screens/NotificationsScreen.tsx", "/api/pulse/posts/${postId}/comments"],
  ["Notification quick message reply", "src/screens/NotificationsScreen.tsx", "/api/pulse/messages/send"],
  ["Notification mark read", "src/screens/NotificationsScreen.tsx", "/api/pulse/notifications/${noteId}/read"],
  ["Profile API", "src/App.tsx", "/api/pulse/profile/me"],
  ["Pull to refresh", "src/screens/ApiListScreen.tsx", "RefreshControl"],
  ["API list reuse", "src/screens/ApiListScreen.tsx", "pulseApi"],
  ["Feed phase report", "docs/feed_phase_report.md", "Performance Strategy"]
];

const failures = [];

for (const [label, relativePath, needle] of checks) {
  const target = path.join(mobileRoot, relativePath);
  const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

const forbidden = [
  ["Token logging", "src", /console\.(log|info|warn|error)\([^)]*token/i],
  ["Password logging", "src", /console\.(log|info|warn|error)\([^)]*password/i]
];

for (const [label, relativeDir, pattern] of forbidden) {
  const dir = path.join(mobileRoot, relativeDir);
  const files = walk(dir).filter(file => /\.(ts|tsx|js)$/.test(file));
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    if (pattern.test(text)) failures.push(`${label} found in ${path.relative(mobileRoot, file)}`);
  }
}

if (failures.length > 0) {
  console.error("PulseSoc mobile feed audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile feed audit passed.");

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap(entry => {
    const fullPath = path.join(dir, entry.name);
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    return entry.isDirectory() ? walk(fullPath) : [fullPath];
  });
}
