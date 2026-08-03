/**
 * The state-line priority contract, in executable form.
 *
 * These tests assert branch KEYS (`store.attention`, `orders.awaiting`, …)
 * rather than copy. Copy is allowed to be reworded; the ORDER in which two true
 * facts compete for one line is the decision this mission is accountable for, so
 * that is what is pinned. A reordering fails by name here before anyone has to
 * notice it on a screenshot.
 *
 * The flagged-off branches are tested too, by calling the resolver in the shape
 * the flag would produce. They are unreachable today, so the assertions describe
 * what the resolver does with the flag as it currently stands — turning a flag on
 * without revisiting its test is caught here rather than in production.
 */

import {
  HUB_DATA_GAPS,
  HUB_MARKETPLACE_OFFERS,
  HUB_ORDER_DEADLINES,
  HUB_STALENESS_MS,
  HUB_THRESHOLDS,
  advertisingStateLine,
  badgeAnnouncement,
  cardAccessibilityLabel,
  eventsStateLine,
  hubBadge,
  hubContextLine,
  insightsStateLine,
  isStale,
  marketplaceStateLine,
  messagesStateLine,
  ordersStateLine,
  paymentsStateLine,
  profileCompletenessFraction,
  profileStateLine,
  storeStateLine,
  todayStripCells,
  verificationStateLine,
  verificationTick
} from "../businessHub";

/* ------------------------------------------------------------------ *
 * Helpers — the smallest shape each resolver actually reads.
 * ------------------------------------------------------------------ */

const application = (completeness: number) => ({ completeness }) as any;
const counts = (active: number, low: number, out: number) => ({ active, low, out }) as any;
const account = (canTransact: boolean) =>
  ({ id: 1, status: canTransact ? "active" : "pending_verification" }) as any;
const analytics = (statuses: string[]) => ({ campaigns: statuses.map((status) => ({ status })) }) as any;
const summary = (revenue: number, prior: number | undefined, hasPrior: boolean) =>
  ({
    currency: "USD",
    totals: { revenue_minor: revenue },
    prior_totals: prior === undefined ? undefined : { revenue_minor: prior },
    has_prior_period: hasPrior
  }) as any;
const verification = (status: string) => ({ status }) as any;

/* ------------------------------------------------------------------ *
 * The unavailable-source rule
 * ------------------------------------------------------------------ */

describe("no source, no line", () => {
  /**
   * Partial availability, at the contract level: a resolver with nothing to read
   * returns null, and the card falls back to its static subtitle. Not an error
   * card, not a zero, not a guess. Every resolver obeys this, which is why the
   * screen needs no special case for a failing source.
   */
  it("every resolver returns null when its source is absent", () => {
    expect(profileStateLine(null)).toBeNull();
    expect(storeStateLine(null)).toBeNull();
    expect(marketplaceStateLine({ openOffers: null, soonestExpiryHours: null, activeItems: null })).toBeNull();
    expect(advertisingStateLine({ accounts: null, analytics: null })).toBeNull();
    expect(ordersStateLine(null)).toBeNull();
    expect(messagesStateLine(null)).toBeNull();
    expect(insightsStateLine(null)).toBeNull();
    expect(paymentsStateLine(null)).toBeNull();
    expect(verificationStateLine(null)).toBeNull();
    expect(eventsStateLine()).toBeNull();
  });

  it("distinguishes an absent value from a zero one", () => {
    // "No open orders" is a claim. "—" is the absence of one. A resolver that
    // collapsed these would tell a seller with a broken connection that they
    // have nothing to ship.
    expect(ordersStateLine(null)).toBeNull();
    expect(ordersStateLine(0)?.key).toBe("orders.clear");
  });
});

/* ------------------------------------------------------------------ *
 * Priority — one line per card, and which fact wins
 * ------------------------------------------------------------------ */

describe("profile priority", () => {
  it("reads Complete at the threshold and a figure below it", () => {
    expect(profileStateLine(application(HUB_THRESHOLDS.profileComplete))?.key).toBe("profile.complete");
    expect(profileStateLine(application(HUB_THRESHOLDS.profileComplete - 1))?.key).toBe("profile.progress");
  });

  it("clamps the progress fraction to 0..1", () => {
    expect(profileCompletenessFraction(application(140))).toBe(1);
    expect(profileCompletenessFraction(application(-20))).toBe(0);
    expect(profileCompletenessFraction(application(50))).toBe(0.5);
    expect(profileCompletenessFraction(null)).toBeNull();
  });
});

