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
 *
 * ## Health is read, never derived
 *
 * Every verdict on this screen comes from `/supplier-status`. It used to come
 * from `listSupplierConnections` crossed with whatever else the screen happened
 * to have, which is how this screen and the hub reached different answers about
 * the same connection at the same moment. The row below contains no rule for
 * deciding whether a supplier is healthy; the closest it gets is choosing which
 * button to draw for a `nextAction` the server picked.
 */

import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  bindConnectionShop,
  checkConnectionHealth,
  getSupplierStatus,
  listConnectionShops,
  requestSupplierResync,
  stateForError,
  type ConnectionShop,
  type DropshippingState,
  type StoreSupplierStatus,
  type SupplierStatus
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  DropshippingStateView,
  EnvironmentBadge,
  ProviderBadge,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import {
  AttentionBadge,
  HealthRowList,
  OperatingModeBanner
} from "../../components/dropshipping/SupplierHealth";
import { NEXT_ACTION_COPY, actionIsBlocking, healthRows } from "./supplierStatusCopy";
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

/**
 * How far a button's label may grow under the OS text-size setting.
 *
 * Capped rather than uncapped because these labels sit inside pills with a
 * minimum tap height: at the largest accessibility sizes an uncapped
 * "Choose fulfilment shop" pushes its own pill past the card edge, which is the
 * defect this file exists to close. Two lines plus this ceiling is the point at
 * which the longest label still fits the narrowest supported card.
 */
const ACTION_MAX_FONT_SCALE = 1.4;

/** One button on a supplier row, ordered by the row rather than by JSX position. */
type RowAction = {
  key: string;
  label: string;
  accessibilityLabel: string;
  onPress: () => void;
  busy?: boolean;
};

