const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const packageJson = fs.readFileSync(path.join(root, "package.json"), "utf8");
const appJson = fs.readFileSync(path.join(root, "app.json"), "utf8");
const index = fs.readFileSync(path.join(root, "index.ts"), "utf8");
const forbidden = ["@livekit", "livekit-client", "react-native-webrtc"];
const failures = forbidden.filter((name) => packageJson.includes(name) || appJson.includes(name) || index.includes(name));

if (failures.length) {
  console.error(`Legacy RTC dependencies remain: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("Native Live dependency audit passed: no legacy RTC dependency remains.");
