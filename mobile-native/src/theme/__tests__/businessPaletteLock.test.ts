import { readFileSync } from "fs";
import { join } from "path";

/**
 * The business surfaces are black, white and green.
 *
 * This is a product requirement, not a preference: the seller-facing surfaces
 * (Store, Marketplace, Orders, Advertising, Payments, Insights, the Business
 * hub) must read as one product, and they are reviewed side by side. A single
 * screen that keeps a blue link or a violet badge makes the whole area look
 * half-finished no matter how correct its neighbours are.
 *
 * The reason this is a test rather than a note in a docstring is that the
 * violation is invisible to every other check. Repainting these themes was a
 * ~60-value sweep across eight files; a `git revert` of one hunk, a merge that
 * resolves the wrong way, or a new token copied from a screen outside this
 * family all reintroduce the old hues while typecheck, lint and every screen
 * test stay green. Nothing but a colour audit catches it, and a colour audit
 * performed by a human happens once.
 *
 * WHAT IS ALLOWED, and why the allowances are shaped this way:
 *
 *   • greens and near-greens — the brand accent and every status/semantic green
 *   • neutrals — white, the page gray, hairlines, the near-black header, and the
 *     gray that Marketplace and content-promotion fell back to when violet was
 *     removed. Note that the business grays are *cool* (#ADB1B8, #C7CDD3): their
 *     blue channel leads. That is not a violation, and it is the reason this
 *     test cannot simply ban "blue leads".
 *   • warm hues — amber/gold for money and warnings, red for destructive. These
 *     were never part of the lock; "black/white/green" names the chrome and the
 *     primary accent, and a warning that is not warm is not a warning.
 *
 * WHAT IS BANNED: a colour whose blue channel leads AND whose saturation clears
 * `SATURATION_FLOOR`. That is the family the sweep removed — the reference navy,
 * the Store blue, the analytics blue, the Marketplace violet, the cyan
 * "scheduled" dot. Violets need no separate rule: a violet is blue-led by
 * definition, and adding one only misfires on the warm pinks in the red washes
 * (#FBE9EC has red leading, so it is correctly left alone).
 *
 * THE LIMIT OF THIS TEST, stated plainly because it would otherwise be mistaken
 * for coverage it does not have: below `SATURATION_FLOOR` a faint blue tint and
 * a faint cool gray are the *same colour* to both the eye and the arithmetic.
 * #EEF3F8 — the palest blue fill the sweep removed — is 4% saturated, less than
 * the 6% of #ADB1B8, a gray this palette keeps. No threshold can separate those
 * two, so this test does not try. The near-invisible washes (#EEF3F8, #F7F4FC,
 * #F6F3FB) were cleaned up by hand; what is mechanically guarded from here on is
 * every blue and violet strong enough for a seller to actually see.
 *
 * Comments are stripped before scanning. The docstrings in these files quote
 * the hexes they replaced (`#131A22`, `#007185`, `#2B6DA8`) on purpose — that
 * history is worth keeping, and a test that forbade naming the old colour would
 * push people into deleting the explanation instead.
 */

/**
 * Every theme module a business/seller surface reads AND owns end to end, so the
 * whole file can be scanned.
 *
 * Two more business palettes exist that cannot be scanned whole, because they
 * share a file with something outside the lock. They get their own describe
 * block at the bottom — see `PARTIAL` there for what and why. Anything added
 * here must be a file where every colour is a business colour.
 */
const BUSINESS_THEMES = [
  "storeLight.ts",
  "marketplaceLight.ts",
  "marketplaceCheckoutDark.ts",
  "ordersLight.ts",
  "insightsLight.ts",
  "paymentsLight.ts",
  "adsLight.ts",
  "hubLight.ts",
  // The seller's commerce inbox (CommerceInboxScreen + components/messages/*).
  // Missed by the original sweep: it is named for Messages, so it read as the
  // consumer Messenger tab, but the dark Messenger does not use it and every
  // one of its consumers is the Business "Sections" inbox.
  "messagesLight.ts"
];

