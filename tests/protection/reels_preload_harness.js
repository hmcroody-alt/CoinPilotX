/**
 * Behavioral contract harness for the Reels preload window.
 *
 * The production functions (`preloadNextReel`, `releaseFarReelMedia`, `warmReelPoster`,
 * `reelCards`, `primaryReelVideo`, `logReelAudioState`) are extracted VERBATIM from
 * bot.py by tests/protection/test_media_playback_contract.py and concatenated ahead of
 * this file, so what runs below is the real shipping implementation — not a
 * reimplementation of it and not a grep for its source text.
 *
 * Everything this harness stubs is a browser primitive (elements, videos, images,
 * console). Nothing about the preload policy itself is stubbed: which neighbours are
 * warmed, at what `preload` level, how many network fetches that costs, and which cards
 * get torn down are all decided by the production code and merely observed here.
 *
 * Reads a JSON scenario list on argv[2]; prints one JSON verdict per scenario.
 */

'use strict';

// --- accounting -------------------------------------------------------------
// HTMLMediaElement.load() means two different things depending on `preload`, and the
// difference is the whole point of the policy under test:
//
//   preload != 'none'  -> the element begins fetching, and settles with data buffered.
//                         This COSTS NETWORK. Counted as a FETCH.
//   preload == 'none'  -> the element is reset and any buffered data is discarded, and
//                         no further bytes are requested. This FREES memory and costs
//                         nothing. Counted as a DROP.
//
// Conflating the two would let a regression that thrashes teardown look identical to
// one that thrashes downloads, so they are counted separately.
let FETCHES = 0;
let DROPS = 0;
let IMAGES = 0;   // poster warms

const HAVE_NOTHING = 0;
const HAVE_ENOUGH_DATA = 4;

