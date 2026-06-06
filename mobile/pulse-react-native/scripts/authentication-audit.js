const fs = require("fs");
const path = require("path");

const mobileRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(mobileRoot, "..", "..");

const checks = [
  ["SecureStore session persistence", "src/api/client.ts", "SecureStore"],
  ["Session restore provider", "src/auth/AuthProvider.tsx", "/api/mobile/auth/session"],
  ["Login endpoint", "src/auth/AuthProvider.tsx", "/api/mobile/auth/login"],
  ["Register endpoint", "src/auth/AuthProvider.tsx", "/api/mobile/auth/register"],
  ["Recovery endpoint", "src/auth/AuthProvider.tsx", "/api/mobile/auth/recover"],
  ["Logout cleanup", "src/auth/AuthProvider.tsx", "clearPulseSession"],
  ["Login register recover UI", "src/screens/AuthScreen.tsx", "mode === \"register\""],
  ["Recover mode UI", "src/screens/AuthScreen.tsx", "mode === \"recover\""],
  ["Deep link reset support", "src/navigation/linking.ts", "login"],
  ["Authentication report", "docs/authentication_phase_report.md", "Validation Results"],
  ["Backend resend confirmation API", "../bot.py", "/api/mobile/auth/resend-confirmation"],
  ["Backend confirm email API", "../bot.py", "/api/mobile/auth/confirm-email"],
  ["Backend reset password API", "../bot.py", "/api/mobile/auth/reset-password"],
  ["Backend refresh API", "../bot.py", "/api/mobile/auth/refresh"],
  ["Backend username lookup", "../bot.py", "load_account_by_email_or_username"]
];

const failures = [];

for (const [label, relativePath, needle] of checks) {
  const target = relativePath.startsWith("../")
    ? path.join(repoRoot, relativePath.slice(3))
    : path.join(mobileRoot, relativePath);
  const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

const forbidden = [
  ["Password logging", "src", /console\.(log|info|warn|error)\([^)]*password/i],
  ["Token logging", "src", /console\.(log|info|warn|error)\([^)]*token/i],
  ["AsyncStorage auth secrets", "src", /AsyncStorage/]
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
  console.error("Pulse mobile authentication audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Pulse mobile authentication audit passed.");

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap(entry => {
    const fullPath = path.join(dir, entry.name);
    if (entry.name === "node_modules" || entry.name === ".expo") return [];
    return entry.isDirectory() ? walk(fullPath) : [fullPath];
  });
}