/**
 * HSV saturation at which a blue-led colour stops being a cool neutral and
 * starts being a blue.
 *
 * Saturation rather than raw channel spread, and the reason is the dark end of
 * the palette. The reference navy #131A22 spans only 15 of 255 between its
 * highest and lowest channel — *less* than the light cool gray #C7CDD3, which
 * spans 12 and is a colour this palette keeps. Absolute spread therefore cannot
 * tell a navy from a gray at all. Divided by the brightest channel the two come
 * apart immediately: the navy is 44% saturated, the gray 6%.
 *
 * 0.30 sits in an empty gap measured from the palette rather than guessed. The
 * cool neutrals that stay top out at 17% (#0F1012); the hues that went start at
 * 44% (#131A22) and run to 74% (#2B6DA8). The gap is asserted below so a future
 * edit to this number has to answer for both sides of it.
 */
const SATURATION_FLOOR = 0.3;

type Rgb = { r: number; g: number; b: number; source: string };

/** Strip block and line comments so historical hexes in prose do not count. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

function parseColours(source: string): Rgb[] {
  const out: Rgb[] = [];

  const hex = /#([0-9a-fA-F]{6})\b/g;
  for (let m = hex.exec(source); m; m = hex.exec(source)) {
    const v = m[1];
    out.push({
      r: parseInt(v.slice(0, 2), 16),
      g: parseInt(v.slice(2, 4), 16),
      b: parseInt(v.slice(4, 6), 16),
      source: m[0]
    });
  }

  // rgba() literals are used for scrims and washes and hide colour just as well.
  const rgba = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/g;
  for (let m = rgba.exec(source); m; m = rgba.exec(source)) {
    out.push({ r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), source: m[0] + ")" });
  }

  return out;
}

/** HSV saturation: (max − min) / max. 0 for any gray, black included. */
const saturation = ({ r, g, b }: Rgb): number => {
  const max = Math.max(r, g, b);
  if (max === 0) return 0;
  return (max - Math.min(r, g, b)) / max;
};

/** Blue leads. Covers true blues, navies, cyans and violets alike. */
const isBlueLed = ({ r, g, b }: Rgb): boolean => b > g && b > r;

const banned = (c: Rgb): boolean => isBlueLed(c) && saturation(c) >= SATURATION_FLOOR;

function read(file: string): Rgb[] {
  const path = join(__dirname, "..", file);
  return parseColours(stripComments(readFileSync(path, "utf8")));
}

