/**
 * Import settings — how this store prices, finishes and distributes imports.
 *
 * ## Why this screen has to exist
 *
 * The four values here are the ones the server consults on every import: the
 * pricing rule, what the supplier charges to ship, whether an import publishes
 * itself, and whether a published import is offered marketplace-wide. Until this
 * screen existed the row was readable, writable over HTTP, and unreachable from
 * the app — which makes it a hardcoded constant wearing a table, and makes the
 * import cart's own line about turning Marketplace listing on a promise about a
 * control nobody could find.
 *
 * The shipping allowance arrived in the same condition and is here for the same
 * reason. It is the one input that decides whether a margin is measured against
 * what the goods cost or against what they cost to get here, and on a cheap heavy
 * product those are different enough to turn a sale into a loss. A merchant who
 * cannot state it is a merchant whose margins are optimistic by construction.
 *
 * ## Every control writes immediately, and one field at a time
 *
 * Each control PATCHes the single field it owns, which is not a shortcut: four
 * independent settings share one row, and a whole-object write would send this
 * screen's stale copy of the other three. A merchant who changed their margin on
 * the import cart and their Marketplace toggle here would lose the margin.
 *
 * The toggles and the rule write on touch. The allowance is the exception and has
 * an explicit Save, because it is typed rather than picked: "1" is a waypoint on
 * the road to "12.50", and a field that wrote every keystroke would briefly price
 * the whole store against one cent of freight.
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
import { Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  CLEAR_SHIPPING_ALLOWANCE,
  getStoreImportPolicy,
  shippingAllowanceFromInput,
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
  /**
   * How many times the server has told us what this policy is.
   *
   * Bumped on a successful read and on a successful write, and never on an
   * optimistic update or a rollback. The allowance card is keyed on it, so it
   * re-seeds its text box from the stored figure exactly when that figure has been
   * confirmed — and holds the merchant's half-typed draft the rest of the time.
   *
   * A `useEffect` watching the value itself cannot do this job. An optimistic
   * write followed by a rollback moves the value there and back inside one React
   * commit, so the effect sees `null` before and `null` after, fires zero times,
   * and whether the draft survives a failed save comes down to how React happened
   * to batch. That is not a behaviour to leave to chance in a field that decides
   * how a whole store is priced.
   */
  const [confirmed, setConfirmed] = useState(0);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(async () => {
    if (!scope) return;
    setState("LOADING");
    try {
      const result = await getStoreImportPolicy(scope);
      setPolicy(result);
      setConfirmed((n) => n + 1);
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
          : { marketplaceAutolist: changes.marketplaceAutolist }),
        ...(changes.shippingAllowanceCents === undefined
          ? {}
          : {
              shippingAllowanceCents:
                changes.shippingAllowanceCents === CLEAR_SHIPPING_ALLOWANCE
                  ? null
                  : changes.shippingAllowanceCents,
              // The source has to move with the number. Left alone it would read
              // "PulseSoc's default" beside a figure the merchant typed a moment
              // ago, or "you set this" beside a field they just emptied.
              shippingAllowanceSource:
                changes.shippingAllowanceCents === CLEAR_SHIPPING_ALLOWANCE
                  ? "PLATFORM_DEFAULT"
                  : "STORE"
            })
      });
      try {
        setPolicy(await updateStoreImportPolicy(scope, changes));
        setConfirmed((n) => n + 1);
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

            {/* Directly under the pricing rule, because it is an input to that
                rule rather than a separate subject: the margin the merchant just
                chose is measured against item cost plus this. */}
            <ShippingAllowanceCard
              // Remounted on each confirmation rather than syncing through an
              // effect. See `confirmed` above: this is what makes "the draft
              // survives a failed save" a decision instead of a batching artefact.
              key={confirmed}
              cents={policy.shippingAllowanceCents}
              saving={saving}
              onSave={(cents) => void save({ shippingAllowanceCents: cents })}
              onClear={() => void save({ shippingAllowanceCents: CLEAR_SHIPPING_ALLOWANCE })}
            />

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

/**
 * What the supplier charges to ship one unit — the merchant's own figure.
 *
 * ## Three states, and none of them may be spelled "free"
 *
 * `null` is *not declared*. The copy for it has to say what that costs the
 * merchant — margins measured against the item alone — and must never round down
 * to "shipping: $0.00", which is the same sentence a supplier who really does ship
 * free would produce. Nothing on PulseSoc knows which is true, so the screen says
 * nobody has said.
 *
 * `0` is a declaration, not an absence: the merchant is stating that freight is
 * already inside the item price. It is the one value a merchant can type that
 * makes their margins *look* better, so saving it is called out before the tap
 * rather than explained after it.
 *
 * `N` is the ordinary case and the whole point of §12.
 *
 * ## Why this one has a Save when nothing else on the screen does
 *
 * It is typed. Every other control here is a tap that produces a complete value,
 * so writing on touch is safe. A number is incomplete while it is being entered,
 * and "1" on the way to "12.50" is a real allowance that would really be saved.
 *
 * ## The box is a draft; the sentence above it is the truth
 *
 * These have to be two different things, and the reason is the failed save. If the
 * box were the only place the allowance appeared, then a rejected write would
 * leave the merchant's "12.50" sitting in the one field they read the setting off,
 * indistinguishable from a store actually priced against $12.50. Discarding what
 * they typed fixes that and creates a worse problem — a 503 silently eating an
 * entry they now believe they made.
 *
 * So the stored figure is stated in the body copy, in prose, and the box holds
 * whatever the merchant is in the middle of. A failed save keeps the draft (Save
 * stays lit, the screen's error line is up, and the sentence above still reads
 * $9.00), and a confirmed save re-seeds the box by remounting this component.
 * Which of those happened is never inferred from a value moving.
 */
function ShippingAllowanceCard({
  cents,
  saving,
  onSave,
  onClear
}: {
  cents: number | null;
  saving: boolean;
  onSave: (cents: number) => void;
  onClear: () => void;
}) {
  // Seeded once. The parent remounts this component when the server confirms a
  // value, which is the only moment the box should be overwritten.
  const [field, setField] = useState(() => centsToField(cents));

  const parsed = shippingAllowanceFromInput(field);
  const typed = field.trim() !== "";
  const unusable = typed && parsed === null;
  const canSave = parsed !== null && parsed !== cents && !saving;
  const declaringFree = canSave && parsed === 0;

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>What your supplier charges to ship</Text>
      {/* States the stored figure, not the typed one. This sentence is what the
          merchant reads to know how their store is priced right now, which is why
          the text box beside it is free to hold an unsaved draft. */}
      <Text style={styles.cardBody}>
        {cents === null
          ? "Nobody has told us, so your margins are worked out on the item cost alone. On a cheap, heavy product that margin can be smaller than it looks — or gone."
          : cents === 0
            ? "You've told us shipping is already inside what your supplier charges for the item, so the item cost is the whole cost."
            : `Your store is set to ${dollars(cents)} a unit. Margins are worked out on the item cost plus that, so a price that only covers the goods shows up as the loss it is.`}
      </Text>

      <View style={styles.fieldRow}>
        <Text style={styles.fieldPrefix}>$</Text>
        <TextInput
          testID="import-policy-shipping-allowance"
          style={styles.field}
          value={field}
          onChangeText={setField}
          keyboardType="decimal-pad"
          placeholder="Not set"
          placeholderTextColor={storeLight.text.muted}
          accessibilityLabel="Supplier shipping cost per unit"
          editable={!saving}
        />
        <Pressable
          testID="import-policy-shipping-allowance-save"
          style={[styles.saveButton, canSave ? null : styles.saveButtonOff]}
          onPress={() => {
            // Guarded on the same condition that greys the button, not just on a
            // null parse. `disabled` is the affordance; this is the rule. They
            // have to be the same rule, or a press that arrives anyway — which is
            // exactly what a responder lagging one flush behind produces — would
            // re-save an unchanged figure or save one mid-keystroke.
            if (!canSave || parsed === null) return;
            onSave(parsed);
          }}
          disabled={!canSave}
          accessibilityRole="button"
          accessibilityLabel="Save shipping cost"
          accessibilityState={{ disabled: !canSave }}
        >
          <Text style={[styles.saveText, canSave ? null : styles.saveTextOff]}>Save</Text>
        </Pressable>
      </View>

      {unusable ? (
        <Text style={styles.warning}>Enter an amount like 4.50, or leave it blank.</Text>
      ) : null}

      {/* Said before the tap, not after it. Zero is the one entry that flatters
          the merchant's own numbers, so it is worth being sure they mean it. */}
      {declaringFree ? (
        <Text style={styles.warning}>
          Saving $0.00 tells us shipping is already in the item price, and prices every import that
          way.
        </Text>
      ) : null}

      {cents === null ? null : (
        <Pressable
          testID="import-policy-shipping-allowance-clear"
          style={styles.clearButton}
          onPress={onClear}
          disabled={saving}
          accessibilityRole="button"
          accessibilityLabel="I don't know what shipping costs"
          accessibilityState={{ disabled: saving }}
        >
          {/* Not the same as typing 0, and worded so it cannot be mistaken for it.
              This is the way back to "unknown" for a merchant who declared a
              figure they can no longer stand behind. */}
          <Text style={styles.clearText}>I'm not sure — take this back out</Text>
        </Pressable>
      )}
    </View>
  );
}

/** Cents to the string the merchant edits. `null` is blank, never "0.00". */
function centsToField(cents: number | null): string {
  return cents === null ? "" : (cents / 100).toFixed(2);
}

/** Cents to the amount the merchant reads in a sentence. */
function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
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
  fieldRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  fieldPrefix: { fontSize: 15, fontWeight: "700", color: storeLight.text.muted },
  field: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 15,
    fontWeight: "600"
  },
  saveButton: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 18,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.cta.from
  },
  // Greyed rather than hidden: a Save that appears the moment the field becomes
  // valid reads as the screen having changed under the merchant's thumb.
  saveButtonOff: { backgroundColor: storeLight.bg.skeleton },
  saveText: { fontSize: 14, fontWeight: "700", color: storeLight.cta.text },
  saveTextOff: { color: storeLight.text.muted },
  clearButton: { minHeight: storeLight.size.tapTarget, justifyContent: "center" },
  clearText: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  warning: { fontSize: 12, fontWeight: "600", color: storeLight.status.warning, lineHeight: 17 },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  switchText: { flex: 1, gap: 4 },
  error: { fontSize: 13, fontWeight: "600", color: storeLight.status.error, lineHeight: 18 }
});
