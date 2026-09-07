/**
 * The eight states a dropshipping screen can be in, and the pills that render
 * supplier facts without flattening them.
 *
 * ## Why one component owns all eight states
 *
 * `DropshippingStateView` returns `null` for `READY` and renders a block for
 * every other state. A screen calls it once, and when it returns a block the
 * screen renders that *instead of* its content, not above it. That is the
 * mechanical reason an error and an empty state cannot co-render here: they are
 * branches of one `switch` over a closed union, so there is no arrangement of
 * booleans that produces both.
 *
 * The distinction being protected is not cosmetic. "This supplier has no
 * products matching your search" and "we could not reach your supplier" look
 * identical if you render the empty copy whenever the list is short — and a
 * merchant who reads the first when the second is true goes looking for a new
 * supplier.
 *
 * ## Why unknown gets its own pill
 *
 * `StockPill` renders `UNKNOWN` as "Stock unknown" in the neutral colour, not as
 * the out-of-stock red. A variant nobody could get a reading for has not sold
 * out. Same for `MarginPill`: no margin reading is grey, not "0%" and not the
 * negative-margin red, because a merchant who sees red re-prices a product whose
 * cost they simply have not fetched yet.
 *
 * ## Money is nullable all the way to the glyph
 *
 * `costText` returns `null` rather than a formatted zero when the amount is
 * `null`. Callers render the em dash themselves. A `?? 0` anywhere in this file
 * would undo the whole `centsOrNull` chain in one character.
 */

import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { StoreRowSkeleton, StoreSectionError } from "../store";
import { storeLight } from "../../theme/storeLight";
import type { DropshippingState } from "../../api/dropshipping";

/** What a screen prints where a number would go when there is no number. */
export const NO_VALUE = "—";

type Formatters = { currency: (value: number, options?: { currency?: string }) => string };

/**
 * A supplier amount as text, or `null` when there is no amount.
 *
 * Deliberately not "0.00 when unknown". The caller decides what absence looks
 * like, and every caller in this feature decides it looks like {@link NO_VALUE}.
 */
export function costText(
  cents: number | null,
  currency: string | null,
  formatters: Formatters
): string | null {
  if (cents === null) return null;
  return formatters.currency(cents / 100, { currency: currency || "USD" });
}

/** A low–high pair, collapsing to one figure when both ends agree. */
export function costRangeText(
  low: number | null,
  high: number | null,
  currency: string | null,
  formatters: Formatters
): string | null {
  const lowText = costText(low, currency, formatters);
  if (lowText === null) return null;
  if (high === null || high === low) return lowText;
  return `${lowText} – ${costText(high, currency, formatters)}`;
}

/* ------------------------------------------------------------------ *
 * Vocabulary pills
 * ------------------------------------------------------------------ */

const STOCK_COPY: Record<string, { label: string; tone: "success" | "error" | "neutral" }> = {
  IN_STOCK: { label: "In stock", tone: "success" },
  OUT_OF_STOCK: { label: "Out of stock", tone: "error" },
  UNKNOWN: { label: "Stock unknown", tone: "neutral" }
};

/**
 * Stock as the supplier reported it.
 *
 * An unrecognised word falls to the `UNKNOWN` treatment rather than to
 * out-of-stock, so a provider inventing a fourth state degrades to "we don't
 * know" instead of to "you can't sell this".
 */
export function StockPill({ state, quantity }: { state: string; quantity?: number | null }) {
  const copy = STOCK_COPY[String(state).toUpperCase()] || STOCK_COPY.UNKNOWN;
  const suffix = copy.tone === "success" && typeof quantity === "number" ? ` · ${quantity}` : "";
  return (
    <View style={[styles.pill, { borderColor: storeLight.status[copy.tone] }]}>
      <Text style={[styles.pillText, { color: storeLight.status[copy.tone] }]}>
        {copy.label}
        {suffix}
      </Text>
    </View>
  );
}

