/**
 * Sync & issues — everything currently wrong between this store and a supplier.
 *
 * ## Derived, not stored
 *
 * There is no issues table. This screen reads the connection's own status and
 * the sync state carried on each imported product, and groups them. That is a
 * deliberate choice over inventing an issue log: an issue log drifts from
 * reality the moment something is fixed elsewhere, and a merchant who resolves
 * a problem then sees it still listed stops trusting the list.
 *
 * ## An empty list is a claim, so it needs both sources
 *
 * "Nothing needs your attention" is only said when the connection loaded *and*
 * the product list loaded. If either request failed the screen reports the
 * failure instead, because a failed fetch cannot support a claim that there is
 * nothing wrong — that is the exact shape of bug that has a merchant believing
 * their catalogue is healthy while every import silently fails.
 *
 * ## Severity is about what the merchant can do
 *
 * A delisted product and an expired credential are both "problems", but one is
 * fixed in thirty seconds and the other means the product is gone. They are
 * separated so the fixable work is at the top.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  DROPSHIPPING_DATA_GAPS,
  connectionNeedsAttention,
  listImportedProducts,
  listSupplierConnections,
  stateForError,
  type DropshippingState,
  type ImportedProductRow,
  type SupplierConnection
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  DropshippingGapNote,
  DropshippingStateView,
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
  route: { params: RootStackParamList["DropshippingSync"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * Product-level sync states that are worth telling the merchant about, and how
 * bad each one is. States not listed here — `OK`, `SYNCED`, and anything this
 * app has not seen — produce no issue: an unrecognised state is not evidence of
 * a problem, and reporting one would fill the screen with noise on the day a
 * provider adds a new value.
 */
const PRODUCT_ISSUES: Record<string, { text: string; action: string; severity: "fix" | "info" }> = {
  ERROR: {
    text: "The last update from your supplier failed",
    action: "Check the connection, then open the product.",
    severity: "fix"
  },
  STALE: {
    text: "Supplier data is out of date",
    action: "Cost and stock may have changed since this was last read.",
    severity: "info"
  },
  PENDING: {
    text: "Waiting on your supplier",
    action: "Nothing to do — this clears itself.",
    severity: "info"
  },
  UNAVAILABLE: {
    text: "Your supplier no longer offers this product",
    action: "Unpublish it so buyers can't order something you can't source.",
    severity: "fix"
  },
  DISCONNECTED: {
    text: "This product's supplier connection is broken",
    action: "Reconnect the supplier.",
    severity: "fix"
  }
};

type ProductIssue = { row: ImportedProductRow; state: string };

