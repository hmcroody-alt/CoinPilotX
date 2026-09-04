/**
 * Stage 22 — client truth table.
 *
 * One row per state a real account can be in, asserted against the parser
 * rather than against a mock of the network, so what is under test is the
 * decision the app makes about a payload — which is the thing that was wrong
 * before, and the thing a future edit is most likely to get wrong again.
 *
 * Every payload here is shaped like the real one. `services/private_office/
 * tiers.py` returns `{user_id, effective_tier, source, status, expires_at,
 * features, verified_at, resolver_state}` and `feature_matrix.availability()`
 * returns a dict per feature, not a bare string — so the fixtures carry the
 * dict form. A fixture that quietly simplified the server's shape would pass
 * while the app failed in production.
 *
 * The two rows that matter most are the last two. An unknown tier name and an
 * unreachable resolver must both grant nothing, and they must do so without
 * claiming the member is on Free.
 */

import fs from "fs";
import path from "path";

import {
  featureAvailability,
  isEntitled,
  isMember,
  parseTierAnswer,
  tierRank,
  tierSatisfies,
  TIER_ORDER,
  UNKNOWN_TIER
} from "../canonicalTier";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");

/** A resolved payload in the server's own shape. */
function serverAnswer(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    resolver_state: "ok",
    user_id: 700,
    effective_tier: "FREE",
    source: "",
    status: "none",
    expires_at: null,
    features: {},
    verified_at: "2026-08-31T10:00:00+00:00",
    ...overrides
  };
}

/** The per-feature dict `feature_matrix.availability()` actually returns. */
function feature(featureId: string, availability: string, minimumTier: string) {
  return {
    feature_id: featureId,
    availability,
    minimum_tier: minimumTier,
    server_enforced: true,
    implementation: "IMPLEMENTED",
    note: ""
  };
}

describe("canonical tier — the ladder", () => {
  it("ranks the four tiers in the server's order", () => {
    expect(TIER_ORDER).toEqual(["FREE", "PREMIUM", "PRIVATE", "PRIVATE_OFFICE"]);
    expect(tierRank("FREE")).toBe(0);
    expect(tierRank("PREMIUM")).toBe(1);
    expect(tierRank("PRIVATE")).toBe(2);
    expect(tierRank("PRIVATE_OFFICE")).toBe(3);
  });

  it("declares the same ladder the server does", () => {
    // Read from the Python source rather than restating it. Two hand-maintained
    // copies of an ordered ladder is exactly the drift this mission exists to
    // remove; if someone inserts a tier server-side, this fails here first.
    const source = fs.readFileSync(
      path.join(REPO_ROOT, "services", "private_office", "tiers.py"),
      "utf8"
    );
    const declared = source.match(/^TIER_ORDER = \((.+)\)$/m);
    expect(declared).not.toBeNull();
    const serverOrder = (declared as RegExpMatchArray)[1]
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean)
      .map((token) => token.replace(/^TIER_/, ""));
    expect(serverOrder).toEqual([...TIER_ORDER]);
  });

  it("ranks an unrecognised tier as FREE rather than throwing", () => {
    expect(tierRank("PLATINUM")).toBe(0);
    expect(tierRank("")).toBe(0);
  });
});

