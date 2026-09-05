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
 *
 * ## Dismissal is a message to the server, not a change of mind about a label
 *
 * Because the server *persists* the envelope per conversation, forgetting it
 * here is not enough to stop it steering "it". Before this module tracked a
 * pending clear, dismissing the chip removed the words from the screen and left
 * the assistant still resolving "how is it doing?" to the coin the member had
 * just said they were finished with — the exact failure of a chip that is
 * decoration rather than state. `clearMarketContext` now arms a one-shot flag
 * that rides the next send as `ui_context.market_context_cleared`, and the
 * server drops its stored copy on arrival.
 *
 * `buildUndxSendContext` is the single place the two facts — "here is a fresh
 * envelope" and "the member ended the topic" — are turned into request fields,
 * so the chip and the request can never disagree about which of them is true.
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
  /**
   * The provider's canonical asset id when the screen has one ("bitcoin").
   * Symbols collide across chains and listings, so the id is the identity and
   * the symbol is a label. When a screen cannot supply it the envelope falls
   * back to the lowercased symbol and the server upgrades it against the
   * canonical market board — see `sanitize_market_context`. The client's guess
   * is never the last word on identity.
   */
  assetId?: string | null;
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
      id: String(input.assetId || "").trim().toLowerCase().slice(0, 60) || symbol.toLowerCase(),
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

/**
 * Set when the member dismisses the chip while the server may still be holding
 * an envelope for this conversation. Cleared once a send has carried the news.
 * A pending clear outlives a failed send on purpose: the topic is over whether
 * or not the network agreed, and the alternative is a context the member
 * believes they ended.
 */
let pendingClear = false;

function expired(entry: ParkedContext | null): boolean {
  return !entry || Date.now() - entry.parkedAt > CLIENT_CONTEXT_TTL_MS;
}

/**
 * Park an envelope for the next assistant send. Replaces any previous one.
 *
 * A new asset also cancels a pending clear: the member has told us what "it"
 * means more recently than they told us it meant nothing, and sending both
 * would ask the server to drop the envelope arriving in the same request.
 */
export function parkMarketContext(envelope: MarketContextEnvelope | null): void {
  parked = envelope ? { envelope, parkedAt: Date.now(), sent: false } : null;
  if (envelope) pendingClear = false;
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

/**
 * Chip dismissed, or the member navigated somewhere that ends the topic.
 *
 * Arms the pending clear as well as forgetting the local envelope, because the
 * server keeps its own copy across turns and would otherwise go on resolving
 * "it" to an asset the member has visibly finished with.
 */
export function clearMarketContext(): void {
  parked = null;
  pendingClear = true;
}

/**
 * True once, if the server needs telling that the topic ended.
 *
 * Consume-once for the same reason the envelope is: the server acts on arrival,
 * and repeating the instruction on every later turn would keep deleting a
 * context the member may since have replaced from another screen.
 */
export function takeMarketContextClearForSend(): boolean {
  if (!pendingClear) return false;
  pendingClear = false;
  return true;
}

/**
 * The market fields for one outgoing assistant request.
 *
 * Stage 3's single source of truth, expressed as code: the chip renders
 * `peekMarketContext()` and the request carries whatever this returns, and both
 * read the same parked state. There is deliberately no way to attach an
 * envelope to a request without it being the one the chip is showing, and no
 * way to dismiss the chip without the next request saying so.
 *
 * Returns an empty object on an ordinary turn, so a message that neither starts
 * nor ends a topic costs nothing and leaves the server's stored context alone.
 */
export function buildUndxSendContext(): {
  market_context?: MarketContextEnvelope;
  market_context_cleared?: true;
} {
  const envelope = takeMarketContextForSend();
  if (envelope) return { market_context: envelope };
  return takeMarketContextClearForSend() ? { market_context_cleared: true } : {};
}

/** Test seam only: forget both the envelope and any pending clear. */
export function resetMarketContextForTests(): void {
  parked = null;
  pendingClear = false;
}
