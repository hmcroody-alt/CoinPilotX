import {
  buildMarketContextEnvelope,
  clearMarketContext,
  parkMarketContext,
  peekMarketContext,
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

afterEach(() => clearMarketContext());

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
});
