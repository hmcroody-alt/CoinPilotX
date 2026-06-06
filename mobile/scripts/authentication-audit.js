const fs = require("fs");
const path = require("path");

const mobileRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(mobileRoot, "..");

const checks = [
  ["SecureStore session persistence", "mobile/services/secureSession.ts", "expo-secure-store"],
  ["Refresh-capable session store", "mobile/store/authStore.ts", "refreshSession"],
  ["Logout cleanup", "mobile/store/authStore.ts", "clearSecureSession"],
  ["Email or username login", "mobile/screens/auth/LoginScreen.tsx", "Email or username"],
  ["Signup confirmation state", "mobile/screens/auth/SignupScreen.tsx", "EmailConfirmationPending"],
  ["Confirmation pending screen", "mobile/screens/auth/EmailConfirmationPendingScreen.tsx", "Resend confirmation"],
  ["Password reset screen", "mobile/screens/auth/ResetPasswordScreen.tsx", "Reset password"],
  ["Deep link reset route", "mobile/navigation/linking.ts", "reset-password/:token"],
  ["Deep link confirmation route", "mobile/navigation/linking.ts", "verify-email/:token"],
  ["Profile bootstrap", "mobile/services/profileBootstrap.ts", "/api/pulse/profile/me"],
  ["Authentication report", "mobile/docs/authentication_phase_report.md", "Validation Results"],
  ["Backend resend confirmation API", "bot.py", "/api/mobile/auth/resend-confirmation"],
  ["Backend confirm email API", "bot.py", "/api/mobile/auth/confirm-email"],
  ["Backend reset password API", "bot.py", "/api/mobile/auth/reset-password"],
  ["Backend refresh API", "bot.py", "/api/mobile/auth/refresh"],
  ["Backend username lookup", "bot.py", "load_account_by_email_or_username"]
];

const failures = [];

for (const [label, relativePath, needle] of checks) {
  const target = path.join(repoRoot, relativePath);
  const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (!text.includes(needle)) failures.push(`${label} missing in ${relativePath}`);
}

const forbidden = [
  ["Password logging", "mobile", /console\.(log|info|warn|error)\([^)]*password/i],
  ["Token logging", "mobile", /console\.(log|info|warn|error)\([^)]*token/i],
  ["AsyncStorage auth secrets", "mobile/store", /AsyncStorage/]
];

for (const [label, relativeDir, pattern] of forbidden) {
  const dir = path.join(repoRoot, relativeDir);
  const files = walk(dir).filter(file => /\.(ts|tsx|js)$/.test(file));
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    if (pattern.test(text)) failures.push(`${label} found in ${path.relative(repoRoot, file)}`);
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
