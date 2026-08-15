/**
 * §5–§10, §13, §14 — what the adapters are allowed to put in front of a user.
 *
 * These assert the two properties that decide whether a suggestion row is an
 * asset or a liability:
 *
 *  1. **Every card has a working destination.** A card is only built from an
 *     item that carries the identifier its route needs. The failure this
 *     prevents is silent and expensive — a card renders fine, the user taps it,
 *     and the app lands on "not found". That is worse than showing nothing,
 *     because the user attributes it to the tap.
 *  2. **One dead endpoint removes one row.** Not four. Groups being down must
 *     not take Reels with it, and the reason must survive to analytics.
 *
 * Everything is mocked at the API-module boundary rather than at `fetch`,
 * because `jest.setup.js` rejects `global.fetch` outright.
 */
import { listReels } from "../../api/reels";
import { listStatuses } from "../../api/status";
import { listGroups } from "../../api/groups";
import { listSuggestedPeople } from "../../api/friends";
import { loadDiscoveryModules } from "../sources";
import { __clearDiscoveryFlagOverrides, __setDiscoveryFlagOverride } from "../flags";
import type { DiscoveryModule, DiscoveryModuleKind } from "../discoveryRows";

jest.mock("../../api/reels", () => ({ listReels: jest.fn() }));
jest.mock("../../api/status", () => ({ listStatuses: jest.fn() }));
jest.mock("../../api/groups", () => ({ listGroups: jest.fn() }));
jest.mock("../../api/friends", () => ({ listSuggestedPeople: jest.fn() }));

const mockListReels = listReels as jest.MockedFunction<typeof listReels>;
const mockListStatuses = listStatuses as jest.MockedFunction<typeof listStatuses>;
const mockListGroups = listGroups as jest.MockedFunction<typeof listGroups>;
const mockListPeople = listSuggestedPeople as jest.MockedFunction<typeof listSuggestedPeople>;

/** Enough of a reel to be accepted; overrides supply the interesting part. */
function reel(overrides: Record<string, unknown> = {}) {
  return { id: 1, reel_id: 1, title: "A reel", ...overrides } as never;
}

function status(overrides: Record<string, unknown> = {}) {
  return { id: 1, status_id: 1, body: "A status", ...overrides } as never;
}

function group(overrides: Record<string, unknown> = {}) {
  return { id: 1, slug: "astro", name: "Astro", ...overrides } as never;
}

function person(overrides: Record<string, unknown> = {}) {
  return {
    profileKey: "nova",
    displayName: "Nova",
    username: "nova",
    publicPlayerId: "nova",
    avatarUrl: "",
    rank: "Member",
    premiumVerified: false,
    ...overrides
  } as never;
}

/**
 * Find a module and narrow it to its kind, so `items` is the concrete
 * suggestion type rather than the union. Without the narrowing every assertion
 * below would have to reach through `as`, which would also hide a module built
 * with the wrong item shape — the exact thing worth catching.
 */
function moduleOf<K extends DiscoveryModuleKind>(
  modules: DiscoveryModule[],
  kind: K
): Extract<DiscoveryModule, { kind: K }> | undefined {
  return modules.find((module): module is Extract<DiscoveryModule, { kind: K }> => module.kind === kind);
}

/** All four sources return one usable item, so a test only overrides what it cares about. */
function resolveEverything() {
  mockListReels.mockResolvedValue({ reels: [reel({ reel_id: 1 }), reel({ reel_id: 2 }), reel({ reel_id: 3 })] } as never);
  mockListStatuses.mockResolvedValue({
    items: [status({ status_id: 1 }), status({ status_id: 2 }), status({ status_id: 3 })]
  } as never);
  mockListGroups.mockResolvedValue({
    groups: [group({ slug: "a" }), group({ slug: "b" }), group({ slug: "c" })]
  } as never);
  mockListPeople.mockResolvedValue([person({ profileKey: "a" }), person({ profileKey: "b" })] as never);
}

beforeEach(() => {
  jest.clearAllMocks();
  __clearDiscoveryFlagOverrides();
  // Every test below is about sourcing, so the gates are open unless a test
  // closes one. The gates themselves are covered in flags.test.ts.
  for (const flag of [
    "homeDiscoveryEnabled",
    "discoveryReelsEnabled",
    "discoveryPeopleEnabled",
    "discoveryStatusesEnabled",
    "discoveryGroupsEnabled"
  ] as const) {
    __setDiscoveryFlagOverride(flag, true);
  }
  resolveEverything();
});

afterEach(() => {
  __clearDiscoveryFlagOverrides();
});

