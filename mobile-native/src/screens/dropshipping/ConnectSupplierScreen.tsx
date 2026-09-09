/**
 * Connect a supplier — supplier chosen, credential in, shop chosen, connected.
 *
 * ## The supplier is picked, not assumed
 *
 * The first step lists the suppliers this app can connect and the ones it
 * cannot yet, and the merchant chooses. CJ is the only connectable entry today,
 * so this step could be skipped — and skipping it is how a feature ends up with
 * a provider's name welded into a screen title, a route param and six strings.
 * Everything after this step reads from the chosen {@link SupplierProviderInfo},
 * so adding Printful is a data change.
 *
 * ## The shop step appears only when there is a choice to make
 *
 * The merchant pastes their supplier API key and this screen asks the provider
 * which shops that key can act for. When it names more than none, the merchant
 * picks one: collapsing that into a single "Connect" would silently bind the
 * store to whichever shop the provider happened to list first, and a merchant
 * running two shops through one supplier account would not find out until their
 * orders went to the wrong warehouse.
 *
 * When it names none, the connection is made without one. A supplier "shop" is
 * an external storefront — Shopify, Woo — authorized inside the merchant's
 * supplier account, and importing products into PulseSoc needs no such thing,
 * because PulseSoc is the storefront. Zero shops is therefore the expected
 * answer for a merchant who sells only here, and the screen used to answer it
 * with "your account connected, but it has no shops we can sell through yet"
 * and no way forward. That was false about the thing they came to do. Binding a
 * shop is still required before anything is *fulfilled* — the server refuses an
 * order it cannot trace back to a bound shop — but that is a later step.
 *
 * ## The credential does not live on this device
 *
 * `apiKey` is component state and nothing else. It is not written to
 * `expo-secure-store`, not put in a navigation param, not logged, and not sent
 * anywhere except the two request bodies that need it. It is cleared the moment
 * the connection succeeds. The server stores it encrypted against the
 * connection; this screen is a conduit, not a keychain.
 *
 * Also why the field is `secureTextEntry` with autocorrect and autocapitalise
 * off: a supplier key that a keyboard has "helpfully" title-cased is a key that
 * fails to connect for a reason the merchant cannot see.
 *
 * ## Sandbox is stated, not implied
 *
 * The platform connects suppliers in sandbox. The merchant is told before they
 * type anything, because discovering it after importing forty products is
 * discovering it too late.
 */

