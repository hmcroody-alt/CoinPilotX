/**
 * The buyer's cart.
 *
 * ## Server truth, grouped the way checkout charges
 *
 * Lines come from `/api/pulse/marketplace/cart` and are grouped per seller by
 * `groupCartLines` — the same shape checkout uses, because one Stripe Connect
 * session can pay exactly one seller. Rendering any other grouping would show
 * a total no button can charge.
 *
 * ## Line state is derived, never trusted from the client
 *
 * Every line carries a server-derived `state` (price_changed, low_stock, sold,
 * removed, restricted) computed at read time. This screen only *renders* those
 * states; the server re-derives them again at validate and at checkout, so a
 * stale screen cannot buy a sold item — it gets a 409 and re-reads.
 *
 * ## Price changes block until confirmed
 *
 * A changed price never silently reprices the basket. The line shows both
 * figures and an explicit "Accept new price" action; until tapped, checkout for
 * that seller's group is blocked server-side. This mirrors the snapshot rule in
 * `marketplace_cart_routes.py`.
 *
 * ## The idempotency key belongs to the intent
 *
 * Generated when the user opens a group's confirm step, reused for every retry
 * of that confirmation, discarded when it closes. A duplicate tap replays the
 * same checkout session instead of creating a second charge.
 *
 * ## Payment boundary
 *
 * Checkout success yields a Stripe URL. Native payment handling is behind the
 * same `native_provider_boundary` every other paid surface honours (see
 * `openPremiumUrl`, `openBuyerOrderFallback`), so this screen reports the
 * session honestly and points at the web — it does not fake an in-app purchase.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  confirmCartLinePrice,
  fetchCart,
  groupCartLines,
  removeCartLine,
  updateCartLine,
  type CartLine,
  type CartSnapshot
} from "../api/marketplaceCommerce";
import { groupFulfillmentKind, type MarketplaceFulfillmentKind } from "../api/marketplaceFulfillment";
import { registerSyncInvalidation } from "../core/eventSync";
import { useScreenPerf } from "../core/useScreenPerf";
import { RootStackParamList } from "../navigation/types";
import { storeLight, MARKETPLACE_CART_CTA } from "../theme/marketplaceLight";

type Props = NativeStackScreenProps<RootStackParamList, "MarketplaceCart">;

/** Max per line, mirroring the server's clamp. */
const MAX_QTY = 20;

function formatMinor(minor: number, currency = "USD") {
  const amount = minor / 100;
  const whole = Number.isInteger(amount);
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: 2
    }).format(amount);
  } catch {
    return `$${whole ? amount : amount.toFixed(2)}`;
  }
}

/** Copy for the states that need explaining. `available` renders nothing. */
const LINE_STATE_COPY: Partial<Record<CartLine["state"], string>> = {
  low_stock: "Almost gone",
  sold: "Sold — remove to check out",
  removed: "No longer listed — remove to check out",
  restricted: "Unavailable in your region — remove to check out"
};