describe("the classifier the lock depends on", () => {
  // A predicate that is quietly wrong would pass every assertion below while
  // checking nothing. These are the colours the sweep actually removed and the
  // ones it actually kept, so the two lists are the real fixed points.
  it("flags the hues the black/white/green lock removed", () => {
    const removed = [
      "#131A22", // reference navy header
      "#232F3E", // navy gradient end
      "#2B6DA8", // Store blue
      "#3FA3D1", // light blue
      "#3E6DB5", // insights axis blue
      "#6B4FA3", // Marketplace violet
      "#7C4DDB", // promotion violet
      "#9B7FD4", // light violet
      "#4FC3F7", // cyan scheduled dot
      "#5B8DEF", // premium "hold" blue
      "#B3A6FF", // checkout dark lavender badge
      "#8ECBFF" // checkout dark blue badge
    ];
    for (const hex of removed) {
      const [c] = parseColours(hex);
      expect({ hex, banned: banned(c) }).toEqual({ hex, banned: true });
    }
  });

  /**
   * The honest boundary. These three WERE removed by the sweep, and this test
   * would not have caught any of them — they are numerically indistinguishable
   * from the cool grays the palette keeps. Asserting that explicitly is better
   * than leaving a reader to assume the scan is exhaustive; if someone later
   * tightens `SATURATION_FLOOR` far enough to catch these, this test tells them
   * immediately that they have also started rejecting #ADB1B8 and #C7CDD3.
   */
  it("does NOT catch the near-invisible tints, which is a known limit", () => {
    for (const hex of ["#EEF3F8", "#F7F4FC", "#F6F3FB"]) {
      const [c] = parseColours(hex);
      expect({ hex, banned: banned(c) }).toEqual({ hex, banned: false });
      expect(saturation(c)).toBeLessThan(SATURATION_FLOOR);
    }
  });

  it("permits the greens, neutrals and warm hues the lock keeps", () => {
    const kept = [
      "#2EE6A8", // brand green
      "#22C48D", // brand green, deep
      "#067D62", // status success
      "#0A7050", // link / analytics green
      "#2A8168", // held / escrow green
      "#FFFFFF",
      "#0B0B0C", // black header
      "#141518", // status strip
      "#EAEDED", // page
      "#D5D9D9", // hairline
      "#565959", // muted text
      "#4A5250", // Marketplace / promotion gray
      "#C7CDD3", // on-dark muted — a COOL gray; blue leads and that is fine
      "#ADB1B8", // secondary button border — likewise
      "#0F1012", // checkout dark card — a near-black with the blue taken out
      "#C7511F", // warning
      "#B12704", // error
      "#FFA41C", // review stars
      "#FFD97A", // money gold
      "#FBE9EC" // the live-badge wash: pink, red-led, not a violet
    ];
    for (const hex of kept) {
      const [c] = parseColours(hex);
      expect({ hex, banned: banned(c) }).toEqual({ hex, banned: false });
    }
  });

  it("sees through rgba() as well as hex", () => {
    const [violetScrim] = parseColours("rgba(124, 77, 255, 0.16)");
    expect(banned(violetScrim)).toBe(true);
    const [greenScrim] = parseColours("rgba(46, 230, 168, 0.16)");
    expect(banned(greenScrim)).toBe(false);
  });

  /**
   * `CHROMA_FLOOR` sits in an empty gap, not on a knife edge. If a future edit
   * moves it, this test states what the move has to respect: every cool gray the
   * palette keeps must stay below it, and every real blue must stay well above.
   */
  it("puts the floor in the gap between the cool neutrals and the real blues", () => {
    const sat = (hex: string) => saturation(parseColours(hex)[0]);
    const coolNeutrals = ["#C7CDD3", "#ADB1B8", "#0F1012", "#070708"].map(sat);
    const realBlues = ["#2B6DA8", "#6B4FA3", "#4FC3F7", "#131A22", "#232F3E"].map(sat);

    expect(Math.max(...coolNeutrals)).toBeLessThan(SATURATION_FLOOR);
    expect(Math.min(...realBlues)).toBeGreaterThan(SATURATION_FLOOR);
    // And the gap is wide: the floor is not wedged against either side of it.
    expect(Math.min(...realBlues) - Math.max(...coolNeutrals)).toBeGreaterThan(0.2);
  });
});

describe("business themes carry no blue or violet", () => {
  it("scans the themes this lock is supposed to cover", () => {
    // Guards against the whole suite passing because a rename made every file
    // unreadable, or because someone trimmed the list instead of a colour.
    expect(BUSINESS_THEMES.length).toBe(9);
    for (const file of BUSINESS_THEMES) {
      expect(read(file).length).toBeGreaterThan(0);
    }
  });

  it.each(BUSINESS_THEMES)("%s", (file) => {
    const offenders = read(file)
      .filter(banned)
      .map((c) => `${c.source} rgb(${c.r},${c.g},${c.b})`);
    expect(offenders).toEqual([]);
  });
});

/**
 * The two business palettes that live in a file with a non-business neighbour.
 *
 * A whole-file scan is the right shape for the nine above and the wrong shape
 * for these two, so each gets the narrowest scan that still covers it. The risk
 * a narrow scan carries is that it silently stops covering anything — a rename,
 * a moved brace — so both extractors assert they found something first.
 */
