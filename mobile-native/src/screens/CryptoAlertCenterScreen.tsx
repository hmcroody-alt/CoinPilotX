/**
 * Crypto Alert Center — the premium alert surface over `/api/mobile/crypto/alerts`.
 *
 * This EXTENDS the existing basic alert flow, it does not replace it. The
 * legacy `AlertManagement` screen keeps owning the free above/below flow it has
 * always owned; this screen speaks the new advanced-rule contract: multi-
 * condition rules (up to five), AND/OR matching, crossings, windowed moves,
 * volume, market-cap and portfolio conditions, frequency and cooldown.
 *
 * Free accounts can still create basic rules here (single price above/below),
 * because the server accepts `rule_type:"basic"` from any account. Advanced
 * options are always *visible* so the user can see what Premium buys, but the
 * moment a draft becomes advanced without the `advanced_alerts` capability,
 * saving routes to the upsell rather than the API. The server re-checks either
 * way — capability truth lives there, this screen only avoids a doomed call.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  AlertConditionType,
  CryptoAlertCapabilities,
  PremiumAlert,
  createPremiumAlert,
  deletePremiumAlert,
  getPremiumAlerts,
  isPremiumRequired,
  updatePremiumAlert
} from "../api/cryptoPremium";
import {
  AlertFormIssue,
  CONDITION_GROUPS,
  ConditionGroupKey,
  CryptoAlertFormState,
  FREE_BASIC_RULE_LIMIT,
  MAX_CONDITIONS_PER_RULE,
  MAX_WINDOW_MINUTES,
  MIN_WINDOW_MINUTES,
  PREMIUM_RULE_LIMIT,
  buildAlertPayload,
  classifyRuleType,
  conditionRequiresWindow,
  emptyConditionDraft,
  emptyCryptoAlertForm,
  formFromAlert,
  validateCryptoAlertForm
} from "../core/cryptoAlertForm";
import { Panel } from "../components/Panel";
import { PremiumUpsellPanel } from "../components/crypto/PremiumUpsellPanel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "CryptoAlertCenter">;

/** Condition type → catalog key. Copy stays in the catalogs, never here. */
export const CONDITION_TYPE_LABEL_KEYS: Record<AlertConditionType, string> = {
  price_above: "discovery:crypto.alerts.conditionTypes.priceAbove",
  price_below: "discovery:crypto.alerts.conditionTypes.priceBelow",
  price_crosses_above: "discovery:crypto.alerts.conditionTypes.priceCrossesAbove",
  price_crosses_below: "discovery:crypto.alerts.conditionTypes.priceCrossesBelow",
  price_move_pct: "discovery:crypto.alerts.conditionTypes.priceMovePct",
  price_move_abs: "discovery:crypto.alerts.conditionTypes.priceMoveAbs",
  volume_above: "discovery:crypto.alerts.conditionTypes.volumeAbove",
  volume_below: "discovery:crypto.alerts.conditionTypes.volumeBelow",
  volume_move_pct: "discovery:crypto.alerts.conditionTypes.volumeMovePct",
  market_cap_above: "discovery:crypto.alerts.conditionTypes.marketCapAbove",
  market_cap_below: "discovery:crypto.alerts.conditionTypes.marketCapBelow",
  market_cap_move_pct: "discovery:crypto.alerts.conditionTypes.marketCapMovePct",
  portfolio_value_above: "discovery:crypto.alerts.conditionTypes.portfolioValueAbove",
  portfolio_value_below: "discovery:crypto.alerts.conditionTypes.portfolioValueBelow",
  portfolio_move_pct: "discovery:crypto.alerts.conditionTypes.portfolioMovePct",
  allocation_above: "discovery:crypto.alerts.conditionTypes.allocationAbove"
};

const GROUP_LABEL_KEYS: Record<ConditionGroupKey, string> = {
  price: "discovery:crypto.alerts.groups.price",
  volume: "discovery:crypto.alerts.groups.volume",
  marketCap: "discovery:crypto.alerts.groups.marketCap",
  portfolio: "discovery:crypto.alerts.groups.portfolio"
};

const STATUS_LABEL_KEYS: Record<string, string> = {
  active: "discovery:crypto.alerts.status.active",
  paused: "discovery:crypto.alerts.status.paused",
  triggered: "discovery:crypto.alerts.status.triggered",
  error: "discovery:crypto.alerts.status.error"
};

function groupOfType(type: AlertConditionType): ConditionGroupKey {
  return CONDITION_GROUPS.find((group) => group.types.includes(type))?.key || "price";
}