const MARGIN_COPY: Record<string, { label: string; tone: "success" | "warning" | "error" | "neutral" }> = {
  HEALTHY: { label: "Healthy margin", tone: "success" },
  LOW_MARGIN: { label: "Low margin", tone: "warning" },
  CRITICAL_MARGIN: { label: "Very low margin", tone: "warning" },
  NEGATIVE_MARGIN: { label: "Selling at a loss", tone: "error" },
  UNKNOWN: { label: "Margin unknown", tone: "neutral" }
};

export function MarginPill({ state, percent }: { state: string; percent?: number | null }) {
  const copy = MARGIN_COPY[String(state).toUpperCase()] || MARGIN_COPY.UNKNOWN;
  // The percentage is printed only when there is one. A margin state of UNKNOWN
  // with "0%" beside it is the exact misreading this whole chain avoids.
  const suffix = typeof percent === "number" && Number.isFinite(percent) ? ` · ${Math.round(percent)}%` : "";
  return (
    <View style={[styles.pill, { borderColor: storeLight.status[copy.tone] }]}>
      <Text style={[styles.pillText, { color: storeLight.status[copy.tone] }]}>
        {copy.label}
        {suffix}
      </Text>
    </View>
  );
}

/** The supplier a row came from. Provider is a value, so this takes any string. */
export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <View style={styles.providerBadge}>
      <Text style={styles.providerText}>{provider ? provider.toUpperCase() : "SUPPLIER"}</Text>
    </View>
  );
}

/**
 * The sandbox marker.
 *
 * Shown wherever a connection is displayed, because a merchant looking at a
 * supplier order needs to know it will not be paid for or shipped. Silence here
 * would read as production.
 */
