const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const failures = [];

const checks = [
  ["Root app uses production navigator", "App.tsx", "AppNavigator"],
  ["Root app does not use retired demo shell", "App.tsx", "src/App", true],
  ["Vector icons installed", "package.json", "@expo/vector-icons"],
  ["Tab icons wired", "navigation/MainTabs.tsx", "MaterialCommunityIcons"],
  ["Web-style tab label Home", "navigation/MainTabs.tsx", "title: \"Home\""],
  ["Web-style tab label Chats", "navigation/MainTabs.tsx", "title: \"Chats\""],
  ["Web-style tab label Alerts", "navigation/MainTabs.tsx", "title: \"Alerts\""],
  ["Videos tab exists", "navigation/MainTabs.tsx", "VideosScreen"],
  ["Shared PulseSoc top chrome exists", "components/PulseChrome.tsx", "PulseTopBar"],
  ["Feed has PulseSoc web hero", "screens/main/HomeFeedScreen.tsx", "Global Pulse Feed"],
  ["Feed has status module", "screens/main/HomeFeedScreen.tsx", "Pulse Status"],
  ["Feed has live module", "screens/main/HomeFeedScreen.tsx", "Live Now"],
  ["Feed has formatted relative timestamps", "components/feed/PostCard.tsx", "formatRelativeTime"],
  ["Feed avoids raw ISO timestamps", "components/feed/PostCard.tsx", "toLocaleDateString", true],
  ["Feed shows author fallback initials", "components/feed/PostCard.tsx", "initials"],
  ["Reels use full screen sizing", "screens/main/ReelsScreen.tsx", "Dimensions.get"],
  ["Reels show For You pill", "screens/main/ReelsScreen.tsx", "For You"],
  ["Reels show muted state pill", "screens/main/ReelsScreen.tsx", "Muted"],
  ["Reels show original sound row", "screens/main/ReelsScreen.tsx", "Original PulseSoc sound"],
  ["Reels use video player", "screens/main/ReelsScreen.tsx", "VideoView"],
  ["Reels autoplay active item", "screens/main/ReelsScreen.tsx", "player.play()"],
  ["Reels pause offscreen", "screens/main/ReelsScreen.tsx", "player.pause()"],
  ["Videos use playable media", "screens/main/VideosScreen.tsx", "VideoView"],
  ["Videos reserve immersive height", "screens/main/VideosScreen.tsx", "minHeight: 430"],
  ["Videos contain media instead of cropping", "screens/main/VideosScreen.tsx", "contentFit=\"contain\""],
  ["Videos support Mux playback", "services/pulseMedia.ts", "stream.mux.com"],
  ["Messages are not API preview", "screens/main/MessagesScreen.tsx", "ApiPreview", true],
  ["Messages use communications surface", "screens/main/MessagesScreen.tsx", "CommunicationsScreen"],
  ["Messages hide diagnostic label", "src/screens/CommunicationsScreen.tsx", "Production Communications V2", true],
  ["Notifications are not API preview", "screens/main/NotificationsScreen.tsx", "ApiPreview", true],
  ["Notifications use production cards", "src/screens/NotificationsScreen.tsx", "NotificationCard"],
  ["Profile binds real profile API", "screens/main/ProfileScreen.tsx", "/api/pulse/profile/me"],
  ["Profile removes John Doe placeholder", "screens/main/ProfileScreen.tsx", "John Doe", true],
  ["Profile exposes logout", "screens/main/ProfileScreen.tsx", "Log Out"],
  ["Premium binds account status", "screens/main/PremiumScreen.tsx", "/api/account/status"],
  ["Premium exposes billing action", "screens/main/PremiumScreen.tsx", "Manage Billing"]
];

for (const [label, relativePath, needle, shouldBeMissing] of checks) {
  const text = read(relativePath);
  const found = text.includes(needle);
  if (shouldBeMissing ? found : !found) failures.push(`${label} failed in ${relativePath}`);
}

if (failures.length) {
  console.error("PulseSoc Android UI quality audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc Android UI quality audit passed.");

function read(relativePath) {
  const target = path.join(root, relativePath);
  return fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
}