export function CryptoAlertCenterScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const presetSymbol = String(route.params?.presetSymbol || "").trim().toUpperCase().slice(0, 12);

  const [items, setItems] = useState<PremiumAlert[]>([]);
  const [capabilities, setCapabilities] = useState<CryptoAlertCapabilities>({ advanced_alerts: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [gated, setGated] = useState(false);
  const [form, setForm] = useState<CryptoAlertFormState>(emptyCryptoAlertForm(presetSymbol));
  const [editingId, setEditingId] = useState<number | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);

  const advancedAllowed = capabilities.advanced_alerts;
  const draftRuleType = classifyRuleType(form);
  const draftNeedsPremium = draftRuleType === "advanced" && !advancedAllowed;

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await getPremiumAlerts();
      setItems(response.items);
      setCapabilities(response.capabilities);
      setGated(false);
    } catch (loadError) {
      if (isPremiumRequired(loadError)) {
        setGated(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : t("discovery:crypto.alerts.loadError"));
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const issueText = useCallback(
    (issue: AlertFormIssue): string => {
      switch (issue.code) {
        case "symbol_required":
          return t("discovery:crypto.alerts.errors.symbolRequired");
        case "symbol_invalid":
          return t("discovery:crypto.alerts.errors.symbolInvalid");
        case "no_conditions":
          return t("discovery:crypto.alerts.errors.noConditions");
        case "too_many_conditions":
          return t("discovery:crypto.alerts.errors.tooManyConditions", { max: MAX_CONDITIONS_PER_RULE });
        case "condition_type_invalid":
          return t("discovery:crypto.alerts.errors.conditionTypeInvalid");
        case "threshold_required":
          return t("discovery:crypto.alerts.errors.thresholdRequired");
        case "threshold_invalid":
          return t("discovery:crypto.alerts.errors.thresholdInvalid");
        case "window_required":
          return t("discovery:crypto.alerts.errors.windowRequired");
        case "window_out_of_range":
          return t("discovery:crypto.alerts.errors.windowOutOfRange", {
            min: MIN_WINDOW_MINUTES,
            max: MAX_WINDOW_MINUTES
          });
        case "cooldown_invalid":
          return t("discovery:crypto.alerts.errors.cooldownInvalid");
        case "premium_required":
          return t("discovery:crypto.alerts.errors.premiumRequired");
        default:
          return t("discovery:crypto.alerts.saveFailed");
      }
    },
    [t]
  );

  function startEdit(alert: PremiumAlert) {
    setEditingId(alert.id);
    setForm(formFromAlert(alert));
    setNotice("");
    setError("");
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyCryptoAlertForm(presetSymbol));
  }

  async function saveForm() {
    const issues = validateCryptoAlertForm(form, { advancedAllowed });
    if (issues.length) {
      setError(issueText(issues[0]));
      setNotice("");
      return;
    }
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const payload = buildAlertPayload(form);
      const result = editingId ? await updatePremiumAlert(editingId, payload) : await createPremiumAlert(payload);
      resetForm();
      await load();
      setNotice(result.message || t("discovery:crypto.alerts.saved"));
    } catch (saveError) {
      if (isPremiumRequired(saveError)) {
        setError(t("discovery:crypto.alerts.errors.premiumRequired"));
      } else {
        setError(saveError instanceof Error ? saveError.message : t("discovery:crypto.alerts.saveFailed"));
      }
    } finally {
      setBusy("");
    }
  }

  async function toggleEnabled(alert: PremiumAlert) {
    setBusy(`toggle:${alert.id}`);
    setError("");
    try {
      await updatePremiumAlert(alert.id, { enabled: !alert.enabled });
      await load();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : t("discovery:crypto.alerts.toggleFailed"));
    } finally {
      setBusy("");
    }
  }

  async function removeAlert(alert: PremiumAlert) {
    setBusy(`delete:${alert.id}`);
    setError("");
    try {
      const result = await deletePremiumAlert(alert.id);
      setPendingDeleteId(null);
      if (editingId === alert.id) resetForm();
      await load();
      setNotice(result.message || t("discovery:crypto.alerts.deleted"));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : t("discovery:crypto.alerts.deleteFailed"));
    } finally {
      setBusy("");
    }
  }

  function updateCondition(index: number, patch: Partial<CryptoAlertFormState["conditions"][number]>) {
    setForm((current) => ({
      ...current,
      conditions: current.conditions.map((condition, at) => (at === index ? { ...condition, ...patch } : condition))
    }));
  }

  function setConditionType(index: number, type: AlertConditionType) {
    setForm((current) => ({
      ...current,
      conditions: current.conditions.map((condition, at) =>
        at === index
          ? {
              ...condition,
              type,
              windowMinutes: conditionRequiresWindow(type)
                ? condition.windowMinutes || String(MIN_WINDOW_MINUTES * 4)
                : ""
            }
          : condition
      )
    }));
  }

  const conditionSummary = useMemo(
    () => (alert: PremiumAlert) =>
      alert.conditions
        .map((condition) => `${t(CONDITION_TYPE_LABEL_KEYS[condition.type] || "discovery:crypto.alerts.listTitle")} ${condition.threshold}`)
        .join(alert.match === "any" ? " / " : " + "),
    [t]
  );

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("discovery:crypto.common.loading")}</Text>
      </View>
    );
  }

  if (gated) {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <PremiumUpsellPanel
          body={t("discovery:crypto.upsell.alertsBody")}
          onUpgrade={() => navigation.navigate("Premium")}
        />
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>{t("discovery:crypto.alerts.title")}</Text>
        <Text style={styles.subtitle}>{t("discovery:crypto.alerts.subtitle")}</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <Text style={styles.sectionTitle}>
          {editingId ? t("discovery:crypto.alerts.formTitleEdit") : t("discovery:crypto.alerts.formTitleCreate")}
        </Text>
        <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.symbolLabel")}</Text>
        <TextInput
          accessibilityLabel={t("discovery:crypto.alerts.symbolLabel")}
          autoCapitalize="characters"
          placeholder="BTC"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={form.symbol}
          onChangeText={(symbol) => setForm((current) => ({ ...current, symbol }))}
        />

        {form.conditions.map((condition, index) => {
          const group = groupOfType(condition.type);
          const groupTypes = CONDITION_GROUPS.find((entry) => entry.key === group)?.types || [];
          return (
            <View key={`condition-${index}`} style={styles.conditionCard}>
              <View style={styles.rowHead}>
                <Text style={styles.rowTitle}>
                  {t("discovery:crypto.alerts.conditionTitle", { number: index + 1 })}
                </Text>
                {form.conditions.length > 1 ? (
                  <ActionButton
                    label={t("discovery:crypto.alerts.removeCondition")}
                    variant="secondary"
                    onPress={() =>
                      setForm((current) => ({
                        ...current,
                        conditions: current.conditions.filter((_, at) => at !== index)
                      }))
                    }
                  />
                ) : null}
              </View>
              <View style={styles.segmentWrap}>
                {CONDITION_GROUPS.map((entry) => (
                  <Segment
                    key={entry.key}
                    label={t(GROUP_LABEL_KEYS[entry.key])}
                    active={group === entry.key}
                    onPress={() => setConditionType(index, entry.types[0])}
                  />
                ))}
              </View>
              <View style={styles.segmentWrap}>
                {groupTypes.map((type) => (
                  <Segment
                    key={type}
                    label={t(CONDITION_TYPE_LABEL_KEYS[type])}
                    active={condition.type === type}
                    onPress={() => setConditionType(index, type)}
                  />
                ))}
              </View>
              <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.thresholdLabel")}</Text>
              <TextInput
                accessibilityLabel={t("discovery:crypto.alerts.thresholdLabel")}
                keyboardType="numeric"
                placeholderTextColor={colors.muted}
                style={styles.input}
                value={condition.threshold}
                onChangeText={(threshold) => updateCondition(index, { threshold })}
              />
              {conditionRequiresWindow(condition.type) ? (
                <>
                  <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.windowLabel")}</Text>
                  <TextInput
                    accessibilityLabel={t("discovery:crypto.alerts.windowLabel")}
                    keyboardType="numeric"
                    placeholderTextColor={colors.muted}
                    style={styles.input}
                    value={condition.windowMinutes}
                    onChangeText={(windowMinutes) => updateCondition(index, { windowMinutes })}
                  />
                  <Text style={styles.hint}>
                    {t("discovery:crypto.alerts.windowHint", { min: MIN_WINDOW_MINUTES, max: MAX_WINDOW_MINUTES })}
                  </Text>
                </>
              ) : null}
            </View>
          );
        })}

        {form.conditions.length < MAX_CONDITIONS_PER_RULE ? (
          <ActionButton
            label={t("discovery:crypto.alerts.addCondition")}
            variant="secondary"
            onPress={() =>
              setForm((current) => ({ ...current, conditions: [...current.conditions, emptyConditionDraft()] }))
            }
          />
        ) : null}

        {form.conditions.length > 1 ? (
          <>
            <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.matchLabel")}</Text>
            <View style={styles.segmentWrap}>
              <Segment
                label={t("discovery:crypto.alerts.match.all")}
                active={form.match === "all"}
                onPress={() => setForm((current) => ({ ...current, match: "all" }))}
              />
              <Segment
                label={t("discovery:crypto.alerts.match.any")}
                active={form.match === "any"}
                onPress={() => setForm((current) => ({ ...current, match: "any" }))}
              />
            </View>
          </>
        ) : null}

        <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.frequencyLabel")}</Text>
        <View style={styles.segmentWrap}>
          <Segment
            label={t("discovery:crypto.alerts.frequency.once")}
            active={form.frequency === "once"}
            onPress={() => setForm((current) => ({ ...current, frequency: "once" }))}
          />
          <Segment
            label={t("discovery:crypto.alerts.frequency.everyCrossing")}
            active={form.frequency === "every_crossing"}
            onPress={() => setForm((current) => ({ ...current, frequency: "every_crossing" }))}
          />
          <Segment
            label={t("discovery:crypto.alerts.frequency.recurring")}
            active={form.frequency === "recurring"}
            onPress={() => setForm((current) => ({ ...current, frequency: "recurring" }))}
          />
        </View>

        <Text style={styles.fieldLabel}>{t("discovery:crypto.alerts.cooldownLabel")}</Text>
        <TextInput
          accessibilityLabel={t("discovery:crypto.alerts.cooldownLabel")}
          keyboardType="numeric"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={form.cooldownSeconds}
          onChangeText={(cooldownSeconds) => setForm((current) => ({ ...current, cooldownSeconds }))}
        />

        {draftNeedsPremium ? (
          <PremiumUpsellPanel
            body={t("discovery:crypto.alerts.advancedLocked")}
            onUpgrade={() => navigation.navigate("Premium")}
          />
        ) : null}

        <Text style={styles.hint}>
          {t("discovery:crypto.alerts.limits", {
            free: FREE_BASIC_RULE_LIMIT,
            premium: PREMIUM_RULE_LIMIT,
            conditions: MAX_CONDITIONS_PER_RULE
          })}
        </Text>

        <View style={styles.actionsRow}>
          <ActionButton
            label={
              busy === "save"
                ? t("discovery:crypto.alerts.saving")
                : editingId
                  ? t("discovery:crypto.alerts.save")
                  : t("discovery:crypto.alerts.create")
            }
            disabled={busy === "save"}
            onPress={saveForm}
          />
          {editingId ? (
            <ActionButton label={t("discovery:crypto.alerts.cancelEdit")} variant="secondary" onPress={resetForm} />
          ) : null}
        </View>
      </Panel>

      <Panel>
        <View style={styles.rowHead}>
          <Text style={styles.sectionTitle}>{t("discovery:crypto.alerts.listTitle")}</Text>
          <ActionButton
            label={t("discovery:crypto.alerts.historyAllCta")}
            variant="secondary"
            onPress={() => navigation.navigate("CryptoAlertHistory", {})}
          />
        </View>
        {items.length ? (
          items.map((alert) => (
            <View key={alert.id} style={styles.alertCard}>
              <View style={styles.rowHead}>
                <Text style={styles.rowTitle}>{alert.symbol}</Text>
                <View style={styles.pillRow}>
                  <Text style={[styles.pill, alert.rule_type === "advanced" ? styles.premiumPill : undefined]}>
                    {alert.rule_type === "advanced"
                      ? t("discovery:crypto.alerts.ruleType.advanced")
                      : t("discovery:crypto.alerts.ruleType.basic")}
                  </Text>
                  <Text style={[styles.pill, alert.enabled ? styles.readyPill : styles.warnPill]}>
                    {STATUS_LABEL_KEYS[alert.status] ? t(STATUS_LABEL_KEYS[alert.status]) : alert.status}
                  </Text>
                </View>
              </View>
              <Text style={styles.muted}>{conditionSummary(alert)}</Text>
              <Text style={styles.rowMeta}>
                {t("discovery:crypto.alerts.conditionCount", { count: alert.conditions.length })}
                {" · "}
                {alert.last_triggered_at
                  ? t("discovery:crypto.alerts.lastTriggered", { time: alert.last_triggered_at })
                  : t("discovery:crypto.common.never")}
              </Text>
              <View style={styles.actionsRow}>
                <ActionButton
                  label={
                    alert.enabled ? t("discovery:crypto.alerts.disable") : t("discovery:crypto.alerts.enable")
                  }
                  variant="secondary"
                  disabled={busy === `toggle:${alert.id}`}
                  onPress={() => toggleEnabled(alert)}
                />
                <ActionButton
                  label={t("discovery:crypto.alerts.edit")}
                  variant="secondary"
                  onPress={() => startEdit(alert)}
                />
                <ActionButton
                  label={t("discovery:crypto.alerts.historyCta")}
                  variant="secondary"
                  onPress={() => navigation.navigate("CryptoAlertHistory", { alertId: alert.id })}
                />
                <ActionButton
                  label={t("discovery:crypto.alerts.delete")}
                  variant="danger"
                  disabled={busy === `delete:${alert.id}`}
                  onPress={() => setPendingDeleteId(alert.id)}
                />
              </View>
              {pendingDeleteId === alert.id ? (
                <View style={styles.confirmBox}>
                  <Text style={styles.confirmText}>{t("discovery:crypto.alerts.deleteWarning")}</Text>
                  <View style={styles.actionsRow}>
                    <ActionButton
                      label={t("discovery:crypto.alerts.cancel")}
                      variant="secondary"
                      onPress={() => setPendingDeleteId(null)}
                    />
                    <ActionButton
                      label={t("discovery:crypto.alerts.confirmDelete")}
                      variant="danger"
                      disabled={busy === `delete:${alert.id}`}
                      onPress={() => removeAlert(alert)}
                    />
                  </View>
                </View>
              ) : null}
            </View>
          ))
        ) : (
          <Text style={styles.muted}>{t("discovery:crypto.alerts.empty")}</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("discovery:crypto.portfolio.title")}</Text>
        <Text style={styles.muted}>{t("discovery:crypto.entry.body")}</Text>
        <View style={styles.actionsRow}>
          <ActionButton
            label={t("discovery:crypto.entry.portfolioCta")}
            variant="secondary"
            onPress={() => navigation.navigate("CryptoPortfolio", {})}
          />
        </View>
      </Panel>
    </ScrollView>
  );
}

