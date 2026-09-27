/**
 * PulseSoc shared web emoji engine + picker — the web half of ONE system.
 *
 * THE one picker for the whole website: comments, replies, messages, composer,
 * reactions, reels comments, marketplace messaging all open this. Native
 * Unicode rendering only (the OS draws the glyphs); no emoji images, no remote
 * emoji API, no vendor IDs. The Unicode string itself is the stored value, on
 * both platforms.
 *
 * This is a deliberate port of `mobile-native/src/emoji/` — not a second
 * implementation of the same idea. What is mirrored, and why each one matters:
 *
 *   dataset      Both platforms read `emoji.json` emitted by the single
 *                generator `mobile-native/scripts/generate-emoji-data.mjs`.
 *                Two files, byte-identical, held so by a test (see the long
 *                comment in that generator for why they cannot be one file).
 *   taxonomy     EMOJI_CATEGORIES order, CATEGORY_ICONS, QUICK_REACTIONS —
 *                copied verbatim from `src/emoji/types.ts` and EmojiPicker.tsx.
 *   ranking      searchEmoji's 100/80/70/60/50/30 scoring and its limit*4
 *                early break, from `src/emoji/emojiData.ts`. A user who learns
 *                that typing "joy" lands on 😂 in the app must not have to
 *                re-learn it here.
 *   storage      `pulsesoc.emoji.recents.v1` / `pulsesoc.emoji.skin_tone.v1`,
 *                MAX_RECENTS 40, dedupe-move-to-front-and-cap. Same key names
 *                so the contract is one contract even though localStorage and
 *                AsyncStorage are different stores on different devices.
 *   look         8 columns, 44px cells, 28px glyphs, 12px/700/1px-tracking
 *                uppercase category headers, a space-around tab bar whose
 *                active tab is a raised-surface pill, 18px sheet radius.
 *
 * What is deliberately NOT mirrored is presentation plumbing, per the parity
 * rule that feature parity is not pixel parity: the app's bottom sheet becomes
 * an anchored popover at pointer widths (and stays a bottom sheet under 640px,
 * where a popover would be the wrong object), long-press becomes right-click
 * or pointer-hold or Shift+Enter, and the FlatList becomes a windowed
 * renderer over the same fixed row geometry.
 *
 * Loading: the 514KB dataset (~60KB over the wire, gzipped) is fetched lazily
 * on first open and memoized. It is never part of initial page JS.
 *
 * Public API — import from `window.PulseEmoji` only. A regression guard
 * (tests/web_surface/test_emoji_primitive.py) fails if a second picker or a
 * hardcoded emoji dataset appears anywhere else in static/ or templates/.
 */
