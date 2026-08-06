import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { formatAbsoluteDate } from "../core/localTime";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  fetchReturns,
  openReturn,
  type MarketplaceReturn,
  type ReturnState
} from "../api/marketplaceCommerce";
import {
  BuyerOrder,
  buyerOrderWebUrl,
  formatOrderMoney,
  getBuyerOrder,
  listBuyerOrders,
  loadCachedBuyerOrders,
  openBuyerOrderFallback,
  supportOrderWebUrl
} from "../api/orders";
import { Panel } from "../components/Panel";
import { registerSyncInvalidation } from "../core/eventSync";
import { useScreenPerf } from "../core/useScreenPerf";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<
  RootStackParamList,
  "BuyerOrders" | "BuyerPurchases" | "BuyerOrdersDashboard" | "BuyerOrderDetail"
>;

const STATUS_COPY: Record<string, string> = {
  pending: "Pending",
  paid: "Paid",
  processing: "Processing",
  shipped: "Shipped",
  delivered: "Delivered",
  cancelled: "Cancelled",
  refunded: "Refunded",
  failed: "Needs review"
};

/**
 * List tabs, derived from `status_group` — a filter over data the server
 * already sends, never a new claim about an order. "Returns" is the one tab
 * with its own source: `/api/pulse/marketplace/returns` (fail-soft, so a
 * not-yet-deployed pack reads as no returns rather than an error tab).
 */
type OrdersTabKey = "all" | "open" | "shipped" | "delivered" | "cancelled" | "returns";

const ORDER_TABS: readonly { key: OrdersTabKey; label: string; groups?: readonly string[] }[] = [
  { key: "all", label: "All" },
  { key: "open", label: "Processing", groups: ["pending", "paid", "processing"] },
  { key: "shipped", label: "Shipped", groups: ["shipped"] },
  { key: "delivered", label: "Delivered", groups: ["delivered"] },
  { key: "cancelled", label: "Cancelled", groups: ["cancelled", "refunded", "failed"] },
  { key: "returns", label: "Returns" }
];

const RETURN_STATE_COPY: Record<ReturnState, string> = {
  opened: "Opened",
  awaiting_seller: "Waiting on seller",
  awaiting_buyer: "Waiting on you",
  under_review: "Under review",
  resolved_refund: "Refund issued",
  resolved_replacement: "Replacement arranged",
  resolved_rejected: "Rejected",
  closed: "Closed"
};

// Values mirror the accepted reason set on the returns route; anything else
// is coerced to "other" over there, so the chip list IS the contract.
const RETURN_REASONS: readonly { value: string; label: string }[] = [
  { value: "not_received", label: "Never arrived" },
  { value: "not_as_described", label: "Not as described" },
  { value: "damaged", label: "Damaged" },
  { value: "wrong_item", label: "Wrong item" },
  { value: "quality", label: "Quality issue" },
  { value: "changed_mind", label: "Changed my mind" },
  { value: "other", label: "Other" }
];

type OrdersListRow =
  | { kind: "order"; order: BuyerOrder }
  | { kind: "return"; ret: MarketplaceReturn };

