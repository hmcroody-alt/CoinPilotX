/**
 * Market Pulse → UNDX context bridge (client side).
 *
 * When a member taps "Ask UNDX" on an asset screen, the app does not fabricate
 * a user message full of numbers. It builds a structured *market context
 * envelope* from the same typed state that rendered the screen, parks it here,
 * and navigates to the one canonical UNDX conversation. The chat screen
 * attaches the envelope to the next assistant send as
 * `ui_context.market_context`; the server validates it, persists it per
 * conversation, and grounds crypto answers in the canonical live market layer.
 *
 * Why a consume-once handoff rather than resending every turn: the server
 * treats an arriving envelope as "the member just looked at this screen" and
 * stamps it fresh. Replaying the same envelope on every message would keep a
 * minutes-old snapshot eternally "fresh". Sent once, the server preserves the
 * stored context across ordinary turns and ages it honestly; opening a
 * different asset simply parks a new envelope, which replaces the old one on
 * arrival (server-side replacement, not merge).
 *
 * The envelope carries READ context only. Nothing in it grants the assistant
 * authority to change alerts or watchlists — those writes still travel the
 * governed capability gateway with confirmation intact.
 */

const SYMBOL_PATTERN = /^[A-Z0-9]{1,12}$/;

/** Mirrors the server's CONTEXT_TTL: past this the chip is stale theatre. */
const CLIENT_CONTEXT_TTL_MS = 6 * 3600 * 1000;

export type MarketContextEnvelope = {
  source: "asset_detail" | "market_pulse" | "watchlist" | "alerts" | "dashboard";
  context_type: "asset_focus";
  asset: { id: string; symbol: string; name: string; rank: number | null };
  market_snapshot: {
    price: number | null;
    change24h: number | null;
    marketCap: number | null;
    volume24h: number | null;
    observedAt: string | null;
    source: string | null;
    stale: boolean;
  };
  chart: { selected_range: string };
  user_overlay: { watchlisted: boolean; alert_count: number };
};

export type MarketContextInput = {
  source: MarketContextEnvelope["source"];
  symbol: string;
  name?: string | null;
  rank?: number | null;
  price?: number | null;
  change24h?: number | null;
  marketCap?: number | null;
  volume24h?: number | null;
  snapshotSource?: string | null;
  snapshotStale?: boolean;
  selectedRange?: string | null;
  watchlisted?: boolean;
  alertCount?: number;
};

function finite(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Build one envelope from typed screen state, or null when there is no asset
 * identity worth sending. Numbers that the screen shows as "--" arrive here as
 * null and stay null — the server's grounding treats absence as absence, never
 * as zero.
 */
export function buildMarketContextEnvelope(input: MarketContextInput): MarketContextEnvelope | null {
  const symbol = String(input.symbol || "").trim().toUpperCase();
  if (!SYMBOL_PATTERN.test(symbol)) return null;
  return {
    source: input.source,
    context_type: "asset_focus",
    asset: {
      id: symbol.toLowerCase(),
      symbol,
      name: String(input.name || symbol).slice(0, 80),
      rank: finite(input.rank)
    },
    market_snapshot: {
      price: finite(input.price),
      change24h: finite(input.change24h),
      marketCap: finite(input.marketCap),
      volume24h: finite(input.volume24h),
      observedAt: new Date().toISOString(),
      source: input.snapshotSource ? String(input.snapshotSource).slice(0, 40) : null,
      stale: Boolean(input.snapshotStale)
    },
    chart: { selected_range: String(input.selectedRange || "24H").toUpperCase().slice(0, 8) },
    user_overlay: {
      watchlisted: Boolean(input.watchlisted),
      alert_count: Math.max(0, Math.trunc(input.alertCount || 0))
    }
  };
}

type ParkedContext = { envelope: MarketContextEnvelope; parkedAt: number; sent: boolean };

let parked: ParkedContext | null = null;

function expired(entry: ParkedContext | null): boolean {
  return !entry || Date.now() - entry.parkedAt > CLIENT_CONTEXT_TTL_MS;
}

/** Park an envelope for the next assistant send. Replaces any previous one. */
export function parkMarketContext(envelope: MarketContextEnvelope | null): void {
  parked = envelope ? { envelope, parkedAt: Date.now(), sent: false } : null;
}

/**
 * The envelope to attach to this send, or null. Consume-once: the first send
 * after a handoff carries it; afterwards the server's stored copy is the truth
 * and resending would falsify its freshness.
 */
export function takeMarketContextForSend(): MarketContextEnvelope | null {
  if (expired(parked)) {
    parked = null;
    return null;
  }
  if (!parked || parked.sent) return null;
  parked.sent = true;
  return parked.envelope;
}

/** What the context chip shows — survives the send, dies on dismiss or expiry. */
export function peekMarketContext(): MarketContextEnvelope | null {
  if (expired(parked)) parked = null;
  return parked ? parked.envelope : null;
}

/** Chip dismissed, or the member navigated somewhere that ends the topic. */
export function clearMarketContext(): void {
  parked = null;
}