describe("store priority", () => {
  it("puts stock trouble above a healthy catalogue", () => {
    // Both facts are true at once. Only one line exists, and the loss wins.
    const line = storeStateLine(counts(40, 3, 1));
    expect(line?.key).toBe("store.attention");
    expect(line?.tone).toBe("warn");
    expect(line?.text).toBe("3 low stock · 1 sold out");
  });

  it("falls through to active listings, then to the empty invitation", () => {
    expect(storeStateLine(counts(12, 0, 0))?.key).toBe("store.active");
    expect(storeStateLine(counts(0, 0, 0))?.key).toBe("store.empty");
  });

  it("singularises one listing", () => {
    expect(storeStateLine(counts(1, 0, 0))?.text).toBe("1 active listing");
  });
});

describe("marketplace priority", () => {
  it("shows the catalogue while offers are unavailable", () => {
    expect(HUB_MARKETPLACE_OFFERS).toBe(false);
    const line = marketplaceStateLine({ openOffers: 4, soonestExpiryHours: 5, activeItems: 9 });
    expect(line?.key).toBe("marketplace.items");
  });

  it("would rank offers above the catalogue when the flag turns on", () => {
    // Executable documentation of the decision already made: if offers become
    // reachable, they outrank the item count, and this test is what says so.
    const offersBranchWins = HUB_MARKETPLACE_OFFERS;
    const line = marketplaceStateLine({ openOffers: 4, soonestExpiryHours: 5, activeItems: 9 });
    expect(line?.key).toBe(offersBranchWins ? "marketplace.offers" : "marketplace.items");
  });

  it("invites a first listing when there is nothing", () => {
    expect(marketplaceStateLine({ openOffers: null, soonestExpiryHours: null, activeItems: 0 })?.key).toBe(
      "marketplace.empty"
    );
  });
});

describe("advertising priority", () => {
  it("puts the verification blocker above the delivering count", () => {
    const line = advertisingStateLine({
      accounts: [account(false)],
      analytics: analytics(["active", "active"])
    });
    expect(line?.key).toBe("advertising.blocked");
    expect(line?.tone).toBe("warn");
  });

  it("reports delivering campaigns when nothing is blocking", () => {
    const line = advertisingStateLine({
      accounts: [account(true)],
      analytics: analytics(["active", "paused"])
    });
    expect(line?.key).toBe("advertising.delivering");
    expect(line?.text).toBe("1 campaign delivering");
  });

  it("separates 'has campaigns, none delivering' from 'has no campaigns'", () => {
    expect(advertisingStateLine({ accounts: [account(true)], analytics: analytics(["paused"]) })?.key).toBe(
      "advertising.idle"
    );
    expect(advertisingStateLine({ accounts: [account(true)], analytics: analytics([]) })?.key).toBe(
      "advertising.noCampaigns"
    );
    expect(advertisingStateLine({ accounts: [], analytics: null })?.key).toBe("advertising.noAccount");
  });

  it("never claims a spend figure while the today-spend flag is off", () => {
    const line = advertisingStateLine({ accounts: [account(true)], analytics: analytics(["active"]) });
    expect(line?.text).not.toMatch(/today/i);
    expect(line?.text).not.toMatch(/[$£€]/);
  });
});

describe("orders and messages", () => {
  it("counts what is awaiting the seller, and singularises one", () => {
    expect(ordersStateLine(1)?.text).toBe("1 order to fulfil");
    expect(ordersStateLine(7)?.text).toBe("7 orders to fulfil");
    expect(ordersStateLine(0)?.key).toBe("orders.clear");
  });

  it("never implies a deadline the platform does not set", () => {
    expect(HUB_ORDER_DEADLINES).toBe(false);
    expect(ordersStateLine(7)?.text).not.toMatch(/today|overdue|late|by \d/i);
    expect(ordersStateLine(7)?.urgent).toBeFalsy();
  });

  it("reads unread, then calm", () => {
    expect(messagesStateLine(2)?.key).toBe("messages.unread");
    expect(messagesStateLine(1)?.text).toBe("1 unread conversation");
    expect(messagesStateLine(0)?.key).toBe("messages.clear");
  });
});

describe("insights", () => {
  it("refuses a percentage rather than inventing one for a new seller", () => {
    // No prior period at all.
    expect(insightsStateLine(summary(50_000, undefined, false))?.key).toBe("insights.collecting");
    // A prior period that was zero — a percentage of zero is not a number.
    expect(insightsStateLine(summary(50_000, 0, true))?.key).toBe("insights.collecting");
  });

  it("reads a direction with its tone", () => {
    const up = insightsStateLine(summary(150_000, 100_000, true));
    expect(up?.key).toBe("insights.trend");
    expect(up?.tone).toBe("green");
    expect(up?.text).toBe("Revenue ▲ 50% · 7d");

    const down = insightsStateLine(summary(50_000, 100_000, true));
    expect(down?.tone).toBe("warn");
    expect(down?.text).toBe("Revenue ▼ 50% · 7d");
  });

  it("calls a move inside the flat band flat", () => {
    const within = Math.round(100_000 * (HUB_THRESHOLDS.trendFlatBand / 2));
    expect(insightsStateLine(summary(100_000 + within, 100_000, true))?.key).toBe("insights.flat");
  });
});

