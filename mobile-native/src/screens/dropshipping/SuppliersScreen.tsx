/**
 * Suppliers — every connection this store has, and what each one can do.
 *
 * This is the screen that owns the word "connected". Elsewhere in the feature a
 * connection is either usable or it is not; here the merchant sees *why*, which
 * is the difference between "re-enter your key" and "wait, the provider is
 * down".
 *
 * ## A status is not a verdict
 *
 * `connectionNeedsAttention` decides what gets the warning treatment, and it is
 * imported rather than re-derived. When this screen and the hub disagreed about
 * whether `VERIFICATION_REQUIRED` counts as connected, the hub offered a
 * catalogue that every search then refused — so the predicate lives in the API
 * module and both read it.
 *
 * ## Provider is a value
 *
 * Nothing here is CJ-shaped: the row renders `connection.provider` as a badge
 * and the actions are the same for every provider. The one CJ-specific thing in
 * this feature — that `listSupplierConnections` currently only returns CJ rows —
 * is a filter in the connection layer beneath this screen, not a shape in it.
 *
 * ## Connected is two questions, and this screen was answering one
 *
 * Importing needs no supplier shop; sending an order does. Connecting without
 * one is allowed on purpose, because a supplier "shop" is an external storefront
 * and selling here means PulseSoc *is* the storefront. So a connection can be
 * perfectly healthy and still refuse every order it receives.
 *
 * The server has always known that — `create_intent` answers
 * `shop_binding_required` — and `bind_shop` has always been the way out. What
 * did not exist was any way to reach it: no screen called it, and the shop list
 * it reads from can only be fetched server-side, because the merchant's key
 * lives in the vault and they have no copy to retype. A routed, tested service
 * function nothing can call is the same dead end one layer up.
 *
 * This screen is that reach. Until it existed the row said "Connected and
 * working" over a connection that could not ship anything.
 */