describe("the business palettes that share a file", () => {
  /**
   * `logiNexus.colors.businessLive` — the dark palette behind
   * `BusinessProfileScreen` and `BusinessBuyerPreviewScreen`, the two screens in
   * the commerce family that are not on `storeLight`'s white page.
   *
   * It sits inside `logiNexus.ts`, whose other namespaces are the app-wide
   * LogiNexus chrome and are emphatically NOT locked — the home surface is
   * indigo and cyan by design. So the scan is the `businessLive` object only,
   * brace-matched out of the source.
   */
  function businessLiveBlock(): string {
    const source = stripComments(readFileSync(join(__dirname, "..", "logiNexus.ts"), "utf8"));
    const start = source.indexOf("businessLive: {");
    if (start < 0) return "";
    let depth = 0;
    for (let i = source.indexOf("{", start); i < source.length; i += 1) {
      if (source[i] === "{") depth += 1;
      if (source[i] === "}") {
        depth -= 1;
        if (depth === 0) return source.slice(start, i + 1);
      }
    }
    return "";
  }

  it("still finds the businessLive block it is scanning", () => {
    const block = businessLiveBlock();
    // Cheap proof the brace-match landed on the right object and did not run
    // away into the rest of the file.
    expect(block).toContain("panelRaised:");
    expect(block).toContain("overlayScrim:");
    expect(block).not.toContain("typography");
    expect(parseColours(block).length).toBeGreaterThan(10);
  });

  it("logiNexus.colors.businessLive carries no blue or violet", () => {
    const offenders = parseColours(businessLiveBlock())
      .filter(banned)
      .map((c) => `${c.source} rgb(${c.r},${c.g},${c.b})`);
    expect(offenders).toEqual([]);
  });

  /**
   * `eventsLight.ts` is read by two screens on opposite sides of the lock:
   * `EventsManagerScreen` (a seller surface, locked) and `ActivityScreen` (the
   * app-wide notification centre, not locked). The tokens divide cleanly along
   * that line, so the file keeps a blue and a violet on purpose.
   *
   * The exemption is carved out BY NAME — the `ACTIVITY_TYPE` object and the
   * three `unread` keys — and everything else in the file is scanned normally.
   *
   * Not by file position, which was the first attempt and was worse than
   * nothing: the Events-manager `status` block sits *after* `ACTIVITY_TYPE` in
   * the source, so a "scan everything above it" slice skipped the violet
   * `status.promoted` that this whole exercise found. The exclusions have to
   * follow the tokens, not the line numbers.
   */
  function splitEventsLight() {
    const source = stripComments(readFileSync(join(__dirname, "..", "eventsLight.ts"), "utf8"));
    const start = source.indexOf("export const ACTIVITY_TYPE");
    const end = source.indexOf("}", source.indexOf("system:", start));
    const activity = start < 0 || end < 0 ? "" : source.slice(start, end + 1);
    const locked = source
      .replace(activity, "")
      // `bg.unread`, `border.unreadEdge` and `text.unread` — the Activity row
      // tint, its left edge and its bold timestamp. Only NotificationRow and
      // ActivityHeader read them.
      .split("\n")
      .filter((line) => !/^\s*(unread|unreadEdge):/.test(line))
      .join("\n");
    return { activity, locked };
  }

  it("still finds the two regions it is splitting eventsLight.ts into", () => {
    const { activity, locked } = splitEventsLight();
    expect(activity).toContain("marketplace:");
    expect(activity).toContain("system:");
    expect(activity).not.toContain("eventsLight");
    // The locked side must still contain the Events-manager tokens, or the
    // exclusion has eaten the thing it is meant to be guarding.
    expect(locked).toContain("EVENT_COVER");
    expect(locked).toContain("DATE_TILE");
    expect(locked).toContain("promoted:");
    expect(locked).toContain("upcomingBand:");
  });

  /**
   * The assertion that would have caught the violet `status.promoted` the sweep
   * left behind on a locked surface. It is now a dark gold.
   */
  it("everything in eventsLight.ts outside the Activity tokens is clean", () => {
    const offenders = parseColours(splitEventsLight().locked)
      .filter(banned)
      .map((c) => `${c.source} rgb(${c.r},${c.g},${c.b})`);
    expect(offenders).toEqual([]);
  });

  /**
   * And the exempt side, pinned as an exact set. A new blue in `ACTIVITY_TYPE`
   * fails because the set grows; removing one fails too, which is deliberate —
   * if the Activity feed is ever recoloured, that is a product decision about
   * the notification centre and someone should record it here rather than have
   * a test quietly accept it.
   */
  it("the Activity exemption is exactly these two hues", () => {
    const exempt = [...new Set(parseColours(splitEventsLight().activity).filter(banned).map((c) => c.source))].sort();
    expect(exempt).toEqual([
      // ACTIVITY_TYPE.orders — the Store blue, on notification rows.
      "#2B6DA8",
      // ACTIVITY_TYPE.marketplace — the Marketplace violet, likewise.
      "#6D4AC4"
    ]);
  });
});
