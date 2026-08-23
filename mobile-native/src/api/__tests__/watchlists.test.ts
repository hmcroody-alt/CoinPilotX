/**
 * Tests for the watchlists client.
 *
 * The property under most scrutiny here is the one the backend suite cannot
 * check: that "we do not know" survives the trip through the normalizer. The
 * server is careful to send `null` for an unknown price, and the conventional
 * `Number(x || 0)` in this folder would convert every one of those into a
 * confident `0` before it ever reached a screen. So most of what follows pins
 * null-in / null-out and the "--" rendering that depends on it.
 *
 * The second property is that the client never invents a request. One screen
 * open is one board call; a chart range the server did not offer is not
 * fetched; a search result the provider cannot price is not offered.
 */

const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

import {
  UNKNOWN_VALUE,
  addWatchlistAsset,
  createWatchlist,
  deleteWatchlist,
  formatCompact,
  formatPercent,
  formatPrice,
  formatRank,
  formatSignedPrice,
  getAssetDetail,
  getAssetHistory,
  getWatchlistMarketView,
  normalizeAssetDetail,
  normalizeAssetHistory,
  normalizeAssetQuote,
  normalizeWatchlistMarketView,
  removeWatchlistAsset,
  renameWatchlist,
  searchAssets,
  setFavoriteAsset
} from "../watchlists";

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

const BTC = {
  symbol: "btc",
  name: "Bitcoin",
  image: "",
  price: 66834.21,
  change_24h: 2.35,
  price_change_24h: 1533.02,
  market_cap: 1.31e12,
  market_cap_rank: 1,
  volume_24h: 2.4e10,
  circulating_supply: 19_700_000,
  max_supply: 21_000_000,
  sparkline: [65000, 65500, 66834.21],
  has_market_data: true
};

/** The provider knows nothing about this one. Every number is null. */
const UNKNOWN_ASSET = {
  symbol: "ZZZ",
  name: "ZZZ",
  image: "",
  price: null,
  change_24h: null,
  price_change_24h: null,
  market_cap: null,
  market_cap_rank: null,
  volume_24h: null,
  circulating_supply: null,
  max_supply: null,
  sparkline: [],
  has_market_data: false
};

describe("honest absence", () => {
  it("keeps a missing price as null rather than zero", () => {
    const quote = normalizeAssetQuote(UNKNOWN_ASSET);
    expect(quote.price).toBeNull();
    expect(quote.change_24h).toBeNull();
    expect(quote.market_cap).toBeNull();
    expect(quote.market_cap_rank).toBeNull();
    expect(quote.has_market_data).toBe(false);
  });

  it("renders unknown values as -- and never as a number", () => {
    expect(formatPrice(null)).toBe(UNKNOWN_VALUE);
    expect(formatPercent(null)).toBe(UNKNOWN_VALUE);
    expect(formatCompact(null)).toBe(UNKNOWN_VALUE);
    expect(formatRank(null)).toBe(UNKNOWN_VALUE);
    expect(formatSignedPrice(null)).toBe(UNKNOWN_VALUE);
  });

  it("distinguishes a real zero change from an unknown one", () => {
    // 0.00% is a fact — the price did not move. It must not collapse into "--",
    // and "--" must not be able to masquerade as it.
    expect(formatPercent(0)).toBe("+0.00%");
    expect(normalizeAssetQuote({ ...UNKNOWN_ASSET, change_24h: 0 }).change_24h).toBe(0);
  });

  it("maps a real price and a negative change through unchanged", () => {
    const quote = normalizeAssetQuote({ ...BTC, change_24h: -3.1 });
    expect(quote.price).toBe(66834.21);
    expect(quote.change_24h).toBe(-3.1);
    expect(formatPercent(-3.1)).toBe("-3.10%");
    expect(quote.symbol).toBe("BTC");
  });

  it("drops a non-numeric price rather than passing NaN to the view", () => {
    expect(normalizeAssetQuote({ ...BTC, price: "not a price" as unknown as number }).price).toBeNull();
  });

  it("drops a gap in the sparkline instead of drawing it as a crash to zero", () => {
    // Number(null) is 0 and 0 is finite, so the obvious filter keeps gaps and
    // plots them on the floor of the chart. A missing sample must leave the
    // shape alone, not add a spike the market never had.
    const quote = normalizeAssetQuote({
      ...BTC,
      sparkline: [65000, null as unknown as number, 66834.21]
    });
    expect(quote.sparkline).toEqual([65000, 66834.21]);
  });

  it("keeps a null aggregate when no asset on the list has a price", () => {
    const view = normalizeWatchlistMarketView({
      watchlists: [{ id: 4, name: "Unpriced", average_change_24h: null, priced_asset_count: 0, assets: [] }]
    });
    expect(view.watchlists[0].average_change_24h).toBeNull();
  });
});

