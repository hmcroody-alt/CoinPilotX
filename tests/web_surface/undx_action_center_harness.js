/**
 * Runs the UNDX Action Center client (`bot.UNDX_ACTION_CENTER_PAGE_JS`) against
 * a stub DOM and stub responses.
 *
 * The native screen this page mirrors has a bug that no Python assertion can
 * see. `UndxActionCenterScreen.tsx` renders its "Action Center unavailable"
 * panel *in addition to* the five sections rather than instead of them, so a
 * failed first load puts "UNDX has nothing waiting for you." on screen next to
 * the error -- a statement about your governance queue that nothing has
 * established, sitting directly below the admission that nothing could be
 * read. The web page must not repeat that, and "must not" is a behaviour of
 * the JavaScript across a fetch boundary, so it is tested in JavaScript.
 *
 * The stub DOM is deliberately small and specific to this client rather than
 * general: it implements exactly the surface the client touches
 * (querySelector/querySelectorAll, closest, dataset, classList, hidden,
 * innerHTML, textContent, value, disabled) and throws on anything else, so a
 * client that starts using a new DOM API fails loudly here instead of silently
 * exercising a stub that says yes to everything.
 *
 * Usage: node undx_action_center_harness.js <path-to-extracted-client.js>
 * Exits non-zero if any expectation fails.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const CLIENT = fs.readFileSync(process.argv[2], "utf8");

const failures = [];
function check(name, condition, detail) {
  if (!condition) failures.push(name + (detail ? " -- " + detail : ""));
}

/** Let the stubbed promise chain drain. */
function drain() {
  return new Promise((resolve) => {
    let turns = 12;
    const tick = () => (turns-- > 0 ? setImmediate(tick) : resolve());
    setImmediate(tick);
  });
}

// --- a very small DOM -------------------------------------------------------

let nodeSeq = 0;

