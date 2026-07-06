import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
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
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

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

  const selected = useMemo(() => detail || orders.find((order) => order.id === orderId) || null, [detail, orderId, orders]);

  async function load(nextOrderId = orderId, nextSource = source, refresh = false) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const result = await listBuyerOrders({ limit: 80 });
      setOrders(result.orders || []);
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

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(orderId, source, true).catch(() => undefined)} tintColor={colors.accent} />}
    >
      <View style={styles.hero}>
        <Text style={styles.kicker}>Commerce Ledger</Text>
        <Text style={styles.title}>{selected ? "Order Detail" : "Purchase History"}</Text>
        <Text style={styles.subtitle}>
          Buyer-side order state is read from PulseSoc payment ledgers. Checkout, refunds, disputes, shipping, and receipts remain server and provider controlled.
        </Text>
      </View>

      {error ? (
        <Panel>
          <Text style={styles.errorTitle}>Commerce state unavailable</Text>
          <Text style={styles.copy}>{error}</Text>
          <Pressable style={styles.secondaryButton} onPress={() => load(orderId, source).catch(() => undefined)}>
            <Text style={styles.secondaryText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {loading ? (
        <Panel>
          <Text style={styles.copy}>Loading your PulseSoc purchase timeline...</Text>
        </Panel>
      ) : null}

      {selected ? (
        <OrderDetail
          order={selected}
          onBack={() => navigation.navigate("BuyerOrders", { title: "Purchase History" })}
          onListing={() => openListing(selected)}
          onSeller={() => openSeller(selected)}
        />
      ) : (
        <>
          <Panel>
            <Text style={styles.sectionTitle}>Transaction Timeline</Text>
            <Text style={styles.copy}>Receipts, disputes, payment confirmation, and seller fulfillment stay aligned with existing PulseSoc backend records.</Text>
            <View style={styles.stats}>
              <Metric label="Orders" value={String(orders.length)} />
              <Metric label="Paid" value={String(orders.filter((order) => order.status_group === "paid").length)} />
              <Metric label="Open" value={String(orders.filter((order) => ["pending", "processing", "shipped"].includes(String(order.status_group))).length)} />
            </View>
          </Panel>
          {orders.length ? orders.map((order) => <OrderRow key={`${order.source_table || "order"}-${order.id}`} order={order} onPress={() => openOrder(order)} />) : (
            <Panel>
              <Text style={styles.sectionTitle}>No purchases yet</Text>
              <Text style={styles.copy}>Marketplace and learning purchases will appear here after checkout creates a server-side transaction.</Text>
              <Pressable style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
                <Text style={styles.primaryText}>Browse Marketplace</Text>
              </Pressable>
            </Panel>
          )}
        </>
      )}
    </ScrollView>
  );
}

function OrderDetail({ order, onBack, onListing, onSeller }: { order: BuyerOrder; onBack: () => void; onListing: () => void; onSeller: () => void }) {
  const canOpenListing = Number(order.marketplace_listing_id || order.item_id || order.listing?.id || 0) > 0;
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
          <Pressable style={styles.secondaryButton} onPress={onSeller}>
            <Text style={styles.secondaryText}>View Seller</Text>
          </Pressable>
          <Pressable style={[styles.secondaryButton, !canOpenListing && styles.disabledButton]} disabled={!canOpenListing} onPress={onListing}>
            <Text style={styles.secondaryText}>Open Listing</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Buyer controls</Text>
        <Text style={styles.copy}>Receipt, support, dispute, shipping, and provider pages open through existing PulseSoc web/provider flows.</Text>
        <View style={styles.buttonRow}>
          <Pressable style={styles.primaryButton} onPress={() => openBuyerOrderFallback(order.receipt_url || buyerOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.primaryText}>View Receipt</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => openBuyerOrderFallback(order.dispute_url || order.support_url || supportOrderWebUrl(order)).catch(() => undefined)}>
            <Text style={styles.secondaryText}>Support</Text>
          </Pressable>
        </View>
        <Pressable style={styles.ghostButton} onPress={onBack}>
          <Text style={styles.ghostText}>Back to Purchase History</Text>
        </Pressable>
      </Panel>
    </>
  );
}

function OrderRow({ order, onPress }: { order: BuyerOrder; onPress: () => void }) {
  return (
    <Pressable style={styles.row} onPress={onPress}>
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
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const styles = StyleSheet.create({
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
  disabledButton: { opacity: 0.45 }
});
