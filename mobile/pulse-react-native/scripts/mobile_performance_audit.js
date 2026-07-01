#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(root, "App.tsx"), "utf8");
const projectRoot = path.resolve(root, "../..");
const media = fs.readFileSync(path.resolve(projectRoot, "static/js/pulse_media_renderer.js"), "utf8");
const mobileCss = fs.readFileSync(path.resolve(projectRoot, "static/css/pulse_mobile_system.css"), "utf8");
const notifications = fs.readFileSync(path.resolve(projectRoot, "static/notifications.js"), "utf8");
const failures = [];

function expect(condition, message) {
  if (!condition) failures.push(message);
}

expect(app.includes("<WebView"), "mobile app must remain a WebView shell");
expect(app.includes("decelerationRate"), "WebView should set iOS deceleration behavior");
expect(app.includes("cacheEnabled"), "WebView should keep cache enabled for website assets");
expect(app.includes("androidLayerType=\"hardware\""), "Android WebView should use hardware composition");
expect(media.includes("mobilePerformanceMode"), "media renderer should expose mobile performance mode");
expect(media.includes("requestAnimationFrame"), "media autoplay should be scheduled through requestAnimationFrame");
expect(media.includes("lastScrollAt"), "media autoplay should avoid fighting active scroll");
expect(media.includes("mediaRootMargin(\"420px 0px\", \"220px 0px\", \"160px 0px\")"), "media hydration should lazy-load closer on mobile and PulseShell constrained modes");
expect(mobileCss.includes(".pulse-media-soft-glow") && mobileCss.includes("display: none !important"), "mobile CSS should remove decorative media layers while scrolling");
expect(notifications.includes("lastScrollAt") && notifications.includes("schedulePolling(30000)"), "notification polling should be scroll-aware and less aggressive");

if (failures.length) {
  console.error("PulseSoc mobile performance audit failed:");
  failures.forEach(failure => console.error(`- ${failure}`));
  process.exit(1);
}

console.log("PulseSoc mobile performance audit passed.");
