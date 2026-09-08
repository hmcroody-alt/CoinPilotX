/**
 * Runs the shared PulseSoc web client (`bot.PULSE_WEB_SECTION_JS`) against a
 * stub DOM and stub responses.
 *
 * The client's whole job is to turn one API answer into one honest screen, and
 * the ways it can lie are all invisible to a Python test: rendering "you have
 * nothing" when the fetch failed, rendering "you do not have this" when the
 * tier resolver merely fell over, or turning a capability with no web page
 * into a link that 404s. Those are behaviours of the JavaScript, so they are
 * tested in JavaScript.
 *
 * Usage: node private_office_web_harness.js <path-to-extracted-client.js>
 * Exits non-zero on the first failing expectation.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const CORE = fs.readFileSync(process.argv[2], "utf8");

/** Render the client once with `cfg`, answering every fetch via `responder`. */
function run(cfg, responder) {
  let html = "";
  const root = {
    set innerHTML(value) { html = value; },
    get innerHTML() { return html; },
    addEventListener() {}
  };
  const sandbox = {
    document: {
      getElementById(id) {
        if (id === "office-root") { return root; }
        // Nodes written into root by innerHTML have to be findable, or the
        // unlock form wiring throws here while working fine in a browser.
        if (html.indexOf("id='" + id + "'") >= 0) {
          return { addEventListener() {}, value: "", textContent: "" };
        }
        return null;
      }
    },
    window: {
      sessionStorage: { getItem: () => "", setItem() {}, removeItem() {} },
      location: { pathname: "/pulse/private-office" }
    },
    fetch: (path) => {
      const answer = responder(path);
      return Promise.resolve({
        status: answer.status,
        json: () => Promise.resolve(answer.body)
      });
    },
    encodeURIComponent
  };
  vm.createContext(sandbox);
  vm.runInContext(CORE.replace("%%CONFIG%%", JSON.stringify(cfg)), sandbox);
  // Let the stubbed promise chain drain. Three turns covers fetch -> json ->
  // render; the client never chains deeper than that.
  return new Promise((resolve) => {
    setImmediate(() => setImmediate(() => setImmediate(() => resolve(html))));
  });
}

const CHILDREN = [
  ["private_facts", "Facts", "/pulse/private-office/facts"],
  ["private_shield.breach_monitoring", "Breach Monitoring", null],
  ["private_meetings", "Meetings", null],
  ["human_concierge", "Concierge", "/pulse/private-office/concierge"]
];

const HUB = { mode: "hub", title: "Private Office", children: CHILDREN, lockable: true };
const SECTION = {
  mode: "section", title: "Facts", blurb: "b", lockable: true,
  back: ["/pulse/private-office", "Back to the Office"],
  api: "/api/private-office/facts", collection: "facts"
};

let failures = 0;
function check(name, condition, html) {
  console.log((condition ? "PASS " : "FAIL ") + name);
  if (!condition) {
    failures += 1;
    if (html) { console.log("      " + html.slice(0, 500)); }
  }
}

(async () => {
  /* --- a degraded resolve is not a denial -------------------------------- */

  // The live endpoint answers 200 with ok:false for this case, so a generic
  // "ok === false -> error" rule would silently eat the distinction.
  let html = await run(HUB, () => ({
    status: 200,
    body: { ok: false, private_office: { state: "ENTRY_UNKNOWN", available: [], unavailable: [] } }
  }));
  check("ENTRY_UNKNOWN never reads as 'you do not have this'",
    /could not confirm/.test(html) && !/Included with/.test(html), html);
  check("ENTRY_UNKNOWN offers a retry", /data-office-retry/.test(html), html);

  /* --- the hub tells the truth about each child -------------------------- */

  html = await run(HUB, () => ({
    status: 200,
    body: {
      ok: true, domains: [],
      private_office: {
        state: "ENTRY_AVAILABLE", upgrade_tier: null,
        available: [{ feature_id: "private_facts", opens: true, reason: "AVAILABLE" }],
        unavailable: [
          { feature_id: "private_shield.breach_monitoring", opens: false, reason: "PROVIDER_REQUIRED", minimum_tier: "PRIVATE" },
          { feature_id: "private_meetings", opens: false, reason: "TEMPORARILY_DISABLED", minimum_tier: "PRIVATE" },
          { feature_id: "human_concierge", opens: false, reason: "UPGRADE_REQUIRED", minimum_tier: "PRIVATE_OFFICE" }
        ]
      }
    }
  }));
  check("an open child with a web page is a link",
    /href='\/pulse\/private-office\/facts'>Facts/.test(html), html);
  check("PROVIDER_REQUIRED does not imply we are already watching",
    /Nothing is being monitored/.test(html), html);
  check("a capability with no web page never becomes a link",
    !/href='[^']*'>Breach Monitoring/.test(html), html);
  check("meetings render as disabled and never link",
    /Meetings/.test(html) && /Temporarily switched off/.test(html) &&
    !/href='[^']*'>Meetings/.test(html), html);
  check("the upgrade line names the tier in title case",
    /Included with Private Office\./.test(html) && !/PRIVATE OFFICE/.test(html), html);

  /* --- error and empty must never co-render ------------------------------ */

  html = await run(SECTION, () => ({
    status: 503,
    body: { ok: false, state: "unavailable", message: "We could not load your information just now." }
  }));
  check("a 503 shows the fault, not an empty list",
    /could not load/.test(html) && !/Nothing here yet/.test(html), html);

  html = await run(SECTION, () => ({ status: 0, body: null }));
  check("a transport failure is not emptiness",
    !/Nothing here yet/.test(html), html);

  html = await run(SECTION, () => ({ status: 200, body: { ok: true, facts: [] } }));
  check("a genuinely empty store says so", /Nothing here yet/.test(html), html);

  html = await run(SECTION, () => ({ status: 200, body: { ok: true } }));
  check("a response missing its collection is a fault, not emptiness",
    /cannot read the answer/.test(html) && !/Nothing here yet/.test(html), html);

  /* --- the second lock --------------------------------------------------- */

  html = await run(SECTION, () => ({
    status: 200, body: { ok: true, locked: true, setup_required: false }
  }));
  check("a locked Office renders the unlock door", /passcode/i.test(html), html);

  /* --- real rows --------------------------------------------------------- */

  html = await run(SECTION, () => ({
    status: 200, body: { ok: true, facts: [{ id: 4, title: "Passport", domain: "identity" }] }
  }));
  check("rows render with their own fields",
    /Passport/.test(html) && /Domain/.test(html), html);

  console.log(failures ? "\n" + failures + " FAILED" : "\nall green");
  process.exit(failures ? 1 : 0);
})();
