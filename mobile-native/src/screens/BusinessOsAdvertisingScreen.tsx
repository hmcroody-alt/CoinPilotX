import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  AD_BUDGET_TYPES,
  AD_CAMPAIGN_OBJECTIVES,
  AdAccount,
  AdBudgetType,
  AdCampaign,
  AdCampaignAction,
  AdCampaignObjective,
  adAccountCanTransact,
  availableAdCampaignActions,
  createAdAccount,
  createAdCampaign,
  formatCampaignBudget,
  formatCents,
  formatObjective,
  listAdAccounts,
  listAdCampaigns,
  loadCachedAdAccounts,
  loadCachedAdCampaigns,
  runAdCampaignAction
} from "../api/businessOs";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { colors } from "../theme/colors";

type Props = {
  navigation?: { navigate: (...args: any[]) => void };
};

const ACTION_LABELS: Record<AdCampaignAction, string> = {
  pause: "Pause",
  resume: "Resume",
  archive: "Archive",
  duplicate: "Duplicate",
  submit: "Submit for review",
  complete: "Mark complete"
};

/**
 * Advertising inside Business OS.
 *
 * Bound to the live `/api/pulse/ads/*` surface. Campaign controls are derived
 * from the campaign's current status via `availableAdCampaignActions`, so the
 * screen only offers transitions the backend will accept — no button here can
 * be tapped into a guaranteed error.
 */
