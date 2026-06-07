const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const firebaseDir = path.join(root, "credentials", "firebase");

const files = [
  {
    env: ["EAS_GOOGLE_SERVICES_JSON", "GOOGLE_SERVICES_JSON"],
    target: path.join(firebaseDir, "google-services.json")
  },
  {
    env: ["EAS_GOOGLE_SERVICE_INFO_PLIST", "GOOGLE_SERVICE_INFO_PLIST"],
    target: path.join(firebaseDir, "GoogleService-Info.plist")
  }
];

fs.mkdirSync(firebaseDir, { recursive: true });

for (const file of files) {
  const source = file.env.map(name => process.env[name]).find(Boolean);
  if (!source) continue;
  if (!fs.existsSync(source)) {
    throw new Error(`Firebase config file variable is set but the source file is unavailable for ${path.basename(file.target)}.`);
  }
  fs.copyFileSync(source, file.target);
  console.log(`Prepared ${path.basename(file.target)} for native build.`);
}
