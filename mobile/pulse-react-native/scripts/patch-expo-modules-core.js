const fs = require("fs");
const path = require("path");

const permissionsServicePath = path.join(
  __dirname,
  "..",
  "node_modules",
  "expo-modules-core",
  "android",
  "src",
  "main",
  "java",
  "expo",
  "modules",
  "adapters",
  "react",
  "permissions",
  "PermissionsService.kt"
);

if (!fs.existsSync(permissionsServicePath)) {
  console.warn("PulseSoc patch skipped: expo-modules-core PermissionsService.kt not found.");
  process.exit(0);
}

const source = fs.readFileSync(permissionsServicePath, "utf8");
const before = "return requestedPermissions.contains(permission)";
const after = "return requestedPermissions?.contains(permission) == true";

if (source.includes(after)) {
  console.log("PulseSoc patch already applied: expo-modules-core API 35 permission check.");
  process.exit(0);
}

if (!source.includes(before)) {
  console.warn("PulseSoc patch skipped: expected expo-modules-core permission check was not found.");
  process.exit(0);
}

fs.writeFileSync(permissionsServicePath, source.replace(before, after));
console.log("PulseSoc patch applied: expo-modules-core API 35 permission check.");
