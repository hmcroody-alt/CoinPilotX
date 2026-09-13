/**
 * Import settings — how this store prices, finishes and distributes imports.
 *
 * ## Why this screen has to exist
 *
 * The three values here are the ones the server consults on every import: the
 * pricing rule, whether an import publishes itself, and whether a published
 * import is offered marketplace-wide. Until this screen existed the row was
 * readable, writable over HTTP, and unreachable from the app — which makes it a
 * hardcoded constant wearing a table, and makes the import cart's own line about
 * turning Marketplace listing on a promise about a control nobody could find.
 *
 * ## Every control writes immediately, and one field at a time
 *
 * There is no Save button. Each control PATCHes the single field it owns, which
 * is not a shortcut: three independent settings share one row, and a whole-object
 * write would send this screen's stale copy of the other two. A merchant who
 * changed their margin on the import cart and their Marketplace toggle here would
 * lose the margin.
 *
 * The server's answer replaces local state, rather than local state being trusted
 * because the request did not throw. `pricing_source` and `configured` are
 * computed server-side, so the screen cannot derive them from what it just sent.
 *
 * ## A failed write says so and puts the control back
 *
 * A toggle that stays where the merchant left it after a 503 is the worst outcome
 * available: the app then disagrees with the server about whether every future
 * import goes marketplace-wide, and the merchant has no way to see which of them
 * is right.
 */

import { useCallback, useEffect, useState } from "react";
import { ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  getStoreImportPolicy,
  stateForError,
  updateStoreImportPolicy,
  type DropshippingState,
  type PricingRule,
  type StoreImportPolicy
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import { DropshippingStateView, stateOwnsScreen } from "../../components/dropshipping/DropshippingStates";
import { PricingRulePicker } from "./PricingRulePicker";
import { useDropshippingScope } from "./useDropshippingScope";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  // Optional, like the hub's: this route's params are entirely optional, so the
  // navigator can mount it with none at all.
  route?: { params?: RootStackParamList["DropshippingImportPolicy"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function ImportPolicyScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [policy, setPolicy] = useState<StoreImportPolicy | null>(null);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(async () => {
    if (!scope) return;
    setState("LOADING");
    try {
      const result = await getStoreImportPolicy(scope);
      setPolicy(result);
      setState("READY");
    } catch (error) {
      // The policy is the whole content of this screen, so a failed read owns it.
      // Drawing an EMPTY here would be the §error-never-empty defect: "you have no
      // settings" instead of "we couldn't read your settings".
      setPolicy(null);
      setState(stateForError(error));
    }
  }, [scope]);

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  /**
   * Write one field and take the server's word for the result.
   *
   * Optimistic on purpose — a toggle that waits for a round trip before moving
   * reads as a dead control — but reconciled, not assumed: the response replaces
   * the whole policy, and a rejection restores what was there before.
   */
  const save = useCallback(
    async (changes: Parameters<typeof updateStoreImportPolicy>[1]) => {
      if (!scope || !policy) return;
      const previous = policy;
      setSaving(true);
      setSaveError(null);
      setPolicy({
        ...policy,
        ...(changes.pricingRule === undefined ? {} : { pricingRule: changes.pricingRule }),
        ...(changes.autoPublish === undefined ? {} : { autoPublish: changes.autoPublish }),
        ...(changes.marketplaceAutolist === undefined
          ? {}
          : { marketplaceAutolist: changes.marketplaceAutolist })
      });
      try {
        setPolicy(await updateStoreImportPolicy(scope, changes));
      } catch {
        setPolicy(previous);
        setSaveError("That didn't save. Your settings are unchanged.");
      } finally {
        setSaving(false);
      }
    },
    [policy, scope]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Your import settings"
      onRetry={state === "UNAUTHORIZED" ? null : () => load()}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={3}
      empty={{
        title: "You need a store before you can set import rules.",
        body: "Set your store up on PulseSoc, then come back to choose how imports are priced."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Import settings"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
        unreadCount={0}
        searchPlaceholder="Import settings"
        reducedMotion={reducedMotion}
      />

      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
      >
        {stateBlock ? (
          <View style={styles.block}>{stateBlock}</View>
        ) : policy ? (
          <View style={styles.body}>
            <View style={styles.card}>
              <Text style={styles.cardTitle}>How imports are priced</Text>
              <Text style={styles.cardBody}>
                {policy.configured
                  ? "Every product you import is priced by this rule, applied to the cost PulseSoc reads from your supplier at import time."
                  : "You haven't set a rule, so PulseSoc uses this one. It's applied to the cost read from your supplier at import time."}
              </Text>
              <PricingRulePicker
                rule={policy.pricingRule}
                onChange={(rule: PricingRule) => void save({ pricingRule: rule })}
                sampleCostCents={[]}
                currency={null}
              />
            </View>

            <View style={styles.card}>
              <PolicySwitch
                testID="import-policy-auto-publish"
                title="Publish imports automatically"
                subtitle={
                  policy.autoPublish
                    ? "Imports that PulseSoc can complete safely go live in your store straight away. Anything it can't stays a draft and tells you why."
                    : "Imports are saved as drafts. Nothing goes live until you publish it yourself."
                }
                value={policy.autoPublish}
                disabled={saving}
                onValueChange={(next) => void save({ autoPublish: next })}
              />
            </View>

            <View style={styles.card}>
              <PolicySwitch
                testID="import-policy-marketplace-autolist"
                title="List imports across the PulseSoc Marketplace"
                subtitle={
                  policy.marketplaceAutolist
                    ? "Imported products are offered marketplace-wide, subject to Marketplace policy and review."
                    : "Imported products stay in your own store. Turn this on to offer them across PulseSoc."
                }
                value={policy.marketplaceAutolist}
                disabled={saving}
                onValueChange={(next) => void save({ marketplaceAutolist: next })}
              />
              {/* Said whichever way the toggle is set, because the distinction is
                  the point of §19/§20: publishing to a store and distributing
                  across a marketplace are two decisions, and one tap makes only
                  the first. */}
              <Text style={styles.footnote}>
                Publishing puts a product in your store. Marketplace listing is a separate decision, and
                it's yours.
              </Text>
            </View>

            {saveError ? <Text style={styles.error}>{saveError}</Text> : null}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function PolicySwitch({
  testID,
  title,
  subtitle,
  value,
  disabled,
  onValueChange
}: {
  testID: string;
  title: string;
  subtitle: string;
  value: boolean;
  disabled: boolean;
  onValueChange: (next: boolean) => void;
}) {
  return (
    <View style={styles.switchRow}>
      <View style={styles.switchText}>
        <Text style={styles.cardTitle}>{title}</Text>
        <Text style={styles.cardBody}>{subtitle}</Text>
      </View>
      <Switch
        testID={testID}
        value={value}
        disabled={disabled}
        onValueChange={onValueChange}
        accessibilityRole="switch"
        accessibilityLabel={title}
        accessibilityState={{ checked: value, disabled }}
        trackColor={{ false: storeLight.border.secondaryButton, true: storeLight.cta.from }}
        thumbColor={storeLight.bg.card}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: 24 },
  block: { padding: storeLight.space.card },
  body: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  card: {
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  footnote: { fontSize: 11, color: storeLight.text.muted, lineHeight: 16 },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  switchText: { flex: 1, gap: 4 },
  error: { fontSize: 13, fontWeight: "600", color: storeLight.status.error, lineHeight: 18 }
});
