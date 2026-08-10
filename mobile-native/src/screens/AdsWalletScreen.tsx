/**
 * Ads Wallet & Billing — `BusinessOsAdvertising { mode: "wallet" }`.
 *
 * Balance header reads `getAdWallet` (businessOs) and honours its
 * `unavailable` flag — a wallet the server could not read shows the retry
 * banner, never a zero. Funding goes through `createAdFundingSession` and the
 * Stripe checkout URL opens in the browser; the iOS 403 sentence from the
 * server ("funding lives on the web portal") is shown verbatim. Transactions
 * page via `before_id`, limits and auto top-up post through `api/adsWallet`,
 * and the auto top-up copy states plainly that it prompts and never charges —
 * the backend pins `auto_charge: false`.
 *
 * The classic payments screen stays reachable at the bottom for Stripe
 * receipts and the older funding history.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Animated,
  Linking,
  Pressable,
  RefreshControl,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View
} from "react-native";

import { primaryAdAccount } from "../api/adsDashboard";
import {
  AdAutoTopup,
  AdSpendingLimits,
  AdWalletInvoice,
  AdWalletTxn,
  createAdFundingSession,
  listAdWalletInvoices,
  listAdWalletTransactions,
  setAdAutoTopup,
  setAdSpendingLimits
} from "../api/adsWallet";
import {
  AdBilling,
  AdWallet,
  formatCents,
  getAdBillingSummary,
  getAdWallet,
  listAdAccounts,
  loadCachedAdAccounts
} from "../api/businessOs";
import {
  AdsEmpty,
  AdsOfflineNote,
  AdsScreenShell,
  AdsSectionError,
  AdsSkeletonBlock,
  AdsWalletUnavailable,
  adsSubStyles as s
} from "../components/ads";
import { useTranslation } from "../i18n";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

type Props = {
  route?: { params?: { title?: string; accountId?: number } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:adsWallet";

/** "12.50" → 1250; empty → null; junk → NaN so the caller can complain. */
function parseDollars(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed.replace(/,/g, "."));
  if (!Number.isFinite(value) || value < 0) return Number.NaN;
  return Math.round(value * 100);
}

function centsToDollarInput(cents: number): string {
  if (!cents) return "";
  return (cents / 100).toFixed(2).replace(/\.00$/, "");
}

