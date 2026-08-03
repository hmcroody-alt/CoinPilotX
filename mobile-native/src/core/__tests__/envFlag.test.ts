/**
 * One rule for "on", pinned, and pinned across every flag rather than once.
 *
 * ## What went wrong
 *
 * This app had no flags module, so each surface wrote its own env parser inline.
 * By Tier 0.5 there were six of them. Most accepted `1`, `true`, `on` and `yes`;
 * the flags Tier 0.5 added accepted the literal `1` and nothing else; one
 * lowercased without trimming, so it took `true` and rejected `" 1"`; the perf
 * overlay took `1`, `true` and `on` but not `yes`.
 *
 * The consequence was not stylistic. `EXPO_PUBLIC_ORDERS_ESCROW=true` turned
 * escrow on and `EXPO_PUBLIC_STORE_READINESS=true` did nothing at all, and the
 * difference was invisible at the place an operator actually sets the variable.
 * A flag that looks set and does nothing is worse than a flag that is off,
 * because it costs somebody an afternoon proving the feature is broken.
 *
 * ## Why this test is a loop and not a list
 *
 * Consolidating the parsers fixes the six that existed. It does not stop a
 * seventh, and a style note has never stopped anything. So the assertion below
 * enumerates the accessors and runs the same four values through every one of
 * them: a new flag that reinvents the rule fails here, and a flag added to the
 * app without being added to this table fails the completeness check that
 * follows it, which reads the source rather than trusting the list.
 */
import { TRUTHY_FLAG_VALUES, envFlagOn, isFlagValueOn } from "../envFlag";
import {
  messagesAwayModeEnabled,
  messagesMockChipsEnabled,
  messagesPresenceEnabled,
  messagesTypingEnabled,
  replyBadgeIncentiveEnabled
} from "../../api/commerceInbox";
import { conversationSplitEnabled } from "../../api/conversationDomain";
import {
  eventAttributionEnabled,
  eventsLiveStatsEnabled,
  eventsMockEnabled
} from "../../api/eventsManager";
import { accountNameFirstEnabled, adsPostModeEnabled } from "../../api/adsDashboard";
import { ordersEscrowIsLive, ordersFulfillmentIsLive } from "../../api/ordersDashboard";
import {
  adTopUpIsLive,
  escrowCardIsLive,
  instantPayoutIsLive,
  payoutInitiationIsLive,
  statementsAreLive,
  taxDocumentsAreLive
} from "../../api/paymentsHub";
import { storeReadinessEnabled } from "../../api/storeDashboard";
import { marketplaceLocationHonestyEnabled } from "../../api/marketplaceScreen";
import { stateLanguageEnabled } from "../../api/stateLanguage";
import { insightsErrorCausesEnabled } from "../../api/insightsDashboard";
import { scopedBadgesEnabled } from "../unreadCounts";
import type { AdBilling } from "../../api/businessOs";
import { readFileSync, readdirSync, statSync } from "fs";
import { extname, join, relative } from "path";

/** Funding live on Advertising's side, so ad top-up answers for its own flag alone. */
const FUNDING_LIVE = { billing_enabled: true, live_charging: true } as AdBilling;

/**
 * Every call-time boolean accessor in the app, with the variable it reads.
 *
 * The two `api/config.ts` constants and the perf overlay in `App.tsx` are absent
 * on purpose: all three are evaluated once at module load, so setting the
 * variable here would not reach them. They are covered by the source scan below
 * instead, which is the only honest way to assert something a test cannot call.
 */