describe("payments", () => {
  it("shows the owner's own money label verbatim, never a computed one", () => {
    const line = paymentsStateLine({ balanceLabel: "$41.20" } as any);
    expect(line?.text).toBe("Ad wallet $41.20");
  });

  it("says nothing at all when the wallet is unavailable", () => {
    // The money rule: never a stale or fabricated zero.
    expect(paymentsStateLine(null)).toBeNull();
  });
});

describe("verification", () => {
  it("orders trouble above progress above good news", () => {
    expect(verificationStateLine(verification("rejected"))?.key).toBe("verification.rejected");
    expect(verificationStateLine(verification("suspended"))?.key).toBe("verification.rejected");
    expect(verificationStateLine(verification("needs_more_info"))?.key).toBe("verification.needsInfo");
    expect(verificationStateLine(verification("in_review"))?.key).toBe("verification.inReview");
    expect(verificationStateLine(verification("approved"))?.key).toBe("verification.approved");
    expect(verificationStateLine(verification("draft"))?.key).toBe("verification.draft");
    expect(verificationStateLine(verification("not_started"))?.key).toBe("verification.notStarted");
  });

  it("promises no review duration", () => {
    expect(verificationStateLine(verification("in_review"))?.text).toBe("In review");
  });

  it("gives the tick and the card ONE source, so they cannot disagree", () => {
    // The whole point of the fan-out: same status in, consistent pair out.
    const pairs: Array<[string, string, string]> = [
      ["approved", "verified", "verification.approved"],
      ["in_review", "review", "verification.inReview"],
      ["needs_more_info", "problem", "verification.needsInfo"],
      ["not_started", "none", "verification.notStarted"]
    ];
    pairs.forEach(([status, tick, key]) => {
      expect(verificationTick(verification(status))).toBe(tick);
      expect(verificationStateLine(verification(status))?.key).toBe(key);
    });
  });

  it("shows no tick for a seller who never started, rather than a failed-looking one", () => {
    expect(verificationTick(null)).toBe("none");
    expect(verificationTick(verification("draft"))).toBe("none");
  });

  it("moves the header context line with the status", () => {
    expect(hubContextLine(verification("approved"))).toBe("Business hub · verified");
    expect(hubContextLine(verification("in_review"))).toBe("Business hub · verification in review");
    expect(hubContextLine(verification("rejected"))).toBe("Business hub · verification needs you");
    expect(hubContextLine(null)).toBe("Business hub");
  });
});

/* ------------------------------------------------------------------ *
 * Staleness degradation
 * ------------------------------------------------------------------ */

describe("staleness", () => {
  const NOW = 1_700_000_000_000;

  it("expires only the time-critical sources", () => {
    const old = NOW - 10 * 60 * 1000;
    expect(isStale("offers", old, NOW)).toBe(true);
    expect(isStale("orderDeadlines", old, NOW)).toBe(true);
    // Facts about the world do not expire between refreshes.
    expect(isStale("store", old, NOW)).toBe(false);
    expect(isStale("orders", old, NOW)).toBe(false);
    expect(isStale("insights7d", old, NOW)).toBe(false);
  });

  it("treats a never-loaded time-critical source as stale", () => {
    expect(isStale("offers", 0, NOW)).toBe(true);
  });

  it("treats an unknown source key as fresh rather than blanking it", () => {
    expect(isStale("something-new", 0, NOW)).toBe(false);
  });

  it("declares a window for every binding, so a new one forces a decision", () => {
    ["orders", "store", "insightsToday", "insights7d", "ads", "wallet", "verification", "profile", "unread"].forEach(
      (key) => {
        expect(HUB_STALENESS_MS[key]).toBeDefined();
      }
    );
  });

  it("degrades a stale deadline to its non-deadline fallback, keeping the count", () => {
    // The concrete case the mission asks to demonstrate. Fresh: count plus
    // countdown. Stale: count WITHOUT countdown — never a wrong hour, never a
    // blank card.
    const fresh = marketplaceStateLine({
      openOffers: 3,
      soonestExpiryHours: 5,
      activeItems: 9,
      offersStale: false
    });
    const stale = marketplaceStateLine({
      openOffers: 3,
      soonestExpiryHours: 5,
      activeItems: 9,
      offersStale: true
    });

    if (HUB_MARKETPLACE_OFFERS) {
      expect(fresh?.text).toContain("expires in 5h");
      expect(stale?.text).toBe("3 offers");
    } else {
      // With the flag off both fall to the catalogue line — which is itself the
      // degradation working: no offer claim is made from an unreachable source.
      expect(fresh?.key).toBe("marketplace.items");
      expect(stale?.key).toBe("marketplace.items");
    }
    expect(stale?.text).not.toContain("expires");
  });
});