export function BusinessOsAdvertisingScreen(_props: Props) {
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [campaigns, setCampaigns] = useState<AdCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const [businessName, setBusinessName] = useState("");
  const [businessEmail, setBusinessEmail] = useState("");

  const [campaignName, setCampaignName] = useState("");
  const [objective, setObjective] = useState<AdCampaignObjective>("awareness");
  const [budgetType, setBudgetType] = useState<AdBudgetType>("daily");
  const [budgetDollars, setBudgetDollars] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setOffline(false);
    const [accountResult, campaignResult] = await Promise.allSettled([listAdAccounts(), listAdCampaigns()]);

    if (accountResult.status === "fulfilled") {
      setAccounts(accountResult.value.accounts);
      setSelectedAccountId((current) => current || accountResult.value.accounts[0]?.id || 0);
    } else {
      const cached = await loadCachedAdAccounts().catch(() => []);
      setAccounts(cached);
      setSelectedAccountId((current) => current || cached[0]?.id || 0);
    }

    if (campaignResult.status === "fulfilled") {
      setCampaigns(campaignResult.value.campaigns);
    } else {
      setCampaigns(await loadCachedAdCampaigns().catch(() => []));
    }

    if (accountResult.status === "rejected" && campaignResult.status === "rejected") {
      setOffline(true);
      setMessage(
        accountResult.reason instanceof Error ? accountResult.reason.message : "Advertising could not reach PulseSoc."
      );
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  async function submitAccount() {
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
      setMessage(
        result.account
          ? `${result.account.business_name} created. It stays in verification until PulseSoc approves it.`
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
    if (!selectedAccountId) {
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
        ad_account_id: selectedAccountId,
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
    setBusy(`campaign-${campaign.id}-${action}`);
    setMessage("");
    try {
      const result = await runAdCampaignAction(campaign.id, action);
      setMessage(result.message || `${ACTION_LABELS[action]} applied to ${campaign.campaign_name}.`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${ACTION_LABELS[action]} could not be applied.`);
    } finally {
      setBusy("");
    }
  }

  const selectedAccount = accounts.find((account) => account.id === selectedAccountId);

  return (
    <Screen title="Advertising" subtitle="Ad accounts, campaigns and budgets for your business.">
      {loading ? (
        <Panel>
          <View style={styles.row}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.muted}>Loading advertising…</Text>
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
          <Text style={styles.muted}>Campaign controls are unavailable until PulseSoc can be reached.</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry loading advertising"
            onPress={() => load().catch(() => undefined)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>Retry</Text>
          </Pressable>
        </Panel>
      ) : null}

      {!loading && !accounts.length ? (
        <Panel>
          <Text style={styles.panelTitle}>Create your ad account</Text>
          <Text style={styles.muted}>
            An ad account is how PulseSoc bills and verifies your advertising. New accounts start unverified and cannot
            deliver campaigns until they are approved.
          </Text>
          <TextInput
            accessibilityLabel="Business name"
            placeholder="Business name"
            placeholderTextColor={colors.muted}
            value={businessName}
            onChangeText={setBusinessName}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="Business email"
            placeholder="Business email (optional)"
            placeholderTextColor={colors.muted}
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
        </Panel>
      ) : null}

      {accounts.length ? (
        <Panel>
          <Text style={styles.panelTitle}>Ad accounts</Text>
          {accounts.map((account) => (
            <Pressable
              key={account.id}
              accessibilityRole="button"
              accessibilityLabel={`Select ${account.business_name}. Status ${account.status}.`}
              accessibilityState={{ selected: account.id === selectedAccountId }}
              onPress={() => setSelectedAccountId(account.id)}
              style={[styles.accountRow, account.id === selectedAccountId && styles.accountRowSelected]}
            >
              <Text style={styles.accountName}>{account.business_name}</Text>
              <Text style={styles.muted}>
                {adAccountCanTransact(account)
                  ? "Active — campaigns can deliver."
                  : `${String(account.status).replace(/_/g, " ")} — campaigns cannot deliver yet.`}
              </Text>
            </Pressable>
          ))}
        </Panel>
      ) : null}

      {selectedAccount ? (
        <Panel>
          <Text style={styles.panelTitle}>New campaign</Text>
          <TextInput
            accessibilityLabel="Campaign name"
            placeholder="Campaign name"
            placeholderTextColor={colors.muted}
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
            placeholderTextColor={colors.muted}
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
          <Text style={styles.footnote}>Campaigns start as drafts. Nothing is charged and nothing delivers until you submit for review.</Text>
        </Panel>
      ) : null}

      {!loading && campaigns.length ? (
        <Panel>
          <Text style={styles.panelTitle}>Campaigns</Text>
          {campaigns.map((campaign) => (
            <View key={campaign.id} style={styles.campaign}>
              <Text style={styles.campaignName}>{campaign.campaign_name}</Text>
              <Text style={styles.muted}>
                {formatObjective(campaign.objective)} · {String(campaign.status).replace(/_/g, " ")} ·{" "}
                {formatCampaignBudget(campaign)}
              </Text>
              <Text style={styles.muted}>Spent {formatCents(campaign.spent_cents)}</Text>
              <View style={styles.chips}>
                {availableAdCampaignActions(campaign).map((action) => {
                  const key = `campaign-${campaign.id}-${action}`;
                  return (
                    <Pressable
                      key={action}
                      accessibilityRole="button"
                      accessibilityLabel={`${ACTION_LABELS[action]} ${campaign.campaign_name}`}
                      accessibilityState={{ disabled: Boolean(busy) || offline }}
                      disabled={Boolean(busy) || offline}
                      onPress={() => applyAction(campaign, action)}
                      style={[styles.chip, (Boolean(busy) || offline) && styles.buttonDisabled]}
                    >
                      <Text style={styles.chipText}>{busy === key ? "Working…" : ACTION_LABELS[action]}</Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          ))}
        </Panel>
      ) : null}

      {!loading && accounts.length && !campaigns.length ? (
        <Panel>
          <Text style={styles.panelTitle}>No campaigns yet</Text>
          <Text style={styles.muted}>Campaigns you create appear here with their delivery status and spend.</Text>
        </Panel>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  accountName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  accountRow: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: 12
  },
  accountRowSelected: {
    borderColor: colors.accent
  },
  buttonDisabled: {
    opacity: 0.5
  },
  campaign: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    gap: 6,
    padding: 12
  },
  campaignName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  chip: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  chipSelected: {
    borderColor: colors.accent
  },
  chipText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600"
  },
  chipTextSelected: {
    color: colors.text
  },
  chips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  fieldLabel: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700"
  },
  footnote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    fontSize: 15,
    paddingHorizontal: 12,
    paddingVertical: 10
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
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 11
  },
  primaryButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "800"
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
  }
});
