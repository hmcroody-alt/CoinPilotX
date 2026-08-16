/**
 * The rollback guarantee, stated as a test rather than as a promise in a doc.
 *
 * Three things are pinned here and they are not the same thing:
 *
 *   1. **Silence ships the feature.** The master and the four sourced modules
 *      are on when nothing is exported. This is the assertion that would have
 *      caught the regression described below, and it is the reason this file no
 *      longer says "unset is off" for all eight.
 *   2. **Silence does not ship the unfinished three.** Creators, topics and
 *      sponsored stay off unset. The master turning on must not drag them along.
 *   3. **The master really is a master.** Turning `EXPO_PUBLIC_HOME_DISCOVERY`
 *      off must take every module down with it regardless of what the module's
 *      own variable says. Otherwise "roll it all back" is one flag flip *plus*
 *      remembering which seven others somebody set in the EAS profile last
 *      month, which is not a rollback procedure, it is a scavenger hunt during
 *      an incident.
 *
 * ## The regression this file now guards
 *
 * All eight flags used to default OFF, and the device QA build that proved the
 * feature worked was made by exporting five of them by hand. Nothing in the repo
 * exported them: no profile in `eas.json`, no `.env` (`.gitignore` excludes
 * `.env` and `.env.*`). So the suggestion rows existed in exactly one build and
 * vanished from the next one, which was made for an unrelated fix by somebody
 * who had no reason to know the exports were load-bearing. Nothing failed — the
 * build was green, this suite was green, and the feature was simply absent.
 *
 * `spatial/__tests__/flagDefaults.test.ts` guards the identical failure for the
 * Reels pager. Two features, same root cause, one week apart.
 *
 * The accepted-value rule itself is not re-tested here; `core/__tests__/envFlag.test.ts`
 * owns it and would fail if this module reinvented it.
 */
import { FALSY_FLAG_VALUES, TRUTHY_FLAG_VALUES } from "../../core/envFlag";
import {
  __clearDiscoveryFlagOverrides,
  discoveryCreatorsEnabled,
  discoveryGroupsEnabled,
  discoveryPeopleEnabled,
  discoveryReelsEnabled,
  discoverySponsoredEnabled,
  discoveryStatusesEnabled,
  discoveryTopicsEnabled,
  homeDiscoveryEnabled
} from "../flags";

const MASTER = "EXPO_PUBLIC_HOME_DISCOVERY";

/** Modules with a real adapter in `sources.ts`. These ship on. */
const SHIPPED: Array<[string, () => boolean]> = [
  ["EXPO_PUBLIC_HOME_DISCOVERY_REELS", discoveryReelsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE", discoveryPeopleEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_STATUSES", discoveryStatusesEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_GROUPS", discoveryGroupsEnabled]
];

/**
 * Modules with no source and, for topics, no destination. These are not
 * "disabled", they are unbuilt — `sources.ts` has no adapter for any of them.
 */
const UNFINISHED: Array<[string, () => boolean]> = [
  ["EXPO_PUBLIC_HOME_DISCOVERY_CREATORS", discoveryCreatorsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_TOPICS", discoveryTopicsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_SPONSORED", discoverySponsoredEnabled]
];

const SUB_FLAGS = [...SHIPPED, ...UNFINISHED];
const ALL = [MASTER, ...SUB_FLAGS.map(([name]) => name)];

/**
 * Saved and restored rather than deleted-and-forgotten: jest shares one
 * `process.env` across a worker, and a suite that runs after this one should see
 * the environment it was started with.
 */
const saved: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const name of ALL) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  __clearDiscoveryFlagOverrides();
});

afterEach(() => {
  for (const name of ALL) {
    if (saved[name] === undefined) delete process.env[name];
    else process.env[name] = saved[name];
  }
  __clearDiscoveryFlagOverrides();
});

describe("a build that sets no discovery variables at all", () => {
  it("still ships the Home suggestion rows", () => {
    // Read this as: a plain `xcodebuild` with an empty environment gets the
    // suggestions. This is the assertion the regression would have tripped.
    expect(homeDiscoveryEnabled()).toBe(true);
    for (const [name, read] of SHIPPED) expect([name, read()]).toEqual([name, true]);
  });

  it("does not switch on a module that has no source", () => {
    // The master defaulting on must not light up three carousels that
    // `sources.ts` cannot fill. Each would render an empty row.
    for (const [name, read] of UNFINISHED) expect([name, read()]).toEqual([name, false]);
  });

  it("is eight flags and no more", () => {
    // The count is asserted because the rollback plan is written in terms of
    // these eight; a ninth gate that nothing here knows about would be a surface
    // that "turn discovery off" does not turn off.
    expect(ALL).toHaveLength(8);
  });
});

describe("rollback is a flag flip and never a revert", () => {
  it.each([...FALSY_FLAG_VALUES, "OFF", "  false  "])(
    "takes the whole feature down from the master for %p",
    value => {
      process.env[MASTER] = value;

      expect(homeDiscoveryEnabled()).toBe(false);
      for (const [name, read] of SUB_FLAGS) expect([name, read()]).toEqual([name, false]);
    }
  );

  it("takes the master down even with every module explicitly on", () => {
    // The scavenger-hunt case: somebody set all seven in a profile months ago.
    // One flip still has to be enough.
    for (const [name] of SUB_FLAGS) process.env[name] = "1";
    process.env[MASTER] = "0";

    expect(homeDiscoveryEnabled()).toBe(false);
    for (const [name, read] of SUB_FLAGS) expect([name, read()]).toEqual([name, false]);
  });

  it("disables one module without touching the others", () => {
    process.env.EXPO_PUBLIC_HOME_DISCOVERY_GROUPS = "0";

    expect(discoveryGroupsEnabled()).toBe(false);
    expect(homeDiscoveryEnabled()).toBe(true);
    expect(discoveryReelsEnabled()).toBe(true);
    expect(discoveryPeopleEnabled()).toBe(true);
    expect(discoveryStatusesEnabled()).toBe(true);
  });

  it("does not accept a misspelled rollback", () => {
    // A shipped feature should not disappear because somebody typed "flase" in
    // a build profile. Turning it off has to be spelled correctly.
    process.env[MASTER] = "flase";
    expect(homeDiscoveryEnabled()).toBe(true);
  });
});

describe("the master switch", () => {
  it("gates each module individually when it is on", () => {
    process.env[MASTER] = "1";

    for (const [name, read] of SUB_FLAGS) {
      for (const value of TRUTHY_FLAG_VALUES) {
        process.env[name] = value;
        expect([name, value, read()]).toEqual([name, value, true]);
      }
      delete process.env[name];
    }
  });

  it("keeps an explicitly-enabled unfinished module gated behind it", () => {
    // Someone turning topics on for a spike must still lose it when discovery
    // is rolled back, or the rollback is partial.
    for (const [name] of UNFINISHED) process.env[name] = "1";
    for (const [name, read] of UNFINISHED) expect([name, read()]).toEqual([name, true]);

    process.env[MASTER] = "0";
    for (const [name, read] of UNFINISHED) expect([name, read()]).toEqual([name, false]);
  });
});