describe("canonical tier — truth table", () => {
  it("FREE grants nothing above FREE", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "FREE",
        status: "none",
        features: {
          market_pulse: feature("market_pulse", "NOT_ENTITLED", "PREMIUM"),
          private_briefings: feature("private_briefings", "NOT_ENTITLED", "PRIVATE")
        }
      })
    );

    expect(answer.state).toBe("resolved");
    expect(answer.effectiveTier).toBe("FREE");
    expect(isMember(answer)).toBe(false);
    expect(tierSatisfies(answer, "FREE")).toBe(true);
    expect(tierSatisfies(answer, "PREMIUM")).toBe(false);
    expect(isEntitled(answer, "market_pulse")).toBe(false);
  });

  it("PREMIUM reaches PREMIUM and stops there", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PREMIUM",
        status: "active",
        source: "storekit",
        expires_at: "2026-09-30T00:00:00+00:00",
        features: {
          market_pulse: feature("market_pulse", "ENTITLED", "PREMIUM"),
          private_briefings: feature("private_briefings", "NOT_ENTITLED", "PRIVATE")
        }
      })
    );

    expect(isMember(answer)).toBe(true);
    expect(tierSatisfies(answer, "PREMIUM")).toBe(true);
    expect(tierSatisfies(answer, "PRIVATE")).toBe(false);
    expect(isEntitled(answer, "market_pulse")).toBe(true);
    expect(isEntitled(answer, "private_briefings")).toBe(false);
    expect(answer.expiresAt).toBe("2026-09-30T00:00:00+00:00");
  });

  it("PRIVATE reaches PREMIUM and PRIVATE but not PRIVATE_OFFICE", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PRIVATE",
        status: "active",
        source: "manual",
        features: {
          private_briefings: feature("private_briefings", "ENTITLED", "PRIVATE"),
          human_concierge: feature("human_concierge", "NOT_ENTITLED", "PRIVATE_OFFICE")
        }
      })
    );

    expect(tierSatisfies(answer, "PREMIUM")).toBe(true);
    expect(tierSatisfies(answer, "PRIVATE")).toBe(true);
    expect(tierSatisfies(answer, "PRIVATE_OFFICE")).toBe(false);
    expect(isEntitled(answer, "private_briefings")).toBe(true);
    expect(isEntitled(answer, "human_concierge")).toBe(false);
  });

  it("PRIVATE_OFFICE reaches every rung", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PRIVATE_OFFICE",
        status: "active",
        source: "manual",
        features: { human_concierge: feature("human_concierge", "ENTITLED", "PRIVATE_OFFICE") }
      })
    );

    TIER_ORDER.forEach((tier) => expect(tierSatisfies(answer, tier)).toBe(true));
    expect(isEntitled(answer, "human_concierge")).toBe(true);
  });

  it("lifetime is PREMIUM with no expiry, and does not read as expired", () => {
    // The specific regression: `lifetime` was in ProfileHeader's array and not
    // in AppNavigator's, so one member saw two different answers on one screen.
    // The client no longer has an array — it has the tier the server resolved,
    // and a null `expires_at` is a grant that does not end, not a missing one.
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PREMIUM",
        status: "active",
        source: "lifetime",
        expires_at: null,
        features: { market_pulse: feature("market_pulse", "ENTITLED", "PREMIUM") }
      })
    );

    expect(isMember(answer)).toBe(true);
    expect(answer.expiresAt).toBeNull();
    expect(answer.source).toBe("lifetime");
    expect(isEntitled(answer, "market_pulse")).toBe(true);
  });

  it("an expired grant is FREE, and the server said so — not the client", () => {
    // The client does not compare `expires_at` to the device clock. A device
    // whose clock is wrong would otherwise revoke a live membership, or extend
    // a dead one. Expiry is resolved server-side and arrives already applied.
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "FREE",
        status: "none",
        source: "",
        expires_at: "2026-01-01T00:00:00+00:00",
        features: { market_pulse: feature("market_pulse", "NOT_ENTITLED", "PREMIUM") }
      })
    );

    expect(answer.state).toBe("resolved");
    expect(isMember(answer)).toBe(false);
    expect(isEntitled(answer, "market_pulse")).toBe(false);
  });

  it("an account hold grants nothing even though the resolve succeeded", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "FREE",
        status: "account_hold",
        source: "account_hold",
        features: { market_pulse: feature("market_pulse", "NOT_ENTITLED", "PREMIUM") }
      })
    );

    // Resolved, so the UI may say *why*; entitled to nothing, so no gate opens.
    expect(answer.state).toBe("resolved");
    expect(answer.status).toBe("account_hold");
    expect(isMember(answer)).toBe(false);
  });

  it("an unrecognised tier name falls back to FREE, never upward", () => {
    const answer = parseTierAnswer(serverAnswer({ effective_tier: "PLATINUM", status: "active" }));

    expect(answer.effectiveTier).toBe("FREE");
    expect(isMember(answer)).toBe(false);
  });

  it("an unrecognised availability word is UNKNOWN, not entitled", () => {
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PREMIUM",
        status: "active",
        features: { market_pulse: feature("market_pulse", "PROBABLY", "PREMIUM") }
      })
    );

    expect(featureAvailability(answer, "market_pulse")).toBe("UNKNOWN");
    expect(isEntitled(answer, "market_pulse")).toBe(false);
  });

  it("a feature the server never mentioned is UNKNOWN, not entitled", () => {
    const answer = parseTierAnswer(
      serverAnswer({ effective_tier: "PRIVATE_OFFICE", status: "active", features: {} })
    );

    // Top of the ladder and still not entitled: a key the server did not send
    // is a capability it has not learned to gate, and rendering it would expose
    // exactly the surface nobody has reviewed.
    expect(tierSatisfies(answer, "PRIVATE_OFFICE")).toBe(true);
    expect(featureAvailability(answer, "capital_graph")).toBe("UNKNOWN");
    expect(isEntitled(answer, "capital_graph")).toBe(false);
  });

  it("NOT_IMPLEMENTED and FEATURE_DISABLED survive the parse distinctly", () => {
    // Stage 4 and Stage 16: "we have not built it" must not be shown as "buy
    // more". Both are false for a gate, but the copy differs, so the words have
    // to reach the screen intact.
    const answer = parseTierAnswer(
      serverAnswer({
        effective_tier: "PRIVATE",
        status: "active",
        features: {
          private_shield: feature("private_shield", "NOT_IMPLEMENTED", "PRIVATE"),
          relationship_intelligence: feature(
            "relationship_intelligence",
            "FEATURE_DISABLED",
            "PRIVATE"
          )
        }
      })
    );

    expect(featureAvailability(answer, "private_shield")).toBe("NOT_IMPLEMENTED");
    expect(featureAvailability(answer, "relationship_intelligence")).toBe("FEATURE_DISABLED");
    expect(isEntitled(answer, "private_shield")).toBe(false);
    expect(isEntitled(answer, "relationship_intelligence")).toBe(false);
  });
});

