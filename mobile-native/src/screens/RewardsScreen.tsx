/**
 * Rewards — Pulse Credits and cash rewards, on the Stripe-first rail.
 *
 * Every figure is a server total. The hero renders `credit_balance` from
 * `GET /api/pulse/rewards` — never a sum of the list — and the ledger's running
 * balances are `balance_after`, written server-side.
 *
 * Redeeming burns credits into promotional ad credits on the seller's ad
 * account. The `redemption_key` is minted when the confirm dialog opens and
 * reused across retries of that confirmation (the cart checkout pattern), so a
 * double-tap or a retried failure replays one burn instead of making two;
 * `duplicate: true` says the server had already processed the key.
 *
 * Claiming an approved cash reward can answer three ways, and each renders
 * honestly: a payout started; `needs_onboarding` with a Stripe onboarding URL
 * that opens in the browser; or `setup_required`, which means the disbursement
 * rail is not available yet — the screen says exactly that rather than
 * spinning or pretending a payout is on its way.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Linking,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { primaryAdAccount } from "../api/adsDashboard";
import { listAdAccounts, loadCachedAdAccounts, formatCents } from "../api/businessOs";
import {
  CreditLedgerEntry,
  Reward,
  RewardStatusTone,
  claimReward,
  fetchCreditLedger,
  fetchRewards,
  mintRedemptionKey,
  redeemCredits,
  rewardIsClaimable,
  rewardIsUnderReview,
  rewardStatusChip
} from "../api/rewards";
import {
  AdsEmpty,
  AdsOfflineNote,
  AdsScreenShell,
  AdsSectionError,
  AdsSkeletonBlock,
  adsSubStyles as s
} from "../components/ads";
import { useTranslation } from "../i18n";
import { adsLight } from "../theme/adsLight";
import { paymentsLight } from "../theme/paymentsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

type Props = {
  route?: { params?: { title?: string } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:rewards";
const REWARDS_PAGE = 30;
const LEDGER_PAGE = 30;

/**
 * Tone → colour for a reward status chip. The tone is decided in
 * `rewardStatusChip`; progress borrows the payments processing blue because
 * "disbursing" is the same claim the payout rail makes in that colour.
 */
const TONE_COLOR: Record<RewardStatusTone, string> = {
  progress: paymentsLight.balance.processingAccent,
  success: adsLight.status.success,
  error: adsLight.status.error,
  neutral: adsLight.status.neutral
};

/** Whole credits only — "25" → 25; empty → null; junk or fractions → NaN. */
function parseCredits(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0 || !Number.isInteger(value)) return Number.NaN;
  return value;
}

