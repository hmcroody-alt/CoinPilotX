/**
 * Advertising — the two-sided ads manager.
 *
 * One screen, two ad products switched by the header ModeToggle without a
 * navigation push:
 *
 *   • MARKETPLACE ADS (gold = money) is fully backed. Accounts, campaigns,
 *     analytics and the wallet come from the live `/api/pulse/ads/*` surface via
 *     `loadAdsMarketplace`. Every money figure is read from the server and
 *     formatted — nothing is computed on the client and presented as truth.
 *
 *   • POST ADS (violet = content) is an unbacked, flag-gated preview. Behind
 *     `EXPO_PUBLIC_ADS_POST_MODE` it shows clearly-tagged MOCK-DATA promotions
 *     and one suggestion; with the flag off it says the product is coming rather
 *     than inventing content. It never shows a fabricated balance or spend — the
 *     one real wallet chip funds both modes and lives on the header.
 *
 * Both mode panes stay mounted (the inactive one is `display:'none'`) so each
 * keeps its own scroll position across a swap, and the last mode is persisted so
 * the screen re-opens where the advertiser left it. This view derives campaign
 * controls from `availableAdCampaignActions`, so no button offers a transition
 * the backend would reject.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  AD_BUDGET_TYPES,
  AD_CAMPAIGN_OBJECTIVES,
  AdBudgetType,
  AdCampaign,
  AdCampaignAction,
  AdCampaignObjective,
  createAdAccount,
  createAdCampaign,
  formatCents,
  formatObjective,
  requestAdAccountVerification,
  runAdCampaignAction
} from "../api/businessOs";
import {
  ADS_POST_MODE_FLAG,
  AdsMarketplaceModel,
  AdsMode,
  adAccountDisplay,
  adCampaignDisplay,
  adsPostModeEnabled,
  availableAdCampaignActions,
  campaignBlockedByVerification,
  campaignPhase,
  loadAdsMarketplace,
  loadMockPostPromotions,
  loadMockSuggestion,
  promotionPhaseLabel,
  promotionPhaseTone
} from "../api/adsDashboard";
// No `attributionNote` here. This screen renders the card without a metric
// strip, and the note is a statement about what the strip does not measure —
// on its own it would be an unprompted disclaimer about numbers that aren't on
// the page. `CampaignCard` enforces the same pairing.
import {
  accountVerificationState,
  deliveryState,
  deliveryStateDetail,
  deliveryStateLabel,
  deliveryStateTone
} from "../api/adsDelivery";
import {
  AdsHeader,
  CampaignCard,
  CampaignCardAction,
  CampaignCardBudget,
  PromotedPostCard,
  ACCOUNT_SPEND_TITLE,
  SEVEN_DAY_SPEND_TITLE,
  SpendBarChart,
  SuggestionCard
} from "../components/ads";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { adsLight } from "../theme/adsLight";

type Props = {
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const MODE_CACHE_KEY = "ads.lastMode.v1";

const ACTION_LABELS: Record<AdCampaignAction, string> = {
  pause: "Pause",
  resume: "Resume",
  archive: "Archive",
  duplicate: "Duplicate",
  submit: "Submit for review",
  complete: "Mark complete"
};

/** Weekday initials for the last seven days, oldest first, ending today. */
function lastSevenDayLabels(): string[] {
  const letters = ["S", "M", "T", "W", "T", "F", "S"];
  const today = new Date().getDay();
  const out: string[] = [];
  for (let i = 6; i >= 0; i--) {
    out.push(letters[(today - i + 7) % 7]);
  }
  return out;
}

/** Compact reach, e.g. 12400 → "12.4k reached". Preview-only figures. */
function formatReach(n: number): string {
  if (n >= 1000) {
    const k = (n / 1000).toFixed(1).replace(/\.0$/, "");
    return `${k}k reached`;
  }
  return `${n} reached`;
}

/**
 * A campaign's budget row for the card, or null when no budget is set. Reads the
 * daily or lifetime figure the backend stored and the real spent figure; the
 * fraction is a pacing ratio for the bar, never presented as a money truth.
 */
