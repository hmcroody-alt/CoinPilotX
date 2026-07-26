import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
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

  useScreenPerf("BuyerOrders");

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

  const hero = (
    <View style={styles.hero}>
      <Text style={styles.kicker}>{t("commerce:orders.kicker")}</Text>
      <Text style={styles.title}>{selected ? t("common:screens.orderDetail") : t("common:screens.purchaseHistory")}</Text>
      <Text style={styles.subtitle}>
        {t("commerce:orders.heroSubtitle")}
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
          onBack={() => navigation.navigate("BuyerOrders", { title: t("common:screens.purchaseHistory") })}
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
      data={orders}
      keyExtractor={(order) => `${order.source_table || "order"}-${order.id}`}
      renderItem={({ item }) => <OrderRow order={item} onPress={() => openOrder(item)} />}
      initialNumToRender={8}
      windowSize={7}
      refreshControl={refreshControl}
      ListHeaderComponent={
        <>
          {hero}
          {errorPanel}
          {loadingPanel}
          <Panel>
            <Text style={styles.sectionTitle}>{t("commerce:orders.transactionTimeline")}</Text>
            <Text style={styles.copy}>{t("commerce:orders.transactionTimelineBody")}</Text>
            <View style={styles.stats}>
              <Metric label={t("commerce:orders.metricOrders")} value={fmt.number(orders.length)} />
              <Metric label={t("commerce:orders.statusPaid")} value={fmt.number(orders.filter((order) => order.status_group === "paid").length)} />
              <Metric label={t("commerce:orders.metricOpen")} value={fmt.number(orders.filter((order) => ["pending", "processing", "shipped"].includes(String(order.status_group))).length)} />
            </View>
          </Panel>
        </>
      }
      ListEmptyComponent={
        <Panel>
          <Text style={styles.sectionTitle}>{t("commerce:orders.noPurchasesTitle")}</Text>
          <Text style={styles.copy}>{t("commerce:orders.noPurchasesBody")}</Text>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
            <Text style={styles.primaryText}>{t("commerce:orders.browseMarketplace")}</Text>
          </Pressable>
        </Panel>
      }
    />
  );
}

function OrderDetail({ order, onBack, onListing, onSeller }: { order: BuyerOrder; onBack: () => void; onListing: () => void; onSeller: () => void }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const canOpenListing = Number(order.marketplace_listing_id || order.item_id || order.listing?.id || 0) > 0;
  const statusGroupKey = STATUS_KEYS[String(order.status_group)];
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
          <TimelineStep label={t("commerce:orders.timelineCreated")} value={orderDate(order.created_at, t, fmt)} active />
          <TimelineStep label={t("commerce:orders.timelinePayment")} value={statusGroupKey ? t(statusGroupKey) : String(order.status || t("commerce:orders.statusPending"))} active={["paid", "processing", "shipped", "delivered"].includes(String(order.status_group))} />
          <TimelineStep label={t("commerce:orders.timelineFulfillment")} value={order.tracking?.message || t("commerce:orders.providerControlled")} active={["shipped", "delivered"].includes(String(order.status_group))} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:orders.financialSummary")}</Text>
        <Line label={t("commerce:orders.amount")} value={orderMoney(order, fmt)} />
        <Line label={t("commerce:orders.currency")} value={String(order.currency || "USD")} />
        <Line label={t("commerce:orders.payment")} value={String(order.payment_status || order.status_group || "pending")} />
        <Line label={t("commerce:orders.updated")} value={orderDate(order.updated_at, t, fmt)} />
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

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:orders.buyerControls")}</Text>
        <Text style={styles.copy}>{t("commerce:orders.buyerControlsBody")}</Text>
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
