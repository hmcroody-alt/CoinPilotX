const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");

const requiredDirs = [
  "assets",
  "components",
  "screens",
  "services",
  "hooks",
  "navigation",
  "store"
];

const requiredFiles = [
  "App.tsx",
  "app.json",
  "package.json",
  "tsconfig.json",
  "services/apiClient.ts",
  "services/auth.ts",
  "services/secureSession.ts",
  "services/push.ts",
  "services/mediaUpload.ts",
  "navigation/AppNavigator.tsx",
  "navigation/linking.ts",
  "store/authStore.ts",
  "docs/architecture-report.md",
  "docs/navigation-map.md",
  "docs/api-integration-map.md",
  "docs/state-management-recommendation.md"
];

const sourceChecks = [
  ["React Navigation", "navigation/AppNavigator.tsx", "AppNavigator"],
  ["Secure token storage", "services/secureSession.ts", "expo-secure-store"],
  ["API client", "services/apiClient.ts", "PULSE_API_BASE_URL"],
  ["Push notifications", "services/push.ts", "expo-notifications"],
  ["Deep linking", "navigation/linking.ts", "pulse://"],
  ["Media upload", "services/mediaUpload.ts", "/api/pulse/media/upload"],
  ["Auth session", "store/authStore.ts", "restoreSession"]
];

const failures = [];

for (const dir of requiredDirs) {
  if (!fs.existsSync(path.join(root, dir))) failures.push(`Missing directory: ${dir}`);
}

for (const file of requiredFiles) {
  if (!fs.existsSync(path.join(root, file))) failures.push(`Missing file: ${file}`);
}

for (const [label, file, needle] of sourceChecks) {
  const target = path.join(root, file);
  const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (!text.includes(needle)) failures.push(`${label} check failed in ${file}`);
}

if (failures.length > 0) {
  console.error("PulseSoc mobile foundation audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile foundation audit passed.");
