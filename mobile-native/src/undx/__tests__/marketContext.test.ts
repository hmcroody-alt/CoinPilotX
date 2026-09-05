import {
  buildMarketContextEnvelope,
  buildUndxSendContext,
  clearMarketContext,
  parkMarketContext,
  peekMarketContext,
  resetMarketContextForTests,
  takeMarketContextForSend
} from "../marketContext";

const eth = () =>
  buildMarketContextEnvelope({
    source: "asset_detail",
    symbol: "eth",
    name: "Ethereum",
    rank: 2,
    price: 3200.5,
    change24h: -1.2,
    marketCap: 4.1e11,
    volume24h: 1.9e10,
    selectedRange: "24H",
    watchlisted: true,
    alertCount: 1
  });

// Not `clearMarketContext()`: dismissal is now a *pending instruction to the
// server*, so using it to tidy up would leak an armed clear into the next test.
afterEach(() => resetMarketContextForTests());

describe("buildMarketContextEnvelope", () => {
  it("builds a normalised envelope from typed screen state", () => {
    const envelope = eth();
    expect(envelope).not.toBeNull();
    expect(envelope!.asset).toEqual({ id: "eth", symbol: "ETH", name: "Ethereum", rank: 2 });
    expect(envelope!.market_snapshot.price).toBe(3200.5);
    expect(envelope!.chart.selected_range).toBe("24H");
    expect(envelope!.user_overlay).toEqual({ watchlisted: true, alert_count: 1 });
  });

  it("refuses to build without a plausible symbol", () => {
    expect(buildMarketContextEnvelope({ source: "asset_detail", symbol: "" })).toBeNull();
    expect(buildMarketContextEnvelope({ source: "asset_detail", symbol: "not a symbol!" })).toBeNull();
  });

  it("keeps unknown figures as null, never zero", () => {
    const envelope = buildMarketContextEnvelope({
      source: "asset_detail",
      symbol: "SOL",
      price: null,
      change24h: Number.NaN
    });
    expect(envelope!.market_snapshot.price).toBeNull();
    expect(envelope!.market_snapshot.change24h).toBeNull();
  });
});

describe("the parked handoff", () => {
  it("sends the envelope exactly once, but keeps showing the chip", () => {
    parkMarketContext(eth());
    expect(takeMarketContextForSend()!.asset.symbol).toBe("ETH");
    // Second send: server already holds the context; resending would re-stamp
    // a stale snapshot as fresh.
    expect(takeMarketContextForSend()).toBeNull();
    // The chip still knows what "it" means.
    expect(peekMarketContext()!.asset.symbol).toBe("ETH");
  });

  it("a new handoff replaces the previous asset outright", () => {
    parkMarketContext(eth());
    takeMarketContextForSend();
    parkMarketContext(
      buildMarketContextEnvelope({ source: "asset_detail", symbol: "SOL", name: "Solana" })
    );
    expect(takeMarketContextForSend()!.asset.symbol).toBe("SOL");
    expect(peekMarketContext()!.asset.symbol).toBe("SOL");
  });

  it("dismissal clears both the chip and any unsent envelope", () => {
    parkMarketContext(eth());
    clearMarketContext();
    expect(peekMarketContext()).toBeNull();
    expect(takeMarketContextForSend()).toBeNull();
  });

  it("carries a caller-supplied canonical id, and falls back to the symbol", () => {
    // Symbols collide; ids do not. A screen that knows the provider's id says
    // so, and the server upgrades the fallback against the canonical board.
    expect(
      buildMarketContextEnvelope({ source: "asset_detail", symbol: "BTC", assetId: "Bitcoin" })!
        .asset.id
    ).toBe("bitcoin");
    expect(buildMarketContextEnvelope({ source: "asset_detail", symbol: "BTC" })!.asset.id).toBe(
      "btc"
    );
  });
});

describe("the request fields, which the chip has no way to disagree with", () => {
  it("attaches the parked envelope to the first send and nothing to the next", () => {
    parkMarketContext(eth());
    expect(buildUndxSendContext().market_context!.asset.symbol).toBe("ETH");
    expect(buildUndxSendContext()).toEqual({});
  });

  it("tells the server the topic ended, once, because the server holds a copy", () => {
    parkMarketContext(eth());
    buildUndxSendContext();
    clearMarketContext();
    expect(buildUndxSendContext()).toEqual({ market_context_cleared: true });
    // Consume-once for the same reason the envelope is: repeating it would keep
    // deleting a context the member may since have replaced.
    expect(buildUndxSendContext()).toEqual({});
  });

  it("a new handoff cancels a pending clear rather than deleting the new topic", () => {
    parkMarketContext(eth());
    buildUndxSendContext();
    clearMarketContext();
    parkMarketContext(
      buildMarketContextEnvelope({ source: "asset_detail", symbol: "SOL", name: "Solana" })
    );
    const sent = buildUndxSendContext();
    expect(sent.market_context!.asset.symbol).toBe("SOL");
    expect(sent.market_context_cleared).toBeUndefined();
  });

  it("costs nothing on an ordinary turn", () => {
    expect(buildUndxSendContext()).toEqual({});
  });
});