function Segment({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      style={[styles.segment, active ? styles.segmentActive : undefined]}
      onPress={onPress}
    >
      <Text style={[styles.segmentText, active ? styles.segmentTextActive : undefined]}>{label}</Text>
    </Pressable>
  );
}

function ActionButton({
  label,
  onPress,
  disabled,
  variant = "primary"
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger";
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      style={[
        styles.actionButton,
        variant === "secondary" ? styles.actionButtonSecondary : undefined,
        variant === "danger" ? styles.actionButtonDanger : undefined,
        disabled ? styles.disabled : undefined
      ]}
      onPress={onPress}
    >
      <Text style={[styles.actionButtonText, variant !== "primary" ? styles.actionButtonTextSecondary : undefined]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 12
  },
  actionButtonDanger: {
    backgroundColor: "rgba(255, 107, 107, 0.16)",
    borderColor: "rgba(255, 107, 107, 0.38)",
    borderWidth: 1
  },
  actionButtonSecondary: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderWidth: 1
  },
  actionButtonText: {
    color: "#08110f",
    fontSize: 13,
    fontWeight: "900"
  },
  actionButtonTextSecondary: {
    color: colors.text
  },
  actionsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  alertCard: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 6,
    paddingBottom: 14,
    paddingTop: 5
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  conditionCard: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 10
  },
  confirmBox: {
    backgroundColor: "rgba(255, 107, 107, 0.08)",
    borderColor: "rgba(255, 107, 107, 0.34)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    marginTop: 10,
    padding: 10
  },
  confirmText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 18
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  fieldLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  header: {
    gap: 5
  },
  hint: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 46,
    paddingHorizontal: 12
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  notice: {
    color: colors.accent,
    fontSize: 13,
    lineHeight: 19
  },
  pill: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 4
  },
  pillRow: {
    flexDirection: "row",
    gap: 6
  },
  premiumPill: {
    borderColor: colors.warning,
    color: colors.warning
  },
  readyPill: {
    borderColor: colors.accent,
    color: colors.accent
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  rowHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  rowMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  rowTitle: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  segment: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 10
  },
  segmentActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  segmentText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  segmentTextActive: {
    color: "#08110f"
  },
  segmentWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  warnPill: {
    borderColor: colors.warning,
    color: colors.warning
  }
}));
