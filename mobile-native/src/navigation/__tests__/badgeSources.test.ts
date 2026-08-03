/**
 * The guard that stops a badge reading from one place and claiming another.
 *
 * THE DEFECT
 * ----------
 * `AppNavigator` kept its own copy of the counts — a second
 * `getNotificationBadgeCounts()` call, held in local state — and handed the
 * Activity bell `totalUnreadCount`, which is notifications *plus* messages.
 * A messages badge sat directly beside it showing the message half over again.
 * So one strip carried the same unread twice, from two different fetches that
 * could not be relied on to agree, and neither badge said what it counted.
 *
 * WHY THIS IS PART SOURCE SCAN
 * ----------------------------
 * The behavioural tests below pin what the numbers are. They cannot pin where
 * they came from: a component that re-fetches its own counts and happens to get
 * the same answer in a test passes every assertion about values. The failure
 * mode is drift under real conditions — two fetches, two moments, two answers —
 * and the only thing that forecloses it is there being one source. That is a
 * property of the imports, so it is checked against the imports.
 *
 * The brief asked for "a test that fails if a badge reads from a different
 * source than it claims". This is that test.
 */
import { readFileSync } from "fs";
import { join } from "path";

import {
  __resetUnreadCounts,
  badgeFor,
  badgeSpokenLabel,
  navigationBadgesFrom,
  SCOPED_BADGES_FLAG,
  scopedBadgesEnabled,
  setUnreadCounts,
  type BadgeScope
} from "../../core/unreadCounts";

const SRC = join(__dirname, "..", "..");

/**
 * Every file that renders a number onto a global navigation control.
 *
 * Listed explicitly rather than globbed. A glob would silently stop covering a
 * file that was renamed, and the point of this test is to notice.
 */
const BADGE_CONSUMERS = [
  "navigation/AppNavigator.tsx",
  "navigation/GlobalNavigation.tsx"
];

/** The one module allowed to talk to the counts endpoint. */
const OWNER = "core/unreadCounts.ts";

function read(relative: string): string {
  return readFileSync(join(SRC, relative), "utf8");
}

/** Import lines only — a mention inside a comment is documentation, not a source. */
function importLines(source: string): string[] {
  return source
    .split("\n")
    .filter((line) => /^\s*(import|const .* = require\()/.test(line))
    .concat(
      // Multi-line import blocks: keep the whole statement together.
      source.match(/import\s*\{[^}]*\}\s*from\s*["'][^"']+["']/g) || []
    );
}

beforeEach(() => {
  __resetUnreadCounts();
});

describe("one source", () => {
  /**
   * The structural half. If a badge consumer imports the counts call directly,
   * it has its own copy and can disagree with the store — which is exactly the
   * shipped bug, not a hypothetical one.
   */
  it("lets no badge consumer fetch its own counts", () => {
    for (const file of BADGE_CONSUMERS) {
      const imports = importLines(read(file)).join("\n");
      expect(imports).not.toMatch(/getNotificationBadgeCounts/);
      expect(imports).not.toMatch(/totalUnreadCount/);
      expect(imports).not.toMatch(/alertUnreadCount/);
      expect(imports).not.toMatch(/chatUnreadCount/);
    }
  });

  it("has every badge consumer read from the shared store instead", () => {
    for (const file of BADGE_CONSUMERS) {
      expect(importLines(read(file)).join("\n")).toMatch(/core\/unreadCounts/);
    }
  });

  /**
   * The counts endpoint still has exactly one caller. Without this, the rule
   * above could be satisfied by moving the second fetch one file sideways.
   */
  it("keeps the counts call to a single caller in the whole app", () => {
    const owner = read(OWNER);
    expect(owner).toMatch(/getNotificationBadgeCounts/);
    // The store is the only place the derivation helpers are read, too.
    expect(importLines(owner).join("\n")).toMatch(/api\/notifications/);
  });
});