const ACCESSORS: Array<[string, () => boolean]> = [
  ["EXPO_PUBLIC_MESSAGES_TYPING", messagesTypingEnabled],
  ["EXPO_PUBLIC_MESSAGES_PRESENCE", messagesPresenceEnabled],
  ["EXPO_PUBLIC_MESSAGES_MOCK_CHIPS", messagesMockChipsEnabled],
  ["EXPO_PUBLIC_MESSAGES_AWAY", messagesAwayModeEnabled],
  ["EXPO_PUBLIC_MESSAGES_REPLY_BADGE", replyBadgeIncentiveEnabled],
  ["EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT", conversationSplitEnabled],
  ["EXPO_PUBLIC_EVENTS_LIVE_STATS", eventsLiveStatsEnabled],
  ["EXPO_PUBLIC_EVENTS_ATTRIBUTION", eventAttributionEnabled],
  ["EXPO_PUBLIC_EVENTS_MOCK", eventsMockEnabled],
  ["EXPO_PUBLIC_ADS_POST_MODE", adsPostModeEnabled],
  ["EXPO_PUBLIC_ACCOUNT_NAME_FIRST", accountNameFirstEnabled],
  ["EXPO_PUBLIC_ORDERS_ESCROW", ordersEscrowIsLive],
  ["EXPO_PUBLIC_ORDERS_FULFILLMENT", ordersFulfillmentIsLive],
  ["EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION", payoutInitiationIsLive],
  ["EXPO_PUBLIC_PAYMENTS_INSTANT_PAYOUT", instantPayoutIsLive],
  ["EXPO_PUBLIC_PAYMENTS_STATEMENTS", statementsAreLive],
  ["EXPO_PUBLIC_PAYMENTS_TAX_DOCUMENTS", taxDocumentsAreLive],
  ["EXPO_PUBLIC_PAYMENTS_ESCROW", escrowCardIsLive],
  ["EXPO_PUBLIC_PAYMENTS_AD_TOPUP", () => adTopUpIsLive(FUNDING_LIVE)],
  ["EXPO_PUBLIC_STORE_READINESS", storeReadinessEnabled],
  ["EXPO_PUBLIC_MARKETPLACE_LOCATION_HONESTY", marketplaceLocationHonestyEnabled],
  ["EXPO_PUBLIC_STATE_LANGUAGE", stateLanguageEnabled],
  ["EXPO_PUBLIC_INSIGHTS_ERROR_CAUSES", insightsErrorCausesEnabled],
  ["EXPO_PUBLIC_SCOPED_BADGES", scopedBadgesEnabled]
];

/** Spellings that must never be read as "on", including the plausible near-misses. */
const FALSY_VALUES = ["", " ", "0", "false", "off", "no", "null", "undefined", "2", "onx", "true false"];

afterEach(() => {
  for (const [name] of ACCESSORS) delete process.env[name];
});

describe("the accepted truthy set", () => {
  it("is exactly these four spellings", () => {
    // Pinned. Widening this is a deliberate edit to two lines, not a side effect
    // of somebody's local parser being slightly more generous than the last one.
    expect([...TRUTHY_FLAG_VALUES]).toEqual(["1", "true", "on", "yes"]);
  });

  it("is case-insensitive and ignores surrounding whitespace", () => {
    // A value from a CI variable panel or a shell export routinely carries a
    // trailing space nobody typed and nobody can see.
    for (const value of TRUTHY_FLAG_VALUES) {
      expect(isFlagValueOn(value)).toBe(true);
      expect(isFlagValueOn(value.toUpperCase())).toBe(true);
      expect(isFlagValueOn(`  ${value}  `)).toBe(true);
      expect(isFlagValueOn(`\t${value}\n`)).toBe(true);
    }
    expect(isFlagValueOn("True")).toBe(true);
    expect(isFlagValueOn("YES")).toBe(true);
    expect(isFlagValueOn("On")).toBe(true);
  });

  it("reads everything else as off, including an unset variable", () => {
    for (const value of FALSY_VALUES) expect(isFlagValueOn(value)).toBe(false);
    expect(isFlagValueOn(undefined)).toBe(false);
    expect(isFlagValueOn(null)).toBe(false);
    delete process.env.EXPO_PUBLIC_NOTHING_SETS_THIS;
    expect(envFlagOn("EXPO_PUBLIC_NOTHING_SETS_THIS")).toBe(false);
  });
});

describe("every flag accessor agrees on what 'on' means", () => {
  it.each(ACCESSORS)("%s", (name, read) => {
    delete process.env[name];
    // The default, first: unset is off, in every build, for every flag.
    expect(read()).toBe(false);

    for (const value of TRUTHY_FLAG_VALUES) {
      process.env[name] = value;
      expect(read()).toBe(true);
      process.env[name] = `  ${value.toUpperCase()} `;
      expect(read()).toBe(true);
    }

    for (const value of FALSY_VALUES) {
      process.env[name] = value;
      expect(read()).toBe(false);
    }
  });

  it("covers every flag the app reads at call time", () => {
    // If this number is wrong, either a flag was added without a row above or a
    // flag was deleted and its row left behind. Both are the drift this file
    // exists to catch.
    expect(ACCESSORS).toHaveLength(24);
    expect(new Set(ACCESSORS.map(([name]) => name)).size).toBe(ACCESSORS.length);
  });
});

/* ------------------------------------------------------------------ *
 * The two gates whose shape is not a plain read
 * ------------------------------------------------------------------ */