export function BuyerOrdersScreen({ route, navigation }: Props) {
  const orderId = Number(
    (route.params as { orderId?: number; order_id?: number; id?: number } | undefined)?.orderId ||
      (route.params as { orderId?: number; order_id?: number; id?: number } | undefined)?.order_id ||
      (route.params as { id?: number } | undefined)?.id ||
      0
  );
  const source = (route.params as { source?: string } | undefined)?.source;
  const [orders, setOrders] = useState<BuyerOrder[]>([]);
  const [detail, setDetail] = useState<BuyerOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<OrdersTabKey>("all");
  const [returns, setReturns] = useState<readonly MarketplaceReturn[]>([]);

  useScreenPerf("BuyerOrders");

  const selected = useMemo(() => detail || orders.find((order) => order.id === orderId) || null, [detail, orderId, orders]);

  const listRows = useMemo<OrdersListRow[]>(() => {
    if (tab === "returns") return returns.map((ret) => ({ kind: "return" as const, ret }));
    const groups = ORDER_TABS.find((entry) => entry.key === tab)?.groups;
    const filtered = groups ? orders.filter((order) => groups.includes(String(order.status_group))) : orders;
    return filtered.map((order) => ({ kind: "order" as const, order }));
  }, [tab, orders, returns]);

  async function load(nextOrderId = orderId, nextSource = source, refresh = false) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const result = await listBuyerOrders({ limit: 80 });
      setOrders(result.orders || []);
      // Fail-soft by contract: an unreachable returns pack reads as [].
      setReturns(await fetchReturns("buyer"));
      if (nextOrderId) {
        const detailResult = await getBuyerOrder(nextOrderId, nextSource);
        setDetail(detailResult.order || null);
      } else {
        setDetail(null);
      }
    } catch (loadError) {
      const cached = await loadCachedBuyerOrders();
      if (cached.length) setOrders(cached);
      setError(loadError instanceof Error ? loadError.message : "Purchase history could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [orderId, source]);

  useEffect(() => {
    const unregisterOrders = registerSyncInvalidation("orders", () => load(orderId, source, true));
    return unregisterOrders;
  }, [orderId, source]);

  function openOrder(order: BuyerOrder) {
    navigation.navigate("BuyerOrderDetail", { orderId: order.id, source: order.source_table, title: order.item_title || "Order" });
  }

  function openListing(order: BuyerOrder) {
    const listingId = Number(order.marketplace_listing_id || order.item_id || order.listing?.id || 0);
    if (listingId) navigation.navigate("MarketplaceDetail", { listingId, title: order.item_title || "Marketplace" });
  }

  function openSeller(order: BuyerOrder) {
    const sellerKey = order.seller?.public_player_id || order.seller?.username || "";
    if (sellerKey) navigation.navigate("MerchantProfile", { sellerId: sellerKey, title: order.seller?.display_name || "Seller" });
    else navigation.navigate("SellerStore", { title: "Seller / Store" });
  }

  function openReturnOrder(ret: MarketplaceReturn) {
    const match = orders.find((order) => Number(order.transaction_id || order.id) === ret.transaction_id);
    if (match) openOrder(match);
  }

  const hero = (
    <View style={styles.hero}>
      <Text style={styles.kicker}>Commerce Ledger</Text>
      <Text style={styles.title}>{selected ? "Order Detail" : "Purchase History"}</Text>
      <Text style={styles.subtitle}>
        Buyer-side order state is read from PulseSoc payment ledgers. Checkout, refunds, disputes, shipping, and receipts remain server and provider controlled.
      </Text>
    </View>
  );

  const errorPanel = error ? (
    <Panel>
      <Text style={styles.errorTitle}>Commerce state unavailable</Text>
      <Text style={styles.copy}>{error}</Text>
      <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => load(orderId, source).catch(() => undefined)}>
        <Text style={styles.secondaryText}>Retry</Text>
      </Pressable>
    </Panel>
  ) : null;

  const loadingPanel = loading ? (
    <Panel>
      <Text style={styles.copy}>Loading your PulseSoc purchase timeline...</Text>
    </Panel>
  ) : null;

  const refreshControl = (
    <RefreshControl refreshing={refreshing} onRefresh={() => load(orderId, source, true).catch(() => undefined)} tintColor={colors.accent} />
  );

  if (selected) {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content} refreshControl={refreshControl}>
        {hero}
        {errorPanel}
        {loadingPanel}
        <OrderDetail
          order={selected}
          existingReturn={returns.find((ret) => ret.transaction_id === Number(selected.transaction_id || selected.id))}
          onReturnOpened={(ret) => setReturns((prev) => [ret, ...prev])}
          onBack={() => navigation.navigate("BuyerOrders", { title: "Purchase History" })}
          onListing={() => openListing(selected)}
          onSeller={() => openSeller(selected)}
        />
      </ScrollView>
    );
  }

  return (
    <FlatList
      style={styles.root}
      contentContainerStyle={styles.content}
      data={listRows}
      keyExtractor={(row) => (row.kind === "order" ? `${row.order.source_table || "order"}-${row.order.id}` : `return-${row.ret.id}`)}
      renderItem={({ item }) =>
        item.kind === "order" ? (
          <OrderRow order={item.order} onPress={() => openOrder(item.order)} />
        ) : (
          <ReturnRow ret={item.ret} onPress={() => openReturnOrder(item.ret)} />
        )
      }
      initialNumToRender={8}
      windowSize={7}
      refreshControl={refreshControl}
      ListHeaderComponent={
        <>
          {hero}
          {errorPanel}
          {loadingPanel}
          <Panel>
            <Text style={styles.sectionTitle}>Transaction Timeline</Text>
            <Text style={styles.copy}>Receipts, disputes, payment confirmation, and seller fulfillment stay aligned with existing PulseSoc backend records.</Text>
            <View style={styles.stats}>
              <Metric label="Orders" value={String(orders.length)} />
              <Metric label="Paid" value={String(orders.filter((order) => order.status_group === "paid").length)} />
              <Metric label="Open" value={String(orders.filter((order) => ["pending", "processing", "shipped"].includes(String(order.status_group))).length)} />
            </View>
          </Panel>
          <View style={styles.tabRow}>
            {ORDER_TABS.map((entry) => (
              <Pressable
                key={entry.key}
                accessibilityRole="button"
                accessibilityState={{ selected: tab === entry.key }}
                style={[styles.tabPill, tab === entry.key && styles.tabPillActive]}
                onPress={() => setTab(entry.key)}
              >
                <Text style={[styles.tabText, tab === entry.key && styles.tabTextActive]}>{entry.label}</Text>
              </Pressable>
            ))}
          </View>
        </>
      }
      ListEmptyComponent={
        tab === "returns" ? (
          <Panel>
            <Text style={styles.sectionTitle}>No returns</Text>
            <Text style={styles.copy}>Returns you open from a marketplace order will appear here with their current state.</Text>
          </Panel>
        ) : tab !== "all" ? (
          <Panel>
            <Text style={styles.sectionTitle}>Nothing here</Text>
            <Text style={styles.copy}>No purchases match this filter right now.</Text>
          </Panel>
        ) : (
          <Panel>
            <Text style={styles.sectionTitle}>No purchases yet</Text>
            <Text style={styles.copy}>Marketplace and learning purchases will appear here after checkout creates a server-side transaction.</Text>
            <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
              <Text style={styles.primaryText}>Browse Marketplace</Text>
            </Pressable>
          </Panel>
        )
      }
    />
  );
}