export function EnvironmentBadge({ environment }: { environment: string }) {
  if (String(environment).toUpperCase() === "PRODUCTION") return null;
  return (
    <View style={styles.sandboxBadge}>
      <Text style={styles.sandboxText}>Sandbox</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * The state view
 * ------------------------------------------------------------------ */

/**
 * Whether the state view owns the screen, or the screen renders its own content.
 *
 * Callers need this answer *before* they render, and a React element is truthy
 * even when it renders to `null` — so `const block = <StateView …/>; block ? [] :
 * rows` silently deletes the READY branch and leaves every screen permanently
 * empty. Asking the question of the state, not of the element, is the whole
 * point of exporting it.
 */
export function stateOwnsScreen(state: DropshippingState): boolean {
  return state !== "READY" && state !== "STALE";
}

export type DropshippingStateViewProps = {
  state: DropshippingState;
  /** The thing that did not load, named. "Products", "Your import cart". */
  subject: string;
  onRetry: (() => void) | null;
  /** Opens the Suppliers screen. Absent when this screen *is* that screen. */
  onFixConnection?: (() => void) | null;
  /** Seconds the server asked us to wait, when it said. */
  retryAfter?: number | null;
  /** Copy for `EMPTY`. Empty is an invitation, so the screen supplies its own. */
  empty?: { title: string; body: string };
  reducedMotion: boolean;
  /** Skeleton rows drawn while `LOADING`. */
  skeletonRows?: number;
};

/**
 * Renders the current state, or `null` when the screen should render itself.
 *
 * `READY` and `STALE` both return `null`: stale data is real data and belongs on
 * screen. The staleness *notice* is a separate strip the screen draws above its
 * content, because hiding a merchant's cart behind a "this may be out of date"
 * card would be a worse lie than the staleness.
 */
export function DropshippingStateView({
  state,
  subject,
  onRetry,
  onFixConnection,
  retryAfter,
  empty,
  reducedMotion,
  skeletonRows = 4
}: DropshippingStateViewProps) {
  // Derived from the same predicate the callers use, so the two can never
  // disagree about which states hide a screen's content.
  if (!stateOwnsScreen(state)) return null;

  switch (state) {
    case "LOADING":
      return (
        <View accessibilityLabel={`Loading ${subject}`}>
          {Array.from({ length: skeletonRows }, (_, index) => (
            <StoreRowSkeleton key={index} reducedMotion={reducedMotion} />
          ))}
        </View>
      );

    case "EMPTY":
      return (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>{empty?.title || `No ${subject.toLowerCase()} yet.`}</Text>
          {empty?.body ? <Text style={styles.emptyBody}>{empty.body}</Text> : null}
        </View>
      );

    case "SUPPLIER_DISCONNECTED":
      return (
        <StoreSectionError
          message={`Your supplier connection needs attention, so ${subject.toLowerCase()} can't load.`}
          // Retrying an expired credential fails identically, so the button
          // goes to the place that can actually fix it.
          onRetry={onFixConnection || onRetry}
          actionLabel={onFixConnection ? "Check suppliers" : "Try again"}
          reducedMotion={reducedMotion}
        />
      );

    case "PROVIDER_UNAVAILABLE":
      return (
        <StoreSectionError
          message={
            typeof retryAfter === "number"
              ? `Your supplier isn't responding. Try again in about ${retryAfter} seconds.`
              : "Your supplier isn't responding right now. Nothing in your store has changed."
          }
          onRetry={onRetry}
          reducedMotion={reducedMotion}
        />
      );

    case "UNAUTHORIZED":
      return (
        <StoreSectionError
          message="You're not signed in to this store any more."
          onRetry={onRetry}
          actionLabel="Sign in"
          reducedMotion={reducedMotion}
        />
      );

    default:
      return (
        <StoreSectionError
          message={`${subject} didn't load.`}
          onRetry={onRetry}
          reducedMotion={reducedMotion}
        />
      );
  }
}

/**
 * The "these numbers are from a while ago" strip.
 *
 * Drawn above real content, never instead of it. The import re-fetches every
 * figure from the provider before writing anything, so staleness on this screen
 * is only ever a display concern — the strip says as much rather than implying
 * the merchant is about to import a wrong price.
 */
export function DropshippingStaleNote({ text }: { text: string }) {
  return (
    <View style={styles.stale} accessibilityLiveRegion="polite">
      <Ionicons name="time-outline" size={13} color={storeLight.text.muted} />
      <Text style={styles.staleText}>{text}</Text>
    </View>
  );
}

/**
 * A one-line explanation of something this app cannot show yet.
 *
 * Used where the mission's design has a surface and the backend has no source
 * for it. Says what is missing in the merchant's terms; the machine-readable
 * version is `DROPSHIPPING_DATA_GAPS`.
 */
export function DropshippingGapNote({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.gap}>
      <Ionicons name="construct-outline" size={16} color={storeLight.text.muted} />
      <View style={styles.gapBody}>
        <Text style={styles.gapTitle}>{title}</Text>
        <Text style={styles.gapText}>{body}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    borderWidth: 1,
    borderRadius: storeLight.radius.pill,
    paddingHorizontal: 8,
    paddingVertical: 2,
    alignSelf: "flex-start"
  },
  pillText: { fontSize: 11, fontWeight: "700" },
  providerBadge: {
    borderRadius: storeLight.radius.control,
    paddingHorizontal: 6,
    paddingVertical: 2,
    backgroundColor: storeLight.bg.skeleton,
    alignSelf: "flex-start"
  },
  providerText: { fontSize: 10, fontWeight: "800", color: storeLight.text.muted, letterSpacing: 0.5 },
  sandboxBadge: {
    borderRadius: storeLight.radius.control,
    paddingHorizontal: 6,
    paddingVertical: 2,
    backgroundColor: storeLight.bg.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.warning,
    alignSelf: "flex-start"
  },
  sandboxText: { fontSize: 10, fontWeight: "800", color: storeLight.status.warning },
  empty: {
    padding: 20,
    gap: 8,
    alignItems: "flex-start",
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card
  },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  emptyBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  stale: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 8
  },
  staleText: { fontSize: 11, color: storeLight.text.muted, flex: 1 },
  gap: {
    flexDirection: "row",
    gap: 10,
    padding: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  gapBody: { flex: 1, gap: 4 },
  gapTitle: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  gapText: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 }
});