export function DropshippingSyncScreen({ route, navigation }: Props) {
  const { connectionId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [connection, setConnection] = useState<SupplierConnection | null>(null);
  const [rows, setRows] = useState<ImportedProductRow[]>([]);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        // Both, or neither. `Promise.all` rather than `allSettled` on purpose:
        // a half-loaded issues screen would show fewer problems than exist,
        // and "fewer problems than exist" is the wrong direction to be wrong in.
        const [connections, imported] = await Promise.all([
          listSupplierConnections(scope),
          listImportedProducts(scope, connectionId, { limit: 200 })
        ]);
        setConnection(connections.find((row) => row.id === connectionId) || null);
        setRows(imported.items);
        setState("READY");
      } catch (error) {
        setConnection(null);
        setRows([]);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [connectionId, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const issues = useMemo<ProductIssue[]>(
    () =>
      rows
        .map((row) => ({ row, state: (row.syncState || "").toUpperCase() }))
        .filter((entry) => Boolean(PRODUCT_ISSUES[entry.state])),
    [rows]
  );

  const fixable = issues.filter((issue) => PRODUCT_ISSUES[issue.state].severity === "fix");
  const informational = issues.filter((issue) => PRODUCT_ISSUES[issue.state].severity === "info");
  const connectionProblem = connection ? connectionNeedsAttention(connection) : false;

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Sync status"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={3}
      empty={{
        title: "Nothing to sync yet.",
        body: "Once you've imported products, anything that goes wrong with them shows up here."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Sync & issues"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Sync & issues"
        reducedMotion={reducedMotion}
      />

      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
      >
        {stateBlock}

        {!stateBlock && state === "READY" ? (
          <>
            {connection ? (
              <View style={styles.card}>
                <View style={styles.cardHead}>
                  <ProviderBadge provider={connection.provider} />
                  <View style={styles.spacer} />
                  <View
                    style={[
                      styles.dot,
                      {
                        backgroundColor: connectionProblem
                          ? storeLight.status.warning
                          : storeLight.status.success
                      }
                    ]}
                  />
                </View>
                <Text style={styles.cardTitle}>
                  {connectionProblem ? "Your supplier connection needs attention" : "Connection is working"}
                </Text>
                <Text style={styles.cardBody}>
                  {connection.message || (connectionProblem ? connection.status : "Nothing wrong here.")}
                </Text>
                {connection.lastSyncAt ? (
                  <Text style={styles.meta}>Last synced {formatters.relative(connection.lastSyncAt)}</Text>
                ) : (
                  <Text style={styles.meta}>Hasn't synced since you connected it.</Text>
                )}
                {connectionProblem ? (
                  <Pressable
                    style={styles.secondary}
                    onPress={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
                    accessibilityRole="button"
                    accessibilityLabel="Open suppliers"
                  >
                    <Text style={styles.secondaryText}>Fix connection</Text>
                  </Pressable>
                ) : null}
              </View>
            ) : null}

            {/* Only sayable because both requests succeeded — see the header
                comment. A partial load reports the failure instead. */}
            {!connectionProblem && issues.length === 0 ? (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>Nothing needs your attention</Text>
                <Text style={styles.cardBody}>
                  All {formatters.count(rows.length)} of your imported products are in step with your
                  supplier.
                </Text>
              </View>
            ) : null}

            {fixable.length > 0 ? (
              <IssueGroup title="Needs you" issues={fixable} navigate={navigation.navigate} connectionId={connectionId} />
            ) : null}

            {informational.length > 0 ? (
              <IssueGroup
                title="Worth knowing"
                issues={informational}
                navigate={navigation.navigate}
                connectionId={connectionId}
              />
            ) : null}

            {DROPSHIPPING_DATA_GAPS.map((gap) => (
              <DropshippingGapNote
                key={gap.surface}
                title={`${gap.surface} isn't available yet`}
                body="Supplier order and tracking problems will appear here once fulfilment is switched on."
              />
            ))}
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

function IssueGroup({
  title,
  issues,
  navigate,
  connectionId
}: {
  title: string;
  issues: ProductIssue[];
  navigate: (...args: any[]) => void;
  connectionId: string;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {issues.map(({ row, state }) => {
        const copy = PRODUCT_ISSUES[state];
        return (
          <Pressable
            key={row.listingId}
            style={styles.issue}
            onPress={() =>
              navigate("DropshippingDraft", {
                connectionId,
                listingId: row.listingId,
                title: row.title || "Imported product"
              })
            }
            accessibilityRole="button"
            accessibilityLabel={`${row.title || "Untitled product"}: ${copy.text}`}
          >
            <Text style={styles.issueTitle} numberOfLines={1}>
              {row.title || "Untitled product"}
            </Text>
            <Text style={copy.severity === "fix" ? styles.issueTextFix : styles.issueText}>
              {copy.text}
            </Text>
            <Text style={styles.meta}>{copy.action}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  card: {
    padding: storeLight.space.card,
    gap: 8,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  spacer: { flex: 1 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  cardTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  meta: { fontSize: 11, color: storeLight.text.muted },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  issue: {
    gap: 3,
    paddingTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  issueTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  issueText: { fontSize: 12, color: storeLight.text.muted },
  issueTextFix: { fontSize: 12, fontWeight: "600", color: storeLight.status.warning }
});
