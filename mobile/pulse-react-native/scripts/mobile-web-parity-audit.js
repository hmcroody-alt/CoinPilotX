const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const failures = [];

const checks = [
  ["Web color token background", "components/theme.ts", "#050b14"],
  ["Web color token cyan", "components/theme.ts", "#6edff6"],
  ["Web color token green", "components/theme.ts", "#36e58f"],
  ["Shared mobile web chrome", "components/PulseChrome.tsx", "PulseTopBar"],
  ["PulseSoc brand mark", "components/PulseChrome.tsx", "PulseSoc"],
  ["Feed web hero", "screens/main/HomeFeedScreen.tsx", "Global Pulse Feed"],
  ["Feed status controls", "screens/main/HomeFeedScreen.tsx", "Create Status"],
  ["Feed status endpoint", "services/pulseDiscovery.ts", "/api/pulse/status/rail"],
  ["Feed live endpoint", "services/pulseDiscovery.ts", "/api/pulse/live-now"],
  ["Reels For You control", "screens/main/ReelsScreen.tsx", "For You"],
  ["Reels muted autoplay state", "screens/main/ReelsScreen.tsx", "useState(true)"],
  ["Reels original sound row", "screens/main/ReelsScreen.tsx", "Original PulseSoc sound"],
  ["Videos immersive reserved height", "screens/main/VideosScreen.tsx", "minHeight: 430"],
  ["Videos contain fit", "screens/main/VideosScreen.tsx", "contentFit=\"contain\""],
  ["Messages user-facing copy", "src/screens/CommunicationsScreen.tsx", "Direct messages, groups, rooms, and community channels from PulseSoc."],
  ["Notifications PulseSoc top chrome", "src/screens/NotificationsScreen.tsx", "PulseTopBar"],
  ["Profile PulseSoc identity hero", "screens/main/ProfileScreen.tsx", "Your PulseSoc identity"],
  ["Premium PulseSoc hero", "screens/main/PremiumScreen.tsx", "PulseSoc Premium"]
];

const forbidden = [
  ["John Doe placeholder", "mobile source", /John Doe/],
  ["Diagnostic Communications label", "mobile source", /Production Communications V2/],
  ["Raw date rendering", "mobile source", /toLocaleDateString/]
];

for (const [label, relativePath, needle] of checks) {
  const text = read(relativePath);
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

const mobileSource = walk(root).filter(file => {
  const relative = path.relative(root, file);
  return /\.(tsx?|jsx?)$/.test(file)
    && !file.includes(`${path.sep}node_modules${path.sep}`)
    && !relative.startsWith("scripts/")
    && !relative.startsWith("docs/");
});
for (const [label, _scope, pattern] of forbidden) {
  for (const file of mobileSource) {
    const relative = path.relative(root, file);
    const text = fs.readFileSync(file, "utf8");
    if (pattern.test(text)) failures.push(`${label} found in ${relative}`);
  }
}

for (const file of filesIn("screens").concat(filesIn("navigation"))) {
  const text = fs.readFileSync(file, "utf8");
  if (/ApiPreview/.test(text)) failures.push(`API preview user screen found in ${path.relative(root, file)}`);
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

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

function filesIn(relativePath) {
  const target = path.join(root, relativePath);
  if (!fs.existsSync(target)) return [];
  return walk(target).filter(file => /\.(tsx?|jsx?)$/.test(file));
}