describe("canonical tier — backend unavailable", () => {
  const degraded = [
    ["a degraded resolve", { ok: false, resolver_state: "degraded", degraded_reason: "entitlement_store_unavailable" }],
    ["ok=true with a degraded resolver", { ok: true, resolver_state: "degraded" }],
    ["an error body", { ok: false, error: "unauthorized" }],
    ["an empty body", {}],
    ["null", null],
    ["a string", "service unavailable"]
  ] as const;

  degraded.forEach(([label, payload]) => {
    it(`treats ${label} as unavailable, granting nothing`, () => {
      const answer = parseTierAnswer(payload);

      expect(answer).toEqual(UNKNOWN_TIER);
      expect(answer.state).toBe("unavailable");
      expect(isMember(answer)).toBe(false);
      expect(tierSatisfies(answer, "PREMIUM")).toBe(false);
      expect(featureAvailability(answer, "market_pulse")).toBe("UNKNOWN");
      expect(isEntitled(answer, "market_pulse")).toBe(false);
    });
  });

  it("does not claim the member is on Free when it does not know", () => {
    // `effectiveTier` is FREE because a gate has to be given *something* and
    // FREE grants nothing. `status` is what copy must branch on: a paying member
    // whose resolver blipped is shown "temporarily unavailable", not "Free".
    const answer = parseTierAnswer({ ok: false, resolver_state: "degraded" });

    expect(answer.status).toBe("unavailable");
    expect(answer.state).toBe("unavailable");
    expect(answer.source).toBe("");
    expect(answer.verifiedAt).toBe("");
  });

  it("is false for a null answer, so an unmounted or pre-fetch caller is safe", () => {
    expect(isMember(null)).toBe(false);
    expect(tierSatisfies(null, "FREE")).toBe(false);
    expect(isEntitled(null, "market_pulse")).toBe(false);
  });
});