import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  bindConnectionShop,
  checkConnectionHealth,
  connectionCanFulfil,
  connectionIsUsable,
  connectionNeedsAttention,
  listConnectionShops,
  listSupplierConnections,
  stateForError,
  type ConnectionShop,
  type DropshippingState,
  type SupplierConnection
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  DropshippingStateView,
  EnvironmentBadge,
  ProviderBadge,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route?: { params?: RootStackParamList["DropshippingSuppliers"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * The merchant-facing sentence for each connection status.
 *
 * Statuses this app has not seen fall through to the raw word rather than to a
 * cheerful default — an unrecognised status rendered as "Connected" is how a
 * revoked credential becomes a catalogue full of failed searches.
 */
const STATUS_COPY: Record<string, string> = {
  CONNECTED: "Connected and working",
  AUTH_EXPIRED: "Your credential expired. Reconnect to keep importing.",
  REAUTH_REQUIRED: "Your supplier wants you to sign in again.",
  VERIFICATION_REQUIRED: "Your supplier account needs verifying on their site.",
  API_SUSPENDED: "Your supplier suspended this account's access.",
  REACTIVATION_REQUIRED: "This connection was paused and needs reactivating.",
  DISCONNECTED: "Disconnected.",
  REVOKED: "This credential was revoked."
};

/**
 * Why a shop on the list cannot be chosen, in the merchant's words.
 *
 * The reason itself is the server's — it is `dispatch_shop`'s own refusal code,
 * returned per row — so the list and the bind cannot disagree about what is
 * choosable. This map only translates it.
 *
 * An unrecognised reason falls to a flat "can't take orders" rather than to
 * silence. Silence would leave the row looking ordinary while it is the one row
 * with no button, which reads as a rendering bug rather than as a verdict.
 */
const SHOP_REASON_COPY: Record<string, string> = {
  api_shop_binding_required: "Your supplier won't take orders for this shop from an outside app.",
  ambiguous_shop_name: "Shares its name with another shop, so an order has no single destination."
};

/** An open shop picker: which connection it belongs to, and what it has to show. */
type ShopPickerState = {
  connectionId: string;
  state: DropshippingState;
  shops: ConnectionShop[];
};

export function SuppliersScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [connections, setConnections] = useState<SupplierConnection[]>([]);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);
  // At most one picker is open, so there is no arrangement in which two
  // connections are being bound at once and the second overwrites the first.
  const [picker, setPicker] = useState<ShopPickerState | null>(null);
  const [binding, setBinding] = useState<string | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const rows = await listSupplierConnections(scope);
        setConnections(rows);
        setState(rows.length === 0 ? "EMPTY" : "READY");
      } catch (error) {
        // The list failing is an error about the list. It is never EMPTY —
        // "you have no suppliers" is a claim, and a failed request cannot
        // support it.
        setConnections([]);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const runHealthCheck = useCallback(
    async (connection: SupplierConnection) => {
      if (!scope) return;
      setChecking(connection.id);
      try {
        await checkConnectionHealth(scope, connection.id);
      } catch {
        // Swallowed on purpose: the check's answer is the refreshed list below,
        // not this call's return. A thrown health check still means the list
        // should be re-read, and the new status is what the merchant reads.
      } finally {
        setChecking(null);
        await load("refresh").catch(() => undefined);
      }
    },
    [load, scope]
  );

  const openConnect = useCallback(
    () => navigation.navigate("DropshippingConnect", { title: "Connect a supplier" }),
    [navigation]
  );

  /**
   * Read the live shop list for one connection.
   *
   * Read every time rather than cached with the connection list: the answer is
   * the supplier's, not ours, and a shop can be renamed, disabled or removed in
   * their console between two taps. A stale list here is the one thing that
   * makes the refusals below reachable at all.
   */
  const openPicker = useCallback(
    async (connection: SupplierConnection) => {
      if (!scope) return;
      setPicker({ connectionId: connection.id, state: "LOADING", shops: [] });
      try {
        const result = await listConnectionShops(scope, connection.id);
        setPicker({
          connectionId: connection.id,
          // EMPTY is a real answer here and says something specific: the
          // supplier account owns no shop at all. It is not a failure, and the
          // copy below must not read as one, because the merchant's next move
          // is in their supplier's console and nothing in this app can do it.
          state: result.shops.length === 0 ? "EMPTY" : "READY",
          shops: result.shops
        });
      } catch (error) {
        setPicker({ connectionId: connection.id, state: stateForError(error), shops: [] });
      }
    },
    [scope]
  );

  const chooseShop = useCallback(
    async (connectionId: string, externalShopId: string) => {
      if (!scope) return;
      setBinding(externalShopId);
      try {
        await bindConnectionShop(scope, connectionId, externalShopId);
        setPicker(null);
        // The connection list is what every other surface reads, so the binding
        // is confirmed by re-reading it rather than by trusting this response.
        await load("refresh");
      } catch (error) {
        // The refusal belongs on the picker, where the choice was made — not on
        // the connection list, which loaded perfectly well. The shops are
        // cleared with it: every one of these refusals means the list that was
        // on screen is out of date, so leaving it up beside the explanation
        // would invite the merchant to tap the same wrong row again.
        setPicker((current) =>
          current && current.connectionId === connectionId
            ? { connectionId, state: stateForError(error), shops: [] }
            : current
        );
      } finally {
        setBinding(null);
      }
    },
    [load, scope]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Suppliers"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      reducedMotion={reducedMotion}
      skeletonRows={2}
      empty={{
        title: "No suppliers connected yet.",
        body: "Connect a supplier to browse their catalogue and import products into your store."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Suppliers"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Suppliers"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock ? [] : connections}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={stateBlock ? <View style={styles.block}>{stateBlock}</View> : null}
        renderItem={({ item }) => (
          <SupplierRow
            connection={item}
            checking={checking === item.id}
            lastVerifiedText={
              item.lastVerifiedAt ? `Checked ${formatters.relative(item.lastVerifiedAt)}` : null
            }
            onCheck={() => runHealthCheck(item)}
            onBrowse={
              connectionIsUsable(item)
                ? () =>
                    navigation.navigate("DropshippingCatalog", {
                      connectionId: item.id,
                      title: "Find products"
                    })
                : null
            }
            // Offered only where it can succeed. A connection whose credential
            // is expired cannot read a shop list either, so a picker on it would
            // open straight onto a reauth error.
            onChooseShop={
              connectionIsUsable(item) && !connectionCanFulfil(item) ? () => openPicker(item) : null
            }
            picker={picker && picker.connectionId === item.id ? picker : null}
            binding={binding}
            reducedMotion={reducedMotion}
            onReloadShops={() => openPicker(item)}
            onClosePicker={() => setPicker(null)}
            onPickShop={(shopId) => chooseShop(item.id, shopId)}
          />
        )}
        ListFooterComponent={
          <View style={styles.footer}>
            <Pressable
              style={styles.addButton}
              onPress={openConnect}
              accessibilityRole="button"
              accessibilityLabel="Connect a supplier"
            >
              <Text style={styles.addText}>＋ Connect a supplier</Text>
            </Pressable>
          </View>
        }
      />
    </View>
  );
}

function SupplierRow({
  connection,
  checking,
  lastVerifiedText,
  onCheck,
  onBrowse,
  onChooseShop,
  picker,
  binding,
  reducedMotion,
  onReloadShops,
  onClosePicker,
  onPickShop
}: {
  connection: SupplierConnection;
  checking: boolean;
  lastVerifiedText: string | null;
  onCheck: () => void;
  onBrowse: (() => void) | null;
  /** Absent when this connection already has a shop, or cannot read a list. */
  onChooseShop: (() => void) | null;
  /** The open picker, when it is this row's. */
  picker: ShopPickerState | null;
  binding: string | null;
  reducedMotion: boolean;
  onReloadShops: () => void;
  onClosePicker: () => void;
  onPickShop: (externalShopId: string) => void;
}) {
  const needsAttention = connectionNeedsAttention(connection);
  const status = connection.status.toUpperCase();
  const canFulfil = connectionCanFulfil(connection);
  // The provider's own message wins when it sent one — it is more specific than
  // anything this table can say — but it is only ever shown to the merchant who
  // owns the connection, never logged.
  //
  // "Connected and working" is withheld from a connection with no fulfilment
  // shop, because it is not true of one: every order it receives is refused.
  // That sentence is what this row said before the picker below existed, and a
  // merchant who reads it has no reason to look for anything else to do.
  const detail =
    connection.message ||
    (status === "CONNECTED" && !canFulfil
      ? "Connected. Importing and publishing work; orders need a fulfilment shop."
      : STATUS_COPY[status]) ||
    connection.status;

  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <ProviderBadge provider={connection.provider} />
        <EnvironmentBadge environment={connection.environment} />
        <View style={styles.spacer} />
        <View
          style={[
            styles.dot,
            {
              backgroundColor: needsAttention
                ? storeLight.status.warning
                : connectionIsUsable(connection)
                  ? storeLight.status.success
                  : storeLight.status.neutral
            }
          ]}
        />
      </View>

      <Text style={styles.rowTitle}>
        {connection.externalShopId ? `Shop ${connection.externalShopId}` : "Supplier account"}
      </Text>
      <Text style={[styles.rowDetail, needsAttention ? styles.rowDetailWarning : null]}>{detail}</Text>
      {lastVerifiedText ? <Text style={styles.rowMeta}>{lastVerifiedText}</Text> : null}

      {/* Production fulfilment is off at the platform level while the funding
          kill-switch is closed. Saying so here stops a merchant concluding the
          connection is broken when it is deliberately limited. */}
      {!connection.productionFulfillmentEnabled ? (
        <Text style={styles.rowMeta}>
          Importing works. Sending real orders to this supplier is switched off platform-wide.
        </Text>
      ) : null}

      <View style={styles.rowActions}>
        <Pressable
          style={styles.secondary}
          onPress={onCheck}
          disabled={checking}
          accessibilityRole="button"
          accessibilityState={{ disabled: checking }}
          accessibilityLabel={`Check this supplier connection${checking ? ", checking" : ""}`}
        >
          <Text style={styles.secondaryText}>{checking ? "Checking…" : "Check connection"}</Text>
        </Pressable>
        {onChooseShop && !picker ? (
          <Pressable
            style={styles.secondary}
            onPress={onChooseShop}
            accessibilityRole="button"
            accessibilityLabel="Choose a fulfilment shop for this supplier"
          >
            <Text style={styles.secondaryText}>Choose fulfilment shop</Text>
          </Pressable>
        ) : null}
        {onBrowse ? (
          <Pressable
            style={styles.primary}
            onPress={onBrowse}
            accessibilityRole="button"
            accessibilityLabel="Find products from this supplier"
          >
            <Text style={styles.primaryText}>Find products</Text>
          </Pressable>
        ) : null}
      </View>

      {picker ? (
        <ShopPicker
          picker={picker}
          binding={binding}
          reducedMotion={reducedMotion}
          onReload={onReloadShops}
          onClose={onClosePicker}
          onPick={onPickShop}
        />
      ) : null}
    </View>
  );
}

