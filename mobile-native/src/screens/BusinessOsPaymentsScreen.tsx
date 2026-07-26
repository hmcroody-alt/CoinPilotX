import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import {
  AdBilling,
  AdWallet,
  adFundingIsLive,
  formatCents,
  getAdBillingSummary,
  getAdWallet,
  listAdAccounts
} from "../api/businessOs";
import {
  connectMarketplacePayout,
  loadCachedSellerStore,
  loadSellerStoreSnapshot,
  MarketplaceSellerOrder,
  sellerStoreWebUrl
} from "../api/marketplace";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { registerSyncInvalidation } from "../core/eventSync";
import { colors } from "../theme/colors";

/**
 * Payments — the money side of Business OS in one place.
 *
 * This screen exists because the Payments tile promised "payouts, ad wallet and
 * billing" while routing to the seller store's orders view, which showed none of
 * the three. The wallet and billing contracts were already written and tested;
 * nothing rendered them.
 *
 * Two rules hold throughout. Nothing here invents a number: order money is
 * summed from real orders and labelled as gross so it is never mistaken for a
 * payout, and wallet and billing figures come from the backend or are absent.
 * And no funding control is rendered while `adFundingIsLive` is false — the
 * server hardcodes `live_charging: false`, so an Add Funds button would be a
 * control that cannot charge anything.
 */