describe("flag gating", () => {
  it("fetches nothing at all when the master flag is off", async () => {
    __setDiscoveryFlagOverride("homeDiscoveryEnabled", false);

    const { modules, failures } = await loadDiscoveryModules();

    expect(modules).toEqual([]);
    expect(failures).toEqual({});
    // The point is not just an empty list: an off feature must not cost the user
    // four requests on every Home mount.
    expect(mockListReels).not.toHaveBeenCalled();
    expect(mockListStatuses).not.toHaveBeenCalled();
    expect(mockListGroups).not.toHaveBeenCalled();
    expect(mockListPeople).not.toHaveBeenCalled();
  });

  it("omits a single disabled module and keeps the rest", async () => {
    __setDiscoveryFlagOverride("discoveryGroupsEnabled", false);

    const { modules } = await loadDiscoveryModules();

    expect(moduleOf(modules, "groups")).toBeUndefined();
    expect(moduleOf(modules, "reels")).toBeDefined();
    expect(mockListGroups).not.toHaveBeenCalled();
  });

  it("never builds creators, topics or sponsored — they have no source", async () => {
    // §9 and §10: absence is the implementation. If someone later adds an
    // adapter without a real destination, this fails.
    const { modules } = await loadDiscoveryModules();

    expect(moduleOf(modules, "creators")).toBeUndefined();
    expect(moduleOf(modules, "topics")).toBeUndefined();
    expect(moduleOf(modules, "sponsored")).toBeUndefined();
  });
});

