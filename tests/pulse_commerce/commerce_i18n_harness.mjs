/**
 * End-to-end harness for the commerce card's translation path.
 *
 * There is no jsdom in this repo and no JS test runner for the web statics, so
 * this stands up the smallest DOM the two real files touch and runs them
 * unmodified: `pulse_i18n.js` (the catalogue and the `[data-i18n]` sweeper) and
 * `pulse_commerce_card.js` (the renderer and `localize`). Nothing about the
 * card's copy is restated here -- the harness reports what came out, and the
 * Python side decides whether that is the French the catalogue promised.
 *
 * Two scenarios, because the card reaches the page two different ways and each
 * has its own way of staying English:
 *
 *   inserted  the feed/reels path. The card is appended long after the
 *             DOMContentLoaded sweep, so only `localize` can translate it.
 *   served    the permalink path, in the script order the page actually uses:
 *             the card is in the HTML, `pulse_i18n.js` is deferred from the
 *             head and sweeps it, and `pulse_commerce_card.js` is deferred from
 *             the end of the body -- so it loads *after* the sweep it needs to
 *             react to, and nothing dispatches a second time.
 *   relanguage the reader switches language with both files already loaded.
 *
 * Usage: node commerce_i18n_harness.mjs <i18n.js> <card.js> <cases.json>
 */
import { readFileSync } from "node:fs";

const [i18nPath, cardPath, casesPath] = process.argv.slice(2);
const cases = JSON.parse(readFileSync(casesPath, "utf8"));

/* ------------------------------------------------------------------ DOM ---- */

/** `tag`, `.class` and `[attr]` -- every selector the two files use. */
function parseSelector(selector) {
  const tag = (/^[a-zA-Z][\w-]*/.exec(selector) || [""])[0].toUpperCase();
  return {
    tag,
    classes: Array.from(selector.matchAll(/\.([\w-]+)/g), (m) => m[1]),
    attrs: Array.from(selector.matchAll(/\[([\w-]+)\]/g), (m) => m[1]),
  };
}

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.dataset = {};
    this.ownText = "";
  }

  get classList() {
    return new Set(String(this.attributes.get("class") || "").split(/\s+/).filter(Boolean));
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name.startsWith("data-")) this.dataset[camel(name.slice(5))] = String(value);
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  get firstElementChild() {
    return this.children[0] || null;
  }

  get textContent() {
    return this.ownText + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.children = [];
    this.ownText = String(value);
  }

  set innerHTML(markup) {
    this.children = [];
    this.ownText = "";
    for (const child of parseHtml(String(markup))) this.appendChild(child);
  }

  matches(selector) {
    const { tag, classes, attrs } = parseSelector(selector);
    if (tag && this.tagName !== tag) return false;
    const owned = this.classList;
    return classes.every((name) => owned.has(name)) && attrs.every((name) => this.attributes.has(name));
  }

  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (child.matches(selector)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    found.forEach = Array.prototype.forEach.bind(found);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function camel(name) {
  return name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

const VOID_TAGS = new Set(["img", "br", "hr", "input", "meta", "link"]);

/**
 * Parse the renderer's own markup. Not a general HTML parser: the input is
 * machine-generated, every attribute is single-quoted and every quote inside a
 * value is already `&#x27;`, which is why a tokenizer this small is honest here.
 */
function parseHtml(markup) {
  const roots = [];
  const stack = [];
  const pattern = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:='[^']*')?)*)\s*\/?>|([^<]+)/g;
  let match;
  while ((match = pattern.exec(markup)) !== null) {
    const [, closing, tag, attrs, text] = match;
    const parent = stack[stack.length - 1];
    if (text !== undefined) {
      if (parent) parent.ownText += decode(text);
      continue;
    }
    if (closing) {
      stack.pop();
      continue;
    }
    const element = new El(tag);
    for (const attr of (attrs || "").matchAll(/([\w-]+)(?:='([^']*)')?/g)) {
      if (attr[1]) element.setAttribute(attr[1], decode(attr[2] === undefined ? "" : attr[2]));
    }
    if (parent) parent.appendChild(element);
    else roots.push(element);
    if (!VOID_TAGS.has(tag.toLowerCase())) stack.push(element);
  }
  return roots;
}

function decode(value) {
  return value
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/* -------------------------------------------------------------- globals ---- */

function makeDocument() {
  const root = new El("html");
  const body = root.appendChild(new El("body"));
  const listeners = new Map();
  return {
    documentElement: root,
    body,
    readyState: "complete",
    createElement: (tag) => new El(tag),
    querySelectorAll: (selector) => root.querySelectorAll(selector),
    querySelector: (selector) => root.querySelector(selector),
    addEventListener: (type, handler) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    dispatchEvent: (event) => {
      for (const handler of listeners.get(event.type) || []) handler(event);
      return true;
    },
  };
}

function load(scenario, language, overlay, surface, markup) {
  const document = makeDocument();
  const store = new Map([["pulse.preferred.language", scenario === "relanguage" ? "en" : language]]);

  globalThis.window = {};
  globalThis.document = document;
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
  };
  // The language is already decided by the cached value; the server round trip
  // must not be what makes this pass. A logged-out reader is exactly the case
  // where it fails, and the permalink is the page they land on.
  globalThis.fetch = () => Promise.reject(new Error("offline"));
  globalThis.CustomEvent = class {
    constructor(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    }
  };

  const host = document.body.appendChild(new El("div"));
  const i18nSource = readFileSync(i18nPath, "utf8");
  const cardSource = readFileSync(cardPath, "utf8");

  if (scenario === "served") {
    // Document order, and nothing after it: the card is already parsed, i18n
    // runs and sweeps, then the card script loads with the sweep behind it.
    host.innerHTML = markup;
    new Function(i18nSource)();
    new Function(cardSource)();
  } else {
    new Function(i18nSource)();
    new Function(cardSource)();
    if (scenario === "inserted") {
      window.PulseCommerceCard.render(host, overlay, { surface });
    } else {
      host.innerHTML = markup;
      window.PulseI18n.applyLanguage(language);
    }
  }

  const card = host.querySelector(".pulse-commerce-card");
  const readText = (selector) => {
    const node = card && card.querySelector(selector);
    return node ? node.textContent : null;
  };
  const main = card && card.querySelector(".pulse-commerce-main");
  return {
    label: readText(".pulse-commerce-chip-label"),
    state: readText(".pulse-commerce-chip-state"),
    cta: readText(".pulse-commerce-cta"),
    seller: readText(".pulse-commerce-seller"),
    title: readText(".pulse-commerce-title"),
    price: readText(".pulse-commerce-price"),
    aria: main ? main.getAttribute("aria-label") : null,
  };
}

const out = {};
for (const [name, scenario, language, overlay, surface, markup] of cases) {
  out[name] = load(scenario, language, overlay, surface, markup);
}
process.stdout.write(JSON.stringify(out));