/**
 * The shop list, and the choice.
 *
 * A shop the supplier will not accept an order for renders with no control at
 * all, not with a disabled one. The verdict is structural rather than a prop,
 * so there is no state in which the row is tappable and the refusal arrives
 * afterwards — which is exactly what happened before the list carried
 * `fulfillable`: binding said yes, and the merchant found out at the first
 * order that the shop was never a destination.
 */
function ShopPicker({
  picker,
  binding,
  reducedMotion,
  onReload,
  onClose,
  onPick
}: {
  picker: ShopPickerState;
  binding: string | null;
  reducedMotion: boolean;
  onReload: () => void;
  onClose: () => void;
  onPick: (externalShopId: string) => void;
}) {
  const block = stateOwnsScreen(picker.state) ? (
    <DropshippingStateView
      state={picker.state}
      subject="Fulfilment shops"
      onRetry={onReload}
      reducedMotion={reducedMotion}
      skeletonRows={2}
      empty={{
        title: "This supplier account has no shops.",
        body:
          "Orders go to a shop your supplier's console sets up to accept them from outside apps. Create one there, then reopen this list. Importing and publishing keep working in the meantime."
      }}
    />
  ) : null;

  return (
    <View style={styles.picker}>
      <View style={styles.pickerTop}>
        <Text style={styles.pickerTitle}>Choose a fulfilment shop</Text>
        <View style={styles.spacer} />
        <Pressable
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel="Close the fulfilment shop list"
          style={styles.pickerClose}
        >
          <Text style={styles.secondaryText}>Close</Text>
        </Pressable>
      </View>

      {block}
      {block
        ? null
        : picker.shops.map((shop) => {
            const label = shop.name || shop.externalShopId;
            const reason = shop.fulfillable
              ? null
              : SHOP_REASON_COPY[String(shop.unfulfillableReason || "")] || "Can't take orders.";
            const busy = binding === shop.externalShopId;
            return (
              <View key={shop.externalShopId} style={styles.shop}>
                <View style={styles.shopBody}>
                  <Text style={styles.shopName}>{label}</Text>
                  {shop.platform ? <Text style={styles.rowMeta}>{shop.platform}</Text> : null}
                  {reason ? <Text style={styles.shopReason}>{reason}</Text> : null}
                </View>
                {shop.fulfillable ? (
                  <Pressable
                    style={styles.primary}
                    onPress={() => onPick(shop.externalShopId)}
                    accessibilityRole="button"
                    accessibilityLabel={`Send orders to ${label}${busy ? ", choosing" : ""}`}
                  >
                    <Text style={styles.primaryText}>{busy ? "Choosing…" : "Use this shop"}</Text>
                  </Pressable>
                ) : null}
              </View>
            );
          })}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingTop: storeLight.space.section, gap: storeLight.space.gutter },
  block: { paddingHorizontal: storeLight.space.card },
  row: {
    marginHorizontal: storeLight.space.card,
    padding: storeLight.space.card,
    gap: 6,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  rowTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  spacer: { flex: 1 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  rowTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  rowDetail: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  rowDetailWarning: { color: storeLight.status.warning, fontWeight: "600" },
  rowMeta: { fontSize: 11, color: storeLight.text.muted },
  rowActions: { flexDirection: "row", gap: 8, marginTop: 6 },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  primary: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 16,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryText: { fontSize: 13, fontWeight: "800", color: storeLight.cta.text },
  picker: {
    marginTop: 10,
    paddingTop: 10,
    gap: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  pickerTop: { flexDirection: "row", alignItems: "center" },
  pickerTitle: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  pickerClose: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 8
  },
  shop: { flexDirection: "row", alignItems: "center", gap: 10 },
  shopBody: { flex: 1, gap: 2 },
  shopName: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  shopReason: { fontSize: 11, color: storeLight.status.warning, lineHeight: 15 },
  footer: { padding: storeLight.space.card },
  addButton: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  addText: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary }
});
