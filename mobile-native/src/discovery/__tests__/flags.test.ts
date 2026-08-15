/**
 * The rollback guarantee, stated as a test rather than as a promise in a doc.
 *
 * Two things are pinned here and they are not the same thing:
 *
 *   1. **Unset is off.** For all eight variables, in every combination. This is
 *      what makes the feature shippable dark — the build that goes to TestFlight
 *      with nothing exported renders the Home that exists today.
 *   2. **The master really is a master.** A sub-flag on with the master off must
 *      stay off. Otherwise "roll it all back" is one flag flip *plus* remembering
 *      which seven others somebody set in the EAS profile last month, which is
 *      not a rollback procedure, it is a scavenger hunt during an incident.
 *
 * The accepted-value rule itself is not re-tested here; `core/__tests__/envFlag.test.ts`
 * owns it and would fail if this module reinvented it.
 */
import { TRUTHY_FLAG_VALUES } from "../../core/envFlag";
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

const SUB_FLAGS: Array<[string, () => boolean]> = [
  ["EXPO_PUBLIC_HOME_DISCOVERY_REELS", discoveryReelsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE", discoveryPeopleEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_STATUSES", discoveryStatusesEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_GROUPS", discoveryGroupsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_CREATORS", discoveryCreatorsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_TOPICS", discoveryTopicsEnabled],
  ["EXPO_PUBLIC_HOME_DISCOVERY_SPONSORED", discoverySponsoredEnabled]
];

const ALL = [MASTER, ...SUB_FLAGS.map(([name]) => name)];

beforeEach(() => {
  __clearDiscoveryFlagOverrides();
  for (const name of ALL) delete process.env[name];
});

afterEach(() => {
  __clearDiscoveryFlagOverrides();
  for (const name of ALL) delete process.env[name];
});

describe("defaults", () => {
  it("is eight flags and every one of them is off unset", () => {
    // The count is asserted because the mission's rollback plan is written in
    // terms of these eight; a ninth gate that nothing here knows about would be
    // a surface that "turn discovery off" does not turn off.
    expect(ALL).toHaveLength(8);
    expect(homeDiscoveryEnabled()).toBe(false);
    for (const [, read] of SUB_FLAGS) expect(read()).toBe(false);
  });

  it("stays off with every sub-flag on and the master unset", () => {
    for (const [name] of SUB_FLAGS) process.env[name] = "1";

    expect(homeDiscoveryEnabled()).toBe(false);
    for (const [name, read] of SUB_FLAGS) expect([name, read()]).toEqual([name, false]);
  });
});

describe("the master switch", () => {
  it("does not turn any module on by itself", () => {
    // On is a two-key operation, deliberately: shipping the master alone must
    // not silently light up seven carousels.
    process.env[MASTER] = "1";

    expect(homeDiscoveryEnabled()).toBe(true);
    for (const [name, read] of SUB_FLAGS) expect([name, read()]).toEqual([name, false]);
  });

  it("gates each module individually when it is on", () => {
    process.env[MASTER] = "1";

    for (const [name, read] of SUB_FLAGS) {
      for (const value of TRUTHY_FLAG_VALUES) {
        process.env[name] = value;
        expect([name, value, read()]).toEqual([name, value, true]);
      }
      delete process.env[name];
      expect([name, read()]).toEqual([name, false]);
    }
  });

  it("turns everything off again in one flip", () => {
    for (const name of ALL) process.env[name] = "1";
    for (const [, read] of SUB_FLAGS) expect(read()).toBe(true);

    delete process.env[MASTER];

    for (const [name, read] of SUB_FLAGS) expect([name, read()]).toEqual([name, false]);
  });
});