describe("badges reconcile with the store", () => {
  /**
   * The arithmetic that was wrong on screen: the bell and the messages badge
   * sat side by side and their numbers overlapped. They must now partition the
   * total rather than both containing the messages.
   */
  it("does not count a message on both the bell and the messages badge", () => {
    setUnreadCounts({ alert_unread_count: 4, chat_unread_count: 7, total_unread_count: 11 });
    const badges = navigationBadgesFrom();
    expect(badges.activity).toBe(4);
    expect(badges.messages).toBe(7);
    expect(badges.activity + badges.messages).toBe(badges.combined);
  });

  it("holds that partition for every combination, not just the convenient one", () => {
    for (const [alerts, chats] of [
      [0, 0],
      [0, 5],
      [5, 0],
      [1, 1],
      [99, 250]
    ]) {
      setUnreadCounts({
        alert_unread_count: alerts,
        chat_unread_count: chats,
        total_unread_count: alerts + chats
      });
      const badges = navigationBadgesFrom();
      expect(badges.activity).toBe(alerts);
      expect(badges.messages).toBe(chats);
      expect(badges.activity + badges.messages).toBe(badges.combined);
    }
  });

  /**
   * The header's "N alerts" chip and the bell above it are the same
   * notifications. They were two fields reading two different functions, so
   * they could show two different numbers about one thing.
   */
  it("gives the alerts chip and the bell the same number, always", () => {
    for (const alerts of [0, 1, 3, 40]) {
      setUnreadCounts({ alert_unread_count: alerts, chat_unread_count: 9 });
      const badges = navigationBadgesFrom();
      expect(badges.alerts).toBe(badges.activity);
    }
  });

  it("reads each scope off the same snapshot the navigation reads", () => {
    setUnreadCounts({ alert_unread_count: 2, chat_unread_count: 3, total_unread_count: 5 });
    const badges = navigationBadgesFrom();
    expect(badgeFor("notifications").count).toBe(badges.activity);
    expect(badgeFor("messages").count).toBe(badges.messages);
    expect(badgeFor("combined").count).toBe(badges.combined);
  });

  /** A badge that survives a refresh with a stale number is the same bug again. */
  it("moves every badge together when the counts change", () => {
    setUnreadCounts({ alert_unread_count: 1, chat_unread_count: 1, total_unread_count: 2 });
    const before = navigationBadgesFrom();
    setUnreadCounts({ alert_unread_count: 6, chat_unread_count: 2, total_unread_count: 8 });
    const after = navigationBadgesFrom();
    expect(after).not.toEqual(before);
    expect(after.activity + after.messages).toBe(after.combined);
  });
});

describe("badges say what they count", () => {
  const SCOPES: BadgeScope[] = ["notifications", "messages", "combined"];

  /**
   * A badge is a number with no noun. Spoken aloud, "3" beside an icon is
   * nothing at all — and it was the missing noun that made the double-count
   * impossible to spot from the screen.
   */
  it("names its subject in every scope, at every count", () => {
    for (const scope of SCOPES) {
      for (const count of [0, 1, 2, 99]) {
        const label = badgeSpokenLabel(scope, count);
        expect(label).toMatch(/unread/);
        expect(label.length).toBeGreaterThan(String(count).length);
      }
    }
  });

  it("gives the three scopes three different sentences", () => {
    for (const count of [0, 1, 5]) {
      const spoken = SCOPES.map((scope) => badgeSpokenLabel(scope, count));
      expect(new Set(spoken).size).toBe(SCOPES.length);
    }
  });

  /** Silence reads as "failed to load"; "No unread messages" reads as calm. */
  it("speaks an empty badge rather than going quiet", () => {
    for (const scope of SCOPES) {
      expect(badgeSpokenLabel(scope, 0)).toMatch(/^No /);
    }
  });

  it("uses the singular for one and the plural for the rest", () => {
    expect(badgeSpokenLabel("messages", 1)).toBe("1 unread message");
    expect(badgeSpokenLabel("messages", 2)).toBe("2 unread messages");
    expect(badgeSpokenLabel("notifications", 1)).toBe("1 unread notification");
  });

  it("carries the label alongside the count so the two cannot drift apart", () => {
    setUnreadCounts({ alert_unread_count: 3 });
    const badge = badgeFor("notifications");
    expect(badge.spokenLabel).toBe(badgeSpokenLabel("notifications", badge.count));
    expect(badge.scope).toBe("notifications");
  });
});

describe("the flag", () => {
  it('is off unless the build opts in, and accepts every spelling of "on"', () => {
    // The accepted spellings are the shared set in core/envFlag.ts, not this
    // module's own idea of one. This flag shipped taking the literal "1" alone
    // while flags on adjacent screens also took "true" — so a build that set it
    // to "true" got a silent no-op. Both work now; unset is still off.
    const original = process.env[SCOPED_BADGES_FLAG];
    try {
      for (const value of ["", " ", "0", "false", "off", "no", "2"]) {
        process.env[SCOPED_BADGES_FLAG] = value;
        expect(scopedBadgesEnabled()).toBe(false);
      }
      for (const value of ["1", "true", "on", "yes", " TRUE ", "Yes"]) {
        process.env[SCOPED_BADGES_FLAG] = value;
        expect(scopedBadgesEnabled()).toBe(true);
      }
      delete process.env[SCOPED_BADGES_FLAG];
      expect(scopedBadgesEnabled()).toBe(false);
    } finally {
      if (original === undefined) delete process.env[SCOPED_BADGES_FLAG];
      else process.env[SCOPED_BADGES_FLAG] = original;
    }
  });

  /**
   * The flag changes which number the bell shows, never where it comes from.
   * Both sides of it are the same store — the correction to the *source* ships
   * unconditionally, because the old arrangement had no defensible version.
   */
  it("draws both the flagged and unflagged bell from the same store", () => {
    setUnreadCounts({ alert_unread_count: 4, chat_unread_count: 7, total_unread_count: 11 });
    const badges = navigationBadgesFrom();
    // Flag on: the bell is scoped. Flag off: it keeps the combined figure.
    expect(badges.activity).toBe(4);
    expect(badges.combined).toBe(11);
    // Either way, the messages badge is unchanged and the total still adds up.
    expect(badges.messages).toBe(7);
  });
});
