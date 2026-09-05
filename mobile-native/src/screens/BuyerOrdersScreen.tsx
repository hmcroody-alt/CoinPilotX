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
  getBuyerOrder,
  listBuyerOrders,
  loadCachedBuyerOrders,
  openBuyerOrderFallback,
  supportOrderWebUrl
} from "../api/orders";
import { Panel } from "../components/Panel";
import { registerSyncInvalidation } from "../core/eventSync";
import { useScreenPerf } from "../core/useScreenPerf";
import { Formatters, useFormatters, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<
  RootStackParamList,
  "BuyerOrders" | "BuyerPurchases" | "BuyerOrdersDashboard" | "BuyerOrderDetail"
>;

/**
 * Server status slugs mapped to catalog keys rather than English literals, so a
 * status pill reads in the user's language without the screen carrying a second
 * copy of the wording.
 */
const STATUS_KEYS: Record<string, string> = {
  pending: "commerce:orders.statusPending",
  paid: "commerce:orders.statusPaid",
  processing: "commerce:orders.statusProcessing",
  shipped: "commerce:orders.statusShipped",
  delivered: "commerce:orders.statusDelivered",
  cancelled: "commerce:orders.statusCancelled",
  refunded: "commerce:orders.statusRefunded",
  failed: "commerce:orders.statusFailed"
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
  const { t } = useTranslation();
  const fmt = useFormatters();
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
      setError(loadError instanceof Error ? loadError.message : t("commerce:orders.loadFailed"));
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
    navigation.navigate("BuyerOrderDetail", { orderId: order.id, source: order.source_table, title: order.item_title || t("commerce:orders.orderFallbackTitle") });
  }

  function openListing(order: BuyerOrder) {
    const listingId = Number(order.marketplace_listing_id || order.item_id || order.listing?.id || 0);
    if (listingId) navigation.navigate("MarketplaceDetail", { listingId, title: order.item_title || t("commerce:marketplace.title") });
  }

  function openSeller(order: BuyerOrder) {
    const sellerKey = order.seller?.public_player_id || order.seller?.username || "";
    if (sellerKey) navigation.navigate("MerchantProfile", { sellerId: sellerKey, title: order.seller?.display_name || t("commerce:marketplace.seller") });
    else navigation.navigate("SellerStore", { title: t("common:screens.sellerStore") });
  }

  function openReturnOrder(ret: MarketplaceReturn) {
    const match = orders.find((order) => Number(order.transaction_id || order.id) === ret.transaction_id);
    if (match) openOrder(match);
  }

  const hero = (
    <View style={styles.hero}>
      <Text style={styles.kicker}>YOUR PURCHASES</Text>
      <Text style={styles.title}>{selected ? "Order Detail" : "Purchase History"}</Text>
      <Text style={styles.subtitle}>
        Track purchases, delivery, receipts, returns, and support in one place.
      </Text>
    </View>
  );

  const errorPanel = error ? (
    <Panel>
      <Text style={styles.errorTitle}>{t("commerce:orders.errorTitle")}</Text>
      <Text style={styles.copy}>{error}</Text>
      <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => load(orderId, source).catch(() => undefined)}>
        <Text style={styles.secondaryText}>{t("commerce:orders.retry")}</Text>
      </Pressable>
    </Panel>
  ) : null;

  const loadingPanel = loading ? (
    <Panel>
      <Text style={styles.copy}>{t("commerce:orders.loadingTimeline")}</Text>
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
            <Text style={styles.copy}>Open an order to view its receipt, seller, delivery status, or request support.</Text>
            <View style={styles.stats}>
              <Metric label={t("commerce:orders.metricOrders")} value={fmt.number(orders.length)} />
              <Metric label={t("commerce:orders.statusPaid")} value={fmt.number(orders.filter((order) => order.status_group === "paid").length)} />
              <Metric label={t("commerce:orders.metricOpen")} value={fmt.number(orders.filter((order) => ["pending", "processing", "shipped"].includes(String(order.status_group))).length)} />
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
            <Text style={styles.copy}>Completed and processing purchases will appear here after checkout.</Text>
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
            <Text style={styles.sectionTitle}>{order.item_title || order.title || t("commerce:orders.purchaseFallbackTitle")}</Text>
            <Text style={styles.meta}>{t("commerce:orders.orderMeta", { id: String(order.id), type: String(order.item_type || "purchase").replace(/_/g, " ") })}</Text>
          </View>
          <StatusPill status={String(order.status_group || order.status || "pending")} />
        </View>
        <View style={styles.timeline}>
          <TimelineStep label="Created" value={formatDate(order.created_at)} active />
          <TimelineStep label="Payment" value={STATUS_COPY[String(order.status_group)] || String(order.status || "Pending")} active={["paid", "processing", "shipped", "delivered"].includes(String(order.status_group))} />
          <TimelineStep label="Fulfillment" value={order.tracking?.message || "Waiting for seller update"} active={["shipped", "delivered"].includes(String(order.status_group))} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Order summary</Text>
        <Line label="Amount" value={formatOrderMoney(order)} />
        <Line label="Currency" value={String(order.currency || "USD")} />
        <Line label="Payment" value={String(order.payment_status || order.status_group || "pending")} />
        <Line label="Updated" value={formatDate(order.updated_at)} />
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:orders.sellerAndItem")}</Text>
        <Line label={t("commerce:marketplace.seller")} value={order.seller?.display_name || t("commerce:marketplace.sellerNameFallback")} />
        <Line label={t("commerce:orders.item")} value={order.item_title || order.title || t("commerce:orders.purchaseFallbackTitle")} />
        <View style={styles.buttonRow}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={onSeller}>
            <Text style={styles.secondaryText}>{t("commerce:orders.viewSeller")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: !canOpenListing }} style={[styles.secondaryButton, !canOpenListing && styles.disabledButton]} disabled={!canOpenListing} onPress={onListing}>
            <Text style={styles.secondaryText}>{t("commerce:orders.openListing")}</Text>
          </Pressable>
        </View>
      </Panel>

      {returnEligible ? (
        <ReturnPanel order={order} existing={existingReturn} onOpened={onReturnOpened} />
      ) : null}

      <Panel>
        <Text style={styles.sectionTitle}>Receipt and support</Text>
        <Text style={styles.copy}>Your receipt reflects the confirmed payment and order state.</Text>
        <View style={styles.buttonRow}>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => openBuyerOrderFallback(order.receipt_url || buyerOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.primaryText}>{t("commerce:orders.viewReceipt")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => openBuyerOrderFallback(order.dispute_url || order.support_url || supportOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.secondaryText}>{t("commerce:orders.support")}</Text>
          </Pressable>
        </View>
        <Pressable accessibilityRole="button" style={styles.ghostButton} onPress={onBack}>
          <Text style={styles.ghostText}>{t("commerce:orders.backToPurchaseHistory")}</Text>
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
  const { t } = useTranslation();
  const fmt = useFormatters();
  return (
    <Pressable accessibilityRole="button" style={styles.row} onPress={onPress}>
      <View style={styles.rowGlow} />
      <View style={styles.flex}>
        <Text style={styles.rowTitle} numberOfLines={1}>{order.item_title || order.title || t("commerce:orders.purchaseFallbackTitle")}</Text>
        <Text style={styles.meta} numberOfLines={1}>{order.seller?.display_name || t("commerce:marketplace.sellerNameFallback")} · {orderDate(order.created_at, t, fmt)}</Text>
      </View>
      <View style={styles.rowAmount}>
        <Text style={styles.amount}>{orderMoney(order, fmt)}</Text>
        <StatusPill status={String(order.status_group || order.status || "pending")} />
      </View>
    </Pressable>
  );
}