export function AdsWalletScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(7, reducedMotion);

  const [accountId, setAccountId] = useState<number>(Number(route?.params?.accountId || 0));
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [wallet, setWallet] = useState<AdWallet | null>(null);
  const [billing, setBilling] = useState<AdBilling | null>(null);

  const [txns, setTxns] = useState<AdWalletTxn[]>([]);
  const [txNext, setTxNext] = useState<number | null>(null);
  const [txError, setTxError] = useState("");
  const [txLoadingMore, setTxLoadingMore] = useState(false);

  const [invoices, setInvoices] = useState<AdWalletInvoice[]>([]);
  const [invError, setInvError] = useState("");

  const [fundAmount, setFundAmount] = useState("");
  const [fundBusy, setFundBusy] = useState(false);
  const [fundNote, setFundNote] = useState("");

  const [dailyInput, setDailyInput] = useState("");
  const [lifetimeInput, setLifetimeInput] = useState("");
  const [limitsBusy, setLimitsBusy] = useState(false);
  const [limitsNote, setLimitsNote] = useState("");

  const [topupEnabled, setTopupEnabled] = useState(false);
  const [topupThreshold, setTopupThreshold] = useState("");
  const [topupAmount, setTopupAmount] = useState("");
  const [topupBusy, setTopupBusy] = useState(false);
  const [topupNote, setTopupNote] = useState("");

  const currency = String(wallet?.currency || "USD");
  const money = useCallback((cents: number) => formatCents(cents, currency), [currency]);

  const applyLimits = useCallback((limits: AdSpendingLimits) => {
    setDailyInput(centsToDollarInput(limits.daily_limit_cents));
    setLifetimeInput(centsToDollarInput(limits.lifetime_limit_cents));
  }, []);

  const applyTopup = useCallback((topup: AdAutoTopup) => {
    setTopupEnabled(topup.enabled);
    setTopupThreshold(centsToDollarInput(topup.threshold_cents));
    setTopupAmount(centsToDollarInput(topup.amount_cents));
  }, []);

  const load = useCallback(
    async (asRefresh = false) => {
      if (asRefresh) setRefreshing(true);
      else setStatus("loading");
      setErrorText("");
      try {
        let id = accountId;
        if (!id) {
          const res = await listAdAccounts().catch(async () => ({
            accounts: await loadCachedAdAccounts().catch(() => [])
          }));
          id = primaryAdAccount(res.accounts || [])?.id || 0;
          if (id) setAccountId(id);
        }
        if (!id) {
          setStatus("error");
          setErrorText(t(`${NS}.noAccount`));
          return;
        }
        const [walletRes, billingRes, txRes, invRes] = await Promise.allSettled([
          getAdWallet(id),
          getAdBillingSummary(id),
          listAdWalletTransactions(id, { limit: 50 }),
          listAdWalletInvoices(id)
        ]);
        if (walletRes.status === "fulfilled") {
          setWallet(walletRes.value.wallet || null);
        } else {
          throw walletRes.reason;
        }
        if (billingRes.status === "fulfilled") {
          // The summary carries one combined limit figure only; the limits form
          // fills from the dedicated endpoint's echo after a save rather than
          // guessing which limit that figure was.
          setBilling(billingRes.value.billing || null);
        } else {
          setBilling(null);
        }
        if (txRes.status === "fulfilled") {
          setTxns(txRes.value.transactions);
          setTxNext(txRes.value.next_before_id);
          setTxError("");
        } else {
          setTxError(
            txRes.reason instanceof Error && txRes.reason.message
              ? txRes.reason.message
              : t(`${NS}.loadError`)
          );
        }
        if (invRes.status === "fulfilled") {
          setInvoices(invRes.value.invoices);
          setInvError("");
        } else {
          setInvError(
            invRes.reason instanceof Error && invRes.reason.message
              ? invRes.reason.message
              : t(`${NS}.loadError`)
          );
        }
        setStatus("ok");
      } catch (error) {
        setStatus("error");
        setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      } finally {
        if (asRefresh) setRefreshing(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accountId, t]
  );

  useEffect(() => {
    load().catch(() => setStatus("error"));
  }, [load]);

  const loadMoreTxns = useCallback(async () => {
    if (!accountId || !txNext || txLoadingMore) return;
    setTxLoadingMore(true);
    try {
      const page = await listAdWalletTransactions(accountId, { limit: 50, beforeId: txNext });
      setTxns((prev) => [...prev, ...page.transactions]);
      setTxNext(page.next_before_id);
    } catch (error) {
      setTxError(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
    } finally {
      setTxLoadingMore(false);
    }
  }, [accountId, t, txLoadingMore, txNext]);

  const addFunds = useCallback(async () => {
    const cents = parseDollars(fundAmount);
    if (cents === null || Number.isNaN(cents) || cents <= 0) {
      setFundNote(t(`${NS}.invalidAmount`));
      return;
    }
    setFundBusy(true);
    setFundNote("");
    try {
      const session = await createAdFundingSession(accountId, cents);
      if (!session.checkout_url) {
        setFundNote(t(`${NS}.fundingNoUrl`));
        return;
      }
      await Linking.openURL(session.checkout_url);
      setFundNote(t(`${NS}.fundingOpened`));
    } catch (error) {
      // The iOS-native 403 arrives as a full sentence from the server and is
      // shown verbatim — it explains where funding lives, it is not a failure.
      setFundNote(error instanceof Error && error.message ? error.message : t(`${NS}.fundingError`));
    } finally {
      setFundBusy(false);
    }
  }, [accountId, fundAmount, t]);

  const saveLimits = useCallback(() => {
    const daily = parseDollars(dailyInput);
    const lifetime = parseDollars(lifetimeInput);
    if (Number.isNaN(daily) || Number.isNaN(lifetime)) {
      setLimitsNote(t(`${NS}.invalidAmount`));
      return;
    }
    Alert.alert(t(`${NS}.limitsConfirmTitle`), t(`${NS}.limitsConfirmBody`), [
      { text: t(`${NS}.cancel`), style: "cancel" },
      {
        text: t(`${NS}.confirm`),
        onPress: async () => {
          setLimitsBusy(true);
          setLimitsNote("");
          try {
            const saved = await setAdSpendingLimits(accountId, {
              daily_limit_cents: daily,
              lifetime_limit_cents: lifetime
            });
            applyLimits(saved);
            setLimitsNote(t(`${NS}.limitsSaved`));
          } catch (error) {
            setLimitsNote(
              error instanceof Error && error.message ? error.message : t(`${NS}.saveError`)
            );
          } finally {
            setLimitsBusy(false);
          }
        }
      }
    ]);
  }, [accountId, applyLimits, dailyInput, lifetimeInput, t]);

  const saveTopup = useCallback(async () => {
    const threshold = parseDollars(topupThreshold);
    const amount = parseDollars(topupAmount);
    if (Number.isNaN(threshold) || Number.isNaN(amount)) {
      setTopupNote(t(`${NS}.invalidAmount`));
      return;
    }
    setTopupBusy(true);
    setTopupNote("");
    try {
      const saved = await setAdAutoTopup(accountId, {
        enabled: topupEnabled,
        threshold_cents: threshold ?? 0,
        amount_cents: amount ?? 0
      });
      applyTopup(saved);
      setTopupNote(t(`${NS}.topupSaved`));
    } catch (error) {
      setTopupNote(error instanceof Error && error.message ? error.message : t(`${NS}.saveError`));
    } finally {
      setTopupBusy(false);
    }
  }, [accountId, applyTopup, t, topupAmount, topupEnabled, topupThreshold]);

  const owedCents = Number(wallet?.amount_owed_cents || 0);
  const credits = useMemo(
    () =>
      [
        { key: "creditsPromo", cents: Number(wallet?.promotional_credits_cents || 0) },
        { key: "creditsBonus", cents: Number(wallet?.bonus_credits_cents || 0) },
        { key: "creditsRefund", cents: Number(wallet?.refund_credits_cents || 0) }
      ].filter((row) => row.cents > 0),
    [wallet]
  );

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={() => navigation?.goBack?.()}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={adsLight.text.muted} />
      }
    >
      {status === "loading" ? (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={132} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={96} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={200} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
        </View>
      ) : status === "error" ? (
        <View style={s.stack}>
          <AdsSectionError
            message={errorText}
            onRetry={() => load()}
            reducedMotion={reducedMotion}
            retryLabel={t(`${NS}.retry`)}
          />
        </View>
      ) : (
        <>
          {/* Balance */}
          <Animated.View style={[s.stack, entrance.styleFor(0)]}>
            {wallet?.unavailable ? (
              <AdsWalletUnavailable onRetry={() => load()} reducedMotion={reducedMotion} />
            ) : (
              <View style={s.card}>
                <Text style={s.meta}>{t(`${NS}.balanceAvailable`)}</Text>
                <Text style={styles.balanceAmount}>
                  {money(Number(wallet?.available_balance_cents || 0))}
                </Text>
                <View style={styles.balanceGrid}>
                  <View style={styles.balanceCell}>
                    <Text style={s.meta}>{t(`${NS}.balanceSpendable`)}</Text>
                    <Text style={styles.balanceCellValue}>
                      {money(Number(wallet?.spendable_balance_cents || 0))}
                    </Text>
                  </View>
                  <View style={styles.balanceCell}>
                    <Text style={s.meta}>{t(`${NS}.balanceReserved`)}</Text>
                    <Text style={styles.balanceCellValue}>
                      {money(Number(wallet?.reserved_budget_cents || 0))}
                    </Text>
                  </View>
                  <View style={styles.balanceCell}>
                    <Text style={s.meta}>{t(`${NS}.balancePending`)}</Text>
                    <Text style={styles.balanceCellValue}>
                      {money(Number(wallet?.pending_balance_cents || 0))}
                    </Text>
                  </View>
                  <View style={styles.balanceCell}>
                    <Text style={s.meta}>{t(`${NS}.lifetimeSpent`)}</Text>
                    <Text style={styles.balanceCellValue}>
                      {money(Number(wallet?.lifetime_spent_cents || 0))}
                    </Text>
                  </View>
                </View>
                {owedCents > 0 ? (
                  <Text style={styles.owed}>{t(`${NS}.balanceOwed`, { amount: money(owedCents) })}</Text>
                ) : null}
                {credits.map((row) => (
                  <Text key={row.key} style={s.meta}>
                    {t(`${NS}.${row.key}`)}: {money(row.cents)}
                  </Text>
                ))}
              </View>
            )}
          </Animated.View>

          {/* Add funds */}
          <Animated.View style={[s.stack, entrance.styleFor(1)]}>
            <View style={s.card}>
              <Text style={s.cardTitle}>{t(`${NS}.addFundsTitle`)}</Text>
              <Text style={s.cardBody}>{t(`${NS}.addFundsBody`)}</Text>
              <Text style={s.inputLabel}>{t(`${NS}.addFundsAmountLabel`, { currency })}</Text>
              <TextInput
                style={s.input}
                value={fundAmount}
                onChangeText={setFundAmount}
                keyboardType="decimal-pad"
                placeholder="25"
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.addFundsAmountLabel`, { currency })}
              />
              <Pressable
                style={[s.primaryBtn, fundBusy ? styles.busy : null]}
                onPress={addFunds}
                disabled={fundBusy}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.addFundsCta`)}
              >
                <Text style={s.primaryBtnText}>
                  {fundBusy ? t(`${NS}.working`) : t(`${NS}.addFundsCta`)}
                </Text>
              </Pressable>
              {fundNote ? <AdsOfflineNote text={fundNote} /> : null}
            </View>
          </Animated.View>

          {/* Payment method / billing status */}
          <Animated.View style={[s.stack, entrance.styleFor(2)]}>
            <View style={s.card}>
              <Text style={s.cardTitle}>{t(`${NS}.paymentTitle`)}</Text>
              <Text style={s.cardBody}>{t(`${NS}.paymentBody`)}</Text>
              {billing ? (
                <>
                  <Text style={s.meta}>
                    {t(`${NS}.paymentBillingStatus`, {
                      status: String(billing.billing_status || "")
                    })}
                  </Text>
                  <Text style={s.meta}>
                    {t(`${NS}.paymentFundingStatus`, {
                      status: String(billing.funding_status || "")
                    })}
                  </Text>
                  {!billing.live_charging ? (
                    <Text style={s.meta}>{t(`${NS}.paymentNoLiveCharge`)}</Text>
                  ) : null}
                </>
              ) : null}
            </View>
          </Animated.View>

          {/* Transactions */}
          <Animated.View style={[s.stack, entrance.styleFor(3)]}>
            <Text style={s.sectionTitle}>{t(`${NS}.txTitle`)}</Text>
            {txError ? (
              <AdsSectionError
                message={txError}
                onRetry={() => load()}
                reducedMotion={reducedMotion}
                retryLabel={t(`${NS}.retry`)}
              />
            ) : txns.length === 0 ? (
              <AdsEmpty
                title={t(`${NS}.txEmptyTitle`)}
                body={t(`${NS}.txEmptyBody`)}
                reducedMotion={reducedMotion}
              />
            ) : (
              <>
                {txns.map((txn) => (
                  <View key={txn.id} style={s.card}>
                    <View style={s.headRow}>
                      <View style={styles.txnLeft}>
                        <Text style={s.cardTitle} numberOfLines={2}>
                          {txn.description || txn.transaction_type}
                        </Text>
                        <Text style={s.meta}>
                          {txn.created_at ? txn.created_at.slice(0, 10) : ""}
                          {txn.status ? ` · ${txn.status}` : ""}
                          {txn.campaign_id
                            ? ` · ${t(`${NS}.txCampaign`, { id: String(txn.campaign_id) })}`
                            : ""}
                        </Text>
                      </View>
                      <Text
                        style={[
                          styles.txnAmount,
                          txn.amount_cents < 0 ? styles.txnAmountNeg : styles.txnAmountPos
                        ]}
                      >
                        {formatCents(txn.amount_cents, txn.currency)}
                      </Text>
                    </View>
                  </View>
                ))}
                {txNext ? (
                  <Pressable
                    style={s.secondaryBtn}
                    onPress={loadMoreTxns}
                    disabled={txLoadingMore}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.txLoadMore`)}
                  >
                    <Text style={s.secondaryBtnText}>
                      {txLoadingMore ? t(`${NS}.working`) : t(`${NS}.txLoadMore`)}
                    </Text>
                  </Pressable>
                ) : null}
              </>
            )}
          </Animated.View>

          {/* Invoices */}
          <Animated.View style={[s.stack, entrance.styleFor(4)]}>
            <Text style={s.sectionTitle}>{t(`${NS}.invTitle`)}</Text>
            {invError ? (
              <AdsSectionError
                message={invError}
                onRetry={() => load()}
                reducedMotion={reducedMotion}
                retryLabel={t(`${NS}.retry`)}
              />
            ) : invoices.length === 0 ? (
              <AdsEmpty
                title={t(`${NS}.invEmptyTitle`)}
                body={t(`${NS}.invEmptyBody`)}
                reducedMotion={reducedMotion}
              />
            ) : (
              invoices.map((invoice) => (
                <View key={invoice.id} style={s.card}>
                  <View style={s.headRow}>
                    <View style={styles.txnLeft}>
                      <Text style={s.cardTitle}>{invoice.invoice_number || `#${invoice.id}`}</Text>
                      <Text style={s.meta}>
                        {t(`${NS}.invPeriod`, {
                          start: invoice.period_start.slice(0, 10),
                          end: invoice.period_end.slice(0, 10)
                        })}
                        {invoice.status ? ` · ${invoice.status}` : ""}
                      </Text>
                    </View>
                    <Text style={styles.txnAmount}>
                      {formatCents(invoice.amount_cents, invoice.currency)}
                    </Text>
                  </View>
                </View>
              ))
            )}
          </Animated.View>

          {/* Spending limits */}
          <Animated.View style={[s.stack, entrance.styleFor(5)]}>
            <View style={s.card}>
              <Text style={s.cardTitle}>{t(`${NS}.limitsTitle`)}</Text>
              <Text style={s.cardBody}>{t(`${NS}.limitsBody`)}</Text>
              <Text style={s.inputLabel}>{t(`${NS}.limitsDaily`, { currency })}</Text>
              <TextInput
                style={s.input}
                value={dailyInput}
                onChangeText={setDailyInput}
                keyboardType="decimal-pad"
                placeholder={t(`${NS}.limitsNone`)}
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.limitsDaily`, { currency })}
              />
              <Text style={s.inputLabel}>{t(`${NS}.limitsLifetime`, { currency })}</Text>
              <TextInput
                style={s.input}
                value={lifetimeInput}
                onChangeText={setLifetimeInput}
                keyboardType="decimal-pad"
                placeholder={t(`${NS}.limitsNone`)}
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.limitsLifetime`, { currency })}
              />
              <Pressable
                style={[s.primaryBtn, limitsBusy ? styles.busy : null]}
                onPress={saveLimits}
                disabled={limitsBusy}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.limitsSave`)}
              >
                <Text style={s.primaryBtnText}>
                  {limitsBusy ? t(`${NS}.working`) : t(`${NS}.limitsSave`)}
                </Text>
              </Pressable>
              {limitsNote ? <AdsOfflineNote text={limitsNote} /> : null}
            </View>
          </Animated.View>

          {/* Auto top-up + billing info + classic payments link */}
          <Animated.View style={[s.stack, entrance.styleFor(6)]}>
            <View style={s.card}>
              <View style={s.headRow}>
                <Text style={s.cardTitle}>{t(`${NS}.topupTitle`)}</Text>
                <Switch
                  value={topupEnabled}
                  onValueChange={setTopupEnabled}
                  accessibilityLabel={t(`${NS}.topupTitle`)}
                />
              </View>
              <Text style={s.cardBody}>{t(`${NS}.topupBody`)}</Text>
              <Text style={s.inputLabel}>{t(`${NS}.topupThreshold`, { currency })}</Text>
              <TextInput
                style={s.input}
                value={topupThreshold}
                onChangeText={setTopupThreshold}
                keyboardType="decimal-pad"
                placeholder="10"
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.topupThreshold`, { currency })}
              />
              <Text style={s.inputLabel}>{t(`${NS}.topupAmount`, { currency })}</Text>
              <TextInput
                style={s.input}
                value={topupAmount}
                onChangeText={setTopupAmount}
                keyboardType="decimal-pad"
                placeholder="25"
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.topupAmount`, { currency })}
              />
              <Pressable
                style={[s.secondaryBtn, topupBusy ? styles.busy : null]}
                onPress={saveTopup}
                disabled={topupBusy}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.topupSave`)}
              >
                <Text style={s.secondaryBtnText}>
                  {topupBusy ? t(`${NS}.working`) : t(`${NS}.topupSave`)}
                </Text>
              </Pressable>
              {topupNote ? <AdsOfflineNote text={topupNote} /> : null}
            </View>

            <View style={s.card}>
              <Text style={s.cardTitle}>{t(`${NS}.billingInfoTitle`)}</Text>
              <Text style={s.cardBody}>{t(`${NS}.billingInfoBody`)}</Text>
              <Pressable
                onPress={() => navigation?.navigate("BusinessOsPayments", {})}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.openPayments`)}
              >
                <Text style={s.inlineLink}>{t(`${NS}.openPayments`)}</Text>
              </Pressable>
            </View>
          </Animated.View>
        </>
      )}
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  balanceAmount: {
    fontSize: 30,
    fontWeight: "800",
    color: adsLight.wallet.amount,
    lineHeight: 36
  },
  balanceGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  balanceCell: { minWidth: "45%", gap: 2 },
  balanceCellValue: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  owed: { fontSize: 13, fontWeight: "700", color: adsLight.status.error, lineHeight: 19 },
  txnLeft: { flex: 1, gap: 2 },
  txnAmount: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  txnAmountNeg: { color: adsLight.status.error },
  txnAmountPos: { color: adsLight.status.success },
  busy: { opacity: 0.6 }
});

export default AdsWalletScreen;