function OrderDetail({
  order,
  existingReturn,
  onReturnOpened,
  onBack,
  onListing,
  onSeller
}: {
  order: BuyerOrder;
  existingReturn?: MarketplaceReturn;
  onReturnOpened: (ret: MarketplaceReturn) => void;
  onBack: () => void;
  onListing: () => void;
  onSeller: () => void;
}) {
  const canOpenListing = Number(order.marketplace_listing_id || order.item_id || order.listing?.id || 0) > 0;
  // Same gate the returns route enforces: marketplace physical/product
  // purchases with a real transaction row. Everything else keeps the
  // existing web support path only.
  const returnEligible = order.source_table === "seller_transactions" && order.item_type === "marketplace_product" && Number(order.transaction_id || order.id || 0) > 0;
  return (
    <>
      <Panel>
        <View style={styles.detailHeader}>
          <View style={styles.orb} />
          <View style={styles.flex}>
            <Text style={styles.sectionTitle}>{order.item_title || order.title || "PulseSoc purchase"}</Text>
            <Text style={styles.meta}>Order #{order.id} · {String(order.item_type || "purchase").replace(/_/g, " ")}</Text>
          </View>
          <StatusPill status={String(order.status_group || order.status || "pending")} />
        </View>
        <View style={styles.timeline}>
          <TimelineStep label="Created" value={formatDate(order.created_at)} active />
          <TimelineStep label="Payment" value={STATUS_COPY[String(order.status_group)] || String(order.status || "Pending")} active={["paid", "processing", "shipped", "delivered"].includes(String(order.status_group))} />
          <TimelineStep label="Fulfillment" value={order.tracking?.message || "Provider controlled"} active={["shipped", "delivered"].includes(String(order.status_group))} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Financial summary</Text>
        <Line label="Amount" value={formatOrderMoney(order)} />
        <Line label="Currency" value={String(order.currency || "USD")} />
        <Line label="Payment" value={String(order.payment_status || order.status_group || "pending")} />
        <Line label="Updated" value={formatDate(order.updated_at)} />
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Seller and item</Text>
        <Line label="Seller" value={order.seller?.display_name || "PulseSoc Seller"} />
        <Line label="Item" value={order.item_title || order.title || "PulseSoc purchase"} />
        <View style={styles.buttonRow}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={onSeller}>
            <Text style={styles.secondaryText}>View Seller</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: !canOpenListing }} style={[styles.secondaryButton, !canOpenListing && styles.disabledButton]} disabled={!canOpenListing} onPress={onListing}>
            <Text style={styles.secondaryText}>Open Listing</Text>
          </Pressable>
        </View>
      </Panel>

      {returnEligible ? (
        <ReturnPanel order={order} existing={existingReturn} onOpened={onReturnOpened} />
      ) : null}

      <Panel>
        <Text style={styles.sectionTitle}>Buyer controls</Text>
        <Text style={styles.copy}>Receipt, support, dispute, shipping, and provider pages open through existing PulseSoc web/provider flows.</Text>
        <View style={styles.buttonRow}>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => openBuyerOrderFallback(order.receipt_url || buyerOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.primaryText}>View Receipt</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => openBuyerOrderFallback(order.dispute_url || order.support_url || supportOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.secondaryText}>Support</Text>
          </Pressable>
        </View>
        <Pressable accessibilityRole="button" style={styles.ghostButton} onPress={onBack}>
          <Text style={styles.ghostText}>Back to Purchase History</Text>
        </Pressable>
      </Panel>
    </>
  );
}

