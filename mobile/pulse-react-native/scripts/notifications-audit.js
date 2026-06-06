const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const screen = read("src/screens/NotificationsScreen.tsx");
const app = read("src/App.tsx");
const failures = [];

[
  ["Notifications tab uses custom screen", app, "NotificationsScreen"],
  ["Loads notification API", screen, "/api/pulse/notifications?limit=80"],
  ["Shows preview text", screen, "preview_text"],
  ["Shows original context", screen, "original_preview"],
  ["Supports Open", screen, "Open"],
  ["Supports Mark Read", screen, "Mark Read"],
  ["Supports Delete", screen, "Delete"],
  ["Supports inline Reply", screen, "Reply..."],
  ["Sends status quick reply", screen, "/api/pulse/status/${statusId}/reply"],
  ["Sends post quick reply", screen, "/api/pulse/posts/${postId}/comments"],
  ["Sends message quick reply", screen, "/api/pulse/messages/send"],
  ["Uses safe fallback for hidden content", screen, "Reply hidden or unavailable."]
].forEach(([label, text, needle]) => {
  if (!text.includes(needle)) failures.push(`${label} missing`);
});

if (failures.length) {
  console.error("Pulse mobile notifications audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Pulse mobile notifications audit passed.");

function read(relativePath) {
  const file = path.join(root, relativePath);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
}