(function (global) {
  "use strict";

  if (global.PulseEmoji) return;

  /* ------------------------------------------------------------------ *
   * Canonical taxonomy — mirror of mobile-native/src/emoji/types.ts
   * ------------------------------------------------------------------ */

  var EMOJI_CATEGORIES = [
    "RECENT",
    "SMILEYS & EMOTION",
    "PEOPLE & BODY",
    "ANIMALS & NATURE",
    "FOOD & DRINK",
    "ACTIVITIES",
    "TRAVEL & PLACES",
    "OBJECTS",
    "SYMBOLS",
    "FLAGS"
  ];

  /** Tab glyphs — themselves native Unicode, of course. */
  var CATEGORY_ICONS = {
    "RECENT": "🕘",
    "SMILEYS & EMOTION": "😀",
    "PEOPLE & BODY": "👋",
    "ANIMALS & NATURE": "🐻",
    "FOOD & DRINK": "🍔",
    "ACTIVITIES": "⚽",
    "TRAVEL & PLACES": "✈️",
    "OBJECTS": "💡",
    "SYMBOLS": "🔣",
    "FLAGS": "🏳️"
  };

  var CATEGORY_LABELS = {
    "RECENT": "Recent",
    "SMILEYS & EMOTION": "Smileys & emotion",
    "PEOPLE & BODY": "People & body",
    "ANIMALS & NATURE": "Animals & nature",
    "FOOD & DRINK": "Food & drink",
    "ACTIVITIES": "Activities",
    "TRAVEL & PLACES": "Travel & places",
    "OBJECTS": "Objects",
    "SYMBOLS": "Symbols",
    "FLAGS": "Flags"
  };

  var QUICK_REACTIONS = ["❤️", "😂", "😮", "😢", "😡", "👍"];

  var COLUMNS = 8;
  var CELL = 44;
  var HEADER = 40;

  var DATA_URL = "/static/emoji/emoji.json";
  var RECENTS_KEY = "pulsesoc.emoji.recents.v1";
  var TONE_KEY = "pulsesoc.emoji.skin_tone.v1";
  var MAX_RECENTS = 40;

  /* ------------------------------------------------------------------ *
   * Grapheme utilities — mirror of mobile-native/src/emoji/grapheme.ts
   *
   * Never use String.length to count emoji: "\u{1F468}‍\u{1F469}‍
   * \u{1F467}‍\u{1F466}".length is 11. Intl.Segmenter exists in modern
   * browsers but not in every one this PWA still serves, and the native side
   * has no Segmenter at all, so both platforms use this same scoped clusterer.
   * ------------------------------------------------------------------ */

  var ZWJ = 0x200d;
  var VS15 = 0xfe0e;
  var VS16 = 0xfe0f;
  var KEYCAP = 0x20e3;

  function isRegionalIndicator(cp) { return cp >= 0x1f1e6 && cp <= 0x1f1ff; }
  function isSkinToneModifier(cp) { return cp >= 0x1f3fb && cp <= 0x1f3ff; }
  function isTag(cp) { return cp >= 0xe0020 && cp <= 0xe007f; }
  function isVariationSelector(cp) { return cp === VS15 || cp === VS16; }

  function isEmojiCodePoint(cp) {
    return (
      (cp >= 0x1f000 && cp <= 0x1faff) ||
      (cp >= 0x2600 && cp <= 0x27bf) ||
      (cp >= 0x2190 && cp <= 0x21ff) ||
      (cp >= 0x2300 && cp <= 0x23ff) ||
      (cp >= 0x25a0 && cp <= 0x25ff) ||
      (cp >= 0x2900 && cp <= 0x297f) ||
      (cp >= 0x2b00 && cp <= 0x2bff) ||
      (cp >= 0x3030 && cp <= 0x303d) ||
      (cp >= 0x3297 && cp <= 0x3299) ||
      cp === 0x00a9 || cp === 0x00ae ||
      cp === 0x203c || cp === 0x2049 ||
      cp === 0x2122 || cp === 0x2139 ||
      (cp >= 0x2194 && cp <= 0x2199) ||
      (cp >= 0x0030 && cp <= 0x0039) || cp === 0x0023 || cp === 0x002a
    );
  }

  function splitEmojiClusters(input) {
    var cps = Array.from(String(input || ""));
    var clusters = [];
    var i = 0;
    while (i < cps.length) {
      var j = i + 1;
      var startCp = cps[i].codePointAt(0);
      if (isRegionalIndicator(startCp) && j < cps.length && isRegionalIndicator(cps[j].codePointAt(0))) {
        j += 1;
      } else {
        while (j < cps.length) {
          var cp = cps[j].codePointAt(0);
          if (isVariationSelector(cp) || isSkinToneModifier(cp) || cp === KEYCAP || isTag(cp)) {
            j += 1;
          } else if (cp === ZWJ && j + 1 < cps.length) {
            j += 2;
          } else {
            break;
          }
        }
      }
      clusters.push(cps.slice(i, j).join(""));
      i = j;
    }
    return clusters;
  }

  function countEmojiClusters(input) { return splitEmojiClusters(input).length; }

  function isSingleEmoji(input) {
    if (!input || input.length > 32) return false;
    var clusters = splitEmojiClusters(input);
    if (clusters.length !== 1) return false;
    return isEmojiCodePoint(clusters[0].codePointAt(0));
  }

  function stripSkinTone(emoji) {
    return Array.from(String(emoji || ""))
      .filter(function (ch) { return !isSkinToneModifier(ch.codePointAt(0)); })
      .join("");
  }

  /* ------------------------------------------------------------------ *
   * Dataset access — mirror of mobile-native/src/emoji/emojiData.ts
   * ------------------------------------------------------------------ */

  var artifact = null;
  var loadPromise = null;
  var byCategory = null;
  var byEmoji = null;

  /**
   * Fetch + memoize the dataset. Rejects loudly once; a later call retries,
   * because the usual cause is a dropped connection on first open and the
   * second click should not inherit the first click's failure.
   */
  function load() {
    if (artifact) return Promise.resolve(artifact);
    if (loadPromise) return loadPromise;
    loadPromise = fetch(DATA_URL, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("emoji dataset: HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.emojis) || !data.emojis.length) {
          throw new Error("emoji dataset: empty or malformed");
        }
        artifact = data;
        return artifact;
      })
      .catch(function (error) {
        loadPromise = null;
        throw error;
      });
    return loadPromise;
  }

  function loaded() { return Boolean(artifact); }
  function allEmoji() { return artifact ? artifact.emojis : []; }
  function emojiDataVersion() { return artifact ? artifact.version : ""; }

  function emojiByCategory(category) {
    if (!artifact) return [];
    if (!byCategory) {
      byCategory = {};
      EMOJI_CATEGORIES.forEach(function (cat) { byCategory[cat] = []; });
      artifact.emojis.forEach(function (e) {
        if (byCategory[e.category]) byCategory[e.category].push(e);
      });
    }
    return byCategory[category] || [];
  }

  /**
   * Metadata for an emoji string. Skin-tone variants resolve to their base
   * entry, and the VS16-less form is indexed too, because backends store
   * either and a reaction row written by the app must resolve on the web.
   */
  function findEmoji(emoji) {
    if (!artifact || !emoji) return null;
    if (!byEmoji) {
      byEmoji = Object.create(null);
      artifact.emojis.forEach(function (e) {
        byEmoji[e.emoji] = e;
        var bare = e.emoji.replace(/️/g, "");
        if (!(bare in byEmoji)) byEmoji[bare] = e;
      });
    }
    return byEmoji[emoji] || byEmoji[emoji.replace(/️/g, "")] || byEmoji[stripSkinTone(emoji)] || null;
  }

  /**
   * Accessible name for any emoji value: "face with tears of joy", not
   * "U+1F602" and not the raw glyph. Screen readers announce a bare emoji
   * inconsistently, so every glyph this file renders carries one of these.
   */
  function emojiA11yLabel(emoji) {
    var entry = findEmoji(emoji);
    if (!entry) return emoji;
    if (entry.emoji !== emoji) {
      for (var i = 0; i < entry.variants.length; i += 1) {
        if (entry.variants[i].emoji === emoji) return entry.variants[i].name;
      }
    }
    return entry.name;
  }

  /**
   * Local search over names + keywords. The score ladder and the `limit * 4`
   * early break are copied from emojiData.ts on purpose: same query, same
   * order, same first result on both platforms.
   */
  function searchEmoji(query, limit) {
    limit = limit || 120;
    var q = String(query || "").trim().toLowerCase();
    if (!q || !artifact) return [];
    var scored = [];
    var list = artifact.emojis;
    for (var i = 0; i < list.length; i += 1) {
      var e = list[i];
      var s = 0;
      var name = e.name;
      if (name === q) s = 100;
      else if (name.indexOf(q) === 0) s = 80;
      else if (name.indexOf(q) !== -1) s = 60;
      else {
        for (var k = 0; k < e.keywords.length; k += 1) {
          var kw = e.keywords[k];
          if (kw === q) { s = Math.max(s, 70); break; }
          if (kw.indexOf(q) === 0) s = Math.max(s, 50);
          else if (kw.indexOf(q) !== -1) s = Math.max(s, 30);
        }
      }
      if (s > 0) scored.push({ e: e, s: s, i: i });
      if (scored.length >= limit * 4) break;
    }
    // Stable by dataset order within a score band, so equal-scoring results
    // come back in canonical order rather than engine-dependent sort order.
    scored.sort(function (a, b) { return b.s - a.s || a.i - b.i; });
    return scored.slice(0, limit).map(function (x) { return x.e; });
  }

  /* ------------------------------------------------------------------ *
   * Recents + skin tone — mirror of mobile-native/src/emoji/recents.ts
   *
   * Synchronous here (localStorage) where the native side is async
   * (AsyncStorage); the keys, the cap and the dedupe rule are identical.
   * Every access is try/caught: Safari private mode throws on setItem and an
   * emoji picker must not be the thing that breaks a page.
   * ------------------------------------------------------------------ */

  function readStore(key) {
    try { return global.localStorage ? global.localStorage.getItem(key) : null; }
    catch (e) { return null; }
  }

  function writeStore(key, value) {
    try { if (global.localStorage) global.localStorage.setItem(key, value); }
    catch (e) { /* best effort; in-memory state already updated */ }
  }

  var recentsCache = null;
  var toneCache = null;

  function getRecentEmoji() {
    if (recentsCache) return recentsCache;
    var parsed = [];
    try { parsed = JSON.parse(readStore(RECENTS_KEY) || "[]"); }
    catch (e) { parsed = []; }
    recentsCache = Array.isArray(parsed)
      ? parsed.filter(function (x) { return typeof x === "string"; }).slice(0, MAX_RECENTS)
      : [];
    return recentsCache;
  }

  function recordRecentEmoji(emoji) {
    var current = getRecentEmoji();
    var next = [emoji].concat(current.filter(function (e) { return e !== emoji; })).slice(0, MAX_RECENTS);
    recentsCache = next;
    writeStore(RECENTS_KEY, JSON.stringify(next));
    return next;
  }

  function getSkinTonePreference() {
    if (toneCache !== null) return toneCache;
    var raw = readStore(TONE_KEY);
    var n = raw === null ? 0 : Number(raw);
    toneCache = (n >= 0 && n <= 5 && !isNaN(n)) ? n : 0;
    return toneCache;
  }

  function setSkinTonePreference(tone) {
    toneCache = tone;
    writeStore(TONE_KEY, String(tone));
  }

  /** Apply the persisted tone to a tone-capable entry. */
  function applyTone(entry, tone) {
    if (!tone || !entry.skin_tone_capable) return entry.emoji;
    var variant = entry.variants[tone - 1];
    return variant ? variant.emoji : entry.emoji;
  }

  /* ------------------------------------------------------------------ *
   * The picker
   * ------------------------------------------------------------------ */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  var open = null; // the single live picker instance, or null

  /**
   * The stylesheet is injected on first open rather than linked from every
   * page head. A primitive that seven surfaces embed should not need seven
   * `<link>` edits to exist, and nothing on a page that never opens the
   * picker should pay for it.
   */
  // Versioned for the same reason every other asset here is: static files are
  // served `Cache-Control: immutable, max-age=31536000`, so a browser that has
  // this stylesheet will never ask for it again. Without a token in the URL an
  // edit to the picker's CSS would reach no one who had already opened it.
  // Bump this whenever pulse_emoji.css changes.
  var STYLE_HREF = "/static/css/pulse_emoji.css?v=emoji-primitive-20260927b";
  function ensureStyles() {
    if (document.querySelector('link[data-pulse-emoji-style]')) return;
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.setAttribute("data-pulse-emoji-style", "");
    document.head.appendChild(link);
  }

  function prefersReducedMotion() {
    return Boolean(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function isSheetWidth() {
    return Boolean(global.matchMedia && global.matchMedia("(max-width: 639px)").matches);
  }

  /**
   * Row model: one header row per category then ceil(n/8) emoji rows, exactly
   * the shape EmojiPicker.tsx builds for its FlatList. Fixed row heights are
   * what make both the windowed renderer and the category jump exact.
   */
  function buildRows(recents) {
    var rows = [];
    var index = {};
    EMOJI_CATEGORIES.forEach(function (cat) {
      var entries;
      if (cat === "RECENT") {
        entries = recents.map(function (e) {
          return {
            emoji: e, name: emojiA11yLabel(e), keywords: [],
            category: "RECENT", subgroup: "recent",
            skin_tone_capable: false, variants: []
          };
        });
        if (!entries.length) return;
      } else {
        entries = emojiByCategory(cat);
        if (!entries.length) return;
      }
      index[cat] = rows.length;
      rows.push({ kind: "header", category: cat });
      for (var i = 0; i < entries.length; i += COLUMNS) {
        rows.push({ kind: "emojis", category: cat, items: entries.slice(i, i + COLUMNS) });
      }
    });
    return { rows: rows, index: index };
  }

  function rowsFromEntries(entries) {
    var rows = [];
    for (var i = 0; i < entries.length; i += COLUMNS) {
      rows.push({ kind: "emojis", category: null, items: entries.slice(i, i + COLUMNS) });
    }
    return rows;
  }

  function rowHeight(row) { return row.kind === "header" ? HEADER : CELL; }

  function PickerInstance(options) {
    var self = this;
    ensureStyles();
    this.options = options || {};
    this.onSelect = this.options.onSelect || function () {};
    this.stayOpen = Boolean(this.options.stayOpenOnSelect);
    this.anchor = this.options.anchor || null;
    this.returnFocusTo = this.options.returnFocusTo || this.anchor;
    this.tone = getSkinTonePreference();
    this.query = "";
    this.rows = [];
    this.rowIndex = {};
    this.offsets = [];
    this.totalHeight = 0;
    this.activeCategory = "SMILEYS & EMOTION";
    this.focusFlat = -1;     // index into the flat entry list, for keyboard nav
    this.flatEntries = [];   // entry + row/col, rebuilt whenever rows change
    this.tonePopover = null;
    this.holdTimer = null;
    this.destroyed = false;

    this.root = el("div", "pulse-emoji-root");
    this.root.setAttribute("data-pulse-emoji-root", "");
    if (isSheetWidth()) this.root.classList.add("is-sheet");
    if (prefersReducedMotion()) this.root.classList.add("is-static");

    this.backdrop = el("div", "pulse-emoji-backdrop");
    this.backdrop.addEventListener("mousedown", function (event) {
      event.preventDefault();
      self.close();
    });

    this.panel = el("div", "pulse-emoji-panel");
    this.panel.setAttribute("role", "dialog");
    this.panel.setAttribute("aria-modal", "false");
    this.panel.setAttribute("aria-label", this.options.label || "Emoji picker");

    // The grab handle is the app's sheet affordance. At popover widths it is
    // hidden by CSS rather than skipped here, so one DOM shape serves both.
    var handleWrap = el("div", "pulse-emoji-handle-wrap");
    handleWrap.appendChild(el("div", "pulse-emoji-handle"));
    this.panel.appendChild(handleWrap);

    var searchWrap = el("div", "pulse-emoji-search-wrap");
    this.search = el("input", "pulse-emoji-search");
    this.search.type = "search";
    this.search.placeholder = "Search emoji";
    this.search.setAttribute("aria-label", "Search emoji");
    this.search.setAttribute("autocomplete", "off");
    this.search.setAttribute("autocorrect", "off");
    this.search.addEventListener("input", function () { self.setQuery(self.search.value); });
    searchWrap.appendChild(this.search);
    this.panel.appendChild(searchWrap);

    this.scroller = el("div", "pulse-emoji-scroll");
    this.scroller.setAttribute("role", "grid");
    this.scroller.setAttribute("aria-label", "Emoji");
    this.spacer = el("div", "pulse-emoji-spacer");
    this.scroller.appendChild(this.spacer);
    this.scroller.addEventListener("scroll", function () { self.renderWindow(); }, { passive: true });
    this.panel.appendChild(this.scroller);

    this.empty = el("p", "pulse-emoji-empty", "No emoji found");
    this.empty.hidden = true;
    this.panel.appendChild(this.empty);

    this.tabs = el("div", "pulse-emoji-tabs");
    this.tabs.setAttribute("role", "tablist");
    this.tabs.setAttribute("aria-label", "Emoji categories");
    this.panel.appendChild(this.tabs);

    this.status = el("p", "pulse-emoji-status", "Loading emoji…");
    this.panel.appendChild(this.status);

    this.root.appendChild(this.backdrop);
    this.root.appendChild(this.panel);
    document.body.appendChild(this.root);

    this.onKeyDown = function (event) { self.handleKey(event); };
    this.onDocPointer = function (event) {
      if (self.root.contains(event.target)) return;
      if (self.anchor && self.anchor.contains && self.anchor.contains(event.target)) return;
      self.close();
    };
    this.onReposition = function () { self.position(); };

    document.addEventListener("keydown", this.onKeyDown, true);
    document.addEventListener("mousedown", this.onDocPointer, true);
    global.addEventListener("resize", this.onReposition);
    global.addEventListener("scroll", this.onReposition, true);

    this.panel.addEventListener("click", function (event) { self.handleClick(event); });
    this.panel.addEventListener("contextmenu", function (event) {
      var cell = event.target.closest ? event.target.closest("[data-emoji]") : null;
      if (!cell) return;
      event.preventDefault();
      self.openTonePopover(cell);
    });
    // Pointer-hold is the mouse/touch analogue of the app's 250ms long press.
    this.panel.addEventListener("pointerdown", function (event) {
      var cell = event.target.closest ? event.target.closest("[data-emoji]") : null;
      if (!cell || cell.getAttribute("data-tone-capable") !== "1") return;
      self.clearHold();
      self.holdTimer = global.setTimeout(function () {
        self.holdTimer = null;
        self.holdFired = true;
        self.openTonePopover(cell);
      }, 250);
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
      self.panel.addEventListener(name, function () { self.clearHold(); });
    });

    this.position();
    this.search.focus({ preventScroll: true });

    load().then(function (data) {
      if (self.destroyed) return;
      self.status.hidden = true;
      self.status.textContent = "";
      self.buildTabs();
      self.rebuild();
      // The first position() ran against an empty panel, before 1,914 emoji
      // and nine category tabs existed to measure. Re-run it now that the
      // panel is the size it will actually be -- and so a trigger that had no
      // layout at open time (a viewer still animating in) gets a second,
      // truthful measurement instead of staying where the zero-rect guess
      // parked it.
      self.position();
    }).catch(function (error) {
      if (self.destroyed) return;
      // A failed dataset load is not an empty dataset. Say which one it is,
      // and leave the six quick reactions usable so the surface still works.
      self.status.textContent = "Emoji could not be loaded. Check your connection.";
      self.status.classList.add("is-error");
      self.renderQuickFallback();
      if (global.console && global.console.warn) global.console.warn("[pulse-emoji]", error);
    });
  }

  PickerInstance.prototype.clearHold = function () {
    if (this.holdTimer) {
      global.clearTimeout(this.holdTimer);
      this.holdTimer = null;
    }
  };

  /**
   * Anchored popover at pointer widths, bottom sheet under 640px. Collision
   * handling flips above the anchor when there is no room below and clamps
   * horizontally into the viewport, so a picker opened from a comment box at
   * the bottom of a long thread is never half off-screen.
   */
  PickerInstance.prototype.position = function () {
    if (this.destroyed) return;
    var sheet = isSheetWidth();
    this.root.classList.toggle("is-sheet", sheet);
    // An anchor with no area cannot be positioned against: every offset
    // computes from zero and the panel pins itself to the top-left corner,
    // which is where it then stays, because nothing repositions it afterwards.
    // A trigger measures zero more often than it looks -- inside a viewer that
    // is still opening, a tab that is display:none, a card mid-transition. A
    // centred panel is a worse guess than a correct anchor and a much better
    // one than the corner of the screen.
    var anchorRect = this.anchor && this.anchor.getBoundingClientRect
      ? this.anchor.getBoundingClientRect()
      : null;
    if (sheet || !anchorRect || !anchorRect.width || !anchorRect.height) {
      this.root.classList.toggle("is-centered", !sheet);
      this.panel.style.left = "";
      this.panel.style.top = "";
      // A picker that was shortened to clear a cramped anchor keeps that
      // inline height until something clears it. Narrowing the window turns
      // the popover into a sheet, and the sheet's own `min(62vh, 560px)` is a
      // stylesheet rule -- an inline height outranks it, so without this the
      // sheet would inherit whatever the desktop anchor happened to allow.
      if (this.panel.style.height) {
        this.panel.style.height = "";
        this.renderWindow();
      }
      return;
    }
    this.root.classList.remove("is-centered");
    var previousHeight = this.panel.style.height;
    // Measure the panel at its natural height. A previous call may have
    // shortened it for a cramped anchor; carrying that over would make the
    // panel ratchet smaller on every reposition and never grow back.
    this.panel.style.height = "";
    var rect = anchorRect;
    var panelRect = this.panel.getBoundingClientRect();
    var width = panelRect.width || 360;
    var height = panelRect.height || 420;
    var margin = 8;
    var left = Math.min(
      Math.max(margin, rect.left),
      Math.max(margin, global.innerWidth - width - margin)
    );
    // Room on each side of the anchor, excluding the gap and the viewport
    // margin. A comment box sits in the vertical middle of a tall feed card,
    // so neither side holds the full 420px panel -- and the obvious
    // `Math.max(margin, ...)` clamp resolves that by sliding the panel over
    // the very button the reader just pressed. Hiding the control that owns
    // the popover is worse than showing fewer rows, so when the panel does not
    // fit we take the roomier side and shorten it. The grid scrolls; the
    // anchor stays visible.
    var roomBelow = global.innerHeight - rect.bottom - margin * 2;
    var roomAbove = rect.top - margin * 2;
    var top;
    if (height <= roomBelow) {
      top = rect.bottom + margin;
    } else if (height <= roomAbove) {
      top = rect.top - height - margin;
    } else {
      // MIN_PANEL keeps a shortened panel worth opening -- search, a heading
      // and ~2 rows. Below that there is no useful picker left to show, so we
      // stop shrinking and accept the overlap rather than render a sliver.
      var MIN_PANEL = 240;
      var useBelow = roomBelow >= roomAbove;
      var room = Math.max(MIN_PANEL, useBelow ? roomBelow : roomAbove);
      this.panel.style.height = Math.round(Math.min(height, room)) + "px";
      height = this.panel.getBoundingClientRect().height;
      top = useBelow
        ? rect.bottom + margin
        : Math.max(margin, rect.top - height - margin);
    }
    this.panel.style.left = Math.round(left) + "px";
    this.panel.style.top = Math.round(top) + "px";
    // The virtualizer windows rows against the scroller's client height, so a
    // panel whose height just changed is rendering the wrong number of them.
    if (this.panel.style.height !== previousHeight) this.renderWindow();
  };

  PickerInstance.prototype.buildTabs = function () {
    var self = this;
    this.tabs.textContent = "";
    var recents = getRecentEmoji();
    EMOJI_CATEGORIES.forEach(function (cat) {
      if (cat === "RECENT" && !recents.length) return;
      if (cat !== "RECENT" && !emojiByCategory(cat).length) return;
      var tab = el("button", "pulse-emoji-tab", CATEGORY_ICONS[cat]);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("data-category", cat);
      tab.setAttribute("aria-label", CATEGORY_LABELS[cat]);
      tab.setAttribute("aria-selected", String(cat === self.activeCategory));
      tab.tabIndex = -1;
      self.tabs.appendChild(tab);
    });
  };

  PickerInstance.prototype.syncTabs = function () {
    var self = this;
    Array.prototype.forEach.call(this.tabs.children, function (tab) {
      var selected = tab.getAttribute("data-category") === self.activeCategory;
      tab.setAttribute("aria-selected", String(selected));
      tab.classList.toggle("is-active", selected);
    });
  };

  PickerInstance.prototype.setQuery = function (value) {
    this.query = value || "";
    this.rebuild();
  };

  PickerInstance.prototype.rebuild = function () {
    if (!loaded()) return;
    var searching = Boolean(this.query.trim());
    if (searching) {
      var results = searchEmoji(this.query);
      this.rows = rowsFromEntries(results);
      this.rowIndex = {};
    } else {
      var model = buildRows(getRecentEmoji());
      this.rows = model.rows;
      this.rowIndex = model.index;
    }
    this.tabs.hidden = searching;
    this.empty.hidden = !(searching && this.rows.length === 0);

    this.offsets = [];
    var offset = 0;
    this.flatEntries = [];
    for (var i = 0; i < this.rows.length; i += 1) {
      this.offsets.push(offset);
      offset += rowHeight(this.rows[i]);
      if (this.rows[i].kind === "emojis") {
        for (var c = 0; c < this.rows[i].items.length; c += 1) {
          this.flatEntries.push({ row: i, col: c, entry: this.rows[i].items[c] });
        }
      }
    }
    this.totalHeight = offset;
    this.spacer.style.height = this.totalHeight + "px";
    this.scroller.scrollTop = 0;
    this.focusFlat = -1;
    this.renderWindow();
    this.position();
  };

  /**
   * Windowed render. 1,914 emoji is ~240 rows; committing all of them to the
   * DOM costs a visible hitch on open and makes every scroll a layout of
   * 2,000 buttons. Only the rows intersecting the viewport (plus overscan)
   * exist at any moment, positioned absolutely at their exact offsets.
   */
  PickerInstance.prototype.renderWindow = function () {
    if (this.destroyed) return;
    var viewTop = this.scroller.scrollTop;
    var viewHeight = this.scroller.clientHeight || 320;
    var overscan = CELL * 4;
    var top = viewTop - overscan;
    var bottom = viewTop + viewHeight + overscan;

    var frag = document.createDocumentFragment();
    var firstVisible = null;
    for (var i = 0; i < this.rows.length; i += 1) {
      var y = this.offsets[i];
      if (y + rowHeight(this.rows[i]) < top) continue;
      if (y > bottom) break;
      if (firstVisible === null && y + rowHeight(this.rows[i]) >= viewTop) firstVisible = i;
      frag.appendChild(this.renderRow(this.rows[i], i, y));
    }
    this.spacer.textContent = "";
    this.spacer.appendChild(frag);

    if (firstVisible !== null && !this.query.trim()) {
      var cat = this.rows[firstVisible].category;
      if (cat && cat !== this.activeCategory) {
        this.activeCategory = cat;
        this.syncTabs();
      }
    }
    this.restoreFocusRing();
  };

  PickerInstance.prototype.renderRow = function (row, rowIdx, y) {
    var node;
    if (row.kind === "header") {
      node = el("div", "pulse-emoji-heading", CATEGORY_LABELS[row.category] || row.category);
      node.setAttribute("role", "presentation");
    } else {
      node = el("div", "pulse-emoji-row");
      node.setAttribute("role", "row");
      for (var c = 0; c < row.items.length; c += 1) {
        node.appendChild(this.renderCell(row.items[c], rowIdx, c));
      }
    }
    node.style.top = y + "px";
    return node;
  };

  PickerInstance.prototype.renderCell = function (entry, rowIdx, col) {
    var shown = applyTone(entry, this.tone);
    var cell = el("button", "pulse-emoji-cell");
    cell.type = "button";
    cell.setAttribute("role", "gridcell");
    cell.setAttribute("data-emoji", shown);
    cell.setAttribute("data-row", String(rowIdx));
    cell.setAttribute("data-col", String(col));
    // Screen readers get the CLDR name, never the raw codepoint; the glyph
    // itself is hidden from the tree so it is not announced twice.
    cell.setAttribute("aria-label", emojiA11yLabel(shown));
    cell.tabIndex = -1;
    if (entry.skin_tone_capable) {
      cell.setAttribute("data-tone-capable", "1");
      cell.title = emojiA11yLabel(shown) + " — hold or right-click for skin tone";
    }
    var glyph = el("span", "pulse-emoji-glyph", shown);
    glyph.setAttribute("aria-hidden", "true");
    cell.appendChild(glyph);
    return cell;
  };

  PickerInstance.prototype.renderQuickFallback = function () {
    var self = this;
    this.tabs.hidden = true;
    this.search.disabled = true;
    this.spacer.style.height = CELL + "px";
    this.spacer.textContent = "";
    var row = el("div", "pulse-emoji-row");
    row.style.top = "0px";
    QUICK_REACTIONS.forEach(function (glyph, i) {
      var cell = el("button", "pulse-emoji-cell");
      cell.type = "button";
      cell.setAttribute("data-emoji", glyph);
      cell.setAttribute("aria-label", "Emoji " + (i + 1));
      var span = el("span", "pulse-emoji-glyph", glyph);
      span.setAttribute("aria-hidden", "true");
      cell.appendChild(span);
      row.appendChild(cell);
    });
    this.spacer.appendChild(row);
  };

  PickerInstance.prototype.cellAt = function (flatIdx) {
    var slot = this.flatEntries[flatIdx];
    if (!slot) return null;
    return this.spacer.querySelector(
      '[data-row="' + slot.row + '"][data-col="' + slot.col + '"]'
    );
  };

  PickerInstance.prototype.restoreFocusRing = function () {
    if (this.focusFlat < 0) return;
    var cell = this.cellAt(this.focusFlat);
    if (cell && document.activeElement !== cell) cell.focus({ preventScroll: true });
  };

  PickerInstance.prototype.moveFocus = function (delta) {
    if (!this.flatEntries.length) return;
    var next = this.focusFlat < 0
      ? (delta > 0 ? 0 : this.flatEntries.length - 1)
      : this.focusFlat + delta;
    next = Math.max(0, Math.min(this.flatEntries.length - 1, next));
    this.focusFlat = next;
    var slot = this.flatEntries[next];
    var y = this.offsets[slot.row];
    var viewTop = this.scroller.scrollTop;
    var viewBottom = viewTop + this.scroller.clientHeight;
    if (y < viewTop) this.scroller.scrollTop = y;
    else if (y + CELL > viewBottom) this.scroller.scrollTop = y + CELL - this.scroller.clientHeight;
    this.renderWindow();
    var cell = this.cellAt(next);
    if (cell) cell.focus({ preventScroll: true });
  };

  PickerInstance.prototype.handleKey = function (event) {
    if (this.destroyed) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (this.tonePopover) { this.closeTonePopover(); return; }
      this.close();
      return;
    }
    if (!this.root.contains(document.activeElement)) return;

    var inSearch = document.activeElement === this.search;
    switch (event.key) {
      case "ArrowRight":
        if (inSearch && this.search.selectionStart !== this.search.value.length) return;
        event.preventDefault(); this.moveFocus(1); break;
      case "ArrowLeft":
        if (inSearch && this.search.selectionStart !== 0) return;
        event.preventDefault(); this.moveFocus(-1); break;
      case "ArrowDown":
        event.preventDefault(); this.moveFocus(this.focusFlat < 0 ? 1 : COLUMNS); break;
      case "ArrowUp":
        if (this.focusFlat < COLUMNS && this.focusFlat >= 0) {
          event.preventDefault();
          this.focusFlat = -1;
          this.search.focus();
          return;
        }
        event.preventDefault(); this.moveFocus(-COLUMNS); break;
      case "Home":
        if (inSearch) return;
        event.preventDefault(); this.focusFlat = -1; this.moveFocus(1); break;
      case "End":
        if (inSearch) return;
        event.preventDefault(); this.focusFlat = this.flatEntries.length; this.moveFocus(-1); break;
      case "Enter":
      case " ": {
        var cell = document.activeElement && document.activeElement.closest
          ? document.activeElement.closest("[data-emoji]") : null;
        if (!cell) return;
        event.preventDefault();
        if (event.shiftKey && cell.getAttribute("data-tone-capable") === "1") {
          this.openTonePopover(cell);
        } else {
          this.pick(cell.getAttribute("data-emoji"));
        }
        break;
      }
      default:
        break;
    }
  };

  PickerInstance.prototype.handleClick = function (event) {
    var target = event.target;
    var tone = target.closest ? target.closest("[data-tone-variant]") : null;
    if (tone) {
      event.preventDefault();
      var toneIndex = Number(tone.getAttribute("data-tone-variant"));
      setSkinTonePreference(toneIndex);
      this.tone = toneIndex;
      var glyph = tone.getAttribute("data-emoji");
      this.closeTonePopover();
      this.renderWindow();
      this.pick(glyph);
      return;
    }
    var tab = target.closest ? target.closest("[data-category]") : null;
    if (tab) {
      event.preventDefault();
      this.jumpTo(tab.getAttribute("data-category"));
      return;
    }
    var cell = target.closest ? target.closest("[data-emoji]") : null;
    if (cell) {
      event.preventDefault();
      if (this.holdFired) { this.holdFired = false; return; }
      this.pick(cell.getAttribute("data-emoji"));
    }
  };

  PickerInstance.prototype.jumpTo = function (category) {
    var idx = this.rowIndex[category];
    if (idx === undefined) return;
    this.activeCategory = category;
    this.scroller.scrollTop = this.offsets[idx];
    this.renderWindow();
    this.syncTabs();
  };

  PickerInstance.prototype.openTonePopover = function (cell) {
    this.closeTonePopover();
    var base = stripSkinTone(cell.getAttribute("data-emoji") || "");
    var entry = findEmoji(base);
    if (!entry || !entry.skin_tone_capable) return;
    var self = this;
    var pop = el("div", "pulse-emoji-tones");
    pop.setAttribute("role", "group");
    pop.setAttribute("aria-label", "Skin tone for " + entry.name);
    [entry.emoji].concat(entry.variants.map(function (v) { return v.emoji; }))
      .forEach(function (glyph, i) {
        var button = el("button", "pulse-emoji-tone");
        button.type = "button";
        button.setAttribute("data-tone-variant", String(i));
        button.setAttribute("data-emoji", glyph);
        button.setAttribute("aria-label", emojiA11yLabel(glyph));
        button.setAttribute("aria-pressed", String(i === self.tone));
        var span = el("span", "pulse-emoji-tone-glyph", glyph);
        span.setAttribute("aria-hidden", "true");
        button.appendChild(span);
        pop.appendChild(button);
      });
    this.panel.appendChild(pop);
    this.tonePopover = pop;
    var first = pop.querySelector("button");
    if (first) first.focus({ preventScroll: true });
  };

  PickerInstance.prototype.closeTonePopover = function () {
    if (!this.tonePopover) return;
    this.tonePopover.remove();
    this.tonePopover = null;
  };

  PickerInstance.prototype.pick = function (emoji) {
    if (!emoji) return;
    recordRecentEmoji(emoji);
    try { this.onSelect(emoji); }
    catch (error) {
      if (global.console && global.console.error) global.console.error("[pulse-emoji] onSelect", error);
    }
    if (this.stayOpen) {
      // RECENT changed, so the model has, but keep the scroll position: a
      // composer user picking four emoji in a row should not be thrown back
      // to the top of the list after each one.
      var scrollTop = this.scroller.scrollTop;
      var focus = this.focusFlat;
      this.buildTabs();
      this.rebuild();
      this.scroller.scrollTop = scrollTop;
      this.focusFlat = focus;
      this.renderWindow();
    } else {
      this.close();
    }
  };

  PickerInstance.prototype.close = function () {
    if (this.destroyed) return;
    this.destroyed = true;
    this.clearHold();
    document.removeEventListener("keydown", this.onKeyDown, true);
    document.removeEventListener("mousedown", this.onDocPointer, true);
    global.removeEventListener("resize", this.onReposition);
    global.removeEventListener("scroll", this.onReposition, true);
    this.root.remove();
    if (open === this) open = null;
    markAnchorExpanded();
    // Focus restoration: keyboard users must land back on the trigger, not at
    // the top of the document.
    var target = this.returnFocusTo;
    if (target && target.isConnected && target.focus) target.focus({ preventScroll: true });
    if (typeof this.options.onClose === "function") this.options.onClose();
  };

  /**
   * Open THE picker. Only one exists at a time; opening from a new anchor
   * closes the old one, and re-opening from the same anchor toggles it shut,
   * which is what every trigger button in the product expects.
   */
  function openPicker(options) {
    options = options || {};
    if (open) {
      var sameAnchor = open.anchor && open.anchor === options.anchor;
      open.close();
      if (sameAnchor) return null;
    }
    open = new PickerInstance(options);
    markAnchorExpanded();
    return open;
  }

  /**
   * A trigger that opens a panel has to say so, and has to say when it closed.
   * Only anchors that already declare `aria-haspopup` are touched: an anchor
   * that is not advertising a popup is not one, and stamping state onto it
   * would describe a widget it is not.
   */
  function markAnchorExpanded() {
    document.querySelectorAll('[data-emoji-for][aria-expanded]').forEach(function (node) {
      node.setAttribute("aria-expanded", open && open.anchor === node ? "true" : "false");
    });
  }

  function closePicker() { if (open) open.close(); }
  function isOpen() { return Boolean(open); }

  /**
   * Convenience for the common case: a trigger button that inserts into a
   * text field at the caret. Keeps the picker open so several emoji can be
   * added in one visit, which is how the app's composer behaves.
   */
  function attachToInput(trigger, input, options) {
    if (!trigger || !input) return;
    options = options || {};
    trigger.addEventListener("click", function (event) {
      event.preventDefault();
      openPicker({
        anchor: trigger,
        returnFocusTo: input,
        stayOpenOnSelect: options.stayOpenOnSelect !== false,
        label: options.label,
        onSelect: function (emoji) { insertAtCaret(input, emoji); }
      });
    });
  }

  /**
   * Declarative opt-in: `data-emoji-for` on any button wires it to a field.
   *
   *   <button data-emoji-for="[data-status-story-reply]" aria-label="Add emoji">☺</button>
   *
   * `attachToInput` needs both elements in hand and binds one listener per
   * pair, which cannot serve a feed that renders its cards after load. This is
   * one document-level listener for the whole product instead, so a surface
   * opts in with an attribute rather than by writing its own handler -- which
   * is how a picker stays one system instead of becoming seven. An empty value
   * means "the field next to me": the nearest text input or textarea in the
   * trigger's own form or `data-emoji-scope`.
   */
  document.addEventListener("click", function (event) {
    var target = event.target;
    var trigger = target && target.closest && target.closest("[data-emoji-for]");
    if (!trigger) return;
    var selector = trigger.getAttribute("data-emoji-for");
    var input = selector
      ? document.querySelector(selector)
      : (trigger.closest("form,[data-emoji-scope]") || document)
          .querySelector("input[type=text],input:not([type]),textarea");
    if (!input) return;
    event.preventDefault();
    openPicker({
      anchor: trigger,
      returnFocusTo: input,
      stayOpenOnSelect: true,
      label: trigger.getAttribute("data-emoji-label")
        || trigger.getAttribute("aria-label")
        || "Add emoji",
      onSelect: function (emoji) { insertAtCaret(input, emoji); }
    });
  });

  /** Insert at the caret and leave the caret after the insertion. */
  function insertAtCaret(input, text) {
    if (!input || !text) return;
    if (input.isContentEditable) {
      input.focus();
      var selection = global.getSelection && global.getSelection();
      if (selection && selection.rangeCount) {
        var range = selection.getRangeAt(0);
        range.deleteContents();
        var node = document.createTextNode(text);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
      } else {
        input.textContent = (input.textContent || "") + text;
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    var start = typeof input.selectionStart === "number" ? input.selectionStart : (input.value || "").length;
    var end = typeof input.selectionEnd === "number" ? input.selectionEnd : start;
    var value = input.value || "";
    input.value = value.slice(0, start) + text + value.slice(end);
    var caret = start + text.length;
    try { input.setSelectionRange(caret, caret); } catch (e) { /* number inputs etc. */ }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  global.PulseEmoji = {
    // data
    load: load,
    loaded: loaded,
    allEmoji: allEmoji,
    emojiDataVersion: emojiDataVersion,
    emojiByCategory: emojiByCategory,
    findEmoji: findEmoji,
    emojiA11yLabel: emojiA11yLabel,
    searchEmoji: searchEmoji,
    applyTone: applyTone,
    // recents / tone
    getRecentEmoji: getRecentEmoji,
    recordRecentEmoji: recordRecentEmoji,
    getSkinTonePreference: getSkinTonePreference,
    setSkinTonePreference: setSkinTonePreference,
    // grapheme
    splitEmojiClusters: splitEmojiClusters,
    countEmojiClusters: countEmojiClusters,
    isSingleEmoji: isSingleEmoji,
    isEmojiCodePoint: isEmojiCodePoint,
    stripSkinTone: stripSkinTone,
    // picker
    open: openPicker,
    close: closePicker,
    isOpen: isOpen,
    attachToInput: attachToInput,
    insertAtCaret: insertAtCaret,
    // taxonomy
    EMOJI_CATEGORIES: EMOJI_CATEGORIES,
    CATEGORY_ICONS: CATEGORY_ICONS,
    CATEGORY_LABELS: CATEGORY_LABELS,
    QUICK_REACTIONS: QUICK_REACTIONS,
    COLUMNS: COLUMNS,
    CELL: CELL
  };

  /* The 514KB dataset stays lazy. The 9KB stylesheet cannot: it now carries
   * `.pulse-emoji-field` / `.pulse-emoji-trigger`, which style the button
   * BEFORE anyone clicks it. Injected on open only, every trigger on the site
   * would render as whatever the host page's bare `button` rule says -- a
   * 44px-tall bordered block sitting on top of the input -- until first use,
   * and then silently reflow. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensureStyles, { once: true });
  } else {
    ensureStyles();
  }
})(typeof window !== "undefined" ? window : this);