describe("reels (§4, §13)", () => {
  it("drops reels with no id, because the whole point is the exact reel", async () => {
    mockListReels.mockResolvedValue({
      reels: [reel({ id: 0, reel_id: 0 }), reel({ reel_id: 7 })]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "reels");

    expect(module?.items).toHaveLength(1);
    expect(module?.items[0]).toMatchObject({ reelId: 7 });
  });

  it("prefers reel_id over id, since the route and transfer slot are keyed on it", async () => {
    mockListReels.mockResolvedValue({ reels: [reel({ id: 100, reel_id: 42 })] } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "reels");

    expect(module?.items[0]).toMatchObject({ reelId: 42 });
  });

  it("excludes anything unplayable rather than shipping a card to an error screen", async () => {
    mockListReels.mockResolvedValue({
      reels: [
        reel({ reel_id: 1, is_removed: true }),
        reel({ reel_id: 2, deleted_at: "2026-01-01T00:00:00Z" }),
        reel({ reel_id: 3, moderation_status: "pending" }),
        reel({ reel_id: 4, availability: "processing" }),
        reel({ reel_id: 5, moderation_status: "approved", availability: "available" })
      ]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "reels");

    expect(module?.items.map((item) => item.reelId)).toEqual([5]);
  });

  it("never suggests a reel the caller says is already on screen", async () => {
    mockListReels.mockResolvedValue({ reels: [reel({ reel_id: 1 }), reel({ reel_id: 2 })] } as never);

    const module = moduleOf(
      (await loadDiscoveryModules({ excludeReelIds: new Set([1]) })).modules,
      "reels"
    );

    expect(module?.items.map((item) => item.reelId)).toEqual([2]);
  });

  it("de-duplicates within its own response", async () => {
    mockListReels.mockResolvedValue({
      reels: [reel({ reel_id: 9 }), reel({ reel_id: 9 }), reel({ reel_id: 10 })]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "reels");

    expect(module?.items.map((item) => item.reelId)).toEqual([9, 10]);
  });

  it("carries the raw reel so the player can seed its first frame", async () => {
    // §4's "never flash an unrelated reel" depends on this payload existing at
    // tap time. Re-fetching it in the handler would put a wrong frame on screen
    // for exactly as long as that request takes.
    const raw = reel({ reel_id: 3, title: "Exact" });
    mockListReels.mockResolvedValue({ reels: [raw] } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "reels");

    expect(module?.items[0].source).toBe(raw);
  });
});

describe("statuses (§6, §13)", () => {
  it("drops statuses that have already expired", async () => {
    const past = new Date(Date.now() - 60_000).toISOString();
    const future = new Date(Date.now() + 60_000).toISOString();
    mockListStatuses.mockResolvedValue({
      items: [status({ status_id: 1, expires_at: past }), status({ status_id: 2, expires_at: future })]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "statuses");

    expect(module?.items.map((item) => item.statusId)).toEqual([2]);
  });

  it("drops private statuses and ones that are not ready", async () => {
    mockListStatuses.mockResolvedValue({
      items: [
        status({ status_id: 1, visibility: "private" }),
        status({ status_id: 2, fixture_state: "uploading" }),
        status({ status_id: 3 })
      ]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "statuses");

    expect(module?.items.map((item) => item.statusId)).toEqual([3]);
  });

  it("never re-suggests a status already on Home's rail", async () => {
    mockListStatuses.mockResolvedValue({
      items: [status({ status_id: 1 }), status({ status_id: 2 })]
    } as never);

    const module = moduleOf(
      (await loadDiscoveryModules({ excludeStatusIds: new Set([2]) })).modules,
      "statuses"
    );

    expect(module?.items.map((item) => item.statusId)).toEqual([1]);
  });
});

describe("groups (§7)", () => {
  it("requires a slug, because GroupDetail is keyed on it", async () => {
    mockListGroups.mockResolvedValue({
      groups: [group({ slug: "" }), group({ slug: "   " }), group({ slug: "real" })]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "groups");

    expect(module?.items.map((item) => item.slug)).toEqual(["real"]);
  });

  it("excludes groups the viewer already joined", async () => {
    mockListGroups.mockResolvedValue({
      groups: [group({ slug: "mine", joined: true }), group({ slug: "new" })]
    } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "groups");

    expect(module?.items.map((item) => item.slug)).toEqual(["new"]);
  });

  it("falls back to the slug when a group has no name", async () => {
    mockListGroups.mockResolvedValue({ groups: [group({ slug: "astro", name: "" })] } as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "groups");

    expect(module?.items[0]).toMatchObject({ name: "astro" });
  });
});

describe("people (§5)", () => {
  it("addresses people by profileKey, which is what the payload actually carries", async () => {
    mockListPeople.mockResolvedValue([person({ profileKey: "nova", username: "nova" })] as never);

    const module = moduleOf((await loadDiscoveryModules()).modules, "people");

    expect(module?.items[0]).toMatchObject({ profileKey: "nova", username: "nova" });
  });
});

describe("empty and failing sources", () => {
  it("omits a module whose source returned nothing, rather than an empty carousel", async () => {
    mockListGroups.mockResolvedValue({ groups: [] } as never);

    const { modules, failures } = await loadDiscoveryModules();

    expect(moduleOf(modules, "groups")).toBeUndefined();
    // Empty is not a failure: there is nothing to report to §14.
    expect(failures.groups).toBeUndefined();
  });

  it("keeps every other module alive when one endpoint throws", async () => {
    mockListGroups.mockRejectedValue(new Error("groups_unavailable"));

    const { modules, failures } = await loadDiscoveryModules();

    expect(moduleOf(modules, "groups")).toBeUndefined();
    expect(moduleOf(modules, "reels")).toBeDefined();
    expect(moduleOf(modules, "statuses")).toBeDefined();
    expect(moduleOf(modules, "people")).toBeDefined();
    // §14 wants a reason, not silence.
    expect(failures.groups).toBe("groups_unavailable");
  });

  it("reports a reason even for a throw with no message", async () => {
    mockListReels.mockRejectedValue("nope");

    const { failures } = await loadDiscoveryModules();

    expect(failures.reels).toBe("unknown_error");
  });

  it("survives every source failing at once", async () => {
    mockListReels.mockRejectedValue(new Error("a"));
    mockListStatuses.mockRejectedValue(new Error("b"));
    mockListGroups.mockRejectedValue(new Error("c"));
    mockListPeople.mockRejectedValue(new Error("d"));

    const { modules, failures } = await loadDiscoveryModules();

    expect(modules).toEqual([]);
    expect(failures).toEqual({ reels: "a", statuses: "b", groups: "c", people: "d" });
  });

  it("tolerates a source resolving without its collection key", async () => {
    // Real responses do this on error paths; `reels || []` is the guard, and a
    // crash here would take Home's whole mount with it.
    mockListReels.mockResolvedValue({} as never);
    mockListStatuses.mockResolvedValue({} as never);
    mockListGroups.mockResolvedValue({} as never);

    const { modules, failures } = await loadDiscoveryModules();

    expect(failures).toEqual({});
    expect(moduleOf(modules, "people")).toBeDefined();
  });
});

describe("titles", () => {
  it("uses namespaced i18n keys, never literal copy", async () => {
    // A key written with a dot instead of a colon resolves to the `common`
    // namespace and silently renders as a humanized leaf ("Reels title"), which
    // typechecks and passes i18n validation. This is the only thing that catches it.
    const { modules } = await loadDiscoveryModules();

    expect(modules.length).toBeGreaterThan(0);
    for (const module of modules) {
      expect(module.titleKey).toMatch(/^social:feed\.discovery\.[a-zA-Z]+$/);
    }
  });
});