class El {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.dataset = {};
    this.children = [];
    this.parent = null;
    this.classes = new Set();
    this.attrs = Object.assign({}, attrs);
    // <video> surface
    this.preload = attrs.preload || '';
    this.autoplay = !!attrs.autoplay;
    this.playsInline = false;
    this.muted = attrs.muted !== undefined ? !!attrs.muted : true;
    this.defaultMuted = true;
    this.volume = 0;
    this.paused = attrs.paused === undefined ? true : !!attrs.paused;
    this.readyState = attrs.readyState === undefined ? HAVE_NOTHING : attrs.readyState;
    this.poster = attrs.poster || '';
    this.src = attrs.src || '';
    this.fetchCount = 0;
    this.dropCount = 0;
    this.pauseCount = 0;
    this.playCount = 0;
    this.currentTime = 0;
  }
  append(child) { child.parent = this; this.children.push(child); return child; }
  get nextElementSibling() {
    if (!this.parent) return null;
    const i = this.parent.children.indexOf(this);
    return this.parent.children[i + 1] || null;
  }
  get previousElementSibling() {
    if (!this.parent) return null;
    const i = this.parent.children.indexOf(this);
    return i <= 0 ? null : this.parent.children[i - 1];
  }
  descendants() {
    return this.children.flatMap((c) => [c, ...c.descendants()]);
  }
  matchesOne(sel) {
    sel = sel.trim();
    if (sel === 'video') return this.tagName === 'VIDEO';
    if (sel === 'img[loading="lazy"]') {
      return this.tagName === 'IMG' && this.attrs.loading === 'lazy';
    }
    if (sel === '.reel-card') return this.classes.has('reel-card');
    if (sel === '[data-reel-media]') return this.dataset.reelMedia !== undefined;
    if (sel === '[data-live-reel-media]') return this.dataset.liveReelMedia !== undefined;
    if (sel === '[data-reel-media] video') {
      return this.tagName === 'VIDEO' && this.parent
        && this.parent.dataset.reelMedia !== undefined;
    }
    if (sel === '.pulse-reel-media-shell video') {
      return this.tagName === 'VIDEO' && this.parent
        && this.parent.classes.has('pulse-reel-media-shell');
    }
    if (sel === 'video.reel-media') {
      return this.tagName === 'VIDEO' && this.classes.has('reel-media');
    }
    throw new Error('harness: unsupported selector ' + JSON.stringify(sel));
  }
  querySelectorAll(sel) {
    const parts = sel.split(',');
    return this.descendants().filter((n) => parts.some((p) => n.matchesOne(p)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() {
    const r = this._rect || { top: 10000, bottom: 10001 };
    return { top: r.top, bottom: r.bottom, height: r.bottom - r.top };
  }
  load() {
    if (this.preload === 'none') {
      this.dropCount += 1; DROPS += 1;
      this.readyState = HAVE_NOTHING;
    } else {
      this.fetchCount += 1; FETCHES += 1;
      this.readyState = HAVE_ENOUGH_DATA;
    }
  }
  pause() { this.paused = true; this.pauseCount += 1; }
  play() { this.paused = false; this.playCount += 1; return Promise.resolve(); }
}

// --- scenario construction --------------------------------------------------
const VIEW_H = 800;

function buildFeed(n, opts = {}) {
  FETCHES = 0;
  DROPS = 0;
  IMAGES = 0;
  const feed = new El('div');
  for (let i = 0; i < n; i += 1) {
    const card = new El('article');
    card.classes.add('reel-card');
    card.dataset.reelId = 'r' + i;
    const wrap = card.append(new El('div'));
    wrap.dataset.reelMedia = '1';
    wrap.dataset.reelPoster = 'https://cdn.test/poster' + i + '.jpg';
    const video = wrap.append(new El('video', {
      poster: wrap.dataset.reelPoster,
      readyState: opts.readyState === undefined ? HAVE_NOTHING : opts.readyState,
      preload: opts.preload,
    }));
    video.classes.add('reel-media');
    // A lazy thumbnail with a deferred src, as the feed markup ships it.
    const img = card.append(new El('img', { loading: 'lazy' }));
    img.dataset.src = 'https://cdn.test/thumb' + i + '.jpg';
    feed.append(card);
  }
  return feed;
}

function layout(feed, activeIndex) {
  // Only the active card straddles the viewport midpoint, matching the real
  // scroll geometry the production code reads.
  feed.children.forEach((card, i) => {
    const offset = (i - activeIndex) * VIEW_H;
    card._rect = { top: offset, bottom: offset + VIEW_H };
  });
}

function snapshot(feed) {
  return feed.children.map((card) => {
    const v = card.querySelector('video');
    const img = card.querySelector('img[loading="lazy"]');
    return {
      id: card.dataset.reelId,
      window: card.dataset.reelWindow || '',
      posterWarmed: card.dataset.reelPosterWarmed === '1',
      preloadSuccess: card.dataset.preloadSuccess,
      video: {
        preload: v.preload, autoplay: v.autoplay, playsInline: v.playsInline,
        muted: v.muted, paused: v.paused, readyState: v.readyState,
        fetches: v.fetchCount, drops: v.dropCount,
        pauses: v.pauseCount, plays: v.playCount,
      },
      img: { src: img.src, loads: img.src ? 1 : 0 },
    };
  });
}

function counters() { return { fetches: FETCHES, drops: DROPS, images: IMAGES }; }

// --- browser globals the production code touches ----------------------------
let CURRENT_FEED = null;
global.document = {
  hidden: false,
  querySelectorAll: (sel) => (CURRENT_FEED ? CURRENT_FEED.querySelectorAll(sel) : []),
};
global.innerHeight = VIEW_H;
global.Image = class { constructor() { IMAGES += 1; this.decoding = ''; this.src = ''; } };
global.console = Object.assign({}, console, { info() {}, debug() {}, warn() {} });
global.reelsSoundEnabled = false;
global.reelDebugEnabled = () => false;

// One scroll settle: exactly what syncPlayback does around the active card.
function settle(feed, i) {
  layout(feed, i);
  const warmed = preloadNextReel(feed.children[i]);
  releaseFarReelMedia(feed.children[i]);
  return warmed;
}

// --- scenarios --------------------------------------------------------------
const SCENARIOS = {
  /** Long feed, mid-scroll: current + next two prepared, nothing further. */
  window_shape() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    const warmed = settle(feed, 3);
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Auto-detects the active card from scroll geometry when not handed one. */
  window_shape_autodetect() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    layout(feed, 3);
    const warmed = preloadNextReel();
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Rapid scrolling re-enters the same active card many times. */
  rapid_scroll_idempotent() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    layout(feed, 3);
    const first = preloadNextReel(feed.children[3]);
    releaseFarReelMedia(feed.children[3]);
    const fetchesAfterFirst = FETCHES;
    const dropsAfterFirst = DROPS;
    let extra = 0;
    for (let i = 0; i < 25; i += 1) {
      extra += preloadNextReel(feed.children[3]);
      releaseFarReelMedia(feed.children[3]);
    }
    return Object.assign({ first, fetchesAfterFirst, dropsAfterFirst, extra },
                         counters(), { cards: snapshot(feed) });
  },

  /**
   * A fling that skips cards must not download the cards it flew past. Only the
   * window around wherever the scroll SETTLES may cost bytes.
   */
  fling_skips_cards() {
    const feed = buildFeed(12);
    CURRENT_FEED = feed;
    settle(feed, 0);
    const afterStart = counters();
    settle(feed, 9);              // fling straight to the far end
    const afterFling = counters();
    return { afterStart, afterFling, cards: snapshot(feed) };
  },

  /** Walking forward one card at a time never re-downloads a warmed card. */
  sequential_walk() {
    const feed = buildFeed(6);
    CURRENT_FEED = feed;
    const perStep = [];
    for (let i = 0; i < 6; i += 1) {
      const before = FETCHES;
      settle(feed, i);
      perStep.push(FETCHES - before);
    }
    return Object.assign({ perStep }, counters(), { cards: snapshot(feed) });
  },

  /** Single-card feed: no neighbours to warm, must not throw. */
  short_feed_one() {
    const feed = buildFeed(1);
    CURRENT_FEED = feed;
    const warmed = settle(feed, 0);
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Two-card feed: exactly one neighbour exists. */
  short_feed_two() {
    const feed = buildFeed(2);
    CURRENT_FEED = feed;
    const warmed = settle(feed, 0);
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Last card active: `next` and `next-next` are both absent. */
  end_of_feed() {
    const feed = buildFeed(5);
    CURRENT_FEED = feed;
    const warmed = settle(feed, 4);
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Second-to-last active: exactly one forward neighbour exists. */
  penultimate() {
    const feed = buildFeed(5);
    CURRENT_FEED = feed;
    const warmed = settle(feed, 3);
    return Object.assign({ warmed }, counters(), { cards: snapshot(feed) });
  },

  /** Empty feed: nothing active, must return 0 rather than throw. */
  empty_feed() {
    const feed = buildFeed(0);
    CURRENT_FEED = feed;
    const warmed = preloadNextReel();
    releaseFarReelMedia(undefined);
    return Object.assign({ warmed }, counters(), { cards: [] });
  },

  /**
   * Network interruption / replay: a card already holding buffered data is torn
   * down when it leaves the window, and re-armed and re-fetched on return.
   */
  release_then_replay() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    settle(feed, 1);                      // warms 2 and 3
    const afterWarm = snapshot(feed);
    const countersAfterWarm = counters();
    settle(feed, 6);                      // jump far away; 2 and 3 must be released
    const afterJump = snapshot(feed);
    const countersAfterJump = counters();
    settle(feed, 1);                      // come back; released cards must be usable
    const afterReturn = snapshot(feed);
    return { countersAfterWarm, countersAfterJump, countersAfterReturn: counters(),
             afterWarm, afterJump, afterReturn };
  },

  /**
   * Leak / cleanup check: a released card must be paused AND stripped of its
   * buffer, so an offscreen reel cannot keep a player alive or keep audio going.
   */
  release_stops_and_frees() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    settle(feed, 1);
    // Card 2 is buffered and playing, as the active reel would be.
    const playing = feed.children[2].querySelector('video');
    playing.play();
    playing.muted = false;
    const before = { paused: playing.paused, readyState: playing.readyState };
    settle(feed, 6);
    const after = {
      paused: playing.paused, readyState: playing.readyState,
      preload: playing.preload, autoplay: playing.autoplay,
      pauses: playing.pauseCount, drops: playing.dropCount,
      fetches: playing.fetchCount,
      window: feed.children[2].dataset.reelWindow,
    };
    const stillPlaying = snapshot(feed).filter((c) => !c.video.paused).map((c) => c.id);
    return { before, after, stillPlaying };
  },

  /** A media element whose load() throws must not abort the whole pass. */
  load_failure_is_contained() {
    const feed = buildFeed(6);
    CURRENT_FEED = feed;
    layout(feed, 1);
    const victim = feed.children[2].querySelector('video');
    victim.load = () => { throw new Error('simulated network failure'); };
    let threw = false;
    let warmed = 0;
    try { warmed = preloadNextReel(feed.children[1]); } catch (_) { threw = true; }
    return Object.assign({ threw, warmed }, counters(), { cards: snapshot(feed) });
  },

  /** A release whose load() throws must not abort the teardown pass either. */
  release_failure_is_contained() {
    const feed = buildFeed(8);
    CURRENT_FEED = feed;
    settle(feed, 1);
    const victim = feed.children[2].querySelector('video');
    const realLoad = victim.load.bind(victim);
    victim.load = () => { throw new Error('simulated teardown failure'); };
    let threw = false;
    try { settle(feed, 6); } catch (_) { threw = true; }
    victim.load = realLoad;
    return Object.assign({ threw }, counters(), { cards: snapshot(feed) });
  },

  /** Poster warming is once-per-card, however many passes run. */
  poster_warm_once() {
    const feed = buildFeed(6);
    CURRENT_FEED = feed;
    layout(feed, 2);
    for (let i = 0; i < 10; i += 1) preloadNextReel(feed.children[2]);
    return Object.assign({}, counters(), { cards: snapshot(feed) });
  },
};

const wanted = JSON.parse(process.argv[2] || '[]');
const out = {};
for (const name of wanted) {
  if (!SCENARIOS[name]) { out[name] = { harness_error: 'unknown scenario' }; continue; }
  try {
    out[name] = SCENARIOS[name]();
  } catch (err) {
    out[name] = { harness_error: String((err && err.stack) || err) };
  }
}
process.stdout.write(JSON.stringify(out));