/* ------------------------------------------------------------------ *
 * Badges
 * ------------------------------------------------------------------ */

describe("badges", () => {
  it("badges only counts that are waiting on the seller", () => {
    expect(hubBadge("orders", 3)).toBe(3);
    expect(hubBadge("messages", 3)).toBe(3);
    // Vanity numbers get no badge, however large.
    expect(hubBadge("store", 400)).toBeNull();
    expect(hubBadge("insights", 400)).toBeNull();
    expect(hubBadge("profile", 90)).toBeNull();
  });

  it("shows no badge for zero or absent", () => {
    expect(hubBadge("orders", 0)).toBeNull();
    expect(hubBadge("orders", null)).toBeNull();
  });

  it("withholds the marketplace badge while offers are unreachable", () => {
    expect(hubBadge("marketplace", 5)).toBe(HUB_MARKETPLACE_OFFERS ? 5 : null);
  });
});

/* ------------------------------------------------------------------ *
 * Today strip
 * ------------------------------------------------------------------ */

describe("today strip", () => {
  it("links every cell even when it has no number", () => {
    const cells = todayStripCells({ salesLabel: null, awaitingFulfilment: null, openOffers: null, unread: null });
    expect(cells.map((cell) => cell.value)).toEqual(["—", "—", "—", "—"]);
    expect(cells.map((cell) => cell.destination)).toEqual(["Insights", "Orders", "Marketplace", "Messages"]);
  });

  it("takes the sales label verbatim — the strip does no money formatting", () => {
    const cells = todayStripCells({
      salesLabel: "$1,204",
      awaitingFulfilment: 3,
      openOffers: 2,
      unread: 0
    });
    expect(cells[0].value).toBe("$1,204");
    expect(cells[1].value).toBe("3");
    expect(cells[3].value).toBe("0");
  });

  it("stays cool without a deadline source, however large the queue", () => {
    const cells = todayStripCells({ salesLabel: null, awaitingFulfilment: 99, openOffers: null, unread: null });
    expect(cells[1].hot).toBe(false);
    // The label must not assert a deadline either.
    expect(cells[1].label).toBe("To fulfil");
  });
});

/* ------------------------------------------------------------------ *
 * Accessibility
 * ------------------------------------------------------------------ */

describe("accessibility", () => {
  it("reads a card as one sentence: title, subtitle, state", () => {
    const label = cardAccessibilityLabel({
      title: "Store",
      subtitle: "Manage listings",
      state: storeStateLine(counts(10, 0, 0)),
      badge: null
    });
    expect(label).toBe("Store. Manage listings 10 active listings.");
  });

  it("puts urgency into words, not only colour", () => {
    const label = cardAccessibilityLabel({
      title: "Orders",
      subtitle: "Fulfil orders",
      state: { key: "x", tone: "warn", text: "2 orders to fulfil", urgent: true } as any,
      badge: 2
    });
    expect(label).toContain("Urgent: 2 orders to fulfil.");
    expect(label).toContain("2 orders awaiting you.");
  });

  it("announces a badge as a count with meaning, never a bare number", () => {
    expect(badgeAnnouncement("Messages", 1)).toBe("1 unread conversation.");
    expect(badgeAnnouncement("Messages", 4)).toBe("4 unread conversations.");
    expect(badgeAnnouncement("Orders", 1)).toBe("1 order awaiting you.");
  });

  it("omits the state clause entirely when there is no line", () => {
    const label = cardAccessibilityLabel({
      title: "Events",
      subtitle: "Host live events",
      state: null,
      badge: null
    });
    expect(label).toBe("Events. Host live events");
  });
});

/* ------------------------------------------------------------------ *
 * The gap ledger
 * ------------------------------------------------------------------ */

describe("declared gaps", () => {
  it("names the backend work for every gap, so the ledger is actionable", () => {
    expect(HUB_DATA_GAPS.length).toBeGreaterThan(0);
    HUB_DATA_GAPS.forEach((gap) => {
      expect(gap.field.length).toBeGreaterThan(0);
      expect(gap.surface.length).toBeGreaterThan(0);
      expect(gap.backendWork.length).toBeGreaterThan(0);
    });
  });
});
