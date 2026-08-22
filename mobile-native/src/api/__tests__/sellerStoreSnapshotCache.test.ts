/**
 * `loadSellerStoreSnapshot` resolves even when both of its requests fail — it
 * wraps them in `allSettled` and substitutes empty arrays. That is the right
 * shape for a caller that wants to render something regardless, but it made the
 * *cache write* dangerous: an empty snapshot is indistinguishable from a seller
 * who genuinely has no listings and no orders, and writing one replaced a good
 * cache with a picture of an empty business.
 *
 * The seller then opened the store offline and was shown nothing, labelled
 * "saved data" — the cache reporting the opposite of the truth it exists to
 * preserve. That is a correctness failure, not a performance one, and it gets
 * worse the more the app leans on cache for fast paints, which is exactly what
 * the Business OS speed work does.
 *
 * So: only a snapshot both requests actually backed is persisted, and the
 * snapshot carries `live` so callers can tell an authoritative picture from a
 * degraded one instead of guessing from whether the arrays are empty.
 */
/**
 * Mocked at the request layer, not the module layer: `loadSellerStoreSnapshot`
 * calls its two siblings through direct in-module references, so a
 * `jest.mock("../marketplace")` never intercepts them. It would leave the real
 * functions reaching `pulseApi`, and the failure cases would then pass for the
 * wrong reason — because the request 503'd in the test environment, not because
 * the code under test did anything.
 */
const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

/** Route a mocked request by path so the two calls can settle differently. */
function respond(handlers: { listings?: () => unknown; orders?: () => unknown }) {
  mockPulseApi.mockImplementation(async (path: string) => {
    if (path.includes("/marketplace/seller/listings")) {
      if (!handlers.listings) throw new Error("unexpected listings call");
      return handlers.listings();
    }
    if (path.includes("/seller/orders")) {
      if (!handlers.orders) throw new Error("unexpected orders call");
      return handlers.orders();
    }
    throw new Error(`unmocked route ${path}`);
  });
}

const mockWrite = jest.fn();
jest.mock("../../core/cache", () => ({
  ...jest.requireActual("../../core/cache"),
  writeJsonCache: (...args: unknown[]) => mockWrite(...args),
  readJsonCache: jest.fn().mockResolvedValue(null)
}));

import { loadSellerStoreSnapshot } from "../marketplace";

beforeEach(() => {
  jest.clearAllMocks();
  mockWrite.mockResolvedValue(undefined);
});

describe("loadSellerStoreSnapshot — the cache is only written from truth", () => {
  it("persists the snapshot when both requests answer", async () => {
    respond({
      listings: () => ({ items: [{ id: 1 }, { id: 2 }] }),
      orders: () => ({ orders: [{ id: 9 }] })
    });

    const snapshot = await loadSellerStoreSnapshot();

    expect(snapshot.live).toBe(true);
    expect(snapshot.listings).toHaveLength(2);
    expect(mockWrite).toHaveBeenCalledTimes(1);
  });

  it("does not overwrite the cache with empties when both requests fail", async () => {
    respond({
      listings: () => {
        throw new Error("offline");
      },
      orders: () => {
        throw new Error("offline");
      }
    });

    const snapshot = await loadSellerStoreSnapshot();

    // It still resolves — callers depend on that — but it says so, and it does
    // not tell the device that this seller has nothing.
    expect(snapshot.live).toBe(false);
    expect(snapshot.listings).toEqual([]);
    expect(mockWrite).not.toHaveBeenCalled();
  });

  it("does not persist a half-answered snapshot", async () => {
    // Listings arrived, orders did not. Writing this would cache "no orders"
    // for a seller who may well have some.
    respond({
      listings: () => ({ items: [{ id: 1 }] }),
      orders: () => {
        throw new Error("orders timed out");
      }
    });

    const snapshot = await loadSellerStoreSnapshot();

    expect(snapshot.live).toBe(false);
    expect(mockWrite).not.toHaveBeenCalled();
  });
});
