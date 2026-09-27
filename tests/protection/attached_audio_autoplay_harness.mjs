/**
 * Differential harness for the attached-music autoplay rule in
 * static/js/pulse_media_renderer.js.
 *
 * There is no jsdom in this repo and no JS test runner for the web statics, so
 * this stands up the smallest DOM the renderer's attached-audio path actually
 * touches and drives the real, unmodified exported functions against it.
 *
 * It is run twice by attached_audio_autoplay_check.py -- once against the file
 * on disk and once against the file as it was before the change -- and the
 * before-run is REQUIRED to fail. That is what stops a stub too weak to
 * exercise the real logic from reporting a vacuous pass: if the harness cannot
 * tell the two versions apart, the check reports inconclusive rather than ok.
 *
 * Two scenarios, run as separate processes because the renderer's unlock flag is
 * module scope and a fresh page load is exactly what one of them is about:
 *
 *   strict     a browser that refuses unmuted autoplay outright (Safari).
 *   permissive a browser that allows it (Chrome with media engagement).
 *
 * Usage: node attached_audio_autoplay_harness.mjs <path-to-renderer.js> [scenario]
 */
import { readFileSync } from "node:fs";

const source = readFileSync(process.argv[2], "utf8");
const scenario = process.argv[3] || "strict";
if (!["strict", "permissive"].includes(scenario)) {
  console.log(`INCONCLUSIVE: unknown scenario ${scenario}`);
  process.exit(3);
}

/* ------------------------------------------------------------------ DOM ---- */

class Ev {
  constructor(type, init = {}) {
    this.type = type;
    this.defaultPrevented = false;
    Object.assign(this, init);
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() {}
  stopImmediatePropagation() {}
}

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.dataset = {};
    this.children = [];
    this.parentElement = null;
    this.classList = new Set();
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.textContent = "";
    this.muted = false;
    this.defaultMuted = false;
    this.paused = true;
    this.volume = 1;
    this.currentTime = 0;
    this.duration = 30;
    this.playbackRate = 1;
    this.loop = false;
    this._src = "";
  }
  // A real media element normalizes `src` to an absolute URL, and the renderer
  // compares against `new URL(...).href` to decide whether to reuse the player.
  // A raw passthrough here would hand out a brand new element on every call and
  // quietly invalidate every assertion below.
  get src() { return this._src; }
  set src(value) { this._src = value ? new URL(value, windowObj.location.href).href : ""; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === "muted") this.muted = true; }
  removeAttribute(name) { delete this.attributes[name]; if (name === "muted") this.muted = false; }
  toggleAttribute(name, force) { if (force) this.setAttribute(name, ""); else this.removeAttribute(name); }
  hasAttribute(name) { return name in this.attributes; }
  matches(sel) { return descend(this, sel); }
  closest(sel) {
    let node = this;
    while (node) { if (descend(node, sel)) return node; node = node.parentElement; }
    return null;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = node => node.children.forEach(child => { if (descend(child, sel)) out.push(child); walk(child); });
    walk(this);
    return out;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); }
  dispatchEvent(event) { (this.listeners[event.type] || []).forEach(fn => fn(event)); return true; }
  async play() {
    if (this.tagName === "AUDIO") {
      // The point of the harness: a browser with no user activation refuses an
      // unmuted start and allows a muted one.
      if (!this.muted && !host.userActivated) {
        const error = new Error("play() failed because the user didn't interact with the document first");
        error.name = "NotAllowedError";
        throw error;
      }
    }
    this.paused = false;
  }
  pause() { this.paused = true; }
}

/** Selector support limited to what the renderer actually passes. */
function descend(node, selector) {
  return String(selector).split(",").some(part => {
    const sel = part.trim().replace(/^:scope\s*>\s*/, "");
    if (!sel) return false;
    return sel.split(/(?=[.[#])/).every(token => {
      if (token.startsWith(".")) return node.classList.has(token.slice(1));
      if (token.startsWith("[")) {
        const m = /^\[([^\]=]+)(?:=['"]?([^\]'"]*)['"]?)?\]$/.exec(token);
        if (!m) return false;
        const attr = m[1];
        const key = attr.startsWith("data-")
          ? attr.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())
          : attr;
        const have = attr.startsWith("data-") ? node.dataset[key] : node.attributes[attr];
        if (have === undefined) return false;
        return m[2] === undefined || String(have) === m[2];
      }
      return node.tagName === token.toUpperCase();
    });
  });
}

const store = new Map();
const host = {
  userActivated: scenario === "permissive",
  createdAudio: [],
};

const documentEl = new El("html");
const body = new El("body");
documentEl.appendChild(body);

const document = {
  documentElement: documentEl,
  body,
  hidden: false,
  listeners: {},
  createElement(tag) {
    const el = new El(tag);
    if (el.tagName === "AUDIO") host.createdAudio.push(el);
    return el;
  },
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); },
  dispatchEvent(event) { (this.listeners[event.type] || []).forEach(fn => fn(event)); return true; },
  querySelector(sel) { return documentEl.querySelector(sel); },
  querySelectorAll(sel) { return documentEl.querySelectorAll(sel); },
  getElementById() { return null; },
};

class NoopObserver {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
}