describe("gates with a condition in front of them", () => {
  /**
   * `EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT` is the one inverted flag in
   * the app — a kill switch, so its permissive side is the default. It is safe
   * because it is the last term of an `&&` chain whose first term is a loopback
   * API base URL, and the two terms before it are opt-ins that default off. The
   * composite defaults off; the individual flag does not.
   *
   * The inversion is preserved exactly. What changed is that it now reads the
   * shared set, so `DISABLE...=true` disables. Before, a kill switch spelled
   * `true` failed open, which is the worst direction for that particular bug.
   */
  it("keeps the QA kill switch inverted, and behind the loopback fence", () => {
    jest.isolateModules(() => {
      jest.doMock("../../api/config", () => ({ PULSE_API_BASE_URL: "http://localhost:8000" }));
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { canUseTemporaryQaAccount } = require("../../session/qaTemporaryAccount");

      process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN = "1";
      process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT = "1";
      delete process.env.EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT;
      expect(canUseTemporaryQaAccount()).toBe(true);

      for (const value of TRUTHY_FLAG_VALUES) {
        process.env.EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT = value;
        expect(canUseTemporaryQaAccount()).toBe(false);
      }

      // The two opt-ins in front of it read the same set as everything else.
      delete process.env.EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT;
      process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN = "yes";
      process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT = "TRUE";
      expect(canUseTemporaryQaAccount()).toBe(true);

      delete process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN;
      expect(canUseTemporaryQaAccount()).toBe(false);
    });

    delete process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN;
    delete process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT;
    delete process.env.EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT;
  });

  it("leaves the QA account unreachable when the base URL is not loopback", () => {
    jest.isolateModules(() => {
      jest.doMock("../../api/config", () => ({ PULSE_API_BASE_URL: "https://pulsesoc.com" }));
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { canUseTemporaryQaAccount } = require("../../session/qaTemporaryAccount");
      process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN = "1";
      process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT = "1";
      expect(canUseTemporaryQaAccount()).toBe(false);
    });
    delete process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN;
    delete process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT;
  });

  /**
   * Ad top-up is the only accessor that takes an argument, and its flag can only
   * ever subtract from Advertising's own funding gate. The table above runs the
   * truthy set through it with funding live; this pins the other half.
   */
  it("keeps ad top-up off when funding is dark, whatever the flag says", () => {
    for (const value of TRUTHY_FLAG_VALUES) {
      process.env.EXPO_PUBLIC_PAYMENTS_AD_TOPUP = value;
      expect(adTopUpIsLive({ billing_enabled: false, live_charging: false } as AdBilling)).toBe(false);
      expect(adTopUpIsLive(null)).toBe(false);
    }
    delete process.env.EXPO_PUBLIC_PAYMENTS_AD_TOPUP;
  });
});

/* ------------------------------------------------------------------ *
 * Completeness — read the source, do not trust the list above
 * ------------------------------------------------------------------ */

const SRC = join(__dirname, "..", "..");
const APP_ENTRY = join(SRC, "..", "App.tsx");

/**
 * The environment variables that are not booleans and are correctly read
 * directly: a base URL, a project id, and four free-form QA strings. Each has
 * its own validation and its own fallback chain, so passing one through a
 * truthiness reader would answer a question nobody asked of it.
 */
const NON_BOOLEAN_VARS = [
  "EXPO_PUBLIC_PULSE_API_BASE_URL",
  "EXPO_PUBLIC_EXPO_PROJECT_ID",
  "EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER",
  "EXPO_PUBLIC_PULSESOC_QA_CHAT_STATE",
  "EXPO_PUBLIC_PULSESOC_QA_REELS_STATE",
  "EXPO_PUBLIC_PULSESOC_QA_START_ROUTE"
];

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "node_modules" || name === "__tests__") continue;
      sourceFiles(full, out);
      continue;
    }
    if (![".ts", ".tsx"].includes(extname(name))) continue;
    if (/\.test\.tsx?$/.test(name)) continue;
    out.push(full);
  }
  return out;
}

describe("nothing reads a boolean flag on its own terms any more", () => {
  it("finds no direct read of a boolean EXPO_PUBLIC_ variable", () => {
    const offenders: string[] = [];
    const files = [...sourceFiles(SRC), APP_ENTRY];
    for (const file of files) {
      const rel = relative(SRC, file).split("\\").join("/");
      // The shared reader is the one place allowed to touch process.env by name.
      if (rel === "core/envFlag.ts") continue;
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, index) => {
          const trimmed = line.trim();
          if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
          for (const match of line.matchAll(/process\.env\.(EXPO_PUBLIC_[A-Z0-9_]+)/g)) {
            if (NON_BOOLEAN_VARS.includes(match[1])) continue;
            offenders.push(`${rel}:${index + 1} reads ${match[1]} directly`);
          }
          // `process.env[SOME_FLAG]` — the indexed form the old parsers used.
          if (/process\.env\[/.test(line)) offenders.push(`${rel}:${index + 1} indexes process.env directly`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("is reading the source it is guarding", () => {
    // If the walker stops finding files the assertion above passes vacuously.
    expect(sourceFiles(SRC).length).toBeGreaterThan(200);
  });
});