function parseAttrs(tag) {
  const attrs = {};
  const re = /([a-zA-Z-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g;
  let match;
  let first = true;
  while ((match = re.exec(tag))) {
    if (first) { first = false; continue; } // the tag name
    attrs[match[1]] = match[2] !== undefined ? match[2] : (match[3] !== undefined ? match[3] : "");
  }
  return attrs;
}

function makeNode(tag, attrs) {
  const node = {
    id: ++nodeSeq,
    tag: tag,
    attrs: attrs || {},
    children: [],
    parent: null,
    handlers: {},
    _html: "",
    _text: "",
    value: "",
    // Reflected from the markup, so the harness starts in the state a browser
    // would rather than in a friendlier one.
    disabled: Object.prototype.hasOwnProperty.call(attrs || {}, "disabled"),
    hidden: Object.prototype.hasOwnProperty.call(attrs || {}, "hidden"),
    dataset: {},
    classes: new Set(String((attrs || {}).class || "").split(/\s+/).filter(Boolean)),
  };
  Object.keys(node.attrs).forEach((name) => {
    if (name.indexOf("data-") !== 0) return;
    const key = name.slice(5).replace(/-([a-z])/g, (_m, ch) => ch.toUpperCase());
    node.dataset[key] = node.attrs[name];
  });
  node.classList = {
    toggle(name, on) { if (on) node.classes.add(name); else node.classes.delete(name); },
    contains(name) { return node.classes.has(name); },
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(html) { node._html = String(html); node.children = parseFragment(String(html), node); },
  });
  Object.defineProperty(node, "textContent", {
    get() { return node._text; },
    set(text) { node._text = String(text); },
  });
  node.addEventListener = function (type, fn) { node.handlers[type] = fn; };
  node.querySelector = function (selector) { return query(node, selector)[0] || null; };
  node.querySelectorAll = function (selector) { return query(node, selector); };
  node.closest = function (selector) {
    let cursor = node;
    while (cursor) {
      if (matches(cursor, selector)) return cursor;
      cursor = cursor.parent;
    }
    return null;
  };
  return node;
}

const VOID_TAGS = new Set(["input", "img", "br", "hr", "meta", "link"]);

/** Parse a fragment into the node tree. Small on purpose: it handles nesting,
 *  void elements and text, which is everything the page's markup and the rows
 *  built by `rowHtml` contain, and nothing else. */
function parseFragment(html, parent) {
  const roots = [];
  const stack = [];
  const re = /<(\/?)([a-zA-Z]+)([^>]*?)(\/?)>|([^<]+)/g;
  let match;
  const push = (node) => {
    const host = stack[stack.length - 1];
    node.parent = host || parent;
    if (host) host.children.push(node); else roots.push(node);
  };
  while ((match = re.exec(html))) {
    if (match[5] !== undefined) {
      const host = stack[stack.length - 1];
      if (host) host._text += match[5];
      continue;
    }
    const [, closing, tag, attrs, selfClosed] = match;
    if (closing) {
      const node = stack.pop();
      if (node) node._html = node._html || "";
      continue;
    }
    const node = makeNode(tag, parseAttrs(tag + attrs));
    push(node);
    if (!VOID_TAGS.has(tag.toLowerCase()) && !selfClosed) stack.push(node);
  }
  return roots;
}

function matches(node, selector) {
  const attr = selector.match(/^\[([a-z-]+)(?:="([^"]*)")?\]$/);
  if (attr) {
    const present = Object.prototype.hasOwnProperty.call(node.attrs, attr[1]);
    return attr[2] === undefined ? present : node.attrs[attr[1]] === attr[2];
  }
  if (selector.charAt(0) === ".") return node.classes.has(selector.slice(1));
  throw new Error("stub DOM does not implement the selector " + selector);
}

function query(root, selector) {
  const out = [];
  (function walk(node) {
    node.children.forEach((child) => {
      if (matches(child, selector)) out.push(child);
      walk(child);
    });
  })(root);
  return out;
}

// --- the page's own markup --------------------------------------------------

/** The parts of the server-rendered page the client reads or writes.
 *  Kept as literal markup so a rename in `bot.py` breaks this file loudly
 *  rather than quietly testing a shape the page no longer has. */
const PAGE_HTML = `
<div data-undx-root>
  <div data-undx-signal hidden><strong data-undx-pending>0</strong></div>
  <div data-undx-loading><p>loading</p></div>
  <div data-undx-error hidden><p data-undx-error-message></p><button data-undx-retry>Retry</button></div>
  <div data-undx-body hidden>
    <div><strong data-undx-metric="decisions">0</strong></div>
    <div><strong data-undx-metric="requests">0</strong></div>
    <div><strong data-undx-metric="tools">0</strong></div>
    <div><strong data-undx-metric="permissions">0</strong></div>
    <div class="undx-rows" data-undx-section="pending" data-undx-empty="UNDX has nothing waiting for you."></div>
    <div class="undx-rows" data-undx-section="decisions" data-undx-empty="No decisions returned yet."></div>
    <div class="undx-rows" data-undx-section="tools" data-undx-empty="No registered Marketplace tools returned."></div>
    <div class="undx-rows" data-undx-section="permissions" data-undx-empty="No actor permissions returned."></div>
    <div class="undx-rows" data-undx-section="receipts" data-undx-empty="No receipts or active emergency stops returned."></div>
  </div>
  <div>
    <p data-undx-workflow-message hidden></p>
    <input data-undx-field="title"><textarea data-undx-field="description"></textarea>
    <input data-undx-field="price_cents"><input data-undx-field="inventory_qty">
    <input data-undx-field="product_id"><input data-undx-field="request_id">
    <div data-undx-plan hidden>
      <span data-undx-plan-risk></span><p data-undx-plan-summary></p><small data-undx-plan-expiry></small>
    </div>
    <button data-undx-action="draft">Create governed draft</button>
    <button data-undx-action="plan">Plan publish</button>
    <button data-undx-action="execute" disabled>Review and publish</button>
  </div>
</div>`;

/** Boot the client once against `responder`, and hand back handles to poke it. */
function run(responder) {
  const document = makeNode("#document", {});
  document.children = parseFragment(PAGE_HTML, document);
  const calls = [];
  const confirms = [];
  let confirmAnswer = true;

  const sandbox = {
    document: document,
    console: console,
    Promise: Promise,
    Number: Number,
    String: String,
    Boolean: Boolean,
    Array: Array,
    Object: Object,
    Date: Date,
    Error: Error,
    setTimeout: setTimeout,
    JSON: JSON,
    window: {
      confirm(message) { confirms.push(message); return confirmAnswer; },
    },
    async fetch(url, options) {
      calls.push({ url: url, options: options || {} });
      return responder(url, options || {});
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext('const UNDX_CSRF = "harness-csrf";\n' + CLIENT, sandbox);

  const root = document.querySelector("[data-undx-root]");
  return {
    root: root,
    calls: calls,
    confirms: confirms,
    setConfirmAnswer(answer) { confirmAnswer = answer; },
    node(selector) { return root.querySelector(selector); },
    visible(selector) {
      const node = root.querySelector(selector);
      return Boolean(node) && node.hidden === false;
    },
    section(key) { return root.querySelector('[data-undx-section="' + key + '"]'); },
    metric(key) { return root.querySelector('[data-undx-metric="' + key + '"]').textContent; },
    fire(selector, type) {
      const target = root.querySelector(selector);
      const handler = root.handlers[type];
      if (!handler) throw new Error("no delegated " + type + " handler on the root");
      handler({ target: target });
    },
  };
}

function ok(body, status) {
  return {
    ok: (status || 200) < 400,
    status: status || 200,
    json: async () => body,
  };
}

function boom(status, body) {
  return { ok: false, status: status, json: async () => body || { ok: false, message: "Server said no." } };
}

const EMPTY_SNAPSHOT = { ok: true, result: { org_id: "coinplotxai", requests: [], decisions: [], receipts: [], active_stops: [], permissions: [], confirmations: [] } };
const EMPTY_TOOLS = { ok: true, result: { tools: [] } };
const EMPTY_PERMISSIONS = { ok: true, result: { permissions: [] } };

function route(url, answers) {
  if (url.indexOf("/action-center") >= 0) return answers.center;
  if (url.indexOf("/tools") >= 0) return answers.tools;
  if (url.indexOf("/permissions") >= 0) return answers.permissions;
  throw new Error("the client called an unexpected URL: " + url);
}

// --- expectations -----------------------------------------------------------

async function main() {
  // 1. The reason this file exists. A failed read must not also assert that
  //    your governance queue is empty.
  {
    const page = run((url) => route(url, {
      center: boom(503, { ok: false, message: "The governance store is unreachable." }),
      tools: ok(EMPTY_TOOLS),
      permissions: ok(EMPTY_PERMISSIONS),
    }));
    await drain();
    check("failed load shows the error panel", page.visible("[data-undx-error]"));
    check("failed load hides the sections", !page.visible("[data-undx-body]"),
          "the five empty strings were on screen next to the error");
    check("failed load hides the pending badge", !page.visible("[data-undx-signal]"),
          "a pending count of 0 is a claim, and nothing established it");
    check("failed load stops showing the loading panel", !page.visible("[data-undx-loading]"));
    check("failed load surfaces the server's reason",
          page.node("[data-undx-error-message]").textContent.indexOf("governance store") >= 0,
          page.node("[data-undx-error-message]").textContent);
    check("failed load renders no empty copy",
          page.section("pending").innerHTML === "",
          page.section("pending").innerHTML);
  }

  // 2. The other half of the same claim: a successful *empty* read is allowed
  //    to say so, and must not look like an error.
  {
    const page = run((url) => route(url, {
      center: ok(EMPTY_SNAPSHOT), tools: ok(EMPTY_TOOLS), permissions: ok(EMPTY_PERMISSIONS),
    }));
    await drain();
    check("empty load hides the error panel", !page.visible("[data-undx-error]"));
    check("empty load shows the sections", page.visible("[data-undx-body]"));
    check("empty load shows the pending badge", page.visible("[data-undx-signal]"));
    check("empty load renders the section's own empty copy",
          page.section("pending").innerHTML.indexOf("UNDX has nothing waiting for you.") >= 0,
          page.section("pending").innerHTML);
    check("empty load counts zero pending", page.node("[data-undx-pending]").textContent === "0");
  }

  // 3. Rows render, and the empty copy goes away when they do.
  {
    const snapshot = {
      ok: true,
      result: {
        requests: [{ action_type: "marketplace.publish", risk: "high", actor: "user:7", request_id: "req_1" }],
        decisions: [{ action_type: "marketplace.publish", effect: "require_approval", request_id: "req_1" }],
        receipts: [{ receipt_id: "rcp_1", status: "verified", canonical_ref: "product:12" }],
        active_stops: [{ reason: "Manual halt during incident", active: true, org_id: "coinplotxai" }],
        permissions: [],
      },
    };
    const page = run((url) => route(url, {
      center: ok(snapshot),
      tools: ok({ ok: true, result: { tools: [{ tool_name: "marketplace.publish", product_area: "marketplace" }] } }),
      permissions: ok({ ok: true, result: { permissions: [{ action_type: "marketplace.publish", effect: "allow", actor: "user:7" }] } }),
    }));
    await drain();
    check("rows replace the empty copy",
          page.section("pending").innerHTML.indexOf("UNDX has nothing waiting for you.") < 0,
          page.section("pending").innerHTML);
    check("the pending row names the action",
          page.section("pending").innerHTML.indexOf("marketplace.publish") >= 0);
    check("metrics count what was returned",
          page.metric("decisions") === "1" && page.metric("requests") === "1"
          && page.metric("tools") === "1" && page.metric("permissions") === "1",
          [page.metric("decisions"), page.metric("requests"), page.metric("tools"), page.metric("permissions")].join(","));

    // The native screen reads `emergency_stops`, which the backend has never
    // sent, so its safety rows silently vanish. This is that regression.
    check("an active emergency stop reaches the receipts section",
          page.section("receipts").innerHTML.indexOf("Manual halt during incident") >= 0,
          page.section("receipts").innerHTML);
    check("an active emergency stop is marked dangerous",
          page.section("receipts").innerHTML.indexOf("is-danger") >= 0,
          page.section("receipts").innerHTML);
    check("a verified receipt is not marked dangerous",
          page.section("receipts").children.filter((row) => row.classes.has("is-danger")).length === 1,
          String(page.section("receipts").children.length));
  }

  // 4. Permissions are additive context. Losing them must cost the permissions
  //    count and nothing else -- not the whole page.
  {
    const page = run((url) => route(url, {
      center: ok(EMPTY_SNAPSHOT), tools: ok(EMPTY_TOOLS), permissions: boom(500),
    }));
    await drain();
    check("a permissions failure does not take down the page",
          page.visible("[data-undx-body]") && !page.visible("[data-undx-error]"));
    check("a permissions failure reports zero permissions", page.metric("permissions") === "0");
  }

  // 5. Retry re-reads rather than sitting on the error.
  {
    let attempt = 0;
    const page = run((url) => {
      if (url.indexOf("/action-center") >= 0) {
        attempt += 1;
        return attempt === 1 ? boom(503) : ok(EMPTY_SNAPSHOT);
      }
      return url.indexOf("/tools") >= 0 ? ok(EMPTY_TOOLS) : ok(EMPTY_PERMISSIONS);
    });
    await drain();
    check("retry starts from the error state", page.visible("[data-undx-error]"));
    page.fire("[data-undx-retry]", "click");
    await drain();
    check("retry clears the error once the read succeeds", !page.visible("[data-undx-error]"));
    check("retry shows the sections", page.visible("[data-undx-body]"));
  }

  // 6. Publishing is irreversible enough to ask first, and the request must
  //    carry the CSRF header or every write 400s.
  {
    const page = run((url) => {
      if (url.indexOf("/publish/plan") >= 0) {
        return ok({ ok: true, result: {
          plan: { confirmation_token: "tok_9", risk: "high", summary: "Publish listing 12.", expires_at: "" },
          confirmation: { request_id: "req_9" },
        } });
      }
      if (url.indexOf("/publish/execute") >= 0) return ok({ ok: true, result: { published: true } });
      return route(url, { center: ok(EMPTY_SNAPSHOT), tools: ok(EMPTY_TOOLS), permissions: ok(EMPTY_PERMISSIONS) });
    });
    await drain();
    check("publish is disabled before a plan exists", page.node('[data-undx-action="execute"]').disabled);

    page.node('[data-undx-field="product_id"]').value = "12";
    page.fire('[data-undx-action="plan"]', "click");
    await drain();
    const planCall = page.calls.filter((call) => call.url.indexOf("/publish/plan") >= 0)[0];
    check("the plan request carries the CSRF header",
          Boolean(planCall) && planCall.options.headers["X-CSRF-Token"] === "harness-csrf",
          JSON.stringify(planCall && planCall.options.headers));
    check("the plan panel appears", page.visible("[data-undx-plan]"));
    check("the plan panel names the risk",
          page.node("[data-undx-plan-risk]").textContent === "high risk",
          page.node("[data-undx-plan-risk]").textContent);
    check("the request id comes back from the server",
          page.node('[data-undx-field="request_id"]').value === "req_9");
    check("publish is enabled once a plan exists", !page.node('[data-undx-action="execute"]').disabled);

    page.setConfirmAnswer(false);
    page.fire('[data-undx-action="execute"]', "click");
    await drain();
    check("publish asks before it publishes", page.confirms.length === 1, String(page.confirms.length));
    check("declining the confirm sends nothing",
          page.calls.filter((call) => call.url.indexOf("/publish/execute") >= 0).length === 0);

    page.setConfirmAnswer(true);
    page.fire('[data-undx-action="execute"]', "click");
    await drain();
    const executeCall = page.calls.filter((call) => call.url.indexOf("/publish/execute") >= 0)[0];
    check("accepting the confirm publishes", Boolean(executeCall));
    check("the execute request carries the confirmation token",
          Boolean(executeCall) && JSON.parse(executeCall.options.body).confirmation_token === "tok_9",
          executeCall && executeCall.options.body);
    check("publish is disabled again after the token is spent",
          page.node('[data-undx-action="execute"]').disabled);
  }

  // 7. A rejected write reports the server's reason and does not fake success.
  {
    const page = run((url) => {
      if (url.indexOf("/listings/draft") >= 0) return boom(409, { ok: false, error: "Listing title is required." });
      return route(url, { center: ok(EMPTY_SNAPSHOT), tools: ok(EMPTY_TOOLS), permissions: ok(EMPTY_PERMISSIONS) });
    });
    await drain();
    page.fire('[data-undx-action="draft"]', "click");
    await drain();
    const message = page.node("[data-undx-workflow-message]");
    check("a rejected draft says why", message.textContent === "Listing title is required.", message.textContent);
    check("a rejected draft is styled as a failure", message.classes.has("is-error"));
    check("a rejected draft is not styled as a success", !message.classes.has("is-ok"));
  }

  if (failures.length) {
    console.error("UNDX Action Center client failures:\n  " + failures.join("\n  "));
    process.exit(1);
  }
  console.log("undx action center client ok");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