export function RewardsScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();

  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  /** The server's finished credit total. Never derived from the list below. */
  const [creditBalance, setCreditBalance] = useState(0);
  const [rewards, setRewards] = useState<Reward[]>([]);
  const [rewardsNext, setRewardsNext] = useState<number | null>(null);
  const [rewardsHasMore, setRewardsHasMore] = useState(false);
  const [rewardsLoadingMore, setRewardsLoadingMore] = useState(false);

  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [ledger, setLedger] = useState<CreditLedgerEntry[]>([]);
  const [ledgerNext, setLedgerNext] = useState<number | null>(null);
  const [ledgerHasMore, setLedgerHasMore] = useState(false);
  const [ledgerError, setLedgerError] = useState("");
  const [ledgerLoadingMore, setLedgerLoadingMore] = useState(false);

  /** The ad account promotional credits land on. 0 until resolved. */
  const [accountId, setAccountId] = useState(0);

  const [redeemInput, setRedeemInput] = useState("");
  const [redeemBusy, setRedeemBusy] = useState(false);
  const [redeemNote, setRedeemNote] = useState("");
  /** The redeem intent key — minted when the confirm dialog opens, reused
   *  across retries of it, discarded on success or when the amount changes. */
  const redemptionKey = useRef("");

  const [claimBusyId, setClaimBusyId] = useState(0);
  const [claimNote, setClaimNote] = useState("");

  const load = useCallback(
    async (asRefresh = false) => {
      if (asRefresh) setRefreshing(true);
      else setStatus("loading");
      setErrorText("");
      try {
        const [rewardsRes, ledgerRes, accountsRes] = await Promise.allSettled([
          fetchRewards({ limit: REWARDS_PAGE }),
          fetchCreditLedger({ limit: LEDGER_PAGE }),
          listAdAccounts().catch(async () => ({
            accounts: await loadCachedAdAccounts().catch(() => [])
          }))
        ]);
        if (rewardsRes.status === "fulfilled") {
          setRewards(rewardsRes.value.rewards);
          setRewardsNext(rewardsRes.value.next_before_id);
          setRewardsHasMore(rewardsRes.value.has_more);
          setCreditBalance(rewardsRes.value.credit_balance);
        } else {
          throw rewardsRes.reason;
        }
        if (ledgerRes.status === "fulfilled") {
          setLedger(ledgerRes.value.entries);
          setLedgerNext(ledgerRes.value.next_before_id);
          setLedgerHasMore(ledgerRes.value.has_more);
          setLedgerError("");
        } else {
          setLedgerError(
            ledgerRes.reason instanceof Error && ledgerRes.reason.message
              ? ledgerRes.reason.message
              : t(`${NS}.loadError`)
          );
        }
        if (accountsRes.status === "fulfilled") {
          setAccountId(primaryAdAccount(accountsRes.value.accounts || [])?.id || 0);
        }
        setStatus("ok");
      } catch (error) {
        setStatus("error");
        setErrorText(error instanceof Error && error.message ? error.message : t(`${NS}.loadError`));
      } finally {
        if (asRefresh) setRefreshing(false);
      }
    },
    [t]
  );

  useEffect(() => {
    load().catch(() => setStatus("error"));
  }, [load]);

  const loadMoreRewards = useCallback(async () => {
    if (!rewardsNext || !rewardsHasMore || rewardsLoadingMore) return;
    setRewardsLoadingMore(true);
    try {
      const page = await fetchRewards({ limit: REWARDS_PAGE, beforeId: rewardsNext });
      setRewards((prev) => {
        const known = new Set(prev.map((reward) => reward.id));
        return prev.concat(page.rewards.filter((reward) => !known.has(reward.id)));
      });
      setRewardsNext(page.next_before_id);
      setRewardsHasMore(page.has_more);
    } catch {
      // A failed page is not a shorter list — leave the button tappable again.
    } finally {
      setRewardsLoadingMore(false);
    }
  }, [rewardsHasMore, rewardsLoadingMore, rewardsNext]);

  const loadMoreLedger = useCallback(async () => {
    if (!ledgerNext || !ledgerHasMore || ledgerLoadingMore) return;
    setLedgerLoadingMore(true);
    try {
      const page = await fetchCreditLedger({ limit: LEDGER_PAGE, beforeId: ledgerNext });
      setLedger((prev) => {
        const known = new Set(prev.map((entry) => entry.id));
        return prev.concat(page.entries.filter((entry) => !known.has(entry.id)));
      });
      setLedgerNext(page.next_before_id);
      setLedgerHasMore(page.has_more);
    } catch {
      // Same rule as above.
    } finally {
      setLedgerLoadingMore(false);
    }
  }, [ledgerHasMore, ledgerLoadingMore, ledgerNext]);

  const changeRedeemInput = useCallback((text: string) => {
    // A new amount is a new intent; the old key must not replay a different burn.
    redemptionKey.current = "";
    setRedeemInput(text);
  }, []);

  const submitRedeem = useCallback(
    async (credits: number) => {
      setRedeemBusy(true);
      setRedeemNote("");
      try {
        const result = await redeemCredits(credits, accountId, redemptionKey.current);
        redemptionKey.current = "";
        setCreditBalance(result.credit_balance);
        setRedeemInput("");
        setRedeemNote(
          result.duplicate
            ? t(`${NS}.redeemDuplicate`)
            : t(`${NS}.redeemDone`, {
                amount: formatCents(result.promo_credit_cents, "USD")
              })
        );
        await load(true).catch(() => undefined);
      } catch (error) {
        // The key survives, so confirming again replays this burn, not a second.
        setRedeemNote(
          error instanceof Error && error.message ? error.message : t(`${NS}.redeemError`)
        );
      } finally {
        setRedeemBusy(false);
      }
    },
    [accountId, load, t]
  );

  const startRedeem = useCallback(() => {
    const credits = parseCredits(redeemInput);
    if (credits === null || Number.isNaN(credits) || credits <= 0) {
      setRedeemNote(t(`${NS}.redeemInvalid`));
      return;
    }
    if (credits > creditBalance) {
      setRedeemNote(t(`${NS}.redeemTooMany`));
      return;
    }
    if (!accountId) {
      setRedeemNote(t(`${NS}.redeemNoAccount`));
      return;
    }
    setRedeemNote("");
    // Minted when the confirm step opens; reused if the same confirmation is
    // retried after a failure, because the amount has not changed.
    if (!redemptionKey.current) redemptionKey.current = mintRedemptionKey();
    Alert.alert(
      t(`${NS}.redeemConfirmTitle`),
      t(`${NS}.redeemConfirmBody`, { amount: credits }),
      [
        { text: t(`${NS}.cancel`), style: "cancel" },
        { text: t(`${NS}.confirm`), onPress: () => void submitRedeem(credits) }
      ]
    );
  }, [accountId, creditBalance, redeemInput, submitRedeem, t]);

  const claim = useCallback(
    async (reward: Reward) => {
      setClaimBusyId(reward.id);
      setClaimNote("");
      try {
        const result = await claimReward(reward.id);
        if (result.needs_onboarding && result.onboarding_url) {
          setClaimNote(t(`${NS}.claimOnboarding`));
          await Linking.openURL(result.onboarding_url);
        } else if (result.setup_required) {
          // The disbursement rail is not available yet. Saying so plainly beats
          // a spinner that implies money is moving.
          setClaimNote(t(`${NS}.claimSetupRequired`));
        } else {
          setClaimNote(t(`${NS}.claimStarted`));
          await load(true).catch(() => undefined);
        }
      } catch (error) {
        setClaimNote(
          error instanceof Error && error.message ? error.message : t(`${NS}.claimError`)
        );
      } finally {
        setClaimBusyId(0);
      }
    },
    [load, t]
  );

  /** Amount line: a credit count for credits, money for cash — the server's
   *  own distinction, carried through untouched. */
  const rewardAmount = useCallback(
    (reward: Reward): string =>
      reward.reward_kind === "cash"
        ? formatCents(reward.amount, reward.currency)
        : t(`${NS}.creditsAmount`, { amount: reward.amount }),
    [t]
  );

  return (
    <AdsScreenShell
      title={route?.params?.title || t(`${NS}.title`)}
      backLabel={t(`${NS}.back`)}
      onBack={() => navigation?.goBack?.()}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load(true)}
          tintColor={adsLight.text.muted}
        />
      }
    >
      {status === "loading" ? (
        <View style={s.stack}>
          <AdsSkeletonBlock width="100%" height={120} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={180} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
          <AdsSkeletonBlock width="100%" height={140} radius={adsLight.radius.card} reducedMotion={reducedMotion} />
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
          {/* Credit balance — the server's total, never a client sum. */}
          <View style={s.stack}>
            <View style={s.card}>
              <Text style={s.meta}>{t(`${NS}.creditHeroLabel`)}</Text>
              <Text style={styles.heroAmount} accessibilityRole="header" allowFontScaling>
                {t(`${NS}.creditsAmount`, { amount: creditBalance })}
              </Text>
              <Text style={s.cardBody}>{t(`${NS}.creditHeroBody`)}</Text>
            </View>
          </View>

          {/* Redeem — credits → promotional ad credits on the ad account. */}
          <View style={s.stack}>
            <View style={s.card}>
              <Text style={s.cardTitle}>{t(`${NS}.redeemTitle`)}</Text>
              <Text style={s.cardBody}>{t(`${NS}.redeemBody`)}</Text>
              <Text style={s.inputLabel}>{t(`${NS}.redeemAmountLabel`)}</Text>
              <TextInput
                style={s.input}
                value={redeemInput}
                onChangeText={changeRedeemInput}
                keyboardType="number-pad"
                placeholder="100"
                placeholderTextColor={adsLight.text.muted}
                accessibilityLabel={t(`${NS}.redeemAmountLabel`)}
              />
              <Pressable
                style={[s.primaryBtn, redeemBusy ? styles.busy : null]}
                onPress={startRedeem}
                disabled={redeemBusy}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.redeemCta`)}
              >
                <Text style={s.primaryBtnText}>
                  {redeemBusy ? t(`${NS}.working`) : t(`${NS}.redeemCta`)}
                </Text>
              </Pressable>
              {redeemNote ? <AdsOfflineNote text={redeemNote} /> : null}
            </View>
          </View>

          {/* Rewards list */}
          <View style={s.stack}>
            <Text style={s.sectionTitle}>{t(`${NS}.rewardsTitle`)}</Text>
            {claimNote ? <AdsOfflineNote text={claimNote} /> : null}
            {rewards.length === 0 ? (
              <AdsEmpty
                title={t(`${NS}.rewardsEmptyTitle`)}
                body={t(`${NS}.rewardsEmptyBody`)}
                reducedMotion={reducedMotion}
              />
            ) : (
              <>
                {rewards.map((reward) => {
                  const chip = rewardStatusChip(reward.status);
                  const chipColor = TONE_COLOR[chip.tone];
                  return (
                    <View key={reward.id} style={s.card}>
                      <View style={s.headRow}>
                        <View style={styles.rowLeft}>
                          <Text style={s.cardTitle} numberOfLines={2}>
                            {reward.event_type || reward.event_key}
                          </Text>
                          <Text style={s.meta}>
                            {reward.created_at ? reward.created_at.slice(0, 10) : ""}
                            {rewardIsUnderReview(reward) ? ` · ${t(`${NS}.underReview`)}` : ""}
                          </Text>
                        </View>
                        <View style={styles.rowRight}>
                          <Text style={styles.rowAmount}>{rewardAmount(reward)}</Text>
                          <View style={[styles.chip, { borderColor: chipColor }]}>
                            <Text style={[styles.chipText, { color: chipColor }]}>
                              {chip.key ? t(`${NS}.${chip.key}`) : reward.status}
                            </Text>
                          </View>
                        </View>
                      </View>
                      {rewardIsClaimable(reward) ? (
                        <Pressable
                          style={[s.primaryBtn, claimBusyId === reward.id ? styles.busy : null]}
                          onPress={() => void claim(reward)}
                          disabled={claimBusyId !== 0}
                          accessibilityRole="button"
                          accessibilityLabel={t(`${NS}.claimCta`)}
                          accessibilityState={{
                            disabled: claimBusyId !== 0,
                            busy: claimBusyId === reward.id
                          }}
                        >
                          <Text style={s.primaryBtnText}>
                            {claimBusyId === reward.id ? t(`${NS}.working`) : t(`${NS}.claimCta`)}
                          </Text>
                        </Pressable>
                      ) : null}
                    </View>
                  );
                })}
                {rewardsHasMore ? (
                  <Pressable
                    style={s.secondaryBtn}
                    onPress={() => void loadMoreRewards()}
                    disabled={rewardsLoadingMore}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.loadMore`)}
                  >
                    <Text style={s.secondaryBtnText}>
                      {rewardsLoadingMore ? t(`${NS}.working`) : t(`${NS}.loadMore`)}
                    </Text>
                  </Pressable>
                ) : null}
              </>
            )}
          </View>

          {/* Credit ledger — collapsed by default; balances are server-written. */}
          <View style={s.stack}>
            <Pressable
              onPress={() => setLedgerOpen((open) => !open)}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.${ledgerOpen ? "ledgerHide" : "ledgerShow"}`)}
              accessibilityState={{ expanded: ledgerOpen }}
              style={styles.ledgerToggle}
            >
              <Text style={s.sectionTitle}>{t(`${NS}.ledgerTitle`)}</Text>
              <Text style={s.inlineLink}>
                {t(`${NS}.${ledgerOpen ? "ledgerHide" : "ledgerShow"}`)}
              </Text>
            </Pressable>
            {!ledgerOpen ? null : ledgerError ? (
              <AdsSectionError
                message={ledgerError}
                onRetry={() => load()}
                reducedMotion={reducedMotion}
                retryLabel={t(`${NS}.retry`)}
              />
            ) : ledger.length === 0 ? (
              <Text style={s.meta}>{t(`${NS}.ledgerEmpty`)}</Text>
            ) : (
              <>
                {ledger.map((entry) => (
                  <View key={entry.id} style={s.card}>
                    <View style={s.headRow}>
                      <View style={styles.rowLeft}>
                        <Text style={s.cardTitle} numberOfLines={2}>
                          {entry.reason}
                        </Text>
                        <Text style={s.meta}>
                          {entry.created_at ? entry.created_at.slice(0, 10) : ""}
                          {" · "}
                          {t(`${NS}.ledgerBalanceAfter`, { amount: entry.balance_after })}
                        </Text>
                      </View>
                      <Text
                        style={[
                          styles.rowAmount,
                          entry.delta < 0 ? styles.deltaNeg : styles.deltaPos
                        ]}
                      >
                        {entry.delta > 0 ? `+${entry.delta}` : String(entry.delta)}
                      </Text>
                    </View>
                  </View>
                ))}
                {ledgerHasMore ? (
                  <Pressable
                    style={s.secondaryBtn}
                    onPress={() => void loadMoreLedger()}
                    disabled={ledgerLoadingMore}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.loadMore`)}
                  >
                    <Text style={s.secondaryBtnText}>
                      {ledgerLoadingMore ? t(`${NS}.working`) : t(`${NS}.loadMore`)}
                    </Text>
                  </Pressable>
                ) : null}
              </>
            )}
          </View>
        </>
      )}
    </AdsScreenShell>
  );
}

const styles = StyleSheet.create({
  busy: { opacity: 0.6 },
  chip: {
    borderRadius: adsLight.radius.pill,
    borderWidth: 1,
    marginTop: 4,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  chipText: { fontSize: 12, fontWeight: "700" },
  deltaNeg: { color: adsLight.status.error },
  deltaPos: { color: adsLight.status.success },
  heroAmount: {
    color: adsLight.text.primary,
    fontSize: 30,
    fontWeight: "800",
    lineHeight: 36
  },
  ledgerToggle: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  rowAmount: { color: adsLight.text.primary, fontSize: 15, fontWeight: "800" },
  rowLeft: { flex: 1, gap: 2 },
  rowRight: { alignItems: "flex-end" }
});

export default RewardsScreen;