export function BusinessOsPaymentsScreen() {
  const [orders, setOrders] = useState<MarketplaceSellerOrder[]>([]);
  const [wallet, setWallet] = useState<AdWallet | null>(null);
  const [billing, setBilling] = useState<AdBilling | null>(null);
  const [hasAdAccount, setHasAdAccount] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setOffline(false);

    const [storeResult, accountsResult] = await Promise.allSettled([loadSellerStoreSnapshot(), listAdAccounts()]);

    if (storeResult.status === "fulfilled") {
      setOrders(storeResult.value.orders || []);
    } else {
      const cached = await loadCachedSellerStore().catch(() => null);
      setOrders(cached?.orders || []);
    }

    if (accountsResult.status === "fulfilled") {
      const accountId = accountsResult.value.accounts[0]?.id || 0;
      setHasAdAccount(Boolean(accountId));
      if (accountId) {
        // Wallet and billing are separate endpoints and either can fail on its
        // own; a missing wallet must not blank out billing, so they settle
        // independently and each renders only what it actually returned.
        const [walletResult, billingResult] = await Promise.allSettled([
          getAdWallet(accountId),
          getAdBillingSummary(accountId)
        ]);
        setWallet(walletResult.status === "fulfilled" ? walletResult.value.wallet : null);
        setBilling(billingResult.status === "fulfilled" ? billingResult.value.billing : null);
      } else {
        setWallet(null);
        setBilling(null);
      }
    } else {
      setHasAdAccount(false);
      setWallet(null);
      setBilling(null);
      setOffline(true);
      setMessage(
        accountsResult.reason instanceof Error ? accountsResult.reason.message : "Payments could not reach PulseSoc."
      );
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const refresh = () => {
      load().catch(() => undefined);
    };
    const unregisterOrders = registerSyncInvalidation("orders", refresh);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refresh);
    return () => {
      unregisterOrders();
      unregisterMarketplace();
    };
  }, [load]);

  async function startPayoutConnect() {
    setBusy("payout");
    setMessage("");
    try {
      const result = await connectMarketplacePayout();
      setMessage(result.message || "Payout onboarding checked.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Payout onboarding is not available yet.");
    } finally {
      setBusy("");
    }
  }

  const grossCents = orders.reduce(
    (total, order) => total + Number(order.gross_amount_cents || order.amount_cents || 0),
    0
  );
  const paidOrders = orders.filter((order) => String(order.status || "").toLowerCase() === "paid").length;
  const orderCurrency = String(orders.find((order) => order.currency)?.currency || "USD");

  return (
    <Screen title="Payments" subtitle="Payouts, ad wallet and billing.">
      {loading ? (
        <Panel>
          <View style={styles.row}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.muted}>Loading payments…</Text>
          </View>
        </Panel>
      ) : null}

      {message ? (
        <Panel>
          <Text style={styles.muted}>{message}</Text>
        </Panel>
      ) : null}

      {offline && !loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Showing saved data</Text>
          <Text style={styles.muted}>Wallet and billing figures are unavailable until PulseSoc can be reached.</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading payments"
            onPress={() => load().catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {!loading ? (
        <Panel>
          <Text style={styles.panelTitle}>Seller payouts</Text>
          <View style={styles.metrics}>
            <Metric label="Orders" value={String(orders.length)} />
            <Metric label="Paid orders" value={String(paidOrders)} />
            <Metric label="Gross from orders" value={formatCents(grossCents, orderCurrency)} />
          </View>
          <Text style={styles.footnote}>
            Gross is the total buyers were charged across your orders, before platform fees, refunds and taxes. It is not
            your payout amount.
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Check payout onboarding"
            accessibilityState={{ disabled: busy === "payout" || offline }}
            disabled={busy === "payout" || offline}
            onPress={startPayoutConnect}
            style={[styles.secondaryButton, (busy === "payout" || offline) && styles.buttonDisabled]}
          >
            <Text style={styles.secondaryButtonText}>
              {busy === "payout" ? "Checking…" : "Check payout onboarding"}
            </Text>
          </Pressable>
          <Text style={styles.footnote}>
            Bank onboarding, tax forms and dispute handling stay on the provider's own flow at{" "}
            {sellerStoreWebUrl("payouts")}.
          </Text>
        </Panel>
      ) : null}

      {!loading && wallet ? (
        <Panel>
          <Text style={styles.panelTitle}>Ad wallet</Text>
          <View style={styles.metrics}>
            <Metric label="Available" value={formatCents(wallet.available_balance_cents, wallet.currency)} />
            <Metric label="Pending" value={formatCents(wallet.pending_balance_cents, wallet.currency)} />
            <Metric label="Spendable" value={formatCents(wallet.spendable_balance_cents, wallet.currency)} />
            <Metric label="Reserved" value={formatCents(wallet.reserved_budget_cents, wallet.currency)} />
            <Metric label="Credits" value={formatCents(walletCredits(wallet), wallet.currency)} />
            <Metric label="Lifetime spent" value={formatCents(wallet.lifetime_spent_cents, wallet.currency)} />
          </View>
          {wallet.transactions?.length ? (
            <>
              <Text style={styles.subheading}>Recent activity</Text>
              {wallet.transactions.slice(0, 5).map((transaction, index) => (
                <View key={`${transaction.created_at || index}`} style={styles.transaction}>
                  <Text style={styles.transactionTitle}>
                    {String(transaction.transaction_type || "transaction").replace(/_/g, " ")}
                  </Text>
                  <Text style={styles.muted}>
                    {transaction.amount || formatCents(transaction.amount_cents, transaction.currency || wallet.currency)}
                    {transaction.status ? ` · ${String(transaction.status).replace(/_/g, " ")}` : ""}
                  </Text>
                </View>
              ))}
            </>
          ) : (
            <Text style={styles.muted}>No wallet activity yet.</Text>
          )}
        </Panel>
      ) : null}

      {!loading && billing ? (
        <Panel>
          <Text style={styles.panelTitle}>Billing</Text>
          <View style={styles.metrics}>
            <Metric label="Wallet balance" value={formatCents(billing.wallet_balance_cents)} />
            <Metric label="Spend limit" value={formatCents(billing.spend_limit_cents)} />
          </View>
          <Text style={styles.muted}>
            Status {String(billing.billing_status || "not configured").replace(/_/g, " ")} · funding{" "}
            {String(billing.funding_status || "prepared").replace(/_/g, " ")}
          </Text>
          <Text style={styles.footnote}>
            {adFundingIsLive(billing)
              ? "Funding is live on this account."
              : "Card funding is not enabled on this account yet, so nothing can be charged from the app and no funds can be added here."}
          </Text>
        </Panel>
      ) : null}

      {!loading && !hasAdAccount && !offline ? (
        <Panel>
          <Text style={styles.panelTitle}>No ad account yet</Text>
          <Text style={styles.muted}>
            Wallet and billing appear here once you create an ad account in Advertising.
          </Text>
        </Panel>
      ) : null}
    </Screen>
  );
}

/** Promotional, bonus and refund credits are three fields describing one idea. */
function walletCredits(wallet: AdWallet) {
  return (
    Number(wallet.promotional_credits_cents || 0) +
    Number(wallet.bonus_credits_cents || 0) +
    Number(wallet.refund_credits_cents || 0)
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View accessible accessibilityLabel={`${label}: ${value}`} style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  buttonDisabled: {
    opacity: 0.5
  },
  footnote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    flexBasis: "30%",
    flexGrow: 1,
    gap: 4,
    padding: 12
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12
  },
  metricValue: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "800"
  },
  metrics: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  panelTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "700"
  },
  row: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  secondaryButton: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600"
  },
  subheading: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  transaction: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    gap: 2,
    padding: 10
  },
  transactionTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600",
    textTransform: "capitalize"
  }
});
