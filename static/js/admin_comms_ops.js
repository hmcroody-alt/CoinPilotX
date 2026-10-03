/* PulseSoc admin — Communications operations, live refresh.

   The server renders this page. This script only re-reads the same snapshot the
   server rendered from and writes scalar numbers back into the cells that carry
   a data-comms path. It never draws a row, because a second renderer in the
   browser is a second thing that can disagree with the database about what is
   happening. When the shape of the page changes -- a call starts or ends, an
   incident opens or clears, a provider changes state -- it says so and offers a
   reload rather than guessing at the markup.

   Numbers are written with textContent, never innerHTML: a failure reason is
   pipeline text and a session id is data, and neither is ever parsed as markup. */
(function () {
  "use strict";

  var ENDPOINT = "/admin/communications/snapshot.json";
  var INTERVAL_MS = 15000;          // the server caches for 10s; polling faster only burns requests
  var BACKOFF_MAX_MS = 120000;
  var timer = null;
  var failures = 0;
  var shape = null;

  function $$(sel) {
    return Array.prototype.slice.call(document.querySelectorAll(sel));
  }

  /* "calls.15m.started" -> snapshot.calls.windows["15m"].started
     "calls.active_count" -> snapshot.calls.active_count
     Window labels contain digits and the metric names do not, so the shape of
     the path is enough; there is no guessing about which section is meant. */
  function readPath(snapshot, path) {
    var parts = String(path || "").split(".");
    if (parts.length === 3) {
      var section = snapshot[parts[0]];
      if (!section || !section.windows) return undefined;
      var row = section.windows[parts[1]];
      return row ? row[parts[2]] : undefined;
    }
    if (parts.length === 2) {
      var sec = snapshot[parts[0]];
      return sec ? sec[parts[1]] : undefined;
    }
    return snapshot[parts[0]];
  }

  function formatCount(value) {
    if (value === null || value === undefined) return null;
    var n = Number(value);
    if (!isFinite(n)) return null;
    return n.toLocaleString();
  }

  function paintCounts(snapshot) {
    $$("[data-comms]").forEach(function (node) {
      var value = readPath(snapshot, node.getAttribute("data-comms"));
      var text = formatCount(value);
      if (text === null) {
        // Unreadable now. An em-dash, never a zero: the page must not assert
        // "no calls" on the strength of a query that failed.
        node.textContent = "—";
        node.setAttribute("title", "Unavailable — this metric could not be read");
        return;
      }
      node.removeAttribute("title");
      if (node.textContent !== text) node.textContent = text;
    });
  }

  var STATE_LABELS = {
    healthy: "HEALTHY", ok: "OK", ready: "OK", degraded: "DEGRADED",
    failed: "FAILED", critical: "CRITICAL", error: "UNAVAILABLE", unknown: "UNKNOWN"
  };

  function paintState(snapshot) {
    var holder = document.querySelector("[data-comms-chip='state']");
    if (holder) {
      var chip = holder.querySelector(".cchip");
      var key = String(snapshot.state || "unknown").toLowerCase();
      if (chip) {
        chip.className = "cchip is-" + key.replace(/[^a-z]/g, "");
        chip.textContent = STATE_LABELS[key] || key.toUpperCase();
      }
    }
    var stamp = document.querySelector("[data-comms-stamp='generated_at'] .smart-time");
    if (stamp && snapshot.generated_at) {
      stamp.setAttribute("datetime", snapshot.generated_at);
      stamp.setAttribute("data-timestamp", snapshot.generated_at);
      if (window.CoinPilotTime && window.CoinPilotTime.hydrate) {
        window.CoinPilotTime.hydrate(stamp.parentNode);
      }
    }
  }

  /* A cheap signature of everything this script cannot repaint. */
  function shapeOf(snapshot) {
    var calls = snapshot.calls || {};
    var providers = (snapshot.providers && snapshot.providers.providers) || [];
    return JSON.stringify({
      sessions: (calls.active || []).map(function (c) { return c.session; }).sort(),
      incidents: (snapshot.incidents || []).map(function (i) { return i.severity + "|" + i.title; }).sort(),
      providers: providers.map(function (p) { return p.key + "|" + p.state; }).sort(),
      failed: Object.keys(snapshot.section_errors || {}).sort()
    });
  }

  function announce(message, offerReload) {
    var box = document.querySelector("[data-comms-stale]");
    if (!box) return;
    if (!message) { box.hidden = true; box.textContent = ""; return; }
    box.textContent = message + " ";
    if (offerReload) {
      var link = document.createElement("a");
      link.href = window.location.href;
      link.textContent = "Reload";
      box.appendChild(link);
    }
    box.hidden = false;
  }

  function apply(snapshot) {
    paintCounts(snapshot);
    paintState(snapshot);
    var next = shapeOf(snapshot);
    if (shape === null) { shape = next; return; }
    if (next !== shape) {
      shape = next;
      announce("Call, incident or provider state changed since this page was drawn.", true);
    }
  }

  function poll() {
    fetch(ENDPOINT, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store"
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (snapshot) {
        if (!snapshot || snapshot.ok === false) throw new Error("snapshot unavailable");
        failures = 0;
        announce(null);
        apply(snapshot);
        schedule(INTERVAL_MS);
      })
      .catch(function () {
        // Say the numbers have stopped moving rather than leaving stale ones
        // looking live, and back off so a struggling database is not polled
        // harder than a healthy one.
        failures += 1;
        if (failures >= 2) {
          announce("Live refresh is not reaching the server. The figures below are from when the page loaded.", true);
        }
        schedule(Math.min(INTERVAL_MS * Math.pow(2, failures), BACKOFF_MAX_MS));
      });
  }

  function schedule(delay) {
    clearTimeout(timer);
    if (document.hidden) return;        // a backgrounded tab costs nothing
    timer = setTimeout(poll, delay);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) clearTimeout(timer);
    else schedule(1000);
  });

  /* §16 lookup. The one place this script does build elements, because there is
     no server-rendered row to update -- the result does not exist until an
     operator asks for it. Every value goes in through textContent and every
     element through createElement, so an end_reason from a provider or an id
     typed into the box cannot become markup.

     The form is a real GET form. With this script absent, submitting it lands
     on the JSON endpoint: a worse experience, but a truthful one. */
  function cell(tag, text) {
    var el = document.createElement(tag);
    el.textContent = (text === null || text === undefined || text === "") ? "—" : String(text);
    return el;
  }

  function resultTable(caption, columns, rows) {
    var wrap = document.createElement("div");
    wrap.className = "comms-scroll";
    var heading = document.createElement("h4");
    heading.textContent = caption;
    var el = document.createElement("table");
    el.className = "comms-dense";
    var thead = document.createElement("thead");
    var hrow = document.createElement("tr");
    columns.forEach(function (col) { hrow.appendChild(cell("th", col[1])); });
    thead.appendChild(hrow);
    var tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      columns.forEach(function (col) { tr.appendChild(cell("td", row[col[0]])); });
      tbody.appendChild(tr);
    });
    el.appendChild(thead);
    el.appendChild(tbody);
    wrap.appendChild(heading);
    wrap.appendChild(el);
    return wrap;
  }

  function note(text, warn) {
    var el = document.createElement("p");
    el.className = warn ? "comms-refused" : "muted";
    el.textContent = text;
    return el;
  }

  function renderLookup(target, payload) {
    target.textContent = "";
    if (!payload || payload.state === "refused") {
      target.appendChild(note((payload && payload.reason) || "That term was not searched.", true));
      return;
    }
    if (payload.state === "rate_limited") {
      target.appendChild(note(payload.reason || "Too many lookups.", true));
      return;
    }
    if (payload.state === "error") {
      /* Not "no results". A lookup that failed and an id that does not exist
         are different answers, and rendering them the same way would tell an
         operator the session is gone when the truth is that we cannot see. */
      target.appendChild(note("The lookup failed, so this is not a report that the id is unknown. Try again.", true));
      return;
    }
    if (payload.state === "not_found") {
      target.appendChild(note("No call or conversation has that id. Ids match whole or by their first eight characters.", false));
      return;
    }
    if ((payload.calls || []).length) {
      target.appendChild(resultTable("Calls", [
        ["session", "Session"], ["type", "Type"], ["scope", "Scope"], ["status", "State"],
        ["created_at", "Created"], ["duration_seconds", "Duration (s)"],
        ["participants", "In call"], ["provider", "Provider"], ["end_reason", "End reason"]
      ], payload.calls));
    }
    if ((payload.conversations || []).length) {
      target.appendChild(resultTable("Conversations", [
        ["conversation", "Conversation"], ["kind", "Type"], ["members", "Members"],
        ["status", "State"], ["last_message_at", "Last message"],
        ["last_activity_at", "Last activity"]
      ], payload.conversations));
    }
  }

  function wireLookup() {
    var form = document.querySelector("[data-comms-lookup]");
    var target = document.querySelector("[data-comms-lookup-result]");
    if (!form || !target) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var field = form.querySelector("input[name=q]");
      var term = field ? field.value.trim() : "";
      if (!term) {
        renderLookup(target, { state: "refused", reason: "Enter a call or conversation id." });
        return;
      }
      target.textContent = "";
      target.appendChild(note("Looking up…", false));
      fetch(form.getAttribute("action") + "?q=" + encodeURIComponent(term), {
        credentials: "same-origin", headers: { "Accept": "application/json" }
      })
        .then(function (res) {
          return res.json().catch(function () { return { state: "error" }; });
        })
        .then(function (payload) { renderLookup(target, payload); })
        .catch(function () { renderLookup(target, { state: "error" }); });
    });
  }

  function init() {
    wireLookup();
    if (!document.querySelector("[data-comms], [data-comms-chip]")) return;
    schedule(INTERVAL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