describe("reads", () => {
  it("fetches the whole board in a single request", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      watchlists: [
        { id: 1, name: "My Crypto", asset_count: 2, priced_asset_count: 2, average_change_24h: 1.8, assets: [{ ...BTC, id: 9, watchlist_id: 1, alert_count: 2, favorite: true }] },
        { id: 2, name: "Alt", asset_count: 1, priced_asset_count: 1, average_change_24h: -3.1, assets: [{ ...BTC, id: 10, watchlist_id: 2 }] }
      ],
      favorites: [{ ...BTC, id: 9, watchlist_id: 1, favorite: true }],
      market: { source: "coingecko", updated_at: "2026-08-22T16:00:00", warning: "", ready: true }
    });

    const view = await getWatchlistMarketView();

    // Two lists, both holding BTC, still cost exactly one call.
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists/market");
    expect(view.watchlists).toHaveLength(2);
    expect(view.watchlists[0].assets[0].alert_count).toBe(2);
    expect(view.watchlists[0].assets[0].favorite).toBe(true);
    expect(view.market.ready).toBe(true);
  });

  it("caches the board so the next open can paint before the network answers", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, watchlists: [], favorites: [], market: {} });
    await getWatchlistMarketView();
    expect(mockWriteJsonCache).toHaveBeenCalledWith("pulsesoc.native.crypto.watchlists.market", expect.anything());
  });

  it("offers only the chart ranges the server said the asset supports", () => {
    const detail = normalizeAssetDetail({
      asset: BTC,
      ranges: ["24H", "7D", "NOPE"] as never,
      watchlists: [],
      alerts: [],
      market: {}
    });
    expect(detail.ranges).toEqual(["24H", "7D"]);
  });

  it("gives an unknown asset no ranges at all rather than six dead tabs", () => {
    expect(normalizeAssetDetail({ asset: UNKNOWN_ASSET, ranges: [] }).ranges).toEqual([]);
  });

  it("requests the range the user picked", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, symbol: "BTC", range: "7D", points: [{ t: 1, price: 2 }] });
    await getAssetHistory("btc", "7D");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/assets/BTC/history?range=7D");
  });

  it("does not cache an empty history, so an outage cannot look permanent", async () => {
    mockPulseApi.mockResolvedValue({ ok: false, symbol: "ZZZ", range: "24H", points: [], source: "unavailable" });
    const history = await getAssetHistory("ZZZ", "24H");
    expect(history.points).toEqual([]);
    expect(mockWriteJsonCache).not.toHaveBeenCalled();
  });

  it("drops history points that carry no usable price", () => {
    const history = normalizeAssetHistory({
      points: [{ t: 1, price: 10 }, { t: 2, price: null as unknown as number }, { t: 3, price: 12 }]
    });
    expect(history.points).toEqual([{ t: 1, price: 10 }, { t: 3, price: 12 }]);
  });

  it("passes the alerts the server attached to the asset", () => {
    const detail = normalizeAssetDetail({
      asset: BTC,
      // No cast: this is a real AlertRule, so the test proves the detail payload
      // and the alerts module actually agree on the shape.
      alerts: [{ id: 7, symbol: "BTC", condition: "above", threshold: 70000, status: "active" }],
      ranges: [],
      watchlists: [],
      market: {}
    });
    expect(detail.alerts).toHaveLength(1);
    expect(detail.alerts[0].id).toBe(7);
    expect(detail.alerts[0].asset_symbol).toBe("BTC");
  });

  it("only surfaces search results the provider can price", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, assets: [BTC, { ...UNKNOWN_ASSET, symbol: "" }] });
    const results = await searchAssets("b");
    expect(results.map((asset) => asset.symbol)).toEqual(["BTC"]);
  });

  it("loads asset detail for the requested symbol", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, asset: BTC, ranges: ["24H"], watchlists: [], alerts: [], market: {} });
    const detail = await getAssetDetail("btc");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/assets/BTC");
    expect(detail.asset.price).toBe(66834.21);
  });
});

describe("mutations", () => {
  beforeEach(() => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Done." });
  });

  // The common thread: no call carries a user id. Ownership is the session on
  // the server, so there is nothing in a request body for a client to forge.
  it("creates a watchlist without naming a user", async () => {
    await createWatchlist("My Crypto");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists", {
      method: "POST",
      body: JSON.stringify({ name: "My Crypto" })
    });
  });

  it("renames through PATCH on the owned id", async () => {
    await renameWatchlist(12, "Renamed");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists/12", {
      method: "PATCH",
      body: JSON.stringify({ name: "Renamed" })
    });
  });

  it("deletes through DELETE on the owned id", async () => {
    await deleteWatchlist(12);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists/12", { method: "DELETE" });
  });

  it("adds and removes assets", async () => {
    await addWatchlistAsset(12, "SOL");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists/12/assets", {
      method: "POST",
      body: JSON.stringify({ assetSymbol: "SOL", notes: "" })
    });
    await removeWatchlistAsset(12, 44);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/watchlists/12/assets/44", { method: "DELETE" });
  });

  it("stores favorites on the server so they survive a reinstall", async () => {
    await setFavoriteAsset("btc", true);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/favorites", {
      method: "POST",
      body: JSON.stringify({ symbol: "BTC", favorite: true })
    });
  });

  it("reports a rejected mutation instead of pretending it worked", async () => {
    mockPulseApi.mockResolvedValue({ ok: false, message: "BTC is already on this watchlist." });
    const result = await addWatchlistAsset(12, "BTC");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("BTC is already on this watchlist.");
  });
});
