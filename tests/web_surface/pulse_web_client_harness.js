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
 * One client backs every page built on `pulse_web_section_shell` — the Private
 * Office, orders, Pages — so a regression here is a regression on all of them
 * at once. That is why the checks below mix subsystems: they are not about the
 * Office or about orders, they are about the client.
 *
 * Usage: node pulse_web_client_harness.js <path-to-extracted-client.js>
 * Exits non-zero if any expectation fails.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const CORE = fs.readFileSync(process.argv[2], "utf8");

// Let the stubbed promise chain drain. Three turns covers fetch -> json ->
// render; the client never chains deeper than that.
function drain() {
  return new Promise((resolve) => {
    setImmediate(() => setImmediate(() => setImmediate(resolve)));
  });
}

/** Render the client once with `cfg`, answering every fetch via `responder`.
 *
 * Most pages render once and are done, so most checks only need the returned
 * html. A search page is not done after one render — it has to be typed into
 * and clicked, and what it *sends* matters as much as what it draws. The
 * optional `trace` object is filled in with that: every request the client
 * made, where it navigated, and hooks to submit the form or choose a result.
 */
function run(cfg, responder, trace) {
  let html = "";
  const t = trace || {};
  t.calls = [];
  const held = [];
  const location = { pathname: "/pulse/private-office", href: "" };
  t.location = location;
  t.html = () => html;

  // One stub node per id, reused across renders. The search box's value has to
  // survive a re-render or the client could never read back what was typed.
  const stubs = {};
  function stubNode(id) {
    if (!stubs[id]) {
      stubs[id] = {
        id: id, value: "", textContent: "", checked: false, handlers: {},
        // Last handler wins. In a browser each render replaces the element, so
        // an accumulating list here would let the harness fire a handler that
        // belongs to markup no longer on the page.
        addEventListener(type, fn) { this.handlers[type] = fn; },
        // A real input can take focus and move its caret, and the client does
        // both after every search render to keep the caret from being thrown
        // out of the box. Without these no-ops it throws here on a path that
        // works perfectly well in a browser.
        focus() {}, setSelectionRange() {}
      };
    }
    return stubs[id];
  }

  let rootClick = null;
  const root = {
    set innerHTML(value) { html = value; },
    get innerHTML() { return html; },
    addEventListener(type, fn) { if (type === "click") { rootClick = fn; } },
    // Tab buttons are wired by query after each render. Returning the real
    // count matters: a stub that always found nothing would make a broken
    // selector look fine here and dead in a browser.
    querySelectorAll(selector) {
      const attr = /\[([-a-z]+)\]/.exec(selector);
      if (!attr) { return []; }
      const matches = html.match(new RegExp(attr[1] + "='([^']*)'", "g")) || [];
      return matches.map((raw) => ({
        getAttribute: () => /='([^']*)'/.exec(raw)[1],
        addEventListener() {}
      }));
    }
  };
  const sandbox = {
    document: {
      // Nothing holds focus unless a test arranges it. The client reads this to
      // decide whether to *restore* the caret, and has to cope with either.
      activeElement: null,
      getElementById(id) {
        if (id === "office-root") { return root; }
        // Nodes written into root by innerHTML have to be findable, or the
        // unlock form wiring throws here while working fine in a browser.
        if (html.indexOf("id='" + id + "'") >= 0) { return stubNode(id); }
        return null;
      }
    },
    window: {
      sessionStorage: { getItem: () => "", setItem() {}, removeItem() {} },
      location: location
    },
    fetch: (path, options) => {
      const opts = options || {};
      t.calls.push({
        path: path,
        method: opts.method || "GET",
        body: opts.body ? JSON.parse(opts.body) : null
      });
      const answer = responder(path, opts) || {};
      const settle = () => ({
        status: answer.status,
        json: () => Promise.resolve(answer.body)
      });
      // `hold: true` leaves the request in flight until a test releases it, so
      // the order two answers come back in can be chosen. Without that there is
      // no way to prove a slow earlier search cannot overwrite a newer one.
      if (answer.hold) {
        return new Promise((resolve) => { held.push(() => resolve(settle())); });
      }
      return Promise.resolve(settle());
    },
    encodeURIComponent
  };

  /** Answer the n-th held request, oldest first. */
  t.release = (index) => { held[index](); return drain().then(() => html); };
  /** Type `value` into the search box and submit the form, as a member would. */
  t.submit = (value) => {
    const box = stubNode("search-q");
    if (value !== undefined) { box.value = value; }
    const form = stubs["search-form"];
    const handler = form && form.handlers.submit;
    if (!handler) { throw new Error("the search form registered no submit handler"); }
    handler({ preventDefault() {} });
    return drain().then(() => html);
  };
  /** Click the choose button on the n-th result row. */
  t.choose = (index) => {
    if (!rootClick) { throw new Error("the client wired no click delegate"); }
    rootClick({
      target: {
        closest: (selector) => selector === "[data-search-pick]"
          ? { getAttribute: () => String(index) } : null
      }
    });
    return drain().then(() => html);
  };

  vm.createContext(sandbox);
  vm.runInContext(CORE.replace("%%CONFIG%%", JSON.stringify(cfg)), sandbox);
  return drain().then(() => html);
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

  // A nested object used to vanish: the row renderer only emitted scalars, so
  // an order's seller and listing were dropped without a trace. Silence is the
  // one thing this client is not allowed to do with data it was given.
  html = await run(SECTION, () => ({
    status: 200,
    body: { ok: true, facts: [{ id: 9, title: "Deed", holder: { name: "Registry", ref: "R-1" } }] }
  }));
  check("a nested object is rendered, not silently dropped",
    /Registry/.test(html) && /R-1/.test(html), html);

  /* --- rows shaped like the native card ---------------------------------- */

  const ORDERS = {
    mode: "section", title: "Your orders", blurb: "b",
    api: "/api/pulse/orders", collection: "orders",
    tabs_field: "status_group",
    tabs: [
      { key: "all", label: "All" },
      { key: "open", label: "Processing", groups: ["pending", "paid"] },
      { key: "shipped", label: "Shipped", groups: ["shipped"] }
    ],
    row: { title: "title", status: "status_group", href: "detail_url",
           money: ["amount_cents", "currency"], meta: ["seller.store_name"] },
    empty: "You have not bought anything on PulseSoc yet."
  };
  const ORDER_ROWS = [
    { id: 1, title: "Desk lamp", status_group: "shipped", detail_url: "/pulse/orders/1",
      amount_cents: 4250, currency: "usd", seller: { store_name: "Lumen Co" } },
    { id: 2, title: "Notebook", status_group: "pending", detail_url: "/pulse/orders/2",
      amount_cents: 900, currency: "usd", seller: { store_name: "Paper Ltd" } }
  ];

  html = await run(ORDERS, () => ({ status: 200, body: { ok: true, orders: ORDER_ROWS } }));
  check("money is formatted from the cents the server sent",
    /USD 42\.50/.test(html) && /USD 9\.00/.test(html), html);
  check("a dotted meta path reaches into a nested object",
    /Lumen Co/.test(html), html);
  check("the row links to the detail URL the server minted",
    /href='\/pulse\/orders\/1'/.test(html), html);
  // Tab counts are the honest reason to compute them here: a tab that lies
  // about how many orders it holds is worse than no tab at all.
  check("tabs count rows without asking the server again",
    /All \(2\)/.test(html) && /Processing \(1\)/.test(html) && /Shipped \(1\)/.test(html), html);

  html = await run(ORDERS, () => ({ status: 200, body: { ok: true, orders: [] } }));
  check("an empty order list says so in its own words",
    /You have not bought anything on PulseSoc yet/.test(html), html);

  html = await run(ORDERS, () => ({ status: 503, body: { ok: false, message: "x" } }));
  check("a failed order fetch is never an empty order list",
    !/You have not bought anything/.test(html), html);

  // A row whose href field came back empty must stay unlinked. Composing
  // "/pulse/pages/" + "" would send a member to a URL that cannot resolve.
  html = await run(
    { mode: "section", title: "Your Pages", blurb: "b", api: "/api/pages",
      collection: "pages",
      row: { title: "name", href: "handle", href_prefix: "/pulse/pages/" } },
    () => ({ status: 200, body: { ok: true, pages: [{ id: 1, name: "Nameless", handle: "" }] } }));
  check("a row with no handle is not linked to a composed 404",
    /Nameless/.test(html) && !/href='\/pulse\/pages\/'/.test(html), html);

  /* --- an aggregated page -------------------------------------------------
     The Activity inbox is built from three feeds at once, which gives it a
     failure mode the single-source pages do not have: one feed falls over and
     the list still looks complete. `sources_field` names where the server says
     which of them answered, and nothing below may claim emptiness while any of
     them is down.                                                           */

  const FEED = {
    mode: "section", title: "Activity", blurb: "b", api: "/api/activity",
    collection: "items", sources_field: "sources",
    tabs: [{ key: "all", label: "All" },
           { key: "messages", label: "Messages", groups: ["messages"] },
           { key: "calls", label: "Calls", groups: ["calls"] }],
    tabs_field: "category",
    row: { title: "title", status: "category", href: "web_url" },
    empty: "Nothing is waiting for you."
  };

  const ALL_OK = { notifications: "ok", messages: "ok", calls: "ok" };

  html = await run(FEED, () => ({
    status: 200, body: { ok: true, sources: ALL_OK, items: [] }
  }));
  check("an empty inbox may say it is empty when every source answered",
    /Nothing is waiting for you/.test(html) && !/could not read/.test(html), html);

  html = await run(FEED, () => ({
    status: 200,
    body: { ok: true, sources: { notifications: "ok", messages: "failed", calls: "ok" },
            items: [] }
  }));
  check("a dead source is never rendered as an empty inbox",
    !/Nothing is waiting for you/.test(html) &&
    /at least one source did not answer/i.test(html), html);
  check("the dead source is named, not just hinted at",
    /could not read messages/i.test(html), html);

  html = await run(FEED, () => ({
    status: 200,
    body: { ok: true, sources: { notifications: "ok", messages: "ok", calls: "failed" },
            items: [{ id: "n-1", title: "New reaction", category: "social",
                      web_url: "/pulse/post/42" }] }
  }));
  check("a list that is missing a source still says so",
    /New reaction/.test(html) && /could not read calls/i.test(html), html);

  html = await run(FEED, () => ({
    status: 200,
    body: { ok: true, sources: ALL_OK, items: [
      { id: "c-1", title: "Voice call in progress", category: "calls", web_url: "" },
      { id: "m-1", title: "New message from Ada", category: "messages",
        web_url: "/pulse/messages/7" }
    ] }
  }));
  check("a row the web cannot serve is shown but not linked",
    /Voice call in progress/.test(html) && !/href='\/pulse\/calls/.test(html), html);
  check("a row the web can serve is linked",
    /href='\/pulse\/messages\/7'/.test(html), html);
  check("categories become tabs that count without asking again",
    /All \(2\)/.test(html) && /Messages \(1\)/.test(html) && /Calls \(1\)/.test(html), html);

  /* --- one record -------------------------------------------------------- */

  const RECORD = { mode: "record", title: "Order", api: "/api/pulse/orders/1",
                   record: "order", row: { status: "status_group",
                                           money: ["amount_cents", "currency"] },
                   back: ["/pulse/orders", "All your orders"] };

  html = await run(RECORD, () => ({
    status: 200,
    body: { ok: true, order: { id: 1, title: "Desk lamp", status_group: "shipped",
                               amount_cents: 4250, currency: "usd",
                               seller: { store_name: "Lumen Co" } } }
  }));
  check("a record shows its own fields and its nested ones",
    /Desk lamp/.test(html) && /Lumen Co/.test(html) && /USD 42\.50/.test(html), html);

  html = await run(RECORD, () => ({ status: 200, body: { ok: true } }));
  check("a 200 with no record is a fault, not a blank order",
    /cannot read the answer/.test(html), html);

  html = await run(RECORD, () => ({
    status: 404, body: { ok: false, message: "Order not found." }
  }));
  check("a missing record reads as missing, not as broken",
    /could not find that record|Order not found/.test(html) &&
    !/could not load/.test(html), html);

  /* --- a create form ----------------------------------------------------- */

  const FORM = {
    mode: "form", title: "Create a Page", blurb: "b", api: "/api/pages",
    record: "page", created_prefix: "/pulse/pages/", created_key: "handle",
    fields: [
      { name: "name", label: "Page name", required: true },
      { name: "page_type", label: "Type", type: "select",
        options: [["ARTIST", "Artist"], ["BUSINESS", "Business"]] },
      { name: "confirm_owner", label: "I will be the owner", type: "checkbox" }
    ]
  };

  html = await run(FORM, () => ({ status: 200, body: { ok: true } }));
  check("a form renders every field it will send",
    /id='f-name'/.test(html) && /id='f-page_type'/.test(html) &&
    /id='f-confirm_owner'/.test(html), html);
  check("select options come from the server's own vocabulary",
    /value='ARTIST'/.test(html) && /value='BUSINESS'/.test(html), html);
  check("a form never renders an empty list instead of itself",
    !/Nothing here yet/.test(html), html);

  /* --- searching for someone ----------------------------------------------
     A search page has a state the list pages do not: nothing has been asked
     yet. "not asked", "asked and nobody matched", and "could not ask" collapse
     into the same blank screen unless the client keeps them apart, and exactly
     one of the three is allowed to say nobody was found.

     The other half of this is what the page *sends*. Choosing a result opens a
     conversation that did not exist a moment ago, which is a write, so it must
     happen on a click and never as a consequence of the page loading.        */

  const SEARCH = {
    mode: "search", title: "Start a chat", blurb: "b",
    api: "/api/pulse/communications/v2/people/search",
    search_param: "q", collection: "people", initial_query: "",
    // The server's own threshold, not a number the client picked.
    min_query: 2, search_prompt: "Type a name or username to begin.",
    row: { title: "display_name", meta: ["username"] },
    action: { api: "/api/pulse/communications/v2/direct/open",
              from: "user_id", field: "target_user_id",
              goto_key: "conversation_id", goto_prefix: "/pulse/messages/",
              label: "Message", busy_label: "Opening…" }
  };
  // A page reached by a link that already carries the query, so the search runs
  // on arrival and each failure mode below can be tested in one render.
  const SEARCHED = Object.assign({}, SEARCH, { initial_query: "ada" });
  const ADA = [{ user_id: 7, display_name: "Ada Lovelace", username: "ada" }];
  const NOBODY = "Nobody matched";

  let trace = {};
  html = await run(SEARCH, () => ({ status: 200, body: { ok: true, people: [] } }), trace);
  check("an unasked search invites a query instead of claiming nobody exists",
    /Type a name or username to begin/.test(html) && !new RegExp(NOBODY).test(html), html);
  check("an unasked search asks the server nothing",
    trace.calls.length === 0, JSON.stringify(trace.calls));

  html = await trace.submit("a");
  check("a query shorter than the server will run says keep typing, not 'nobody'",
    /Keep typing/.test(html) && /from 2 characters/.test(html) &&
    !new RegExp(NOBODY).test(html), html);
  check("a query too short to run is never sent",
    trace.calls.length === 0, JSON.stringify(trace.calls));

  html = await trace.submit("ada");
  check("the query reaches the server under the parameter the page declared",
    trace.calls.length === 1 && /[?&]q=ada$/.test(trace.calls[0].path),
    JSON.stringify(trace.calls));
  check("a search that ran and matched nobody says so in those terms",
    new RegExp(NOBODY).test(html), html);

  html = await run(SEARCHED, () => ({ status: 0, body: null }));
  check("a transport failure is never an answer about who exists",
    !new RegExp(NOBODY).test(html) && /could not reach PulseSoc/.test(html), html);

  html = await run(SEARCHED, () => ({ status: 500, body: { ok: false, message: "boom" } }));
  check("a server fault rules nobody out",
    !new RegExp(NOBODY).test(html) && /boom/.test(html), html);

  html = await run(SEARCHED, () => ({ status: 200, body: { ok: true } }));
  check("a 200 that forgot its results is a fault, not an absence of people",
    !new RegExp(NOBODY).test(html) && /Nobody has been ruled out/.test(html), html);

  html = await run(SEARCHED, () => ({ status: 401, body: { ok: false } }));
  check("a signed-out visitor is asked to sign in, not told nobody exists",
    /Sign in/.test(html) && !new RegExp(NOBODY).test(html), html);

  /* --- choosing a result is a write --------------------------------------- */

  trace = {};
  html = await run(SEARCHED, (path) => path.indexOf("/direct/open") >= 0
    ? { status: 200, body: { ok: true, conversation_id: "c-9" } }
    : { status: 200, body: { ok: true, people: ADA } }, trace);
  check("a match renders with its own fields and a way to choose it",
    /Ada Lovelace/.test(html) && /ada/.test(html) &&
    /data-search-pick='0'/.test(html) && !new RegExp(NOBODY).test(html), html);
  check("the query stays in the box after results arrive",
    /value='ada'/.test(html), html);
  check("loading a search page never opens a conversation",
    trace.calls.every((call) => call.method === "GET") && trace.location.href === "",
    JSON.stringify(trace.calls));

  html = await trace.choose(0);
  const opened = trace.calls.filter((call) => call.method === "POST");
  check("choosing someone asks the server to open the chat, keyed by that person",
    opened.length === 1 &&
    opened[0].path === "/api/pulse/communications/v2/direct/open" &&
    opened[0].body.target_user_id === 7, JSON.stringify(trace.calls));
  check("the browser is sent to the conversation the server minted",
    trace.location.href === "/pulse/messages/c-9", trace.location.href);

  trace = {};
  await run(SEARCHED, (path) => path.indexOf("/direct/open") >= 0
    ? { status: 503, body: { ok: false } }
    : { status: 200, body: { ok: true, people: ADA } }, trace);
  html = await trace.choose(0);
  check("a chat that could not be opened says nothing was started, and stays put",
    /Nothing was started/.test(html) && /Ada Lovelace/.test(html) &&
    trace.location.href === "", html);

  /* --- a slow answer must not outrank a newer one -------------------------- */

  trace = {};
  await run(SEARCH, (path) => ({
    status: 200, hold: true,
    body: { ok: true, people: /q=adam$/.test(path)
      ? [{ user_id: 2, display_name: "Adam Smith", username: "adam" }] : ADA }
  }), trace);
  trace.submit("ada");
  trace.submit("adam");
  html = await trace.release(1);
  check("the newest search is the one that renders",
    /Adam Smith/.test(html), html);
  html = await trace.release(0);
  check("a slower earlier search cannot overwrite the newer one's results",
    /Adam Smith/.test(html) && !/Ada Lovelace/.test(html), html);

  console.log(failures ? "\n" + failures + " FAILED" : "\nall green");
  process.exit(failures ? 1 : 0);
})();