function deriveCampaignBudget(campaign: AdCampaign): CampaignCardBudget | null {
  const isDaily = campaign.budget_type === "daily";
  const budgetCents = Number((isDaily ? campaign.daily_budget_cents : campaign.lifetime_budget_cents) || 0);
  if (budgetCents <= 0) return null;
  const spent = Number(campaign.spent_cents || 0);
  const fraction = Math.min(1, spent / budgetCents);
  return {
    spentLabel: formatCents(spent),
    budgetLabel: `${formatCents(budgetCents)}${isDaily ? "/day" : ""}`,
    fraction,
    hot: fraction >= 0.85
  };
}

export function BusinessOsAdvertisingScreen({ navigation }: Props) {
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const postEnabled = useMemo(() => adsPostModeEnabled(), []);

  const [model, setModel] = useState<AdsMarketplaceModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<AdsMode>("marketplace");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [suggestionDismissed, setSuggestionDismissed] = useState(false);

  const [businessName, setBusinessName] = useState("");
  const [businessEmail, setBusinessEmail] = useState("");
  const [campaignName, setCampaignName] = useState("");
  const [objective, setObjective] = useState<AdCampaignObjective>("awareness");
  const [budgetType, setBudgetType] = useState<AdBudgetType>("daily");
  const [budgetDollars, setBudgetDollars] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await loadAdsMarketplace();
      setModel(next);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  // Restore the last mode. Post is only restored when the preview flag is on, so
  // a build without the flag never lands the user on the unavailable product.
  useEffect(() => {
    let active = true;
    readJsonCache<{ mode: AdsMode }>(MODE_CACHE_KEY, (value) => value)
      .then((cached) => {
        if (!active || !cached) return;
        if (cached.mode === "post" && !postEnabled) return;
        if (cached.mode === "post" || cached.mode === "marketplace") setMode(cached.mode);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [postEnabled]);

  const changeMode = useCallback((next: AdsMode) => {
    setMode(next);
    writeJsonCache(MODE_CACHE_KEY, { mode: next }).catch(() => undefined);
  }, []);

  const openWallet = useCallback(() => {
    const accountId = model?.wallet?.accountId ?? model?.primaryAccount?.id;
    navigation?.navigate("BusinessOsPayments", { title: "Ad wallet", accountId });
  }, [navigation, model]);

  /**
   * Ask for the ad account to be reviewed.
   *
   * Not the Verification Center, which this used to open. That flow posts to
   * `/api/dashboard/account/verification/request` and decides a profile badge;
   * it never writes `pulse_ad_accounts.status`, which is the only column
   * `select_ads` consults. An advertiser could finish it and still deliver
   * nothing, having been told the opposite by the button they pressed.
   */
  const requestVerification = useCallback(async () => {
    const accountId = model?.primaryAccount?.id;
    if (!accountId) return;
    if (model?.offline) {
      setMessage("You are offline. Reconnect to request verification.");
      return;
    }
    setBusy("verification");
    setMessage("");
    try {
      await requestAdAccountVerification(accountId);
      setMessage("Verification requested. We'll tell you as soon as it's decided.");
      await load();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Verification couldn't be requested. Try again."
      );
    } finally {
      setBusy("");
    }
  }, [load, model]);

  const verificationIsRequestable = useMemo(() => {
    if (!model?.primaryAccount) return false;
    const state = accountVerificationState(model.primaryAccount);
    return state === "unverified" || state === "rejected";
  }, [model?.primaryAccount]);

  async function submitAccount() {
    if (model?.offline) {
      setMessage("You are offline. Reconnect to create an ad account.");
      return;
    }
    if (!businessName.trim()) {
      setMessage("Business name is required.");
      return;
    }
    setBusy("account");
    setMessage("");
    try {
      const result = await createAdAccount({
        business_name: businessName.trim(),
        business_email: businessEmail.trim() || undefined
      });
      setBusinessName("");
      setBusinessEmail("");
      // Through the shared helper rather than reading `business_name` raw: an
      // account created with a blank name produced " created." here, a sentence
      // beginning with a space. Same class as "Ad account 8", same fix.
      setMessage(
        result.account
          ? `${adAccountDisplay(result.account).name} created. It stays in verification until PulseSoc approves it.`
          : "Ad account created."
      );
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ad account could not be created.");
    } finally {
      setBusy("");
    }
  }

  async function submitCampaign() {
    if (model?.offline) {
      setMessage("You are offline. Reconnect to create a campaign.");
      return;
    }
    const account = model?.primaryAccount;
    if (!account) {
      setMessage("Create an ad account first.");
      return;
    }
    if (!campaignName.trim()) {
      setMessage("Campaign name is required.");
      return;
    }
    const cents = Math.round(Number(budgetDollars.replace(/[^0-9.]/g, "")) * 100) || 0;
    setBusy("campaign");
    setMessage("");
    try {
      await createAdCampaign({
        ad_account_id: account.id,
        campaign_name: campaignName.trim(),
        objective,
        budget_type: budgetType,
        daily_budget_cents: budgetType === "daily" ? cents : undefined,
        lifetime_budget_cents: budgetType === "lifetime" ? cents : undefined
      });
      setCampaignName("");
      setBudgetDollars("");
      setMessage("Campaign saved as a draft. Submit it for review when you are ready.");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Campaign could not be created.");
    } finally {
      setBusy("");
    }
  }

  async function applyAction(campaign: AdCampaign, action: AdCampaignAction) {
    if (busy || model?.offline) return;
    setBusy(`campaign-${campaign.id}-${action}`);
    setMessage("");
    try {
      const result = await runAdCampaignAction(campaign.id, action);
      // Same helper as the card below, so the confirmation names the campaign
      // exactly as the list does — and never as an empty space.
      setMessage(
        result.message || `${ACTION_LABELS[action]} applied to ${adCampaignDisplay(campaign).name}.`
      );
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${ACTION_LABELS[action]} could not be applied.`);
    } finally {
      setBusy("");
    }
  }

  function toggleDelivery(campaign: AdCampaign, next: boolean) {
    applyAction(campaign, next ? "resume" : "pause").catch(() => undefined);
  }

  const offline = Boolean(model?.offline);

  // Null rather than a placeholder, same as the manager screen: the chip owns
  // the wording for a balance that has not arrived.
  const walletProp = loading
    ? { balanceLabel: null, fundingLive: false, loading: true }
    : model?.wallet
    ? { balanceLabel: model.wallet.balanceLabel, fundingLive: model.wallet.fundingLive, loading: false }
    : null;

  const contentPad = { paddingBottom: Math.max(insets.bottom, 16) + 24 };

  /* ----------------------------- Marketplace ---------------------------- */

  const dayLabels = useMemo(() => lastSevenDayLabels(), []);
  // Same rule as the manager screen: the card may only name a window its data
  // actually covers. `windowed` is false while the analytics endpoint takes no
  // date range, so this reports a to-date total under a to-date heading.
  const spend = model?.spend;
  const spendEmpty = !spend || spend.daysCents.length === 0;
  const spendWindowed = Boolean(spend?.windowed) && !spendEmpty;
  const spendTotalLabel = formatCents(spend?.totalCents || 0);
  const spendSummary = spendWindowed
    ? `Spend, last 7 days: ${spendTotalLabel} total in that period.`
    : `Account spend. ${spendTotalLabel} total to date. A day-by-day view is not available yet.`;

  const marketplaceBody = (
    <View style={styles.stack}>
      {model?.offline ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Showing saved data</Text>
          <Text style={styles.muted}>Campaign controls are unavailable until PulseSoc can be reached.</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading advertising"
            onPress={() => load().catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </View>
      ) : null}

      {model?.needsVerification ? (
        verificationIsRequestable ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Request verification for this ad account"
            accessibilityState={{ busy: busy === "verification" }}
            onPress={busy === "verification" ? undefined : requestVerification}
            style={styles.verifyBanner}
          >
            <Text style={styles.verifyTitle}>Verification needed</Text>
            <Text style={styles.verifyText}>
              Your ad account can’t deliver campaigns until PulseSoc verifies it.
            </Text>
            <Text style={styles.verifyAction}>
              {busy === "verification" ? "Sending…" : "Request verification ›"}
            </Text>
          </Pressable>
        ) : (
          // In review, or verified but not active. Neither has a move for the
          // advertiser to make, so neither is rendered as something to press.
          <View style={styles.verifyBanner} accessibilityLiveRegion="polite">
            <Text style={styles.verifyTitle}>
              {accountVerificationState(model.primaryAccount || {}) === "pending"
                ? "Verification in review"
                : "Account not active"}
            </Text>
            <Text style={styles.verifyText}>
              {accountVerificationState(model.primaryAccount || {}) === "pending"
                ? "Campaigns can deliver once your account review is decided. Nothing is charged while you wait."
                : "This account is verified but isn’t marked active, which shouldn’t happen — approval writes both. Contact support so it can be corrected."}
            </Text>
          </View>
        )
      ) : null}

      {model?.primaryAccount ? (
        <SpendBarChart
          title={spendWindowed ? SEVEN_DAY_SPEND_TITLE : ACCOUNT_SPEND_TITLE}
          values={spend?.daysCents || []}
          dayLabels={dayLabels}
          summary={spendSummary}
          mock={Boolean(spend?.mock)}
          empty={spendEmpty}
          totalLabel={spendTotalLabel}
          reducedMotion={reducedMotion}
          seriesKey={spend?.totalCents ?? 0}
        />
      ) : null}

      {loading && !model ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={adsLight.money.budget} />
          <Text style={styles.muted}>Loading advertising…</Text>
        </View>
      ) : null}

      {!loading && model && !model.accounts.length ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Create your ad account</Text>
          <Text style={styles.muted}>
            An ad account is how PulseSoc bills and verifies your advertising. New accounts start unverified and cannot
            deliver campaigns until they are approved.
          </Text>
          <TextInput
            accessibilityLabel="Business name"
            placeholder="Business name"
            placeholderTextColor={adsLight.text.muted}
            value={businessName}
            onChangeText={setBusinessName}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="Business email"
            placeholder="Business email (optional)"
            placeholderTextColor={adsLight.text.muted}
            autoCapitalize="none"
            keyboardType="email-address"
            value={businessEmail}
            onChangeText={setBusinessEmail}
            style={styles.input}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Create ad account"
            accessibilityState={{ disabled: busy === "account" || offline }}
            disabled={busy === "account" || offline}
            onPress={submitAccount}
            style={[styles.primaryButton, (busy === "account" || offline) && styles.buttonDisabled]}
          >
            <Text style={styles.primaryButtonText}>{busy === "account" ? "Creating…" : "Create ad account"}</Text>
          </Pressable>
        </View>
      ) : null}

      {!loading && model && model.campaigns.length
        ? model.campaigns.map((campaign) => {
            // Two derivations, deliberately. `phase` is the lifecycle, and it is
            // what the switch keys on — whether a campaign is pausable is a
            // question about its status. The pill asks a different question,
            // "is this reaching anyone", and `campaignPhaseLabel` answered it by
            // mapping `status === 'active'` straight to "Delivering". That is
            // one of the eight conditions `select_ads` requires (§31), so the
            // pill now reads `deliveryState` and carries the reason underneath.
            const phase = campaignPhase(campaign);
            const delivery = deliveryState(model.portal ?? null, campaign);
            const blocked = campaignBlockedByVerification(campaign, model.primaryAccount || undefined);
            const actions: CampaignCardAction[] = availableAdCampaignActions(campaign)
              .filter((a) => a !== "pause" && a !== "resume")
              .map((a) => ({
                key: `${campaign.id}-${a}`,
                label: ACTION_LABELS[a],
                onPress: () => applyAction(campaign, a)
              }));
            return (
              <CampaignCard
                key={campaign.id}
                name={adCampaignDisplay(campaign).name}
                reference={adCampaignDisplay(campaign).reference}
                objectiveLabel={formatObjective(campaign.objective)}
                phase={delivery}
                phaseLabel={deliveryStateLabel(delivery)}
                phaseTone={deliveryStateTone(delivery)}
                phaseDetail={deliveryStateDetail(model.portal ?? null, campaign)}
                budget={deriveCampaignBudget(campaign)}
                showSwitch={phase === "delivering" || phase === "paused"}
                delivering={phase === "delivering"}
                onToggleDelivery={(next) => toggleDelivery(campaign, next)}
                toggleBusy={busy.startsWith(`campaign-${campaign.id}-`)}
                blockedVerification={blocked}
                onVerify={verificationIsRequestable ? requestVerification : undefined}
                actions={actions}
                onPress={() => undefined}
                reducedMotion={reducedMotion}
              />
            );
          })
        : null}

      {!loading && model && model.accounts.length && !model.campaigns.length ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>No campaigns yet</Text>
          <Text style={styles.muted}>Campaigns you create appear here with their delivery status and spend.</Text>
        </View>
      ) : null}

      {!loading && model && model.primaryAccount ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>New campaign</Text>
          <TextInput
            accessibilityLabel="Campaign name"
            placeholder="Campaign name"
            placeholderTextColor={adsLight.text.muted}
            value={campaignName}
            onChangeText={setCampaignName}
            style={styles.input}
          />

          <Text style={styles.fieldLabel}>Objective</Text>
          <View style={styles.chips}>
            {AD_CAMPAIGN_OBJECTIVES.map((option) => (
              <Pressable
                key={option}
                accessibilityRole="button"
                accessibilityLabel={`Objective ${formatObjective(option)}`}
                accessibilityState={{ selected: option === objective }}
                onPress={() => setObjective(option)}
                style={[styles.chip, option === objective && styles.chipSelected]}
              >
                <Text style={[styles.chipText, option === objective && styles.chipTextSelected]}>
                  {formatObjective(option)}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.fieldLabel}>Budget</Text>
          <View style={styles.chips}>
            {AD_BUDGET_TYPES.map((option) => (
              <Pressable
                key={option}
                accessibilityRole="button"
                accessibilityLabel={`${option === "daily" ? "Daily" : "Lifetime"} budget`}
                accessibilityState={{ selected: option === budgetType }}
                onPress={() => setBudgetType(option)}
                style={[styles.chip, option === budgetType && styles.chipSelected]}
              >
                <Text style={[styles.chipText, option === budgetType && styles.chipTextSelected]}>
                  {option === "daily" ? "Daily" : "Lifetime"}
                </Text>
              </Pressable>
            ))}
          </View>
          <TextInput
            accessibilityLabel="Budget amount in dollars"
            placeholder={budgetType === "daily" ? "Daily budget, e.g. 25.00" : "Lifetime budget, e.g. 500.00"}
            placeholderTextColor={adsLight.text.muted}
            keyboardType="decimal-pad"
            value={budgetDollars}
            onChangeText={setBudgetDollars}
            style={styles.input}
          />

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Create campaign"
            accessibilityState={{ disabled: busy === "campaign" || offline }}
            disabled={busy === "campaign" || offline}
            onPress={submitCampaign}
            style={[styles.primaryButton, (busy === "campaign" || offline) && styles.buttonDisabled]}
          >
            <Text style={styles.primaryButtonText}>{busy === "campaign" ? "Creating…" : "Create campaign"}</Text>
          </Pressable>
          <Text style={styles.footnote}>
            Campaigns start as drafts. Nothing is charged and nothing delivers until you submit for review.
          </Text>
        </View>
      ) : null}
    </View>
  );

  /* -------------------------------- Post -------------------------------- */

  const suggestion = useMemo(() => loadMockSuggestion(), [postEnabled]);
  const promotions = useMemo(() => loadMockPostPromotions(), [postEnabled]);

  const postBody = (
    <View style={styles.stack}>
      <View style={styles.previewNote} accessibilityRole="summary">
        <Text style={styles.previewNoteTitle}>Post ads · Preview</Text>
        <Text style={styles.muted}>
          {postEnabled
            ? "Promoting posts, Reels and live replays is a preview. Figures below are sample data — nothing is charged and no promotion is running."
            : "Promoting posts, Reels and live replays isn’t available yet. Your ad wallet still funds Marketplace ads."}
        </Text>
      </View>

      {postEnabled && suggestion && !suggestionDismissed ? (
        <SuggestionCard
          contentType={suggestion.contentType}
          title={suggestion.title}
          reason={suggestion.reason}
          onPromote={() => setMessage("Promoting posts is a preview — nothing is charged.")}
          onDismiss={() => setSuggestionDismissed(true)}
          reducedMotion={reducedMotion}
        />
      ) : null}

      {postEnabled
        ? promotions.map((promotion) => (
            <PromotedPostCard
              key={promotion.id}
              contentType={promotion.contentType}
              title={promotion.title}
              phase={promotion.phase}
              phaseLabel={promotionPhaseLabel(promotion.phase)}
              phaseTone={promotionPhaseTone(promotion.phase)}
              reachLabel={promotion.reach != null ? formatReach(promotion.reach) : null}
              spendLabel={promotion.spendCents != null ? formatCents(promotion.spendCents) : null}
              rejectionReason={promotion.rejectionReason ?? null}
              onPress={() => setMessage("Post promotions are a preview — nothing is charged.")}
              reducedMotion={reducedMotion}
            />
          ))
        : null}

      {!postEnabled ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Not available yet</Text>
          <Text style={styles.muted}>
            Post promotions turn on behind the {ADS_POST_MODE_FLAG} feature flag. Until then this product stays a
            preview and shows no invented promotions or spend.
          </Text>
        </View>
      ) : null}
    </View>
  );

  return (
    <View style={styles.root}>
      <AdsHeader
        title="Advertising"
        mode={mode}
        onChangeMode={changeMode}
        onBack={() => navigation?.goBack?.()}
        postIsPreview
        wallet={walletProp}
        onWallet={openWallet}
        reducedMotion={reducedMotion}
      />

      {message ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${message}. Tap to dismiss.`}
          onPress={() => setMessage("")}
          style={styles.notice}
        >
          <Text style={styles.noticeText}>{message}</Text>
        </Pressable>
      ) : null}

      <View style={[styles.pane, mode === "marketplace" ? null : styles.hidden]}>
        <ScrollView contentContainerStyle={[styles.scroll, contentPad]} keyboardShouldPersistTaps="handled">
          {marketplaceBody}
        </ScrollView>
      </View>

      <View style={[styles.pane, mode === "post" ? null : styles.hidden]}>
        <ScrollView contentContainerStyle={[styles.scroll, contentPad]} keyboardShouldPersistTaps="handled">
          {postBody}
        </ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  pane: { flex: 1 },
  hidden: { display: "none" },
  scroll: { padding: adsLight.space.card, gap: 12 },
  stack: { gap: 12 },
  notice: {
    marginHorizontal: adsLight.space.card,
    marginTop: 10,
    padding: 12,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  noticeText: { fontSize: 13, color: adsLight.text.primary, lineHeight: 18 },
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 10
  },
  cardTitle: { fontSize: 16, fontWeight: "700", color: adsLight.text.primary },
  muted: { fontSize: 13, color: adsLight.text.muted, lineHeight: 19 },
  loadingRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: adsLight.space.card },
  verifyBanner: {
    padding: adsLight.space.card,
    gap: 4,
    borderRadius: adsLight.radius.card,
    backgroundColor: adsLight.bg.warning,
    borderWidth: 1,
    borderColor: adsLight.border.warning
  },
  verifyTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  verifyText: { fontSize: 13, color: adsLight.text.primary, lineHeight: 18 },
  verifyAction: { fontSize: 13, fontWeight: "800", color: adsLight.status.warning, marginTop: 2 },
  previewNote: {
    padding: adsLight.space.card,
    gap: 4,
    borderRadius: adsLight.radius.card,
    backgroundColor: adsLight.bg.postSurface,
    borderWidth: 1,
    borderColor: adsLight.suggestion.border
  },
  previewNoteTitle: { fontSize: 14, fontWeight: "800", color: adsLight.post.base },
  input: {
    backgroundColor: adsLight.bg.page,
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    color: adsLight.text.primary,
    fontSize: 15,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  fieldLabel: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    backgroundColor: adsLight.bg.page,
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  chipSelected: { borderColor: adsLight.money.budget, backgroundColor: adsLight.bg.card },
  chipText: { fontSize: 13, fontWeight: "600", color: adsLight.text.muted },
  chipTextSelected: { color: adsLight.text.primary },
  primaryButton: {
    alignItems: "center",
    backgroundColor: adsLight.cta.from,
    borderRadius: adsLight.radius.control,
    paddingVertical: 12
  },
  primaryButtonText: { fontSize: 15, fontWeight: "800", color: adsLight.cta.text },
  buttonDisabled: { opacity: 0.5 },
  footnote: { fontSize: 12, color: adsLight.text.muted, lineHeight: 18 },
  secondaryButton: {
    alignSelf: "flex-start",
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  secondaryButtonText: { fontSize: 14, fontWeight: "600", color: adsLight.text.primary }
});
