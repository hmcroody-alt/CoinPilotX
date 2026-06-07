const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const screen = read("src/screens/NotificationsScreen.tsx");
const communications = read("src/screens/CommunicationsScreen.tsx");
const communicationsService = read("src/services/communications.ts");
const app = read("src/App.tsx");
const push = read("services/push.ts");
const authStore = read("store/authStore.ts");
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
  ["Uses safe fallback for hidden content", screen, "Reply hidden or unavailable."],
  ["Messages tab uses communications screen", app, "CommunicationsScreen"],
  ["Direct messages loaded", communicationsService, "/api/pulse/messages/conversations"],
  ["Groups loaded", communicationsService, "/api/pulse/groups"],
  ["Rooms loaded", communicationsService, "/api/pulse/rooms"],
  ["Communities loaded", communicationsService, "/api/pulse/communities"],
  ["Channels loaded", communicationsService, "/api/pulse/channels"],
  ["Message previews shown", communications, "previewText"],
  ["Read receipts shown", communications, "readReceiptText"],
  ["Typing indicator shown", communications, "typingText"],
  ["Presence shown", communications, "presenceText"],
  ["Offline queue implemented", communicationsService, "OFFLINE_QUEUE_KEY"],
  ["Realtime typing heartbeat prepared", communicationsService, "sendTypingHeartbeat"],
  ["Registers Expo/native push token", push, "getExpoPushTokenAsync"],
  ["Subscribes native push token", push, "/api/push/subscribe"],
  ["Unsubscribes native push token", push, "/api/push/unsubscribe"],
  ["Foreground notifications can play sound", push, "shouldPlaySound: true"],
  ["Android notification channel is high importance", push, "AndroidImportance.HIGH"],
  ["Android notification channel can vibrate", push, "enableVibrate: true"],
  ["Cleans up native push token on logout", authStore, "unregisterPushNotifications"]
].forEach(([label, text, needle]) => {
  if (!text.includes(needle)) failures.push(`${label} missing`);
});

if (failures.length) {
  console.error("PulseSoc mobile notifications audit failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("PulseSoc mobile notifications audit passed.");

function read(relativePath) {
  const file = path.join(root, relativePath);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
}