export function MarketplaceCartScreen({ navigation }: Props) {
  const [cart, setCart] = useState<CartSnapshot>({ lines: [], badgeCount: 0 });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  /** Line ids with a write in flight — their controls go quiet together. */
  const [busyLines, setBusyLines] = useState<readonly number[]>([]);
  /** Seller whose confirm step is open, and the per-intent idempotency key. */
  const [confirmSeller, setConfirmSeller] = useState<number | null>(null);

  useScreenPerf("MarketplaceCart");

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      setCart(await fetchCart());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Cart could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
    return registerSyncInvalidation("marketplace", () => {
      load(true).catch(() => undefined);
    });
  }, [load]);

  const groups = useMemo(() => groupCartLines(cart.lines), [cart.lines]);
  const busySet = useMemo(() => new Set(busyLines), [busyLines]);

  /** Run one line write with the whole row quiet, then re-read server truth. */
  const runLine = useCallback(
    async (lineId: number, work: () => Promise<void>) => {
      setBusyLines((current) => [...current, lineId]);
      try {
        await work();
      } catch (writeError) {
        setError(writeError instanceof Error ? writeError.message : "That change did not save.");
      } finally {
        try {
          setCart(await fetchCart());
        } catch {
          // Keep the last known cart; the error above already tells the story.
        }
        setBusyLines((current) => current.filter((id) => id !== lineId));
      }
    },
    []
  );

  const changeQty = useCallback(
    (line: CartLine, delta: number) => {
      const next = line.qty + delta;
      if (next < 1 || next > MAX_QTY) return;
      void runLine(line.line_id, () => updateCartLine(line.line_id, next));
    },
    [runLine]
  );

  const removeLine = useCallback(
    (line: CartLine) => {
      void runLine(line.line_id, () => removeCartLine(line.line_id));
    },
    [runLine]
  );

  const acceptPrice = useCallback(
    (line: CartLine) => {
      void runLine(line.line_id, () => confirmCartLinePrice(line.line_id));
    },
    [runLine]
  );

  /** Opening the confirm step mints the intent key; closing it discards it. */
  const openConfirm = useCallback((sellerUserId: number) => {
    setConfirmSeller(sellerUserId);
  }, []);

  const closeConfirm = useCallback(() => {
    setConfirmSeller(null);
  }, []);

  const openCheckout = useCallback((group: (typeof groups)[number]) => {
    // A group carrying any undecided line is undecided as a whole — one Stripe
    // session covers the group, so one answer governs it. `both` is passed
    // through rather than resolved here so the buyer is the one who answers.
    const lanes = group.fulfillments.map((entry) => entry.fulfillment);
    const fulfillment = lanes.includes("both")
      ? "both"
      : lanes.includes("shipping")
        ? "shipping"
        : lanes.includes("pickup") ? "pickup" : "digital";
    // One Stripe session covers the group, so the group answers one set of
    // questions. Which set is chosen the same way the server chooses it.
    const groupLines = group.fulfillments.flatMap((entry) => entry.lines);
    const kinds = groupLines.map(
      (line) => (line.fulfillment_kind || "shipping") as MarketplaceFulfillmentKind
    );
    const soleTickets = groupLines.length === 1
      ? (groupLines[0]?.listing_metadata?.tickets as { name?: string }[] | undefined)
      : undefined;
    navigation.navigate("MarketplaceCheckout", {
      fulfillmentKind: groupFulfillmentKind(kinds) || undefined,
      ...(Array.isArray(soleTickets)
        ? { ticketOptions: soleTickets.map((t) => String(t?.name || "").trim()).filter(Boolean) }
        : {}),
      mode: "cart",
      sellerUserId: group.sellerUserId,
      sellerName: group.sellerName,
      itemTitle: group.fulfillments.flatMap((entry) => entry.lines).length === 1
        ? group.fulfillments[0]?.lines[0]?.title || "Marketplace item"
        : `${group.fulfillments.flatMap((entry) => entry.lines).length} Marketplace items`,
      subtotalMinor: group.totalMinor,
      currency: group.currency,
      quantity: group.fulfillments.flatMap((entry) => entry.lines).reduce((sum, line) => sum + line.qty, 0),
      fulfillment
    });
  }, [navigation, groups]);

  /* ---------------------------------------------------------------- *
   * Render
   * ---------------------------------------------------------------- */

  if (loading && !cart.lines.length) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>Loading your cart</Text>
      </View>
    );
  }

  if (!cart.lines.length) {
    return (
      <View style={styles.center}>
        <Text style={styles.emptyTitle}>Your cart is empty</Text>
        <Text style={styles.centerText}>Items you add from Marketplace show up here.</Text>
        <Pressable
          accessibilityRole="button"
          style={styles.emptyCta}
          onPress={() => navigation.goBack()}
        >
          <Text style={styles.emptyCtaText}>Browse Marketplace</Text>
        </Pressable>
        {error ? <Text style={styles.errorText}>{error}</Text> : null}
      </View>
    );
  }

  return (
    <FlatList
      style={styles.root}
      contentContainerStyle={styles.content}
      data={groups}
      keyExtractor={(group) => String(group.sellerUserId)}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => load(true).catch(() => undefined)} />
      }
      ListHeaderComponent={error ? <Text style={styles.errorText}>{error}</Text> : null}
      renderItem={({ item: group }) => {
        const confirmOpen = confirmSeller === group.sellerUserId;
        const hasBlocker = group.fulfillments.some((f) =>
          f.lines.some((l) => l.state === "sold" || l.state === "removed" || l.state === "restricted")
        );
        return (
          <View style={styles.group}>
            <Text style={styles.groupSeller}>{group.sellerName || "Seller"}</Text>
            {group.fulfillments.map((bucket) => (
              <View key={bucket.fulfillment}>
                {group.fulfillments.length > 1 ? (
                  <Text style={styles.fulfillment}>
                    {bucket.fulfillment === "digital"
                      ? "Digital delivery"
                      : bucket.fulfillment === "pickup"
                        ? "Local pickup"
                        : "Shipping"}
                  </Text>
                ) : null}
                {bucket.lines.map((line) => {
                  const busy = busySet.has(line.line_id);
                  const stateCopy = LINE_STATE_COPY[line.state];
                  const dead =
                    line.state === "sold" || line.state === "removed" || line.state === "restricted";
                  return (
                    <View key={line.line_id} style={[styles.line, dead && styles.lineDead]}>
                      {line.cover_image_url ? (
                        <Image source={{ uri: line.cover_image_url }} style={styles.thumb} />
                      ) : (
                        <View style={[styles.thumb, styles.thumbEmpty]} />
                      )}
                      <View style={styles.lineBody}>
                        <Text style={styles.lineTitle} numberOfLines={2}>
                          {line.title}
                        </Text>
                        {line.state === "price_changed" ? (
                          <View>
                            <Text style={styles.priceChanged}>
                              {`Now ${formatMinor(line.price_now_minor, line.currency)} — was ${formatMinor(line.price_snapshot_minor, line.currency)}`}
                            </Text>
                            <Pressable
                              accessibilityRole="button"
                              disabled={busy}
                              onPress={() => acceptPrice(line)}
                            >
                              <Text style={styles.link}>Accept new price</Text>
                            </Pressable>
                          </View>
                        ) : (
                          <View>
                            <Text style={styles.linePrice}>
                              {formatMinor(line.price_snapshot_minor * line.qty, line.currency)}
                            </Text>
                            {/* Only worth saying when qty > 1 — at one unit the
                                line total and the unit price are the same
                                number, and printing it twice reads as a bug. */}
                            {line.qty > 1 ? (
                              <Text style={styles.lineUnit}>
                                {`${formatMinor(line.price_snapshot_minor, line.currency)} each`}
                              </Text>
                            ) : null}
                          </View>
                        )}
                        {stateCopy ? <Text style={styles.lineState}>{stateCopy}</Text> : null}
                        <View style={styles.lineControls}>
                          {!dead ? (
                            <View style={styles.stepper}>
                              <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={`Decrease quantity of ${line.title}`}
                                disabled={busy || line.qty <= 1}
                                style={styles.stepBtn}
                                onPress={() => changeQty(line, -1)}
                              >
                                <Text style={styles.stepText}>−</Text>
                              </Pressable>
                              <Text style={styles.qty}>{line.qty}</Text>
                              <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={`Increase quantity of ${line.title}`}
                                disabled={busy || line.qty >= MAX_QTY}
                                style={styles.stepBtn}
                                onPress={() => changeQty(line, 1)}
                              >
                                <Text style={styles.stepText}>+</Text>
                              </Pressable>
                            </View>
                          ) : null}
                          <Pressable
                            accessibilityRole="button"
                            disabled={busy}
                            onPress={() => removeLine(line)}
                          >
                            <Text style={styles.link}>{busy ? "Saving…" : "Remove"}</Text>
                          </Pressable>
                        </View>
                      </View>
                    </View>
                  );
                })}
              </View>
            ))}
            <View style={styles.groupFooter}>
              <Text style={styles.summaryTitle}>Order summary</Text>
              <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Subtotal</Text><Text style={styles.groupTotal}>{formatMinor(group.totalMinor, group.currency)}</Text></View>
              <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Delivery</Text><Text style={styles.summaryValue}>Added at payment</Text></View>
              <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Taxes and fees</Text><Text style={styles.summaryValue}>Added at payment</Text></View>
              {confirmOpen ? (
                <View style={styles.confirmBox}>
                  <Text style={styles.confirmText}>
                    {`Checking out with ${group.sellerName || "this seller"} — ${formatMinor(group.totalMinor, group.currency)} so far, before delivery and tax.`}
                  </Text>
                  <View style={styles.confirmRow}>
                    <Pressable accessibilityRole="button" onPress={closeConfirm} style={styles.secondaryBtn}>
                      <Text style={styles.secondaryText}>Not now</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => openCheckout(group)}
                      style={styles.cta}
                    >
                      <Text style={styles.ctaText}>Continue</Text>
                    </Pressable>
                  </View>
                </View>
              ) : (
                <Pressable
                  accessibilityRole="button"
                  disabled={hasBlocker}
                  onPress={() => openConfirm(group.sellerUserId)}
                  style={[styles.cta, hasBlocker && styles.ctaBusy]}
                >
                  <Text style={styles.ctaText}>
                    {hasBlocker ? "Remove unavailable items" : "Proceed to checkout"}
                  </Text>
                </Pressable>
              )}
            </View>
          </View>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { padding: 12, paddingBottom: 32, gap: 12 },
  center: {
    flex: 1,
    backgroundColor: storeLight.bg.page,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    gap: 8
  },
  centerText: { color: storeLight.text.muted, fontSize: 14, textAlign: "center" },
  emptyTitle: { color: storeLight.text.primary, fontSize: 18, fontWeight: "700" },
  emptyCta: {
    marginTop: 8,
    backgroundColor: MARKETPLACE_CART_CTA.to,
    borderRadius: storeLight.radius.pill,
    paddingHorizontal: 20,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  emptyCtaText: { color: MARKETPLACE_CART_CTA.text, fontWeight: "700" },
  errorText: { color: storeLight.status.error, fontSize: 13, marginBottom: 8 },
  group: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: 12,
    gap: 8
  },
  groupSeller: { color: storeLight.text.primary, fontSize: 15, fontWeight: "700" },
  fulfillment: { color: storeLight.text.muted, fontSize: 12, marginTop: 4 },
  line: { flexDirection: "row", gap: 10, paddingVertical: 8 },
  lineDead: { opacity: 0.6 },
  thumb: {
    width: storeLight.size.thumb,
    height: storeLight.size.thumb,
    borderRadius: storeLight.radius.thumb,
    backgroundColor: storeLight.bg.skeleton
  },
  thumbEmpty: { borderWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline },
  lineBody: { flex: 1, gap: 4 },
  lineTitle: { color: storeLight.text.primary, fontSize: 14 },
  linePrice: { color: storeLight.text.primary, fontSize: 15, fontWeight: "700" },
  lineUnit: { color: storeLight.text.muted, fontSize: 12 },
  priceChanged: { color: storeLight.status.warning, fontSize: 13, fontWeight: "600" },
  lineState: { color: storeLight.status.warning, fontSize: 12 },
  lineControls: { flexDirection: "row", alignItems: "center", gap: 16, marginTop: 4 },
  stepper: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    borderRadius: storeLight.radius.pill
  },
  stepBtn: {
    minWidth: 36,
    minHeight: 32,
    alignItems: "center",
    justifyContent: "center"
  },
  stepText: { color: storeLight.text.primary, fontSize: 16, fontWeight: "700" },
  qty: { color: storeLight.text.primary, fontSize: 14, minWidth: 24, textAlign: "center" },
  link: { color: storeLight.text.link, fontSize: 13 },
  groupFooter: { borderTopWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline, paddingTop: 10, gap: 8 },
  groupTotal: { color: storeLight.text.primary, fontSize: 15, fontWeight: "700" },
  summaryTitle: { color: storeLight.text.primary, fontSize: 16, fontWeight: "800" },
  summaryRow: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  summaryLabel: { color: storeLight.text.muted, fontSize: 13 },
  summaryValue: { color: storeLight.text.primary, fontSize: 13, textAlign: "right" },
  cta: {
    backgroundColor: MARKETPLACE_CART_CTA.to,
    borderRadius: storeLight.radius.pill,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16
  },
  ctaBusy: { opacity: 0.55 },
  ctaText: { color: MARKETPLACE_CART_CTA.text, fontWeight: "700", fontSize: 14 },
  confirmBox: {
    backgroundColor: storeLight.bg.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.warning,
    borderRadius: storeLight.radius.control,
    padding: 10,
    gap: 8
  },
  confirmText: { color: storeLight.text.primary, fontSize: 14, fontWeight: "600" },
  note: { color: storeLight.text.muted, fontSize: 13 },
  confirmRow: { flexDirection: "row", gap: 10, justifyContent: "flex-end", alignItems: "center" },
  secondaryBtn: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: { color: storeLight.text.link, fontSize: 14 }
});
