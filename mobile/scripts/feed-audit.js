const fs = require("fs");
const path = require("path");

const mobileRoot = path.resolve(__dirname, "..");

const checks = [
  ["Timeline feed screen", "screens/main/HomeFeedScreen.tsx", "FlatList"],
  ["Pull to refresh", "screens/main/HomeFeedScreen.tsx", "RefreshControl"],
  ["Infinite scroll", "screens/main/HomeFeedScreen.tsx", "onEndReached"],
  ["Feed backend endpoint", "services/feed/feedApi.ts", "/api/pulse/feed"],
  ["Post backend endpoint", "services/feed/feedApi.ts", "/api/pulse/posts"],
  ["Media upload endpoint", "services/feed/mediaUpload.ts", "/api/pulse/media/upload"],
  ["Status composer", "screens/main/feed/CreatePulseScreen.tsx", "Create Pulse"],
  ["Image and video picker", "screens/main/feed/CreatePulseScreen.tsx", "expo-image-picker"],
  ["Upload progress", "screens/main/feed/CreatePulseScreen.tsx", "uploadProgress"],
  ["Like action", "components/feed/PostCard.tsx", "reactToPost"],
  ["Comment action", "screens/main/feed/PostDetailScreen.tsx", "addComment"],
  ["Repost action", "components/feed/PostCard.tsx", "repostPost"],
  ["Share action", "components/feed/PostCard.tsx", "sharePost"],
  ["Edit own post action", "components/feed/PostCard.tsx", "editPost"],
  ["Delete own post action", "components/feed/PostCard.tsx", "deletePost"],
  ["Profile navigation route", "navigation/types.ts", "ProfileDetail"],
  ["Post deep link", "navigation/linking.ts", "post/:postId"],
  ["Profile deep link", "navigation/linking.ts", "profile/:username"],
  ["Feed analytics", "services/analytics.ts", "pulse_mobile"],
  ["Notification event hooks", "services/feed/events.ts", "emitFeedHook"],
  ["Feed phase report", "docs/feed_phase_report.md", "Performance Strategy"]
];

const failures = [];

for (const [label, relativePath, needle] of checks) {
  const target = path.join(mobileRoot, relativePath);
  const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

const forbidden = [
  ["Feed work must not implement Reels", "screens/main/feed", /Reels/i],
  ["Feed work must not implement Messaging", "screens/main/feed", /Messaging/i],
  ["Feed work must not implement Marketplace", "screens/main/feed", /Marketplace/i],
  ["Token logging", ".", /console\.(log|info|warn|error)\([^)]*token/i],
  ["Password logging", ".", /console\.(log|info|warn|error)\([^)]*password/i]
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
  console.error("Pulse mobile feed audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Pulse mobile feed audit passed.");

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap(entry => {
    const fullPath = path.join(dir, entry.name);
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    return entry.isDirectory() ? walk(fullPath) : [fullPath];
  });
}