function ReturnPanel({ order, existing, onOpened }: { order: BuyerOrder; existing?: MarketplaceReturn; onOpened: (ret: MarketplaceReturn) => void }) {
  const [reason, setReason] = useState("not_as_described");
  const [explanation, setExplanation] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  if (existing) {
    return (
      <Panel>
        <Text style={styles.sectionTitle}>Return</Text>
        <Line label="State" value={RETURN_STATE_COPY[existing.state] || String(existing.state)} />
        <Line label="Reason" value={RETURN_REASONS.find((entry) => entry.value === existing.reason)?.label || existing.reason} />
        <Line label="Opened" value={formatDate(existing.created_at)} />
        <Line label="Updated" value={formatDate(existing.updated_at)} />
      </Panel>
    );
  }

  const canSubmit = !busy && explanation.trim().length > 0;

  async function submit() {
    if (busy) return;
    setBusy(true);
    setFormError("");
    try {
      const ret = await openReturn({
        transactionId: Number(order.transaction_id || order.id || 0),
        reason,
        explanation: explanation.trim()
      });
      onOpened(ret);
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : "The return could not be opened. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel>
      <Text style={styles.sectionTitle}>Request a return</Text>
      <Text style={styles.copy}>Pick a reason and describe what happened. The seller responds first; unresolved cases can be escalated for review.</Text>
      <View style={styles.chipRow}>
        {RETURN_REASONS.map((entry) => (
          <Pressable
            key={entry.value}
            accessibilityRole="button"
            accessibilityState={{ selected: reason === entry.value }}
            style={[styles.chip, reason === entry.value && styles.chipActive]}
            onPress={() => setReason(entry.value)}
          >
            <Text style={[styles.chipText, reason === entry.value && styles.chipTextActive]}>{entry.label}</Text>
          </Pressable>
        ))}
      </View>
      <TextInput
        style={styles.input}
        value={explanation}
        onChangeText={setExplanation}
        placeholder="What happened?"
        placeholderTextColor={colors.muted}
        multiline
        maxLength={2000}
      />
      {formError ? <Text style={styles.formError}>{formError}</Text> : null}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: !canSubmit }}
        disabled={!canSubmit}
        style={[styles.primaryButton, !canSubmit && styles.disabledButton]}
        onPress={() => submit().catch(() => undefined)}
      >
        <Text style={styles.primaryText}>{busy ? "Opening..." : "Open return"}</Text>
      </Pressable>
    </Panel>
  );
}

function ReturnRow({ ret, onPress }: { ret: MarketplaceReturn; onPress: () => void }) {
  const reasonLabel = RETURN_REASONS.find((entry) => entry.value === ret.reason)?.label || ret.reason;
  return (
    <Pressable accessibilityRole="button" style={styles.row} onPress={onPress}>
      <View style={styles.rowGlow} />
      <View style={styles.flex}>
        <Text style={styles.rowTitle} numberOfLines={1}>{reasonLabel}</Text>
        <Text style={styles.meta} numberOfLines={1}>Opened {formatDate(ret.created_at)} · Updated {formatDate(ret.updated_at)}</Text>
      </View>
      <View style={styles.rowAmount}>
        <View style={[styles.statusPill, { borderColor: colors.warning }]}>
          <Text style={[styles.statusText, { color: colors.warning }]}>{RETURN_STATE_COPY[ret.state] || String(ret.state)}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function OrderRow({ order, onPress }: { order: BuyerOrder; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={styles.row} onPress={onPress}>
      <View style={styles.rowGlow} />
      <View style={styles.flex}>
        <Text style={styles.rowTitle} numberOfLines={1}>{order.item_title || order.title || "PulseSoc purchase"}</Text>
        <Text style={styles.meta} numberOfLines={1}>{order.seller?.display_name || "PulseSoc Seller"} · {formatDate(order.created_at)}</Text>
      </View>
      <View style={styles.rowAmount}>
        <Text style={styles.amount}>{formatOrderMoney(order)}</Text>
        <StatusPill status={String(order.status_group || order.status || "pending")} />
      </View>
    </Pressable>
  );
}

function StatusPill({ status }: { status: string }) {
  const normalized = String(status || "pending").toLowerCase();
  const color = normalized === "paid" || normalized === "delivered" ? colors.accent : normalized === "failed" || normalized === "cancelled" ? colors.danger : colors.warning;
  return (
    <View style={[styles.statusPill, { borderColor: color }]}>
      <Text style={[styles.statusText, { color }]}>{STATUS_COPY[normalized] || normalized}</Text>
    </View>
  );
}

function TimelineStep({ label, value, active }: { label: string; value: string; active?: boolean }) {
  return (
    <View style={styles.timelineStep}>
      <View style={[styles.timelineDot, active && styles.timelineDotActive]} />
      <View style={styles.flex}>
        <Text style={styles.meta}>{label}</Text>
        <Text style={styles.copy}>{value}</Text>
      </View>
    </View>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.meta}>{label}</Text>
    </View>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.line}>
      <Text style={styles.meta}>{label}</Text>
      <Text style={styles.lineValue}>{value}</Text>
    </View>
  );
}