function StatusPill({ status }: { status: string }) {
  const { t } = useTranslation();
  const normalized = String(status || "pending").toLowerCase();
  const color = normalized === "paid" || normalized === "delivered" ? colors.accent : normalized === "failed" || normalized === "cancelled" ? colors.danger : colors.warning;
  const key = STATUS_KEYS[normalized];
  return (
    <View style={[styles.statusPill, { borderColor: color }]}>
      <Text style={[styles.statusText, { color }]}>{key ? t(key) : normalized}</Text>
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

/**
 * `fmt.date` rather than `formatAbsoluteDate` directly: the formatter layer
 * pins the call to the language the user chose in PulseSoc, not to whatever the
 * device happens to be set to.
 */
function orderDate(value: string | undefined, t: (key: string) => string, fmt: Formatters) {
  if (!value) return t("commerce:orders.statusPending");
  return fmt.date(value, { withYear: true }) || value;
}

/**
 * Amounts are rendered through `fmt.currency` so the symbol, its position and
 * the digit grouping follow the active locale. `formatOrderMoney` in the API
 * layer formats against the device locale and is left for non-React callers.
 */
function orderMoney(order: BuyerOrder, fmt: Formatters) {
  const amount = Number(order.amount_cents || order.gross_amount_cents || 0) / 100;
  return fmt.currency(amount, { currency: String(order.currency || "USD").toUpperCase() });
}

const styles = createThemedStyles(() => ({
  root: { flex: 1, backgroundColor: "transparent" },
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
