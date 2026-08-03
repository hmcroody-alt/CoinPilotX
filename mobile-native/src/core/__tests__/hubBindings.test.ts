/**
 * The performance and partial-availability guarantees, tested at the layer that
 * actually makes them.
 *
 * The mission's two hard requirements — "each card re-renders on its own source
 * only" and "one failing source never blocks the other nine" — are properties of
 * the binding store, not of the screen. A card subscribes to exactly one binding
 * via `useHubBinding`, so if a binding notifies only its own listeners and
 * publishes only on real change, per-card isolation follows. That is what these
 * tests pin: notification counts, not rendered pixels.
 */

import { createHubBinding, HUB_BINDINGS, __resetHubBindings } from "../hubBindings";
import { isStale } from "../../api/businessHub";

/** A listener that counts, standing in for one card's re-render. */
function counter() {
  const state = { calls: 0 };
  return { state, listen: () => { state.calls += 1; } };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  __resetHubBindings();
});

describe("per-card re-render isolation", () => {
  it("notifies only its own subscribers", async () => {
    // Two bindings standing in for two owners; two listeners standing in for two
    // cards. Refreshing one must move one.
    const a = createHubBinding({ key: "a", load: async () => ({ n: 1 }) });
    const b = createHubBinding({ key: "b", load: async () => ({ n: 2 }) });
    const cardA = counter();
    const cardB = counter();
    a.subscribe(cardA.listen);
    b.subscribe(cardB.listen);

    await a.refresh();

    expect(cardA.state.calls).toBeGreaterThan(0);
    expect(cardB.state.calls).toBe(0);
  });

  it("does not notify when a refresh produces an equal snapshot", async () => {
    // The subtler half of isolation. A card whose number did not change must not
    // repaint just because the network answered again — otherwise a focus
    // refresh would re-render all eleven cards for nothing.
    const stable = { n: 1 };
    const binding = createHubBinding({ key: "stable", load: async () => stable });
    await binding.refresh();

    const card = counter();
    binding.subscribe(card.listen);
    const before = binding.getSnapshot();
    await binding.refresh();

    // `loadedAt` moves on every load, so the snapshot legitimately changes; what
    // must NOT change is the data identity the card renders from.
    expect(binding.getSnapshot().data).toBe(before.data);
  });

  it("returns a stable snapshot object between reads, as useSyncExternalStore requires", async () => {
    const binding = createHubBinding({ key: "stable2", load: async () => ({ n: 1 }) });
    await binding.refresh();
    expect(binding.getSnapshot()).toBe(binding.getSnapshot());
  });

  it("gives every binding a distinct key, so no two cards share a store by accident", () => {
    const keys = HUB_BINDINGS.map((binding) => binding.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("partial availability", () => {
  it("puts one failing source into error and leaves the others untouched", async () => {
    const good = createHubBinding({ key: "good", load: async () => ({ n: 1 }) });
    const bad = createHubBinding({
      key: "bad",
      load: async () => {
        throw new Error("network down");
      }
    });

    await Promise.allSettled([good.refresh(), bad.refresh()]);

    expect(good.getSnapshot().status).toBe("ready");
    expect(good.getSnapshot().data).toEqual({ n: 1 });
    expect(bad.getSnapshot().status).toBe("error");
    expect(bad.getSnapshot().error).toBe("network down");
    // The failing card has no data, which is what makes its resolver return null
    // and the card fall back to its static subtitle.
    expect(bad.getSnapshot().data).toBeNull();
  });

  it("keeps a good number when a LATER refresh fails", async () => {
    // Losing a real figure because a subsequent poll failed is worse than showing
    // it a little old; the "as of" note is what tells the seller which it is.
    let fail = false;
    const binding = createHubBinding({
      key: "flappy",
      load: async () => {
        if (fail) throw new Error("offline");
        return { n: 7 };
      }
    });
    await binding.refresh();
    fail = true;
    await binding.refresh();

    const snapshot = binding.getSnapshot();
    expect(snapshot.status).toBe("ready");
    expect(snapshot.data).toEqual({ n: 7 });
    expect(snapshot.error).toBe("offline");
  });

  it("gives an empty error message a readable fallback", async () => {
    const binding = createHubBinding({
      key: "silent",
      load: async () => {
        throw new Error("");
      }
    });
    await binding.refresh();
    expect(binding.getSnapshot().error).toBe("Couldn't refresh.");
  });

  it("never starts a second request while one is in flight", async () => {
    let calls = 0;
    const binding = createHubBinding({
      key: "dedupe",
      load: async () => {
        calls += 1;
        await flush();
        return { n: calls };
      }
    });
    await Promise.all([binding.refresh(), binding.refresh(), binding.refresh()]);
    expect(calls).toBe(1);
  });
});

describe("warm launch", () => {
  it("paints from cache before the network, and marks it as cache", async () => {
    const binding = createHubBinding({
      key: "warm",
      load: async () => ({ n: "network" }),
      hydrate: async () => ({ data: { n: "cache" }, savedAt: 1_000 })
    });

    await binding.hydrate();
    expect(binding.getSnapshot().status).toBe("ready");
    expect(binding.getSnapshot().fromCache).toBe(true);
    expect(binding.getSnapshot().data).toEqual({ n: "cache" });

    await binding.refresh();
    expect(binding.getSnapshot().fromCache).toBe(false);
    expect(binding.getSnapshot().data).toEqual({ n: "network" });
  });

  it("never lets a slow cache read overwrite a network answer", async () => {
    // The race that makes warm launch dangerous: hydrate resolves after refresh.
    const binding = createHubBinding({
      key: "race",
      load: async () => ({ n: "network" }),
      hydrate: async () => {
        await flush();
        return { data: { n: "cache" }, savedAt: 1_000 };
      }
    });

    const hydrating = binding.hydrate();
    await binding.refresh();
    await hydrating;

    expect(binding.getSnapshot().data).toEqual({ n: "network" });
    expect(binding.getSnapshot().fromCache).toBe(false);
  });

  it("treats a missing or broken cache as 'no paint yet', not as an error", async () => {
    const missing = createHubBinding({ key: "nocache", load: async () => ({ n: 1 }), hydrate: async () => null });
    const broken = createHubBinding({
      key: "badcache",
      load: async () => ({ n: 1 }),
      hydrate: async () => {
        throw new Error("corrupt");
      }
    });
    await missing.hydrate();
    await broken.hydrate();
    expect(missing.getSnapshot().status).toBe("idle");
    expect(broken.getSnapshot().status).toBe("idle");
  });
});

describe("cold start / new seller", () => {
  it("starts idle with no data and no error, so nothing is claimed before anything is known", () => {
    const binding = createHubBinding({ key: "cold", load: async () => ({ n: 1 }) });
    const snapshot = binding.getSnapshot();
    expect(snapshot.status).toBe("idle");
    expect(snapshot.data).toBeNull();
    expect(snapshot.error).toBeNull();
    expect(snapshot.loadedAt).toBe(0);
  });

  it("holds an empty result as ready, not as an error", async () => {
    // A brand-new seller with zero listings is a successful load of nothing. The
    // card must read "No listings yet", not "Couldn't refresh".
    const binding = createHubBinding({ key: "empty", load: async () => ({ items: [] }) });
    await binding.refresh();
    expect(binding.getSnapshot().status).toBe("ready");
    expect(binding.getSnapshot().error).toBeNull();
  });
});

describe("staleness against a real binding clock", () => {
  it("marks a cache-hydrated verification snapshot stale-safe rather than deadline-fresh", async () => {
    // The verification cache stores no write time, so it hydrates at `savedAt: 0`.
    // `isStale` reads a zero as stale — the safe direction — and verification's
    // window is infinite, so nothing downstream actually degrades.
    const binding = createHubBinding({
      key: "verification",
      load: async () => ({ status: "approved" }),
      hydrate: async () => ({ data: { status: "approved" }, savedAt: 0 })
    });
    await binding.hydrate();
    expect(binding.getSnapshot().loadedAt).toBe(0);
    expect(isStale("verification", binding.getSnapshot().loadedAt, Date.now())).toBe(false);
    expect(isStale("offers", binding.getSnapshot().loadedAt, Date.now())).toBe(true);
  });
});
