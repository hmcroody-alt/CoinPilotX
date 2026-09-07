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
 */

import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  checkConnectionHealth,
  connectionIsUsable,
  connectionNeedsAttention,
  listSupplierConnections,
  stateForError,
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

export function SuppliersScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [connections, setConnections] = useState<SupplierConnection[]>([]);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);

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
  onBrowse
}: {
  connection: SupplierConnection;
  checking: boolean;
  lastVerifiedText: string | null;
  onCheck: () => void;
  onBrowse: (() => void) | null;
}) {
  const needsAttention = connectionNeedsAttention(connection);
  const status = connection.status.toUpperCase();
  // The provider's own message wins when it sent one — it is more specific than
  // anything this table can say — but it is only ever shown to the merchant who
  // owns the connection, never logged.
  const detail = connection.message || STATUS_COPY[status] || connection.status;

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