const windowObj = {
  location: { href: "https://pulsesoc.com/pulse", hostname: "pulsesoc.com", search: "" },
  localStorage: {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: key => store.delete(key),
  },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
  requestAnimationFrame: fn => setTimeout(() => fn(Date.now()), 0),
  cancelAnimationFrame: id => clearTimeout(id),
  requestIdleCallback: fn => setTimeout(() => fn({ timeRemaining: () => 50 }), 0),
  setTimeout,
  clearTimeout,
  navigator: { connection: undefined, userAgent: "harness" },
  IntersectionObserver: NoopObserver,
  MutationObserver: NoopObserver,
  CustomEvent: Ev,
  URL,
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
windowObj.window = windowObj;
windowObj.document = document;

/* --------------------------------------------------------------- load it --- */

const sandbox = {
  window: windowObj,
  document,
  navigator: windowObj.navigator,
  location: windowObj.location,
  localStorage: windowObj.localStorage,
  IntersectionObserver: NoopObserver,
  MutationObserver: NoopObserver,
  CustomEvent: Ev,
  Event: Ev,
  requestAnimationFrame: windowObj.requestAnimationFrame,
  cancelAnimationFrame: windowObj.cancelAnimationFrame,
  requestIdleCallback: windowObj.requestIdleCallback,
  HTMLElement: El,
  queueMicrotask,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  console,
  URL,
  fetch: windowObj.fetch,
};
const names = Object.keys(sandbox);
// eslint-disable-next-line no-new-func
new Function(...names, source)(...names.map(name => sandbox[name]));

const renderer = windowObj.PulseMediaRenderer;
if (!renderer?.playAttachedAudio) {
  console.log("INCONCLUSIVE: renderer did not expose playAttachedAudio");
  process.exit(3);
}

/* ------------------------------------------------------------------ test --- */

function buildPost() {
  const wrap = new El("div");
  wrap.classList.add("pulse-media-wrap");
  wrap.dataset.attachedAudioUrl = "/static/audio/track.mp3";
  wrap.dataset.audioStartTime = "0";
  wrap.dataset.audioVolume = "1";
  const video = new El("video");
  wrap.appendChild(video);
  const soundButton = new El("button");
  soundButton.dataset.pulseMediaSound = "1";
  soundButton.hidden = true;
  wrap.appendChild(soundButton);
  body.appendChild(wrap);
  return { wrap, video, soundButton };
}

const failures = [];
function check(label, condition) {
  if (condition) console.log(`  ok   - ${label}`);
  else { console.log(`  FAIL - ${label}`); failures.push(label); }
}

const { wrap, video } = buildPost();

// A reader with no stored preference. `soundEnabled()` defaults to true, which
// is the same default a plain video autoplays under.
check("default sound preference is on", renderer.soundEnabled() === true);

// The feed scrolls the post into view and the video starts.
video.paused = false;
await renderer.playAttachedAudio(video, true);

const audio = host.createdAudio[host.createdAudio.length - 1];
check("an attached-audio element was created", !!audio);

if (scenario === "permissive") {
  // The headline case, and the one that matches the app: nothing was tapped,
  // the browser permits it, so the post's music simply plays.
  check("music plays with sound, unprompted", audio && audio.paused === false && audio.muted === false);
  check("state reports playing, not playing-muted", wrap.dataset.attachedAudioState === "playing");
  check("the original video track stays silent", video.muted === true);
} else {
  // Assertion 1: the browser refused the unmuted start, but the music is
  // running silently and in sync -- not stopped, not left in the blocked state.
  check("music is playing after a refused unmuted start", audio && audio.paused === false);
  check("music fell back to muted rather than blocked", wrap.dataset.attachedAudioState === "playing-muted");
  check("the original video track stays silent", video.muted === true);

  // The observer re-fires, or the video emits another `play`. Re-entering must
  // not try to unmute an element that is already running: a bare `muted = false`
  // returns no promise, so the browser's refusal arrives as a silent pause and
  // the music stops for real while the call reports success.
  await renderer.playAttachedAudio(video, true);
  check("a re-entrant call leaves the running track muted", audio && audio.muted === true);
  check("a re-entrant call does not stop the music", audio && audio.paused === false);

  // Assertion 2: the reader taps something -- anything -- and the music comes
  // up on the post already on screen. No second tap on this post's own control.
  host.userActivated = true;
  document.dispatchEvent(new Ev("pointerdown", { target: body }));
  await new Promise(resolve => setTimeout(resolve, 20));

  check("a gesture anywhere unmutes the music in place", audio && audio.muted === false);
  check("the music did not restart from the top", audio && audio.paused === false);
  check("the original video track is still silent", video.muted === true);
}

// Assertion 3: the page now knows audio is permitted, so the NEXT post starts
// with sound instead of repeating the speculative attempt.
const next = buildPost();
next.video.paused = false;
await renderer.playAttachedAudio(next.video, true);
const nextAudio = host.createdAudio[host.createdAudio.length - 1];
check("a later post starts with sound already on", nextAudio && nextAudio.muted === false);
check("later post reports playing, not playing-muted", next.wrap.dataset.attachedAudioState === "playing");

if (failures.length) {
  console.log(`FAILED (${failures.length})`);
  process.exit(1);
}
console.log("PASSED");