export function SuppliersScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [status, setStatus] = useState<StoreSupplierStatus | null>(null);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  // What the last "Sync now" actually queued, per connection. Held rather than
  // discarded because the request only *starts* the work — telling the merchant
  // "synced" the moment it returns would be the §21 defect on a control instead
  // of on a badge.
  const [syncNote, setSyncNote] = useState<Record<string, string>>({});
  // At most one picker is open, so there is no arrangement in which two
  // connections are being bound at once and the second overwrites the first.
  const [picker, setPicker] = useState<ShopPickerState | null>(null);
  const [binding, setBinding] = useState<string | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;
  const suppliers = status?.suppliers ?? [];

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const result = await getSupplierStatus(scope);
        setStatus(result);
        setState(result.suppliers.length === 0 ? "EMPTY" : "READY");
      } catch (error) {
        // The list failing is an error about the list. It is never EMPTY —
        // "you have no suppliers" is a claim, and a failed request cannot
        // support it. The previous payload goes with it for the same reason:
        // leaving a stale banner reading "Real fulfilment OFF" beside an error
        // states a platform fact this render has no evidence for.
        setStatus(null);
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
    async (supplier: SupplierStatus) => {
      if (!scope) return;
      setChecking(supplier.connectionId);
      try {
        await checkConnectionHealth(scope, supplier.connectionId);
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

  /**
   * "Sync now": queue a re-read of this supplier's data.
   *
   * The sentence afterwards says *started*, never *done*. The request returns
   * when the jobs are queued and a background worker drains them, so a merchant
   * told "synced" would read the same stale costs back off the screen and
   * conclude the button is broken. A truncated catalogue is said out loud for
   * the same reason: silently syncing part of it sends them hunting for a
   * failure that is really a cap.
   */
  const runResync = useCallback(
    async (supplier: SupplierStatus) => {
      if (!scope) return;
      const id = supplier.connectionId;
      setSyncing(id);
      setSyncNote((current) => ({ ...current, [id]: "" }));
      try {
        const result = await requestSupplierResync(scope, id);
        setSyncNote((current) => ({
          ...current,
          [id]: result.truncated
            ? `Refreshing the first ${result.maxProducts} products. Sync again when it finishes to cover the rest.`
            : result.queuedProducts > 0
              ? `Refreshing your connection and ${result.queuedProducts} product${
                  result.queuedProducts === 1 ? "" : "s"
                }. New figures appear here once it finishes.`
              : "Checking your connection. The result appears here once it finishes."
        }));
      } catch (error) {
        // Named as a failure to *start*, which is what actually happened. "Sync
        // failed" would describe a sync that never ran.
        setSyncNote((current) => ({
          ...current,
          [id]:
            stateForError(error) === "UNAUTHORIZED"
              ? "Sign in again to refresh this supplier."
              : "Couldn't start a refresh just now. Try again in a moment."
        }));
      } finally {
        setSyncing(null);
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
    async (supplier: SupplierStatus) => {
      if (!scope) return;
      const connectionId = supplier.connectionId;
      setPicker({ connectionId, state: "LOADING", shops: [] });
      try {
        const result = await listConnectionShops(scope, connectionId);
        setPicker({
          connectionId,
          // EMPTY is a real answer here and says something specific: the
          // supplier account owns no shop at all. It is not a failure, and the
          // copy below must not read as one, because the merchant's next move
          // is in their supplier's console and nothing in this app can do it.
          state: result.shops.length === 0 ? "EMPTY" : "READY",
          shops: result.shops
        });
      } catch (error) {
        setPicker({ connectionId, state: stateForError(error), shops: [] });
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
        data={stateBlock ? [] : suppliers}
        keyExtractor={(item) => item.connectionId}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.block}>
            {/* Above the rows and above the error, because it is the sentence
                that tells a merchant whether anything they do on this screen
                results in a parcel. Drawn whenever the payload arrived at all,
                including when every connection on it is broken. */}
            {status ? <OperatingModeBanner status={status} testID="supplier-operating-mode" /> : null}
            {stateBlock}
          </View>
        }
        renderItem={({ item }) => (
          <SupplierRow
            supplier={item}
            relative={formatters.relative}
            checking={checking === item.connectionId}
            syncing={syncing === item.connectionId}
            syncNote={syncNote[item.connectionId] || null}
            onCheck={() => runHealthCheck(item)}
            onSync={() => runResync(item)}
            onBrowse={() =>
              navigation.navigate("DropshippingCatalog", {
                connectionId: item.connectionId,
                title: "Find products"
              })
            }
            onReviewProducts={() =>
              navigation.navigate("DropshippingProducts", {
                connectionId: item.connectionId,
                title: "Products"
              })
            }
            onReviewIssues={() =>
              navigation.navigate("DropshippingSync", {
                connectionId: item.connectionId,
                title: "Sync & issues"
              })
            }
            onReconnect={openConnect}
            onChooseShop={() => openPicker(item)}
            picker={picker && picker.connectionId === item.connectionId ? picker : null}
            binding={binding}
            reducedMotion={reducedMotion}
            onReloadShops={() => openPicker(item)}
            onClosePicker={() => setPicker(null)}
            onPickShop={(shopId) => chooseShop(item.connectionId, shopId)}
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
  supplier,
  relative,
  checking,
  syncing,
  syncNote,
  onCheck,
  onSync,
  onBrowse,
  onReviewProducts,
  onReviewIssues,
  onReconnect,
  onChooseShop,
  picker,
  binding,
  reducedMotion,
  onReloadShops,
  onClosePicker,
  onPickShop
}: {
  supplier: SupplierStatus;
  relative: (iso: string) => string;
  checking: boolean;
  syncing: boolean;
  /** What the last "Sync now" on this row reported, if there was one. */
  syncNote: string | null;
  onCheck: () => void;
  onSync: () => void;
  onBrowse: () => void;
  onReviewProducts: () => void;
  onReviewIssues: () => void;
  onReconnect: () => void;
  onChooseShop: () => void;
  /** The open picker, when it is this row's. */
  picker: ShopPickerState | null;
  binding: string | null;
  reducedMotion: boolean;
  onReloadShops: () => void;
  onClosePicker: () => void;
  onPickShop: (externalShopId: string) => void;
}) {
  const connected = supplier.connectionState === "CONNECTED";
  const action = supplier.nextAction;
  const copy = action ? NEXT_ACTION_COPY[action] : null;

  // The provider's own message wins when it sent one — it is more specific than
  // anything this app can say — but it is only ever shown to the merchant who
  // owns the connection, never logged. Otherwise the reason is the server's
  // chosen next action, so the sentence and the button below it cannot
  // disagree: a button reading "Reconnect" beside a line reading "Connected"
  // is the state merchants report as a bug.
  const detail =
    supplier.message ||
    copy?.body ||
    STATUS_COPY[supplier.connectionState] ||
    (connected ? STATUS_COPY.CONNECTED : supplier.connectionState);

  /**
   * One action is the answer to "what do I do next?", and the rest are things
   * the merchant *may* do.
   *
   * Before this the row offered three pills of equal weight in a single
   * non-wrapping flex row, which did two bad things at once: the third pill was
   * clipped off the right edge of the card on every iPhone narrower than a Pro
   * Max, and the one action that actually unblocks fulfilment looked no more
   * important than a health check. Promoting the server's `nextAction` — rather
   * than rendering whatever happens to be non-null in source order — is what
   * makes the next step legible, and it is the same ordering every other
   * surface sees, because it was decided once on the server.
   */
  const PRESS: Record<string, () => void> = {
    RECONNECT_SUPPLIER: onReconnect,
    CHOOSE_FULFILLMENT_SHOP: onChooseShop,
    RETRY_SYNC: onSync,
    RESOLVE_PRODUCT_ISSUES: onReviewIssues,
    IMPORT_FIRST_PRODUCT: onBrowse,
    REVIEW_DRAFTS: onReviewProducts
  };
  // Suppressed while the picker is open: the primary would be "Choose fulfilment
  // shop" and the list it opens is already on screen.
  const primary: RowAction | null =
    action && copy && !(action === "CHOOSE_FULFILLMENT_SHOP" && picker)
      ? {
          key: action,
          label: action === "RETRY_SYNC" && syncing ? "Starting…" : copy.button,
          accessibilityLabel: `${copy.button} for this supplier${syncing ? ", starting" : ""}`,
          onPress: PRESS[action],
          busy: action === "RETRY_SYNC" && syncing
        }
      : null;

  const secondaries: RowAction[] = [
    // Demoted rather than dropped when it is not the primary: a merchant with
    // setup left to do still needs a way into the catalogue. Withheld entirely
    // from a connection that is not connected, because every search on it fails
    // — an offer that cannot succeed is worse than no offer.
    ...(connected && primary?.key !== "IMPORT_FIRST_PRODUCT"
      ? [
          {
            key: "browse",
            label: "Find products",
            accessibilityLabel: "Find products from this supplier",
            onPress: onBrowse
          }
        ]
      : []),
    ...(connected && primary?.key !== "RETRY_SYNC"
      ? [
          {
            key: "sync",
            label: syncing ? "Starting…" : "Sync now",
            accessibilityLabel: `Refresh this supplier's products${syncing ? ", starting" : ""}`,
            onPress: onSync,
            busy: syncing
          }
        ]
      : []),
    {
      key: "check",
      label: checking ? "Checking…" : "Check connection",
      accessibilityLabel: `Check this supplier connection${checking ? ", checking" : ""}`,
      onPress: onCheck,
      busy: checking
    }
  ];

  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <ProviderBadge provider={supplier.provider} />
        <EnvironmentBadge environment={supplier.environment} />
        <View style={styles.spacer} />
        <AttentionBadge
          needsAttention={supplier.needsAttention}
          testID={`supplier-attention-${supplier.connectionId}`}
        />
      </View>

      <Text style={styles.rowTitle}>
        {supplier.externalShopId ? `Shop ${supplier.externalShopId}` : "Supplier account"}
      </Text>
      <Text
        style={[styles.rowDetail, supplier.needsAttention ? styles.rowDetailWarning : null]}
        testID={`supplier-detail-${supplier.connectionId}`}
      >
        {detail}
      </Text>
      {supplier.lastVerifiedAt ? (
        <Text style={styles.rowMeta}>{`Checked ${relative(supplier.lastVerifiedAt)}`}</Text>
      ) : null}

      <HealthRowList
        rows={healthRows(supplier, relative)}
        testID={`supplier-health-${supplier.connectionId}`}
      />

      {syncNote ? (
        <Text style={styles.syncNote} testID={`supplier-sync-note-${supplier.connectionId}`}>
          {syncNote}
        </Text>
      ) : null}

      {primary ? (
        <Pressable
          style={[styles.primary, action && !actionIsBlocking(action) ? styles.primaryOptional : null]}
          onPress={primary.onPress}
          disabled={primary.busy}
          accessibilityRole="button"
          accessibilityState={{ disabled: Boolean(primary.busy) }}
          accessibilityLabel={primary.accessibilityLabel}
          testID={`supplier-primary-action-${supplier.connectionId}`}
        >
          <Text style={styles.primaryText} numberOfLines={2} maxFontSizeMultiplier={ACTION_MAX_FONT_SCALE}>
            {primary.label}
          </Text>
        </Pressable>
      ) : null}

      {secondaries.length > 0 ? (
        <View style={styles.rowActions} testID={`supplier-secondary-actions-${supplier.connectionId}`}>
          {secondaries.map((rowAction) => (
            <Pressable
              key={rowAction.key}
              style={styles.secondary}
              onPress={rowAction.onPress}
              disabled={rowAction.busy}
              accessibilityRole="button"
              accessibilityState={{ disabled: Boolean(rowAction.busy) }}
              accessibilityLabel={rowAction.accessibilityLabel}
            >
              <Text
                style={styles.secondaryText}
                numberOfLines={2}
                maxFontSizeMultiplier={ACTION_MAX_FONT_SCALE}
              >
                {rowAction.label}
              </Text>
            </Pressable>
          ))}
        </View>
      ) : null}

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
  rowTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  rowDetail: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  rowDetailWarning: { color: storeLight.status.warning, fontWeight: "600" },
  rowMeta: { fontSize: 11, color: storeLight.text.muted },
  /**
   * What the last "Sync now" actually did, kept until the next one.
   *
   * It sits above the button rather than replacing its label because the request
   * finishes when the work is *queued*, not when it is done: a button that said
   * "Synced" would be a claim about the provider that this screen has no
   * evidence for. The figures below it change on the next `/supplier-status`.
   */
  syncNote: { fontSize: 11, color: storeLight.text.muted, lineHeight: 16, marginTop: 6 },
  /**
   * `flexWrap` is the line that fixes the clipped action.
   *
   * Without it this row laid three pills out in a single line that was wider
   * than the card, and React Native does not scroll or shrink an overflowing
   * row — it simply draws the remainder outside the parent's bounds, where it
   * is both invisible and untappable. Wrapping plus a `flexBasis` under half
   * the row means at most two buttons share a line and a third moves down
   * instead of off.
   */
  rowActions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  secondary: {
    // `flexBasis` just under half leaves room for the gap, so two pills fit a
    // line and a third wraps. `flexGrow` then lets a lone pill on the last line
    // take the full width rather than sitting at an arbitrary 47%.
    flexGrow: 1,
    flexBasis: "47%",
    minWidth: 0,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: {
    fontSize: 13,
    fontWeight: "600",
    color: storeLight.text.primary,
    textAlign: "center"
  },
  /**
   * Full width, and above the secondaries rather than beside them. The primary
   * is the answer to "what next?", and a pill of the same size in the same row
   * as two others cannot carry that meaning.
   */
  primary: {
    alignSelf: "stretch",
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 8,
    paddingHorizontal: 16,
    marginTop: 8,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  /**
   * The same button, outlined, when the server's next action is an invitation
   * rather than a blocker.
   *
   * "Review drafts" and "Find products" are things a working supplier offers;
   * "Reconnect supplier" is something a broken one demands. Drawing both as the
   * one filled call-to-action is how a merchant learns to stop reading it — and
   * the day the credential is actually revoked, the button that says so looks
   * exactly like the one that has been suggesting they browse a catalogue.
   * `cta.text` is near-black, so it stays legible once the fill is gone.
   */
  primaryOptional: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: storeLight.select.selectedBorder
  },
  primaryText: {
    fontSize: 13,
    fontWeight: "800",
    color: storeLight.cta.text,
    textAlign: "center"
  },
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