import { useCallback, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  connectSupplier,
  discoverSupplierShops,
  stateForError,
  PLANNED_SUPPLIER_PROVIDERS,
  SUPPLIER_PROVIDERS,
  type DropshippingState,
  type SupplierProviderInfo,
  type SupplierShop
} from "../../api/dropshipping";
import { StoreHeader, StoreSectionError } from "../../components/store";
import { useDropshippingScope } from "./useDropshippingScope";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route?: { params?: RootStackParamList["DropshippingConnect"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

type Step = "provider" | "credential" | "shop" | "connecting";

/** States a second attempt cannot change. */
const NOT_RETRYABLE: readonly DropshippingState[] = [
  "EMPTY",
  "UNAUTHORIZED",
  "SESSION_EXPIRED",
  "SUPPLIER_DISABLED",
  "PROVIDER_NETWORK_DISABLED",
  "STORE_NOT_APPROVED",
  "STORE_NOT_FOUND",
  "STORE_ACCESS_REVOKED",
  "STORE_MAPPING_MISSING",
  "SUPPLIER_CONNECTION_FORBIDDEN"
];

/**
 * The four failures that used to arrive as one sentence.
 *
 * "You're not signed in to this store any more" was shown for a rejected write
 * token, an expired session, a store the server could not match, and a store
 * whose selling access had been withdrawn. Three of those four are things a
 * signed-in merchant cannot fix by signing in again, so the one sentence sent
 * every merchant to the same dead end.
 *
 * Each says what happened and what the merchant can do about it. None of them
 * asserts anything about a store that is not theirs.
 */
const STORE_AUTHORITY_MESSAGES: Partial<Record<DropshippingState, string>> = {
  SESSION_EXPIRED: "Your session has expired. Sign in again to connect a supplier.",
  CSRF_INVALID:
    "This device couldn't prove the request came from you. Nothing was sent to your supplier — try again.",
  STALE_STORE_CONTEXT:
    "Your store details moved on while this screen was open. We've refreshed them — try again.",
  STORE_NOT_FOUND: "We couldn't match this store to your account.",
  STORE_ACCESS_REVOKED: "This store can no longer sell, so it can't connect a supplier.",
  STORE_NOT_APPROVED: "Your store isn't approved to sell yet, so it can't connect a supplier.",
  STORE_MAPPING_MISSING:
    "Your store isn't linked to a seller account yet, so there's nothing to connect a supplier to.",
  SUPPLIER_CONNECTION_FORBIDDEN: "Your role in this store can't connect suppliers."
};

export function ConnectSupplierScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [apiKey, setApiKey] = useState("");
  const [step, setStep] = useState<Step>("provider");
  const [provider, setProvider] = useState<SupplierProviderInfo | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [shops, setShops] = useState<SupplierShop[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ state: DropshippingState; message: string } | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  /**
   * Turn a thrown error into a sentence about *this* step.
   *
   * A rejected credential and an unreachable provider are the same HTTP shape to
   * a careless reader and completely different problems to a merchant: one means
   * "check what you pasted", the other means "this is not your fault, wait".
   */
  const describe = useCallback((error: unknown, during: "discover" | "connect") => {
    const state = stateForError(error);
    // The supplier's own name for the credential, so a merchant told their key
    // was rejected knows which of their keys is meant.
    const credential = provider?.credentialName ?? "supplier key";
    // The three below are conditions of this deployment, not of the merchant's
    // key. Saying "that key didn't work" to someone whose key is fine sends them
    // back to their supplier's dashboard to re-copy a correct credential.
    if (state === "SUPPLIER_DISABLED") {
      return {
        state,
        message:
          "Supplier connections are available in the PulseSoc sandbox but aren't enabled on this server yet."
      };
    }
    if (state === "PROVIDER_NETWORK_DISABLED") {
      return {
        state,
        message: "PulseSoc isn't cleared to talk to this supplier from this server yet. Your key wasn't the problem."
      };
    }
    const storeAuthority = STORE_AUTHORITY_MESSAGES[state];
    if (storeAuthority) return { state, message: storeAuthority };
    if (state === "INVALID_CREDENTIAL") {
      return { state, message: `That ${credential} wasn't accepted. Check you copied the whole thing.` };
    }
    if (state === "PROVIDER_UNAVAILABLE") {
      return { state, message: "Your supplier isn't responding. Nothing was connected — try again shortly." };
    }
    if (state === "UNAUTHORIZED") {
      return { state, message: "You're not signed in to this store any more." };
    }
    if (state === "SUPPLIER_DISCONNECTED") {
      return {
        state,
        message: `That ${credential} didn't work. Check you copied the whole thing.`
      };
    }
    // Everything left is unclassified: the server, or the supplier through it,
    // refused for a reason nobody named. This branch used to say "that key
    // didn't work" for any failure during discovery, which is the exact lie
    // this screen was reported for — a merchant whose key authenticates fine
    // was sent back to their supplier's dashboard to re-copy a correct
    // credential. The state we have does not mention the credential, so
    // neither does the sentence.
    if (during === "discover") {
      return {
        state,
        message: `Your ${provider?.name ?? "supplier"} account turned that request down. Nothing was connected — try again shortly.`
      };
    }
    return { state, message: "That connection couldn't be created. Nothing was saved." };
  }, [provider]);

  /**
   * Describe the failure, and repair the one kind that is repairable here.
   *
   * A stale store context is the app holding a store the server has since
   * stopped treating as canonical. Dropping the cached scope makes the next
   * attempt use the current one, so the merchant retries a screen rather than
   * restarting the app — which is what "sign in again" used to cost them.
   */
  const reload = scopeStatus.reload;
  const reportFailure = useCallback((error: unknown, during: "discover" | "connect") => {
    const outcome = describe(error, during);
    if (outcome.state === "STALE_STORE_CONTEXT") reload();
    return outcome;
  }, [describe, reload]);

  /**
   * Create the connection, with or without a supplier shop.
   *
   * `null` means this account has no external storefront to bind, which is the
   * ordinary shape for a merchant who sells only through PulseSoc. On failure
   * the merchant goes back to the step they actually came from — sending them
   * to a shop list that was empty would strand them on a blank screen.
   */
  const connect = useCallback(
    async (shop: SupplierShop | null) => {
      if (!scope) return;
      setStep("connecting");
      setBusy(true);
      setFailure(null);
      try {
        await connectSupplier(scope, { apiKey: apiKey.trim(), externalShopId: shop?.externalShopId ?? null });
        // Cleared before navigating, so the credential does not survive in a
        // state tree behind the screen the merchant lands on.
        setApiKey("");
        navigation.navigate("DropshippingSuppliers", { title: "Suppliers" });
      } catch (error) {
        setFailure(reportFailure(error, "connect"));
        setStep(shop ? "shop" : "credential");
      } finally {
        setBusy(false);
      }
    },
    [apiKey, navigation, reportFailure, scope]
  );

  const discover = useCallback(async () => {
    if (!scope || !apiKey.trim()) return;
    setBusy(true);
    setFailure(null);
    try {
      const found = await discoverSupplierShops(scope, apiKey.trim());
      if (found.length === 0) {
        // A valid key with no shops used to stop here and tell the merchant
        // their working account had nothing we could sell through — with no
        // way forward. That was never true of what they were trying to do: a
        // supplier "shop" is an external storefront authorized inside their
        // supplier account, and importing products needs none, because PulseSoc
        // is the storefront. Zero shops is the expected answer for a merchant
        // who sells only here, so the right response is to finish connecting.
        await connect(null);
        return;
      }
      setShops(found);
      setStep("shop");
    } catch (error) {
      setFailure(reportFailure(error, "discover"));
    } finally {
      setBusy(false);
    }
  }, [apiKey, connect, reportFailure, scope]);

  const canSubmit = Boolean(scope) && apiKey.trim().length > 0 && !busy;

  /**
   * Why the scope did not resolve, in the merchant's terms.
   *
   * A supplier feature that is switched off on this server answers 404 to the
   * scope lookup, and reporting that as "we couldn't work out which store" would
   * be the same class of lie this whole change exists to remove — blaming the
   * merchant's setup for a server condition.
   */
  const scopeFailure =
    scopeStatus.status.phase === "failed" ? scopeStatus.status.state : null;
  const scopeFailureMessage =
    scopeFailure === "SUPPLIER_DISABLED"
      ? "Supplier connections are available in the PulseSoc sandbox but aren't enabled on this server yet."
      : (scopeFailure && STORE_AUTHORITY_MESSAGES[scopeFailure]) ||
        (scopeFailure === "UNAUTHORIZED"
          ? "You're not signed in to this store any more."
          : "We couldn't work out which store to connect this supplier to.");

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Connect a supplier"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Connect a supplier"
        reducedMotion={reducedMotion}
      />

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={[
            styles.content,
            { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Sandbox connection</Text>
            <Text style={styles.cardBody}>
              Suppliers connect in sandbox on PulseSoc today. You can browse the full catalogue and
              import real product data, but no order is placed with the supplier and nothing ships.
            </Text>
          </View>

          {scopeFailure ? (
            <StoreSectionError
              message={scopeFailureMessage}
              // Nothing to retry when the feature is off or the store is not
              // approved — the second attempt fails identically.
              onRetry={
                scopeFailure === "SUPPLIER_DISABLED" || NOT_RETRYABLE.includes(scopeFailure)
                  ? null
                  : scopeStatus.reload
              }
              reducedMotion={reducedMotion}
            />
          ) : null}

          {step === "provider" ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Available suppliers</Text>
              {SUPPLIER_PROVIDERS.map((entry) => (
                <Pressable
                  key={entry.id}
                  style={styles.shopRow}
                  onPress={() => {
                    setProvider(entry);
                    setStep("credential");
                    setFailure(null);
                  }}
                  accessibilityRole="button"
                  accessibilityLabel={`Connect ${entry.name}`}
                >
                  <View style={styles.flex}>
                    <Text style={styles.shopName}>{entry.name}</Text>
                  </View>
                  <Text style={styles.shopAction}>Connect</Text>
                </Pressable>
              ))}
              {/* Named, and deliberately without a control. See the note on
                  PLANNED_SUPPLIER_PROVIDERS. */}
              <Text style={styles.plannedLabel}>Coming later</Text>
              <Text style={styles.cardBody}>{PLANNED_SUPPLIER_PROVIDERS.join(", ")}</Text>
            </View>
          ) : null}

          {(step === "credential" || step === "connecting") && provider ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Connect {provider.name}</Text>
              <Text style={styles.cardBody}>{provider.blurb}</Text>
              <Pressable
                onPress={() => setShowHelp((shown) => !shown)}
                accessibilityRole="button"
                accessibilityLabel={`Where do I find my ${provider.credentialName}?`}
              >
                <Text style={styles.helpToggle}>
                  Where do I find my {provider.credentialName}?
                </Text>
              </Pressable>
              {showHelp ? (
                <View style={styles.help}>
                  {provider.helpSteps.map((line) => (
                    <Text key={line} style={styles.cardBody}>
                      {line}
                    </Text>
                  ))}
                </View>
              ) : null}
              <TextInput
                style={styles.input}
                value={apiKey}
                onChangeText={setApiKey}
                placeholder={`Paste your ${provider.credentialName}`}
                placeholderTextColor={storeLight.text.muted}
                // A supplier key is case- and character-exact. Every one of
                // these off is a key the keyboard would quietly mangle.
                secureTextEntry
                autoCapitalize="none"
                autoCorrect={false}
                spellCheck={false}
                editable={!busy}
                accessibilityLabel={provider.credentialName}
                onSubmitEditing={() => {
                  if (canSubmit) void discover();
                }}
              />
              <Text style={styles.cardBody}>{provider.securityNote}</Text>
              <Pressable
                style={[styles.primary, canSubmit ? null : styles.primaryDisabled]}
                onPress={() => void discover()}
                disabled={!canSubmit}
                accessibilityRole="button"
                accessibilityState={{ disabled: !canSubmit }}
                accessibilityLabel={provider.connectCta}
              >
                <Text style={styles.primaryText}>{busy ? "Checking…" : provider.connectCta}</Text>
              </Pressable>
              <Pressable
                style={styles.secondary}
                onPress={() => {
                  setStep("provider");
                  setProvider(null);
                  setApiKey("");
                  setShowHelp(false);
                  setFailure(null);
                }}
                disabled={busy}
                accessibilityRole="button"
                accessibilityState={{ disabled: busy }}
                accessibilityLabel="Choose a different supplier"
              >
                <Text style={styles.secondaryText}>Choose a different supplier</Text>
              </Pressable>
            </View>
          ) : null}

          {step === "shop" ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Choose a shop</Text>
              <Text style={styles.cardBody}>
                Your {provider?.name ?? "supplier"} account can act for{" "}
                {shops.length === 1 ? "one shop" : `${shops.length} shops`}. Pick the one this store
                should import from.
              </Text>
              {shops.map((shop) => (
                <Pressable
                  key={shop.externalShopId}
                  style={styles.shopRow}
                  onPress={() => void connect(shop)}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityState={{ disabled: busy }}
                  accessibilityLabel={`Connect ${shop.name || `shop ${shop.externalShopId}`}`}
                >
                  <View style={styles.flex}>
                    <Text style={styles.shopName}>{shop.name || `Shop ${shop.externalShopId}`}</Text>
                    <Text style={styles.shopId}>ID {shop.externalShopId}</Text>
                  </View>
                  <Text style={styles.shopAction}>Connect</Text>
                </Pressable>
              ))}
              <Pressable
                style={styles.secondary}
                onPress={() => {
                  setStep("credential");
                  setShops([]);
                  setFailure(null);
                }}
                accessibilityRole="button"
                accessibilityLabel="Use a different key"
              >
                <Text style={styles.secondaryText}>Use a different key</Text>
              </Pressable>
            </View>
          ) : null}

          {failure ? (
            <StoreSectionError
              message={failure.message}
              // Nothing to retry when the session is gone, when the store
              // cannot sell, or when the blocker is this deployment rather
              // than the key. All of them fail identically the second time,
              // and a retry button says otherwise.
              onRetry={NOT_RETRYABLE.includes(failure.state) ? null : () => void discover()}
              reducedMotion={reducedMotion}
            />
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  flex: { flex: 1 },
  content: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  card: {
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  plannedLabel: { fontSize: 11, fontWeight: "700", color: storeLight.text.muted, letterSpacing: 0.6 },
  helpToggle: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },
  help: { gap: 6 },
  input: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 14
  },
  primary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  shopRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget + 8,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  shopName: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  shopId: { fontSize: 11, color: storeLight.text.muted },
  shopAction: { fontSize: 13, fontWeight: "700", color: storeLight.text.link }
});