function formatDate(value?: string) {
  if (!value) return "Pending";
  return formatAbsoluteDate(value, { withYear: true }) || value;
}

const styles = createThemedStyles(() => ({
  root: { flex: 1, backgroundColor: colors.background },
  content: { gap: 14, padding: 18 },
  hero: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    overflow: "hidden",
    padding: 16
  },
  kicker: { color: colors.accent, fontSize: 12, fontWeight: "900", letterSpacing: 0, textTransform: "uppercase" },
  title: { color: colors.text, fontSize: 28, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "900" },
  copy: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  errorTitle: { color: colors.danger, fontSize: 17, fontWeight: "900" },
  stats: { flexDirection: "row", gap: 10 },
  metric: { backgroundColor: colors.surfaceRaised, borderRadius: 8, flex: 1, padding: 10 },
  metricValue: { color: colors.text, fontSize: 20, fontWeight: "900" },
  row: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    minHeight: 74,
    overflow: "hidden",
    padding: 12
  },
  rowGlow: { backgroundColor: colors.accent, borderRadius: 8, height: 38, opacity: 0.8, width: 4 },
  rowTitle: { color: colors.text, fontSize: 16, fontWeight: "900" },
  rowAmount: { alignItems: "flex-end", gap: 6 },
  amount: { color: colors.text, fontWeight: "900" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  detailHeader: { alignItems: "center", flexDirection: "row", gap: 12 },
  orb: {
    backgroundColor: colors.accent,
    borderColor: colors.accentStrong,
    borderRadius: 24,
    borderWidth: StyleSheet.hairlineWidth,
    height: 46,
    shadowColor: colors.accent,
    shadowOpacity: 0.35,
    shadowRadius: 14,
    width: 46
  },
  flex: { flex: 1 },
  timeline: { gap: 12, marginTop: 8 },
  timelineStep: { flexDirection: "row", gap: 10 },
  timelineDot: { borderColor: colors.border, borderRadius: 8, borderWidth: 2, height: 14, marginTop: 4, width: 14 },
  timelineDotActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  statusPill: { borderRadius: 999, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 9, paddingVertical: 5 },
  statusText: { fontSize: 11, fontWeight: "900", textTransform: "uppercase" },
  line: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", gap: 12 },
  lineValue: { color: colors.text, flex: 1, fontWeight: "800", textAlign: "right" },
  buttonRow: { flexDirection: "row", gap: 10 },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flex: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12
  },
  primaryText: { color: "#08110f", fontWeight: "900" },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12
  },
  secondaryText: { color: colors.text, fontWeight: "900", textAlign: "center" },
  ghostButton: { alignItems: "center", minHeight: 42, justifyContent: "center" },
  ghostText: { color: colors.accentStrong, fontWeight: "900" },
  disabledButton: { opacity: 0.45 },
  tabRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tabPill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 13,
    paddingVertical: 6
  },
  tabPillActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  tabText: { color: colors.muted, fontSize: 13, fontWeight: "800" },
  tabTextActive: { color: "#08110f" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 32,
    justifyContent: "center",
    paddingHorizontal: 11,
    paddingVertical: 5
  },
  chipActive: { backgroundColor: colors.surfaceRaised, borderColor: colors.accent },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  chipTextActive: { color: colors.text },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    fontSize: 14,
    minHeight: 84,
    padding: 10,
    textAlignVertical: "top"
  },
  formError: { color: colors.danger, fontSize: 13, fontWeight: "700" }
}));
